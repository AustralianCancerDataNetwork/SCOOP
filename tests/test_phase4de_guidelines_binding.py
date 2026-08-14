from __future__ import annotations

import inspect
from pathlib import Path

from scoop.guidelines.graph import TransformationGraph
from scoop.guidelines.sampling import GuidelineSample, sample
from scoop.profiles.manifest import load_routed_profile
from scoop.protocols.binding import (
    BINDING_RULES,
    NullBinding,
    NonVariantBinding,
    SurgeryBinding,
    VariantBinding,
    bind,
)
from scoop.protocols.spec import (
    BroadCategory,
    CategoryRuleType,
    CategoryRule,
    ComponentRule,
    PhaseSpec,
)
from scoop.vocabulary.gateway import DefaultVocabularyGateway


def _phase(
    phase_id: str,
    *,
    categories: dict[BroadCategory, tuple[CategoryRuleType, int]] | None = None,
    components: list[list[str]] | None = None,
) -> PhaseSpec:
    return PhaseSpec(
        source_phase_id=phase_id,
        component_requirements=ComponentRule(
            component_lists=components or [],
            category_restrictions={
                category: CategoryRule(type=rule_type, amount=amount)
                for category, (rule_type, amount) in (categories or {}).items()
            },
        ),
    )


def test_seeded_guideline_sampling_is_typed_and_reproducible():
    graph = TransformationGraph.model_validate(
        {
            "nodes": {
                "age": {
                    "type": "numeric",
                    "min_value": 40,
                    "max_value": 80,
                    "as_int": True,
                },
                "pathway": {
                    "type": "categorical",
                    "distribution": {"a": 0.5, "b": 0.5},
                },
            },
            "edges": [],
        }
    )

    first = sample(graph, 17, source_profile="fixture")
    second = sample(graph, 17, source_profile="fixture")

    assert isinstance(first, GuidelineSample)
    assert first == second
    assert first.source_profile == "fixture"
    assert first.seed == 17
    assert first.as_dict == dict(first.values)


def test_routed_profile_retains_typed_entry_and_final_samples():
    root = Path(__file__).resolve().parents[1] / "scoop"
    runtime = load_routed_profile(
        "sclc",
        base_dir=root,
        vocabulary_gateway=DefaultVocabularyGateway(root, engine_factory=lambda: None),
    )

    first = runtime.sample_routed_patient(seed=21)
    second = runtime.sample_routed_patient(seed=21)

    assert first.entry_guideline == second.entry_guideline
    assert first.final_guideline == second.final_guideline
    assert first.entry_sample == first.entry_guideline.as_dict
    assert first.final_sample == first.final_guideline.as_dict


def test_binding_table_is_explicit_and_total_for_declared_shapes():
    assert [rule.name for rule in BINDING_RULES] == [
        "systemic_sig_backed",
        "nonvariant_sig_backed",
        "surgery_only",
        "nothing_operationalisable",
    ]
    assert all(rule.binding_type for rule in BINDING_RULES)

    cases = [
        (
            _phase(
                "systemic",
                categories={
                    BroadCategory.CHEMOTHERAPY: (CategoryRuleType.MIN, 1),
                },
            ),
            VariantBinding,
        ),
        (
            _phase(
                "radiation",
                categories={
                    BroadCategory.RADIOTHERAPY: (CategoryRuleType.EXACT, 1),
                },
            ),
            NonVariantBinding,
        ),
        (
            _phase(
                "surgery",
                categories={BroadCategory.SURGERY: (CategoryRuleType.MIN, 1)},
            ),
            SurgeryBinding,
        ),
        (_phase("supportive"), NullBinding),
    ]

    for phase, expected_type in cases:
        result = bind(phase)
        assert type(result) is expected_type
        assert result.phase_id == phase.source_phase_id
        assert result.origin == phase


def test_binding_does_not_accept_or_inspect_a_gateway():
    assert "gateway" not in inspect.signature(bind).parameters

    phase = _phase(
        "explicit_drug",
        components=[["osimertinib"]],
    )
    assert isinstance(bind(phase), VariantBinding)
