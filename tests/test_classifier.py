"""Phase 6C: the pure classifier reproduces the legacy SQL mapping exactly.

See scoop/protocols/classification/classifier.py's module docstring for the
full account of what is faithfully reproduced, what changed structurally
without changing behavior, and what known gap (CLS-6) is recorded rather than
silently dropped.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scoop.protocols.classification import (
    BroadCategory,
    Classification,
    ComponentFact,
    LAYER_ORDER,
    Unclassified,
    classify,
    get_default_rules,
    load_rules,
)
from scoop.protocols.hemonc.models import (
    COMPONENT_MAP,
    MAIN_CLASS_MAP,
    MAJOR_CLASS_MAP,
)

RULES_DIR = Path(__file__).resolve().parents[1] / "scoop" / "protocols" / "classification" / "rules"

NON_DRUG_COMPONENTS = {"Surgery", "External beam radiotherapy"}


def test_every_legacy_canmed_major_class_entry_reproduces_exactly():
    rules = get_default_rules()
    for key, expected in MAJOR_CLASS_MAP.items():
        result = classify(ComponentFact(canmed_major_class=key), rules)
        assert result.category == BroadCategory(expected), key
        assert result.matched is not None and result.matched.layer == "canmed_major_class"


def test_every_legacy_main_class_entry_reproduces_exactly():
    rules = get_default_rules()
    for key, expected in MAIN_CLASS_MAP.items():
        result = classify(ComponentFact(main_class=key), rules)
        assert result.category == BroadCategory(expected), key
        assert result.matched is not None and result.matched.layer == "main_class"


def test_every_legacy_component_map_entry_reproduces_exactly():
    """Covers both halves of the split (component_overrides + non_drug_components)."""

    rules = get_default_rules()
    for key, expected in COMPONENT_MAP.items():
        result = classify(ComponentFact(component=key), rules)
        assert result.category == BroadCategory(expected), key
        expected_layer = (
            "non_drug_components" if key in NON_DRUG_COMPONENTS else "component_overrides"
        )
        assert result.matched is not None and result.matched.layer == expected_layer


def test_coalesce_precedence_matches_the_legacy_dict_iteration_order():
    """canmed_major_class beats main_class beats component, exactly as
    sa.func.coalesce(*_category_mapping_expressions(...)) did over
    COMPONENT_CLASS_MAPS's insertion order."""

    fact = ComponentFact(
        canmed_major_class="Alkylating agent",  # -> chemotherapy
        main_class="Steroid",  # -> supportive
        component="Surgery",  # -> surgery
    )
    result = classify(fact)
    assert result.category == BroadCategory.CHEMOTHERAPY
    assert result.matched.layer == "canmed_major_class"

    fact_no_major = ComponentFact(main_class="Steroid", component="Surgery")
    result = classify(fact_no_major)
    assert result.category == BroadCategory.SUPPORTIVE
    assert result.matched.layer == "main_class"


def test_unclassified_is_a_value_not_none():
    result = classify(ComponentFact(component="Some Unmapped Novel Drug"))
    assert isinstance(result.category, Unclassified)
    assert result.category.component == "Some Unmapped Novel Drug"
    assert result.matched is None

    all_none = classify(ComponentFact())
    assert isinstance(all_none.category, Unclassified)


def test_classifier_module_has_no_sqlalchemy_import():
    source = (
        Path(__file__).resolve().parents[1]
        / "scoop" / "protocols" / "classification" / "classifier.py"
    ).read_text(encoding="utf-8")
    assert "import sqlalchemy" not in source.lower()
    assert "from sqlalchemy" not in source.lower()


def test_layer_order_is_a_module_constant():
    assert LAYER_ORDER == (
        "canmed_major_class",
        "main_class",
        "component_overrides",
        "non_drug_components",
    )


def test_every_shipped_rule_has_mandatory_provenance():
    for layer in LAYER_ORDER:
        raw = yaml.safe_load((RULES_DIR / f"{layer}.yaml").read_text(encoding="utf-8"))
        assert raw["version"] >= 1
        for entry in raw["rules"]:
            assert entry["source"], (layer, entry["key"])
            assert entry["rationale"], (layer, entry["key"])


def test_unknown_category_is_rejected(tmp_path):
    _write_layer(
        tmp_path,
        "canmed_major_class",
        [{"key": "X", "category": "not-a-real-category", "source": "s", "rationale": "r"}],
    )
    _write_minimal_other_layers(tmp_path, skip="canmed_major_class")
    with pytest.raises(ValueError, match="unknown category"):
        load_rules(tmp_path)


def test_duplicate_key_within_one_layer_is_rejected(tmp_path):
    _write_layer(
        tmp_path,
        "canmed_major_class",
        [
            {"key": "X", "category": "chemotherapy", "source": "s", "rationale": "r"},
            {"key": "X", "category": "supportive", "source": "s", "rationale": "r"},
        ],
    )
    _write_minimal_other_layers(tmp_path, skip="canmed_major_class")
    with pytest.raises(ValueError, match=r"duplicate key\(s\)"):
        load_rules(tmp_path)


def test_missing_provenance_is_rejected(tmp_path):
    _write_layer(
        tmp_path,
        "canmed_major_class",
        [{"key": "X", "category": "chemotherapy", "source": "s", "rationale": ""}],
    )
    _write_minimal_other_layers(tmp_path, skip="canmed_major_class")
    with pytest.raises(ValueError, match="empty rationale"):
        load_rules(tmp_path)


def test_unmarked_cross_layer_conflict_is_rejected(tmp_path):
    _write_layer(
        tmp_path,
        "canmed_major_class",
        [{"key": "Same Name", "category": "chemotherapy", "source": "s", "rationale": "r"}],
    )
    _write_layer(
        tmp_path,
        "main_class",
        [{"key": "Same Name", "category": "supportive", "source": "s", "rationale": "r"}],
    )
    _write_minimal_other_layers(tmp_path, skip={"canmed_major_class", "main_class"})
    with pytest.raises(ValueError, match="disagrees across layers"):
        load_rules(tmp_path)


def test_marked_cross_layer_override_is_accepted(tmp_path):
    _write_layer(
        tmp_path,
        "canmed_major_class",
        [{"key": "Same Name", "category": "chemotherapy", "source": "s", "rationale": "r"}],
    )
    _write_layer(
        tmp_path,
        "main_class",
        [
            {
                "key": "Same Name",
                "category": "supportive",
                "source": "s",
                "rationale": "r",
                "overrides": "canmed_major_class",
            }
        ],
    )
    _write_minimal_other_layers(tmp_path, skip={"canmed_major_class", "main_class"})
    rules = load_rules(tmp_path)
    assert rules.layer("canmed_major_class")["Same Name"].category == BroadCategory.CHEMOTHERAPY


def _write_layer(tmp_path: Path, layer: str, rules: list[dict]) -> None:
    (tmp_path / f"{layer}.yaml").write_text(
        yaml.safe_dump({"version": 1, "layer": layer, "rules": rules})
    )


def _write_minimal_other_layers(tmp_path: Path, skip) -> None:
    skip = {skip} if isinstance(skip, str) else set(skip)
    for layer in LAYER_ORDER:
        if layer not in skip:
            _write_layer(tmp_path, layer, [])
