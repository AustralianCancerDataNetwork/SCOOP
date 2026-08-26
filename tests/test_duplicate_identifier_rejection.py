"""Finding F8: duplicate identified objects must fail to load, not collapse silently.

Three places in the canonical-authoring-list-to-runtime-mapping conversion shared
the same anti-pattern: a dict comprehension/loop keyed by a declared identifier,
built from a YAML list of identified objects, with no check that the identifier
was actually unique -- so a repeated id silently kept only the last declaration.

- InterventionCatalog.from_path(): interventions list keyed by intervention_id.
- ComponentRule.with_defaults(): category_restrictions list keyed by category.
- ProtocolSpec.generate(): optional_phases list keyed by phase_id.

All three are exercised here.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from scoop.protocols.spec import ComponentRule, ProtocolSpec
from scoop.selection.interventions import InterventionCatalog


def test_duplicate_intervention_id_is_rejected():
    raw = {
        "profile_id": "fixture",
        "version": 1,
        "interventions": [
            {"intervention_id": "a", "resolution": {"phase": {"id": "phase"}}},
            {"intervention_id": "a", "resolution": {"phase": {"id": "phase"}}},
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "interventions.yaml"
        path.write_text(yaml.safe_dump(raw))
        with pytest.raises(ValueError, match=r"duplicate intervention_id\(s\).*'a'"):
            InterventionCatalog.from_path(path)


def test_duplicate_category_restriction_is_rejected():
    with pytest.raises(ValueError, match=r"duplicate categor\(y/ies\).*chemotherapy"):
        ComponentRule.with_defaults(
            {
                "category_restrictions": [
                    {"category": "chemotherapy", "type": "min", "amount": 1},
                    {"category": "chemotherapy", "type": "exact", "amount": 0},
                ]
            }
        )


def test_duplicate_optional_phase_id_is_rejected():
    with pytest.raises(ValueError, match=r"duplicate optional_phases phase_id\(s\).*'optional'"):
        ProtocolSpec.generate(
            "protocol",
            {
                "protocol_id": "protocol",
                "source_phase_ids": ["required", "optional"],
                "optional_phases": [
                    {"phase_id": "optional", "criteria": {"shared": "component", "as_phase": "required"}},
                    {"phase_id": "optional", "criteria": {"shared": "component", "as_phase": "required"}},
                ],
            },
        )


def test_nsclc_registry_and_interventions_have_no_duplicates():
    """Regression guard: the real shipped NSCLC content must already be clean."""

    from scoop.protocols.spec import RegistrySpec

    root = Path(__file__).resolve().parents[1] / "scoop" / "profiles" / "nsclc"
    registry = RegistrySpec.from_path(root / "nsclc_protocol_registry.yaml")
    InterventionCatalog.from_path(
        root / "nsclc_interventions.yaml", protocols=registry.protocols
    )
