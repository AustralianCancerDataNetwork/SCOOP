import inspect
from dataclasses import asdict
from datetime import date
from typing import get_type_hints

import pytest

from scoop.export.omop import OmopExportBundle, OmopExportConfig, export
from scoop.guidelines.sampling import (
    GuidelineSample,
    NoDecision,
    TreatmentDecision,
    decide,
    sample,
)
from scoop.protocols.candidates import ProtocolCandidate, assemble
from scoop.protocols.hemonc.gateway import FakeHemOncGateway, HemOncGateway
from scoop.schedule.modality import Modality
from scoop.schedule.rollout import ProtocolSchedule, ScheduleConfig, schedule
from scoop.selection.bridge import InterventionBridge, resolve
from scoop.selection.choose import NoSelection, PatientContext, select
from scoop.selection.interventions import (
    Choice,
    InterventionSpec,
    NonHemOnc,
    PhaseRef,
    ProtocolRef,
    Recipe,
    RecipeStep,
    ResolvedIntervention,
    PhaseTiming,
    SequenceTemplate,
)
from scoop.vocabulary.gateway import FakeVocabularyGateway, VocabularyGateway


def test_stage_boundary_objects_are_frozen_and_serializable():
    sample_value = GuidelineSample(
        values={"person.performance_status": "ps1"},
        source_profile="fixture",
        seed=7,
    )
    decision = TreatmentDecision(
        decision_id="endpoint",
        graph_endpoint="treatment_episode.endpoint",
        modifiers={"person.performance_status": "ps1"},
        source_profile="fixture",
        raw_sample=sample_value,
    )

    assert sample_value == GuidelineSample(
        values={"person.performance_status": "ps1"},
        source_profile="fixture",
        seed=7,
    )
    assert asdict(decision)["raw_sample"]["source_profile"] == "fixture"
    assert NoDecision(reason="no active endpoint") == NoDecision(
        reason="no active endpoint"
    )


def test_intervention_forms_and_sequence_metadata_are_inspectable():
    recipe = Recipe(
        steps=(
            RecipeStep(PhaseRef("systemic"), timing=PhaseTiming.PRE),
            RecipeStep(ProtocolRef("radiation"), timing=PhaseTiming.POST),
        ),
        sequence_template=SequenceTemplate.POSTOPERATIVE,
    )
    spec = InterventionSpec(
        intervention_id="fixture_endpoint",
        resolution=recipe,
        requires=("person.performance_status",),
        source_profile="fixture",
    )

    assert spec.resolution.steps[0].timing == PhaseTiming.PRE
    assert spec.resolution.sequence_template == SequenceTemplate.POSTOPERATIVE
    assert Choice(("a", "b")).intervention_ids == ("a", "b")
    assert NonHemOnc("surgery", "surgery-only").label == "surgery-only"
    assert ResolvedIntervention("fixture_endpoint", recipe=recipe).recipe == recipe


def test_gateway_fakes_conform_without_external_state():
    assert isinstance(FakeHemOncGateway(), HemOncGateway)
    assert isinstance(FakeVocabularyGateway(), VocabularyGateway)

    vocabulary = FakeVocabularyGateway(
        condition_names={"Lung cancer": 123},
        route_concepts={"iv": 456},
        modifiers={"stage": "stage_iii"},
    )
    assert vocabulary.resolve_condition_names(("Lung cancer",)) == (123,)
    assert vocabulary.resolve_condition_cuis((123,)) == (123,)
    assert vocabulary.resolve_route_concept("iv") == 456
    assert vocabulary.sample_modifier("stage", seed=11) == "stage_iii"


def test_spine_functions_have_typed_signatures():
    spine = (sample, decide, resolve, assemble, select, schedule, export)

    for function in spine:
        hints = get_type_hints(function)
        signature = inspect.signature(function)
        assert all(
            parameter.annotation is not inspect.Parameter.empty
            for parameter in signature.parameters.values()
        ), function.__name__
        assert signature.return_annotation is not inspect.Signature.empty
        assert all(annotation is not dict for annotation in hints.values())
        assert all(annotation is not object for annotation in hints.values())


def test_spine_placeholders_fail_closed_without_legacy_fallbacks():
    context = PatientContext(values={})
    decision = TreatmentDecision(
        decision_id="endpoint",
        graph_endpoint="endpoint",
        modifiers={},
        source_profile="fixture",
        raw_sample=GuidelineSample({}, "fixture"),
    )

    with pytest.raises(TypeError, match="TransformationGraph"):
        sample(None, seed=1)
    assert isinstance(decide(GuidelineSample({}, "fixture")), NoDecision)
    with pytest.raises(TypeError, match="InterventionBridge"):
        resolve(None, decision, context)
    with pytest.raises(NotImplementedError, match="Phase 4"):
        assemble(None, FakeHemOncGateway())
    assert isinstance(select([], context, seed=1), NoSelection)
    with pytest.raises(TypeError, match="SelectedProtocol"):
        schedule(None, date(2026, 1, 1), seed=1)
    assert export(ProtocolSchedule(), FakeVocabularyGateway()) == OmopExportBundle()


def test_typed_empty_results_are_available_for_later_phases():
    assert isinstance(NoDecision("not yet"), NoDecision)
    assert isinstance(NoSelection("not yet"), NoSelection)
    assert ScheduleConfig(max_cycles=3).max_cycles == 3
    assert OmopExportConfig(person_id=1).person_id == 1
    assert Modality.SYSTEMIC.value == "systemic"
    assert isinstance(OmopExportBundle(), OmopExportBundle)
    assert ProtocolCandidate.__name__ == "ProtocolCandidate"
    assert InterventionBridge is not None
