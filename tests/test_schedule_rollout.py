from datetime import date

import pytest

from scoop.protocols.candidates import RegimenCandidate
from scoop.schedule.rollout import (
    RadiationAdministration,
    ScheduleConfig,
    SystemicAdministration,
    schedule,
)
from scoop.selection.choose import SelectedProtocol, SelectedRegimen


class StubSignature:
    def __init__(self, component: str = "cisplatin"):
        self.id = 42
        self.component = component
        self.component_cui = 1234
        self.drug_object = None
        self.timing_sequence = "1"
        self.alldays = "1"
        self.cycle_length_lb = 21
        self.cycle_length_ub = 21
        self.cycle_length_unit = "day"
        self.cyclesigs = "21 day cycles for 1 cycle"
        self.portion = ""
        self.step_number = "1 of 1"
        self.route = "intravenous"
        self.route_cui = 777
        self.frequency = "once"
        self.frequency_cui = 778
        self.component_role = ""
        self.class_field = ""
        self.doseminnum = 10
        self.dosemaxnum = 20
        self.doseunit = "mg"
        self.doseunit_cui = 779
        self.divided = False
        self.dosecapnum = None
        self.dosecapunit = None
        self.dosecapunit_cui = None
        self.durationminnum = None
        self.durationmaxnum = None
        self.durationunit = None
        self.durationunit_cui = None


def _selected(signature):
    regimen = RegimenCandidate(
        name="fixture",
        variant_cui=1,
        sigs=[signature],
        regimen="fixture",
        components={signature.component},
        source_phase_id="phase",
    )
    return SelectedProtocol(
        protocol_id="protocol",
        phases=(SelectedRegimen(phase_id="phase", regimen=regimen),),
    )


def test_schedule_rolls_signature_into_typed_systemic_administration():
    result = schedule(
        _selected(StubSignature()),
        date(2026, 1, 1),
        seed=11,
        config=ScheduleConfig(max_cycles=1),
    )

    assert len(result.administrations) == 1
    administration = result.administrations[0]
    assert isinstance(administration, SystemicAdministration)
    assert administration.date == date(2026, 1, 1)
    assert administration.component.name == "cisplatin"
    assert 10 <= administration.dose <= 20
    assert administration.source_sig_id == 42


def test_schedule_classifies_radiation_components_without_placeholder_rows():
    result = schedule(
        _selected(StubSignature("External beam radiotherapy")),
        date(2026, 1, 1),
        seed=11,
        config=ScheduleConfig(max_cycles=1),
    )

    assert isinstance(result.administrations[0], RadiationAdministration)
    assert result.administrations[0].fraction_dose is not None


def test_schedule_rejects_unschedulable_placeholder_signatures():
    regimen = RegimenCandidate(
        name="fixture",
        variant_cui=1,
        sigs=["not-a-signature"],
        regimen="fixture",
        components={"cisplatin"},
    )
    selected = SelectedProtocol(
        protocol_id="protocol",
        phases=(SelectedRegimen(phase_id="phase", regimen=regimen),),
    )

    with pytest.raises(ValueError, match="without the released scheduling fields"):
        schedule(selected, date(2026, 1, 1), seed=11)


def test_schedule_config_validates_probabilities():
    with pytest.raises(ValueError, match="between 0 and 1"):
        ScheduleConfig(optional_cycle_probability=1.1)
