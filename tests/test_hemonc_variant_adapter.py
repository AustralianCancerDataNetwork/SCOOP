from __future__ import annotations

import pytest

from scoop.protocols.hemonc.models import _load_hemonc_models
from scoop.protocols.hemonc.variant_queries import (
    build_variant_query_artifacts,
    build_variant_statement_for_entry,
)
from scoop.protocols.spec import PhaseSpec


def _phase(*, conditions: dict, component_lists: list[list[str]] | None = None) -> PhaseSpec:
    return PhaseSpec.model_validate(
        {
            "source_phase_id": "fixture_phase",
            "conditions": conditions,
            "component_requirements": {
                "component_lists": component_lists or [["osimertinib"]],
            },
        }
    )


def test_variant_adapter_uses_released_model_bundle():
    models = _load_hemonc_models()

    assert models.Variants.__name__ == "Variants"
    assert models.Sigs.__name__ == "Sigs"
    assert hasattr(models, "sigs_StudyMap")


def test_variant_artifacts_accept_typed_cui_phase_specs():
    phase = _phase(conditions={"cuis": [12345]})

    artifacts = build_variant_query_artifacts(phase)
    statement = build_variant_statement_for_entry(phase)

    assert artifacts.components_subquery is not None
    assert artifacts.categorised_components_subquery is not None
    assert artifacts.variant_cuis_subquery.name == "matching_variant_ids"
    assert artifacts.variants_stmt is not None
    assert str(statement)


def test_variant_adapter_requires_a_session_for_condition_names():
    phase = _phase(conditions={"names": ["Lung cancer"]})

    with pytest.raises(TypeError, match="session is required"):
        build_variant_query_artifacts(phase)
