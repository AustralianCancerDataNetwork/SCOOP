"""
Scheduling objects facilitating timing roll out.

Attributes are purpose driven over faithfulness driven.

I.e. although each object should have a from_[treatment_container_object]
method, attributes populated in this way MAY be validly overwritten if the
over writing better serves the purpose of answering the question for which
the object exists. 

E.g. some RT sigs have alldays None. DayPattern exists to answer the question
"on which days of the cycle is this ComponentPart rolled out?" therefore, it is
valid to populate the attributes of DayPattern with lists of integers and not
have all attributes as None just because default population pulls from 
sig.alldays.
"""

import random
import re
from collections import defaultdict
from typing import Any
import warnings
import pandas as pd
from pydantic import BaseModel, ValidationError, computed_field, PrivateAttr
from hemonc_alchemy.toolkit.analytics.treatment.scheduling import resolve_all_days
from hemonc_alchemy.toolkit.analytics.treatment.scheduling.tokens import Indefinite


NEOADJ_WEEKS_TO_SURGERY = 4 # https://doi.org/10.21037/tlcr-24-781
ADJ_WEEKS_FROM_SURGERY = 4
WITHIN_COMPONENT_CONTINUE_PROB = 0.8
WITHIN_CYCLESET_CONTINUE_PROB =  0.97
OPTIONAL_CYCLE_PROB = 0.7 # completely made up without much thought

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

    def _get_value_in_range(self, lb: float, ub: float) -> float: # can be int specifically
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

    def _get_bool(self, true_prob: float=0.5):
        return self._engine.random() < true_prob



class Component(BaseModel):
    name: str
    cui: int
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


class DoseCap(BaseModel):
    dosecapnum: float | None
    dosecapunit: str | None
    dosecapunit_cui: int | None
    @classmethod
    def from_sig(cls, sig):
        return cls(dosecapnum=sig.dosecapnum, 
                   dosecapunit=sig.dosecapunit,
                   dosecapunit_cui=sig.dosecapunit_cui)


class DoseRule(BaseRandomSampler):
    """
    For not continuous. 
    """
    doseminnum: float
    dosemaxnum: float
    doseunit: str
    doseunit_cui: int
    divided: bool
    dose_cap: DoseCap | None
    
    @classmethod
    def from_sig(cls, sig, seed: int | None = None):

        return cls(doseminnum=sig.doseminnum, dosemaxnum=sig.dosemaxnum,
                   doseunit=sig.doseunit, doseunit_cui=sig.doseunit_cui,
                   divided=getattr(sig, "divided", False),
                   dose_cap=try_attribute(DoseCap.from_sig, sig), seed=seed)
    
    def get_dose(self)-> float:
        return self._get_value_in_range(self.doseminnum, self.dosemaxnum)


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
            return self._get_value_in_range(self.durationminnum, self.durationmaxnum)


# This is useful for ComponentPart population of duration and dose info
# whose presense is not guaranteed
def try_attribute(fn, arg):
    """Tries to return fn(arg) result """
    try:
        return fn(arg)
    except (ValidationError, AttributeError, TypeError):
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
    route_cui: int | None
    frequency: str | None
    frequency_cui: int | None
    # This belongs here but delaying until example
    # to guide implementation
    # seq_rel_info: Optional[SeqRelInfo]
    dose_info: DoseRule | None
    duration: Duration | None
    role: str 
    # target level belongs here too
    class_field: str
    source_sig_id: int | None = None

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
                   dose_info=try_attribute(DoseRule.from_sig, sig),
                   duration=try_attribute(Duration.from_sig, sig),
                   role=getattr(sig, "component_role", ""),
                   class_field=getattr(sig, "class_field", "") or "",
                   source_sig_id=getattr(sig, "id", None),)

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
    """Return the cycle step represented by a toolkit indefinite marker."""

    if not ho_timing:
        return None
    # Existing SCOOP profiles use (+1), while the shared parser uses (+n1).
    normalised = re.sub(r"\(\+(\d+)\)$", r"(+n\1)", ho_timing)
    resolved = resolve_all_days(normalised)
    indefinite = resolved.indefinite
    if isinstance(indefinite, Indefinite) and indefinite.kind == "+n":
        return indefinite.max_days
    return None

def split_ho_timing(ho_timing):
    normalised = re.sub(r"\(\+(\d+)\)$", r"(+n\1)", ho_timing or "")
    timing_list = resolve_all_days(normalised)
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

class CycleWindow(BaseRandomSampler):
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
    _discontinued: bool = False

    @classmethod
    def from_sig(cls, sig, seed: int | None = None):
        return cls(**split_ho_timing(sig.timing_sequence), seed=seed)
    
    @computed_field
    @property
    def first_event(self) -> int:
        return self.every[0]

    def check_active_this_cycle(self, cycle_num, ongoing_prob:float | None = None,
                                optional_prob:float | None = None):
        if cycle_num in self.base:
            return True
        if cycle_num in self.optional:
            # TODO: consider guarding against discontinue like for ongoing
            if not optional_prob:
                optional_prob = OPTIONAL_CYCLE_PROB
            return self._get_bool(optional_prob)
        if self.ongoing_step:
            if not ongoing_prob:
                ongoing_prob = WITHIN_COMPONENT_CONTINUE_PROB
            return consider_ongoing(self, cycle_num, ongoing_prob)
        return False

    def reset(self):
        self._discontinued = False

class DayPattern(BaseModel):
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


class OtherSigInfo(BaseModel):
    in_parens: str | None
    tail: str | None
    temp: str | None
    sig_id: int | None
    date_added: Any
    @classmethod
    def from_sig(cls, sig):
        return cls(
            in_parens=getattr(sig, "inparens", None),
            tail=getattr(sig, "tail", None),
            temp=getattr(sig, "temp", None),
            sig_id=getattr(sig, "id", None),
            date_added=getattr(sig, "date_added", None),
        )


class SigSchedule(BaseModel):
    """
    Association of the three core elements for timing:
    which cycles, what component instructions, on which cycle days
    """
    day_pattern: DayPattern
    component_part: ComponentPart
    cycle_window: CycleWindow
    other_info: OtherSigInfo
    @classmethod
    def from_sig(cls, sig, seed: int | None = None):
        return cls(day_pattern=DayPattern.from_sig(sig),
                   component_part= ComponentPart.from_sig(sig),
                   cycle_window = CycleWindow.from_sig(sig, seed),
                   other_info = OtherSigInfo.from_sig(sig))

    def add_to_deliveries(self, cycle_start_date, within_cycle_deliveries):
            days, component_part = self.get_days_for_cycle()
            # HemOnc timing values are one-based: day 1 is the cycle start.
            dates = [cycle_start_date + pd.DateOffset(days=day - 1) for day in days]
            for date in dates:
                within_cycle_deliveries[date].append(component_part)
            return within_cycle_deliveries

    def get_days_for_cycle(self):
        # TODO: Make more sophisticated with ongoing, optional etc. 
        return self.day_pattern.every, self.component_part

    def reset(self):
        self.cycle_window.reset()
    

class OriginalContext(BaseModel):
    phase: str | None
    study: str | None
    regimen: str | None
    regimen_cui: int | None
    variant: str | None
    variant_cui: int | None
    branch: str | None
    branch_type: str | None

    @classmethod
    def from_sig(cls, sig):
        return cls(
            phase=getattr(sig, "phase", None),
            study=getattr(sig, "study", None),
            regimen=getattr(sig, "regimen", None),
            regimen_cui=getattr(sig, "regimen_cui", None),
            variant=getattr(sig, "variant", None),
            variant_cui=getattr(sig, "variant_cui", None),
            branch=getattr(sig, "branch", None),
            branch_type=getattr(sig, "branch_type", None),
        )


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
    sig_schedules: list[SigSchedule]
    label: str
    every: set
    ongoing_step: int | None
    _discontinued: bool = False

    @classmethod
    def from_siglist(cls, siglist: list, cycle_set_seed: int | None = None,
                     sig_schedule_seeds: list[int] | None = None) -> "CycleSet":
        if sig_schedule_seeds:
            sig_schedules = [SigSchedule.from_sig(sig, seed) for sig, seed in zip(siglist, sig_schedule_seeds)]
        else:
            sig_schedules = [SigSchedule.from_sig(sig) for sig in siglist]
        every = set()
        for sig_schedule in sig_schedules:
            every.update(sig_schedule.cycle_window.every)
        return cls(cycle_length_lb=siglist[0].cycle_length_lb, 
                   cycle_length_ub=siglist[0].cycle_length_ub, 
                   cycle_len_unit=siglist[0].cycle_length_unit, 
                   cycle_sigs=siglist[0].cyclesigs, 
                   label = siglist[0].portion, 
                   sig_schedules=sig_schedules,
                   every = every,
                   ongoing_step=cls._get_cycle_set_ongoing(sig_schedules),
                   seed=cycle_set_seed)
    
    def get_cycle_length(self) -> int:
        return self._get_value_in_range(self.cycle_length_lb, self.cycle_length_ub)
    
    def get_one_systemic_cycle(
        self,
        cycle_number: int,
        cycle_start_date,
        ongoing_prob,
        optional_prob: float | None = None,
    ):
        within_cycle_administration = defaultdict(list)
        for sig_schedule in self.sig_schedules:
            if sig_schedule.cycle_window.check_active_this_cycle(
                cycle_number,
                ongoing_prob,
                optional_prob,
            ):
                within_cycle_administration = sig_schedule.add_to_deliveries(
                    cycle_start_date, within_cycle_administration
                    )
        return within_cycle_administration

    @staticmethod
    def _get_cycle_set_ongoing(sig_schedules: list[SigSchedule]) -> int | None:
        any_not_ongoing = any(sig_schedule.cycle_window.ongoing_step is None for sig_schedule in sig_schedules)
        # min_ongoing_step = 
        return None if any_not_ongoing else min(sig_schedule.cycle_window.ongoing_step for sig_schedule in sig_schedules)

    def get_normally_active_this_cycle(self, cycle_number):
        is_active = cycle_number in self.every
        return is_active

    def reset(self):
        self._discontinued = False
        for sig_schedule in self.sig_schedules:
            sig_schedule.reset()


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
        return cls(first_cycle=CycleWindow.from_sig(sig).first_event,
                 cycle_length_lb=sig.cycle_length_lb, cycle_length_ub=sig.cycle_length_ub,
                 cycle_length_unit=sig.cycle_length_unit)
    
    def __lt__(self, other):
        if not isinstance(other, CycleEventValues):
            return NotImplemented
        return (self.first_cycle, self._get_cycle_length_days()) < (other.first_cycle, other._get_cycle_length_days())
        
    def _get_cycle_length_days(self):
        """Rough approximation"""
        DAYS_IN_UNIT = {"day": 1, "week":7, "month": 30}
        central_cycle_length = 0.5 * (self.cycle_length_lb + self.cycle_length_ub)
        return DAYS_IN_UNIT[self.cycle_length_unit]*central_cycle_length


class PhaseSchedule(BaseModel):
    cycle_sets: list[CycleSet]
    original_context: OriginalContext
    @classmethod
    def from_component_sigs(
        cls,
        siglist: list,
        seed: int | None = None,
    ) -> "PhaseSchedule":
        if not siglist:
            raise ValueError("PhaseSchedule requires at least one signature")
        if len(siglist)==1:
            cyclesets = [
                CycleSet.from_siglist(
                    siglist,
                    cycle_set_seed=seed,
                    sig_schedule_seeds=[seed] if seed is not None else None,
                )
            ]
        else:
            cyclesets = cls._get_CycleSets(siglist, seed=seed)
        return cls(cycle_sets=cyclesets, original_context=OriginalContext.from_sig(siglist[0]))

    @staticmethod
    def _get_CycleSets(siglist, seed: int | None = None):
        """Gets CycleSets iff multiple sigs in siglist"""
        rng = random.Random(seed) if seed is not None else None
        sorted_siglist = sorted(siglist, key=lambda sig: CycleEventValues.from_sig(sig))
        cycleset_siglist = [sorted_siglist[0]]
        cyclesets = []

        def make_cycleset(group):
            return CycleSet.from_siglist(
                group,
                cycle_set_seed=rng.randrange(2**32) if rng else None,
                sig_schedule_seeds=(
                    [rng.randrange(2**32) for _ in group] if rng else None
                ),
            )

        for i in range(1, len(sorted_siglist)):
            left_sig = sorted_siglist[i-1]
            right_sig = sorted_siglist[i]
            has_same_cycle_events = CycleEventValues.from_sig(left_sig) == CycleEventValues.from_sig(right_sig)
            if has_same_cycle_events:
                cycleset_siglist.append(right_sig)
            else:
                cyclesets.append(make_cycleset(cycleset_siglist))
                cycleset_siglist = [right_sig]

            is_final_cycle = i == len(sorted_siglist)-1
            if is_final_cycle:
                cyclesets.append(make_cycleset(cycleset_siglist))
        return cyclesets


    def get_scheduled_dates(
        self,
        phase_start_date,
        *,
        max_cycles: int | None = None,
        within_component_continue_probability: float | None = None,
        within_cycleset_continue_probability: float | None = None,
        optional_cycle_probability: float | None = None,
    ):
        if max_cycles is not None and max_cycles < 1:
            raise ValueError("max_cycles must be positive when provided")
        cycle_number = 1
        cycle_start_date = phase_start_date
        all_component_parts = {}
        while True:
            if max_cycles is not None and cycle_number > max_cycles:
                break
            was_cycle_any_set = False
            for cycle_set in self.cycle_sets:
                if (cycle_set.get_normally_active_this_cycle(cycle_number)):
                    process_cycle = True
                    ongoing_prob = None
                elif (cycle_set.ongoing_step and
                    consider_ongoing(
                        cycle_set,
                        cycle_number,
                        within_cycleset_continue_probability
                        or WITHIN_CYCLESET_CONTINUE_PROB,
                    )):
                    process_cycle = True
                    ongoing_prob = 1
                else:
                    process_cycle = False
                if process_cycle:
                    was_cycle_any_set = True
                    cycle_increment = increment_cycle_date(
                        cycle_set.get_cycle_length(), cycle_set.cycle_len_unit
                        )
                    # TODO: Make robust to different portions having overlapping cycles
                    all_component_parts = all_component_parts | cycle_set.get_one_systemic_cycle(
                        cycle_number,
                        cycle_start_date,
                        ongoing_prob,
                        optional_cycle_probability,
                    )

            if not was_cycle_any_set:
                # TODO: Make robust to blank cycles?
                break

            cycle_number += 1
            cycle_start_date += cycle_increment
        return all_component_parts

    def reset(self):
        for cycle_set in self.cycle_sets:
            cycle_set.reset()




def increment_cycle_date(cycle_len: int, cycle_len_unit: str):
    cycle_increment_size = {
        "day":pd.DateOffset(days=cycle_len),
        "week":pd.DateOffset(weeks=cycle_len),
        "month":pd.DateOffset(months=cycle_len),
        "year":pd.DateOffset(years=cycle_len)
    }
    return cycle_increment_size[cycle_len_unit]


def consider_ongoing(self, cycle_num, continue_prob):
    if (not self._discontinued) and ((cycle_num-max(self.every)) % self.ongoing_step == 0):
        use_this_time = self._get_bool(continue_prob)
        self._discontinued = not use_this_time
        return use_this_time
    else:
        return False
