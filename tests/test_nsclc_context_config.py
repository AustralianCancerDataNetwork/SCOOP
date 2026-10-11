from pathlib import Path

import yaml

from scoop.protocols.spec import RegistrySpec


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "scoop" / "profiles" / "nsclc" / "nsclc_protocol_registry.yaml"


def test_every_source_phase_has_an_explicit_context_policy():
    raw = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    policies = raw["phase_context_policies"]

    assert set(policies) == set(raw["source_phases"])
    assert all(
        policy["unknown_context_policy"] in {"exclude", "include", "phase_type"}
        for policy in policies.values()
    )
    best_supportive_care = RegistrySpec.from_path(REGISTRY_PATH).source_phases[
        "best_supportive_care"
    ]
    assert best_supportive_care.curative_intent is False


def test_driver_phases_use_available_nsclc_subtype_conditions():
    registry = RegistrySpec.from_path(REGISTRY_PATH)
    for phase_id in (
        "egfr_intervention",
        "alk_intervention",
        "ros1_intervention",
        "braf_intervention",
        "ret_intervention",
        "erbb2_intervention",
        "met_tki",
    ):
        assert len(registry.source_phases[phase_id].conditions.cuis) >= 3
    assert registry.source_phases["ntrk_intervention"].conditions.cuis is not None


def test_nonsquamous_ici_phase_uses_a_distinct_condition_reference():
    registry = RegistrySpec.from_path(REGISTRY_PATH)
    nonsquamous = registry.source_phases["ici_w_chemo_nonsquamous"].conditions
    squamous = registry.source_phases["ici_w_chemo_squamous"].conditions
    assert nonsquamous.cuis is not None
    assert nonsquamous != squamous
