from datetime import date

import pytest

from scoop.protocols.candidates import RegimenCandidate
from scoop.schedule.rollout import (
    RadiationAdministration,
    ScheduleConfig,
    SystemicAdministration,
    schedule,
)
from scoop.patients.treatment_episode import Treatment_Episode
from scoop.patients.build import assign_regimen
from scoop.patients.condition_occurrence import Condition_Occurrence
from scoop.selection.choose import (
    SelectedProtocol,
    SelectedRegimen,
    apply_recipe_timing,
)
from scoop.selection.interventions import (
    PhaseRef,
    PhaseTiming,
    Recipe,
    RecipeStep,
    SequenceTemplate,
)


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


def test_treatment_episode_attaches_schedule_with_event_provenance():
    selected = _selected(StubSignature())
    protocol_schedule = schedule(
        selected,
        date(2026, 1, 1),
        seed=11,
        config=ScheduleConfig(max_cycles=1),
    )
    episode = Treatment_Episode(
        person_id=1,
        condition_occurrence_id=2,
        regimen_type="fixture",
        regimen_category_id="fixture",
    )

    assert episode.attach_schedule(protocol_schedule) is episode
    drugs, procedures = episode.roll_events()
    assert len(drugs) == 1
    assert procedures == []
    assert drugs[0]["phase_id"] == "phase"
    assert drugs[0]["source_sig_id"] == 42
    # The HemOnc CUI travels as a CUI: it is not an OMOP concept_id, so no
    # *_concept_id key is emitted here at all.
    assert drugs[0]["component_hemonc_cui"] == 1234
    assert "drug_concept_id" not in drugs[0]


def test_assign_regimen_accepts_an_explicit_schedule_without_legacy_lookup():
    protocol_schedule = schedule(
        _selected(StubSignature()),
        date(2026, 1, 1),
        seed=11,
        config=ScheduleConfig(max_cycles=1),
    )
    condition = Condition_Occurrence(
        person_id=1,
        auto_assign_regimen=False,
    )

    episode = assign_regimen(condition, protocol_schedule=protocol_schedule)

    assert episode.registry_resolver_type == "none"
    assert len(episode.drug_exposures) == 1


def test_recipe_timing_sequences_ordered_phases():
    first = _selected(StubSignature("cisplatin")).phases[0]
    second_regimen = RegimenCandidate(
        name="radiation",
        variant_cui=2,
        sigs=[StubSignature("External beam radiotherapy")],
        regimen="radiation",
        components={"External beam radiotherapy"},
    )
    selected = SelectedProtocol(
        protocol_id="protocol",
        phases=(
            SelectedRegimen(phase_id="pre", regimen=first.regimen),
            SelectedRegimen(phase_id="post", regimen=second_regimen),
        ),
    )
    recipe = Recipe(
        steps=(
            # The first signature is a systemic phase and the second is a
            # procedure phase; timing is intentionally declared here.
            RecipeStep(PhaseRef("pre"), timing=PhaseTiming.PRE),
            RecipeStep(PhaseRef("post"), timing=PhaseTiming.POST),
        ),
        sequence_template=SequenceTemplate.POSTOPERATIVE,
    )

    annotated = apply_recipe_timing(selected, recipe)
    result = schedule(annotated, date(2026, 1, 1), seed=11)

    assert result.phase_start_dates == {
        "pre": date(2026, 1, 1),
        "post": date(2026, 1, 2),
    }
    assert [event.date for event in result.administrations] == [
        date(2026, 1, 1),
        date(2026, 1, 2),
    ]


def test_recipe_timing_keeps_concurrent_phases_on_one_anchor_date():
    first = _selected(StubSignature("cisplatin")).phases[0]
    second_regimen = RegimenCandidate(
        name="radiation",
        variant_cui=2,
        sigs=[StubSignature("External beam radiotherapy")],
        regimen="radiation",
        components={"External beam radiotherapy"},
    )
    selected = SelectedProtocol(
        protocol_id="protocol",
        phases=(
            SelectedRegimen(phase_id="systemic", regimen=first.regimen),
            SelectedRegimen(phase_id="radiation", regimen=second_regimen),
        ),
    )
    annotated = apply_recipe_timing(
        selected,
        Recipe(
            steps=(
                RecipeStep(PhaseRef("systemic"), timing=PhaseTiming.CONCURRENT),
                RecipeStep(PhaseRef("radiation"), timing=PhaseTiming.CONCURRENT),
            ),
            sequence_template=SequenceTemplate.CONCURRENT,
        ),
    )
    result = schedule(annotated, date(2026, 1, 1), seed=11)

    assert result.phase_start_dates == {
        "systemic": date(2026, 1, 1),
        "radiation": date(2026, 1, 1),
    }
