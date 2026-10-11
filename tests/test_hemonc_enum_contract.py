from pathlib import Path

import yaml
import pytest
from pydantic import ValidationError

from hemonc_alchemy.model.enums import Sigs_PhaseEnum
from scoop.protocols.spec import BroadCategory, RegistrySpec


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "scoop" / "profiles" / "nsclc" / "nsclc_protocol_registry.yaml"


def test_registry_phase_values_match_hemonc_phase_enum():
    raw = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    expected = {member.value for member in Sigs_PhaseEnum}
    registry = RegistrySpec.from_path(REGISTRY_PATH)

    assert {
        phase.type for phase in registry.source_phases.values() if phase.type
    } <= expected
    assert {
        phase["type"] for phase in raw["source_phases"].values() if phase.get("type")
    } <= expected


def test_registry_load_rejects_unknown_hemonc_phase_value(tmp_path):
    raw = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    raw["source_phases"]["port"]["type"] = "unknown-phase"
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(ValidationError, match="unknown HemOnc phase type"):
        RegistrySpec.from_path(registry_path)


def test_broad_category_matches_scoop_schema_vocabulary():
    schema = yaml.safe_load(
        (ROOT / "schema" / "linkml" / "regimen_spec.yaml").read_text(
            encoding="utf-8"
        )
    )
    schema_values = set(schema["enums"]["BroadCategory"]["permissible_values"])

    assert {category.value for category in BroadCategory} == schema_values
