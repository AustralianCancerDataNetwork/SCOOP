"""
Splitting sig bundles into cohorent parts for timing

"""

import random
import re
from collections import defaultdict
from typing import Any
import warnings
import pandas as pd
from pydantic import BaseModel, ValidationError, computed_field, PrivateAttr
from hemonc_import.final_model.schedule_handling import resolve_all_days


NEOADJ_WEEKS_TO_SURGERY = 4 # https://doi.org/10.21037/tlcr-24-781
ADJ_WEEKS_FROM_SURGERY = 4


class BaseRandomSampler(BaseModel):
    """
    Base for random sampling behaviour in scheduling objects.

    Auto-rescues low>high w/ warning,
    and handles lazy, type-aware local pseudorandom sampling.
    """
    seed: int | None

    _engine_seed: int = PrivateAttr()
    _rng: random.Random | None = PrivateAttr(default=None)

    def model_post_init(self, context:Any) -> None:
        if self.seed is not None:
            self._engine_seed = self.seed
        else:
            self._engine_seed = random.SystemRandom().randint(0, 2**32 - 1)

    @property
    def _engine(self) -> random.Random:
        if self._rng is None:
            self._rng = random.Random(self._engine_seed)
        return self._rng

    def _get_value(self, lb: float, ub: float) -> float: # can be int specifically
        """Obtains a value defined by upper and lower bounds."""
        if lb == ub:
            return lb

        if lb > ub:
            # Replace with logging integration eventually
            warnings.warn(
                f"Inverted boundaries detected in {self.__class__.__name__}: "
                f"lower bound ({lb}) was greater than upper bound ({ub})." 
                "Auto-swapping bounds.",
                RuntimeWarning,
                stacklevel=2
            )
            lb, ub = ub, lb
        
        if isinstance(lb, float) or isinstance(ub, float):
            return self._engine.uniform(lb, ub)
            
        return self._engine.randint(lb, ub)



class Component(BaseModel):
    # all the info here cui etc.
    name: str
    cui: int
    # role: str # maybe this isn't reliable here - move to component part?
    drug_object: Any | None # TODO: fix later as importing ho type

    @classmethod
    def from_sig(cls, sig) -> "Component":
        return cls(name=sig.component, cui=sig.component_cui, drug_object=sig.drug_object)



class SeqRelInfo(BaseModel):
    """
    Relative sequencing info

    Describes the relative order of components delivered
    on the same day as each other when specified.
    """
    sequence: str
    seq_rel_when: str
    seq_rel_when_unit: str
    seq_rel: str
    seq_rel_what: str


class StandardDoseInfo(BaseRandomSampler):
    """
    For when divided is false and frequency is not continuous. 
    """
    doseminnum: float
    dosemaxnum: float
    doseunit: str
    doseunit_cui: int
    @classmethod
    def from_sig(cls, sig, seed: int | None = None):

        return cls(doseminnum=sig.doseminnum, dosemaxnum=sig.dosemaxnum,
                   doseunit=sig.doseunit, doseunit_cui=sig.doseunit_cui, seed=seed)
    
    def get_dose(self)-> float:
        return self._get_value(self.doseminnum, self.dosemaxnum)


class Duration(BaseRandomSampler):
    """
    For when frequency is continuous
    """
    durationminnum: float
    durationmaxnum: float
    durationunit: str #Literal["MINUTE", "HOUR", "DAY" ] # TODO: import ho type
    durationunit_cui: int
    @classmethod
    def from_sig(cls, sig) -> "Duration":
        return cls(durationminnum=sig.durationminnum,
                   durationmaxnum=sig.durationmaxnum,
                   durationunit=sig.durationunit,
                   durationunit_cui=sig.durationunit_cui)
    
    def get_duration(self)-> float:
            return self._get_value(self.durationminnum, self.durationmaxnum)


# TODO: What is this doing here? Deal w/ in testing file
def try_attribute(fn, arg):
    """Tries to return fn(arg) result """
    try:
        return fn(arg)
    except ValidationError:
        return None


class ComponentPart(BaseModel):
    """
    One component step

    Dose etc. is tied to this level as may e.g. be titrated down over parts
    """
    component: Component
    step_int: int
    step_max: int
    route: str #Literal[]
    route_cui: int
    frequency: str
    frequency_cui: int
    # This belongs here but delaying until example
    # to guide implementation
    # seq_rel_info: Optional[SeqRelInfo]
    dose_info: StandardDoseInfo | None
    duration: Duration | None
    role: str 
    # target level belongs here too

    @classmethod
    def from_sig(cls, sig):
        part_text = sig.step_number
        part_numbers = re.findall(r"\d+", part_text)
        return cls(component=Component.from_sig(sig),
                   step_int=part_numbers[0],
                   step_max=part_numbers[1],
                   route=sig.route, route_cui=sig.route_cui,
                   frequency=sig.frequency,
                   frequency_cui=sig.frequency_cui,
                   dose_info=try_attribute(StandardDoseInfo.from_sig, sig),
                   duration=try_attribute(Duration.from_sig, sig),
                   role=sig.component_role)

    def get_dose(self, units_as_cuis=False):
        # TODO: make more robust
        if self.duration:
            if units_as_cuis:
                return self.duration.get_duration(), self.duration.durationunit_cui
            else:
                return self.duration.get_duration(), self.duration.durationunit
        else:
            if units_as_cuis:
                return self.dose_info.get_dose(), self.dose_info.doseunit_cui
            else:
                return self.dose_info.get_dose(), self.dose_info.doseunit


def get_continue(ho_timing):    
    continue_match = re.search(r"\(\+(\d+)\)$", ho_timing)
    if continue_match:
        return continue_match.group(1)
    else:
        return None

def split_ho_timing(ho_timing):
    timing_list = resolve_all_days(ho_timing)
    optional = []
    base = []
    for day in timing_list:
        if day.optional:
            optional.append(day.value)
        else:
            base.append(day.value)
    every = base + optional
    every.sort()
    return {"every":every, "base":base, "optional":optional, 
            "ongoing_step":get_continue(ho_timing)}

class CycleEvents(BaseModel):
    """
    Essentially timing sequence

    Base gives required/core cycles
    optional gives optional cycles
    ongoing is None if no (+n), otherwise is n
    """
    every: list[int]
    base: list[int]
    optional: list[int]
    ongoing_step: int | None

    @classmethod
    def from_sig(cls, sig):
        return cls(**split_ho_timing(sig.timing_sequence))
    
    @computed_field
    @property
    def first_event(self) -> int:
        return self.every[0]

    def check_active_this_cycle(self, cycle_num):
        # TODO: make more sophisticated later, consider ongoing etc.
        return cycle_num in self.every


class PerCycleDays(BaseModel):
    """
    Essentially allDays

    Base gives required/core days
    optional gives optional days
    ongoing is None if no (+n), otherwise is n
    """
    every: list[int]
    base: list[int]
    optional: list[int]
    ongoing_step: int | None
    # NOTE: Currently continuous will mean one day
    # even if it is for over 24 hours
    # this is bad. The days should be duration in hours / 24 (?rounded up)
    @classmethod
    def from_sig(cls, sig):
        return cls(**split_ho_timing(sig.alldays))



class Trio(BaseModel):
    """
    Association of the three core elements for timing:
    which cycles, what component instructions, on which cycle days
    """
    per_cycle_days: PerCycleDays
    component_part: ComponentPart
    cycle_events: CycleEvents
    @classmethod
    def from_sig(cls, sig):
        return cls(per_cycle_days=PerCycleDays.from_sig(sig),
                   component_part= ComponentPart.from_sig(sig),
                   cycle_events = CycleEvents.from_sig(sig))

    def get_days_for_cycle(self):
        # TODO: Make more sophisticated with ongoing, optional etc. 
        return self.per_cycle_days.every, self.component_part


class CycleSet(BaseRandomSampler):
    """
    A cycle set is a set of cycles which share the same
    cycle length, cycle length unit, cycle sigs and
    where each component's cycle events have the same 
    minimum index

    Cycle sets represent a somewhat similar concept to portions
    however portions are not strictly defined
    """
    cycle_length_lb: int
    cycle_length_ub: int
    cycle_len_unit: str
    cycle_sigs: str
    trios: list[Trio]
    label: str
    all_cycle_ints: set

    @classmethod
    def from_siglist(cls, siglist: list, seed: int | None = None) -> "CycleSet":
        trios = [Trio.from_sig(sig) for sig in siglist]
        all_cycle_ints = set()
        for trio in trios:
            all_cycle_ints.update(trio.cycle_events.every)
        return cls(cycle_length_lb=siglist[0].cycle_length_lb, 
                   cycle_length_ub=siglist[0].cycle_length_ub, 
                   cycle_len_unit=siglist[0].cycle_length_unit, 
                   cycle_sigs=siglist[0].cyclesigs, 
                   label = siglist[0].portion, 
                   trios=trios,
                   all_cycle_ints = all_cycle_ints,
                   seed=seed)
    
    def get_cycle_length(self) -> int:
        return self._get_value(self.cycle_length_lb, self.cycle_length_ub)
    
    # def_add_one_cycle
    def get_one_systemic_cycle(self, cycle_number, cycle_start_date):
        within_cycle_administration = defaultdict(list)
        for trio in self.trios:
            if trio.cycle_events.check_active_this_cycle(cycle_number):
                days, component_part = trio.get_days_for_cycle()
                dates = [cycle_start_date + pd.DateOffset(days=day) for day in days]
                for date in dates:
                    within_cycle_administration[date].append(component_part)
        return within_cycle_administration



    # cached property biggest_base


# I made this when I was working on CycleSet population
# because I realised I wanted a way to sort the sigs to ensure the pairwise
# comparisons wouldn't fall apart if sigs that should be in the same cycle set
# had a dissimilar sig between them by chance
class CycleEventValues(BaseModel):
    """Contains CycleSet attributes without sigs for sorting purposes"""
    first_cycle: int
    cycle_length_lb: int
    cycle_length_ub: int
    cycle_length_unit: str

    @classmethod
    def from_sig(cls, sig):
        return cls(first_cycle=CycleEvents.from_sig(sig).first_event,
                 cycle_length_lb=sig.cycle_length_lb, cycle_length_ub=sig.cycle_length_ub,
                 cycle_length_unit=sig.cycle_length_unit)
    
    def __lt__(self, other):
        if not isinstance(other, CycleEventValues):
            return NotImplemented
        return (self.first_cycle, self._get_cycle_length_days()) < (other.first_cycle, other._get_cycle_length_days())
        
    def _get_cycle_length_days(self):
        """Rough approximation"""
        DAYS_IN_UNIT = {"day": 1, "week":7, "month": 30}
        # Can't find true median mean etc. w/o assumptions about probability
        central_cycle_length = 0.5 * (self.cycle_length_lb + self.cycle_length_ub)
        return DAYS_IN_UNIT[self.cycle_length_unit]*central_cycle_length


class SystemicPhase(BaseModel):
    cycle_sets: list[CycleSet]

    @classmethod
    def from_component_sigs(cls, siglist: list) -> "SystemicPhase":
        sorted_siglist = sorted(siglist, key=lambda sig: CycleEventValues.from_sig(sig))
        cycleset_siglist = [sorted_siglist[0]]
        cyclesets = []
        for i in range(1, len(sorted_siglist)):
            left_sig = sorted_siglist[i-1]
            right_sig = sorted_siglist[i]
            has_same_cycle_events = CycleEventValues.from_sig(left_sig) == CycleEventValues.from_sig(right_sig)
            if has_same_cycle_events:
                cycleset_siglist.append(right_sig)
            else:
                cyclesets.append(CycleSet.from_siglist(cycleset_siglist))
                cycleset_siglist = [right_sig]

            is_final_cycle = i == len(sorted_siglist)-1
            if is_final_cycle:
                cyclesets.append(CycleSet.from_siglist(cycleset_siglist))
        return cls(cycle_sets=cyclesets)

    def get_scheduled_dates(self, phase_start_date):
        cycle_number = 1
        cycle_start_date = phase_start_date
        all_component_parts = {}
        while True:
            was_cycle = False
            for cycle_set in self.cycle_sets:
                if cycle_number in cycle_set.all_cycle_ints:
                    was_cycle = True
                    cycle_increment = increment_cycle_date(
                        cycle_set.get_cycle_length(), cycle_set.cycle_len_unit
                        )
                    # TODO: Make robust to different portions having overlapping cycles
                    all_component_parts = all_component_parts | cycle_set.get_one_systemic_cycle(cycle_number, cycle_start_date)

            if not was_cycle:
                # TODO: Make robust to blank cycles?
                break

            cycle_number += 1
            cycle_start_date += cycle_increment
        return all_component_parts




class ProtocolScheduler(BaseModel):
    """
    Full treatment roll out including multi-phase handling
    """
    ...


def increment_cycle_date(cycle_len: int, cycle_len_unit: str):
    cycle_increment_size = {
        "day":pd.DateOffset(days=cycle_len),
        "week":pd.DateOffset(weeks=cycle_len),
        "month":pd.DateOffset(months=cycle_len),
        "year":pd.DateOffset(years=cycle_len)
    }
    return cycle_increment_size[cycle_len_unit]