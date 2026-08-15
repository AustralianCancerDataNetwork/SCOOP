from __future__ import annotations

from pathlib import Path

import pytest

from scoop.guidelines.sampling import (
    AmbiguousDecision,
    GuidelineSample,
    NoDecision,
    TreatmentDecision,
    decide,
)
from scoop.profiles.manifest import load_profile_manifest
from scoop.protocols.candidates import PhaseCandidates, ProtocolCandidate, RegimenCandidate
from scoop.protocols.spec import PhaseSpec, ProtocolSpec
from scoop.selection.bridge import DeclarativeInterventionBridge, resolve
from scoop.selection.choose import NoSelection, PatientContext, SelectedProtocol, select
from scoop.selection.interventions import (
    Choice,
    InterventionCatalog,
    InterventionSpec,
    NonHemOnc,
    PhaseRef,
    PhaseTiming,
    ProtocolRef,
    Recipe,
    RecipeStep,
    ResolvedIntervention,
    SequenceTemplate,
)


def _decision(endpoint: str = "endpoint") -> TreatmentDecision:
    raw = GuidelineSample(
        values={
            "condition_episode.biomarker": "egfr",
            f"treatment_episode.{endpoint}": endpoint,
        },
        source_profile="fixture",
    )
    return decide(raw)  # type: ignore[return-value]


def _candidate(name: str, phase: str, variant_cui: int) -> RegimenCandidate:
    return RegimenCandidate(
        name=name,
        variant_cui=variant_cui,
        sigs=[name],
        regimen="regimen",
        components={name},
        phase=phase,
        variant=f"variant-{variant_cui}",
    )


def test_decide_has_explicit_none_one_and_ambiguous_outcomes():
    assert isinstance(
        decide(GuidelineSample({}, "fixture")),
        NoDecision,
    )
    decision = _decision()
    assert decision.decision_id == "endpoint"

    ambiguous = GuidelineSample(
        values={
            "treatment_episode.a": "a",
            "treatment_episode.b": "b",
        },
        source_profile="fixture",
    )
    with pytest.raises(AmbiguousDecision, match="a, b"):
        decide(ambiguous)


def test_all_intervention_forms_resolve_and_context_is_restricted():
    protocols = {
        "protocol": ProtocolSpec(
            protocol_id="protocol",
            source_phase_ids=["phase"],
            source_phases={"phase": PhaseSpec(source_phase_id="phase")},
        )
    }
    specs = {
        "phase_endpoint": InterventionSpec(
            "phase_endpoint",
            PhaseRef("phase"),
            requires=("condition_episode.biomarker",),
        ),
        "protocol_endpoint": InterventionSpec(
            "protocol_endpoint",
            ProtocolRef("protocol"),
        ),
        "phase_choice": InterventionSpec(
            "phase_choice",
            Choice(("phase_endpoint", "protocol_endpoint")),
        ),
        "recipe_endpoint": InterventionSpec(
            "recipe_endpoint",
            Recipe(
                steps=(
                    RecipeStep(PhaseRef("phase"), timing=PhaseTiming.PRE),
                    RecipeStep(ProtocolRef("protocol"), timing=PhaseTiming.POST),
                ),
                sequence_template=SequenceTemplate.POSTOPERATIVE,
            ),
        ),
        "non_hemonc_endpoint": InterventionSpec(
            "non_hemonc_endpoint",
            NonHemOnc("non_hemonc_endpoint", "surgery"),
        ),
    }
    catalog = InterventionCatalog(
        profile_id="fixture",
        specs=specs,
        protocols=protocols,
    )
    catalog.validate_references()
    bridge = DeclarativeInterventionBridge(catalog)

    phase = resolve(
        bridge,
        _decision("phase_endpoint"),
        PatientContext({"condition_episode.biomarker": "egfr", "unused": True}),
    )
    restricted = resolve(
        bridge,
        _decision("phase_endpoint"),
        PatientContext({"condition_episode.biomarker": "egfr"}),
    )
    assert phase == restricted
    assert phase.phase_refs == (PhaseRef("phase"),)

    protocol = bridge.resolve(_decision("protocol_endpoint"), PatientContext({}))
    assert protocol.protocols == (protocols["protocol"],)
    choice = bridge.resolve(_decision("phase_choice"), PatientContext({}))
    assert len(choice.alternatives) == 2
    recipe = bridge.resolve(_decision("recipe_endpoint"), PatientContext({}))
    assert recipe.recipe is not None
    assert recipe.recipe.sequence_template is SequenceTemplate.POSTOPERATIVE
    non_hemonc = bridge.resolve(_decision("non_hemonc_endpoint"), PatientContext({}))
    assert non_hemonc.non_hemonc is not None


def test_catalog_rejects_cycles_and_missing_coverage():
    cyclic = InterventionCatalog(
        profile_id="fixture",
        specs={
            "a": InterventionSpec("a", Choice(("b", "c"))),
            "b": InterventionSpec("b", Choice(("a", "c"))),
            "c": InterventionSpec("c", PhaseRef("phase")),
        },
    )
    with pytest.raises(ValueError, match="cycle"):
        cyclic.validate_references()

    catalog = InterventionCatalog(
        profile_id="fixture",
        specs={"declared": InterventionSpec("declared", PhaseRef("phase"))},
    )
    with pytest.raises(ValueError, match="undeclared intervention endpoint"):
        catalog.validate_coverage(graph_nodes={"treatment_episode.other"})


def test_nsclc_profile_loads_with_declared_intervention_coverage():
    root = Path(__file__).resolve().parents[1] / "scoop"
    manifest = load_profile_manifest("nsclc", base_dir=root)
    assert manifest.interventions == Path("nsclc_interventions.yaml")


def test_selects_one_regimen_per_phase_reproducibly():
    phases = [
        PhaseCandidates(
            label="first",
            options=[_candidate("a", "first", 1), _candidate("b", "first", 2)],
            conditions=[],
        ),
        PhaseCandidates(
            label="second",
            options=[_candidate("c", "second", 3)],
            conditions=[],
        ),
    ]
    candidates = [
        ProtocolCandidate(
            name="protocol",
            phases=phases,
            selection_requires=("condition_episode.biomarker",),
        )
    ]

    first = select(candidates, PatientContext({"condition_episode.biomarker": "egfr"}), 11)
    second = select(candidates, PatientContext({"condition_episode.biomarker": "egfr"}), 11)
    assert isinstance(first, SelectedProtocol)
    assert first == second
    assert [phase.phase_id for phase in first.phases] == ["first", "second"]


def test_unselectable_context_is_a_value():
    candidate = ProtocolCandidate(
        name="requires_context",
        phases=[],
        selection_requires=("condition_episode.biomarker",),
    )
    result = select([candidate], PatientContext({}), seed=3)
    assert isinstance(result, NoSelection)
    assert "condition_episode.biomarker" in result.reason
