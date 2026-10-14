"""Pure, layered component classification -- extracted from legacy SQL.

This module reproduces the effective behavior of the SQL classification path
in `scoop.protocols.hemonc.queries` (`_category_mapping_expressions`,
`categorise_components`) and its source maps in
`scoop.protocols.hemonc.models` (`COMPONENT_CLASS_MAPS`,
`MAJOR_CLASS_MAP`/`MAIN_CLASS_MAP`/`COMPONENT_MAP`), without querying
anything or extending the classification with new categories, codes, or
layers (Phase 6C: "extract the existing classifier without extending it").

What is faithfully reproduced:

- The coalesce order. The legacy `sa.func.coalesce(*_category_mapping_expressions(...))`
  takes the first non-null value across `canmed_major_class`, then
  `main_class`, then `component`, in that dict-insertion order. `LAYER_ORDER`
  makes that order an explicit, tested module constant (CLS-4) instead of
  living implicitly in a dict's insertion order.
- The exact keys and values of the three legacy maps, transcribed verbatim
  into versioned YAML with mandatory `source`/`rationale` provenance (CLS-5).
  The original per-entry rationale was lost before this migration (see
  functional_specifications.md section 5.5, "Lost provenance") and is not
  recoverable from source; every transcribed rule says so explicitly rather
  than inventing a justification.
- Exact-string matching. The legacy path matches with SQL `==`, not `ILIKE`
  or a prefix; this classifier matches the same way. Nothing here is
  hierarchical yet (no ATC/class-CUI prefix matching) -- that is deliberately
  future work gated on its own review, per the phased delivery plan.

What changes, and why it is not "extending" the classification:

- `COMPONENT_MAP` is split into two rule files -- `component_overrides.yaml`
  (drug/supportive-care component names) and `non_drug_components.yaml`
  (`Surgery`, `External beam radiotherapy`) -- so non-drug components get
  their own layer rather than being mixed with drug-level overrides (CLS-12).
  Both occupy the same relative position the single legacy "component" map
  held in the coalesce order, and between them cover exactly the same keys
  with exactly the same values, so no key classifies differently than it did
  before. `test_classifier.py` proves this by classifying every key from the
  original three Python dicts and asserting the category is unchanged.
- Classification is data (versioned YAML) and a pure function, not inline SQL
  `sa.case`/`coalesce` expressions (CLS-8): `classify()` imports no
  SQLAlchemy and issues no query. It is not yet wired into `assemble()` or
  any candidate/selection path -- that cutover, in shadow mode against the
  Phase 6B reference fixture, is Phase 6D's job, not this chunk's.

What is a known, recorded, and deliberately *not* addressed gap:

- CLS-6 (rules keyed by code, not display text) is not satisfied. The
  legacy maps have no HemOnc CUI/ATC code recorded anywhere reachable from
  this codebase, and authoring one requires a live HemOnc extract this
  environment does not have. Rules stay keyed by the same free-text strings
  the legacy SQL matched on, which is exactly the fragility CLS-6 describes.
  This is recorded as an open item, not silently ignored -- see
  `_design/phase_6c_classifier_extraction.md`.
- `canmed_minor_class` is fetched (see `ComponentFact`) but never classified
  against, matching a pre-existing gap the functional specification already
  documents (section 7's bug table) rather than one discovered here.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Final, Mapping

from yaml import safe_load

from .categories import BroadCategory, Unclassified

# See scoop/profiles/paths.py for why importlib.resources rather than
# Path(__file__).parent: it resolves correctly regardless of install layout,
# and this package-data path was verified missing from a built wheel until
# pyproject.toml declared "scoop.protocols.classification" package-data
# (the same F1-class gap, caught before it shipped this time).
RULES_DIR: Final[Path] = Path(files("scoop.protocols.classification")) / "rules"

# The legacy sa.func.coalesce() order: canmed_major_class, then main_class,
# then component (split here into component_overrides/non_drug_components,
# which together cover exactly what the single legacy "component" map did).
LAYER_ORDER: Final[tuple[str, ...]] = (
    "canmed_major_class",
    "main_class",
    "component_overrides",
    "non_drug_components",
)

# Which ComponentFact field each layer's rules match against.
LAYER_SOURCE_FIELD: Final[Mapping[str, str]] = {
    "canmed_major_class": "canmed_major_class",
    "main_class": "main_class",
    "component_overrides": "component",
    "non_drug_components": "component",
}


@dataclass(frozen=True)
class ComponentFact:
    """One already-fetched HemOnc component row's classification-relevant columns.

    Mirrors `scoop.protocols.hemonc.models.COMPONENT_STR_COLS`. Carries
    `canmed_minor_class` for completeness/provenance only -- see the module
    docstring's note on that pre-existing, documented gap.
    """

    component: str | None = None
    component_cui: int | None = None
    canmed_major_class: str | None = None
    canmed_minor_class: str | None = None
    main_class: str | None = None


@dataclass(frozen=True)
class Rule:
    layer: str
    key: str
    category: BroadCategory
    source: str
    rationale: str
    overrides: str | None = None


@dataclass(frozen=True)
class MatchedRule:
    """Provenance for a successful classification."""

    layer: str
    key: str
    category: BroadCategory
    source: str


@dataclass(frozen=True)
class Classification:
    """Category plus matched-rule provenance, or Unclassified plus why."""

    category: BroadCategory | Unclassified
    matched: MatchedRule | None


@dataclass(frozen=True)
class LayeredRules:
    """Loaded, validated rules for every layer, keyed by layer then by rule key."""

    by_layer: Mapping[str, Mapping[str, Rule]]

    def layer(self, name: str) -> Mapping[str, Rule]:
        return self.by_layer[name]


def _load_layer_file(path: Path, expected_layer: str) -> Mapping[str, Rule]:
    with path.open("r", encoding="utf-8") as handle:
        raw = safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"rule file {path} must contain a mapping")

    allowed_top = {"version", "layer", "rules"}
    unknown_top = set(raw) - allowed_top
    if unknown_top:
        raise ValueError(f"rule file {path} has unknown field(s): {sorted(unknown_top)}")
    missing_top = allowed_top - set(raw)
    if missing_top:
        raise ValueError(f"rule file {path} is missing: {sorted(missing_top)}")

    if raw["layer"] != expected_layer:
        raise ValueError(
            f"rule file {path} declares layer {raw['layer']!r}, expected {expected_layer!r}"
        )
    version = raw["version"]
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValueError(f"rule file {path} has an invalid version: {version!r}")

    declarations = raw["rules"]
    if not isinstance(declarations, list):
        raise ValueError(f"rule file {path}: rules must be a list of identified objects")

    declared_keys = [entry.get("key") if isinstance(entry, dict) else None for entry in declarations]
    duplicates = sorted({key for key in declared_keys if declared_keys.count(key) > 1})
    if duplicates:
        raise ValueError(
            f"rule file {path} has duplicate key(s) within one layer: {duplicates}"
        )

    allowed_entry = {"key", "category", "source", "rationale", "overrides"}
    required_entry = {"key", "category", "source", "rationale"}
    rules: dict[str, Rule] = {}
    for entry in declarations:
        if not isinstance(entry, dict):
            raise ValueError(f"rule file {path} has a non-mapping rule entry")
        unknown = set(entry) - allowed_entry
        if unknown:
            raise ValueError(f"rule file {path} entry has unknown field(s): {sorted(unknown)}")
        missing = required_entry - set(entry)
        if missing:
            raise ValueError(f"rule file {path} entry is missing: {sorted(missing)}")

        key = entry["key"]
        if not key:
            raise ValueError(f"rule file {path} has an empty key")
        if not entry["source"]:
            raise ValueError(f"rule file {path} rule {key!r} has an empty source")
        if not entry["rationale"]:
            raise ValueError(f"rule file {path} rule {key!r} has an empty rationale")
        try:
            category = BroadCategory(entry["category"])
        except ValueError as exc:
            raise ValueError(
                f"rule file {path} rule {key!r} has an unknown category "
                f"{entry['category']!r}"
            ) from exc

        rules[key] = Rule(
            layer=expected_layer,
            key=key,
            category=category,
            source=entry["source"],
            rationale=entry["rationale"],
            overrides=entry.get("overrides"),
        )
    return rules


def _validate_cross_layer_conflicts(by_layer: Mapping[str, Mapping[str, Rule]]) -> None:
    """Fail loudly on an unmarked cross-layer disagreement (CLS-11).

    Agreeing duplicates across layers (the same key, the same category) are
    harmless under coalesce -- whichever layer is consulted first wins, and
    the later layer's rule is simply never reached for that key -- so they do
    not require a marker. A *disagreement* must be acknowledged explicitly
    with `overrides: <the other layer>` on either rule.
    """

    first_seen: dict[str, Rule] = {}
    for layer in LAYER_ORDER:
        for key, rule in by_layer[layer].items():
            earlier = first_seen.get(key)
            if earlier is None:
                first_seen[key] = rule
                continue
            if earlier.category == rule.category:
                continue
            acknowledged = rule.overrides == earlier.layer or earlier.overrides == rule.layer
            if not acknowledged:
                raise ValueError(
                    f"rule key {key!r} disagrees across layers without an explicit "
                    f"override: {earlier.layer!r} says {earlier.category.value!r}, "
                    f"{rule.layer!r} says {rule.category.value!r}"
                )


def load_rules(rules_dir: Path | None = None) -> LayeredRules:
    """Load and validate every layer's rules. Pure: reads local YAML only."""

    rules_dir = rules_dir or RULES_DIR
    by_layer = {
        layer: _load_layer_file(rules_dir / f"{layer}.yaml", layer) for layer in LAYER_ORDER
    }
    _validate_cross_layer_conflicts(by_layer)
    return LayeredRules(by_layer=by_layer)


@lru_cache(maxsize=1)
def get_default_rules() -> LayeredRules:
    """The shipped legacy-extracted ruleset, loaded once and cached."""

    return load_rules()


def classify(
    fact: ComponentFact,
    rules: LayeredRules | None = None,
) -> Classification:
    """Classify one already-fetched component row. Pure: no query, no I/O.

    Walks `LAYER_ORDER`, returning the first layer's match -- the same
    first-non-null-wins semantics as the legacy
    `sa.func.coalesce(*_category_mapping_expressions(...))` -- or an
    `Unclassified` naming the component and every column that failed to
    match, never a bare `None`/SQL `NULL` (CLS-1).
    """

    rules = rules or get_default_rules()
    for layer in LAYER_ORDER:
        source_value = getattr(fact, LAYER_SOURCE_FIELD[layer])
        if source_value is None:
            continue
        rule = rules.layer(layer).get(source_value)
        if rule is not None:
            return Classification(
                category=rule.category,
                matched=MatchedRule(
                    layer=layer, key=rule.key, category=rule.category, source=rule.source
                ),
            )

    checked = ", ".join(
        f"{column}={getattr(fact, column)!r}"
        for column in dict.fromkeys(LAYER_SOURCE_FIELD.values())
    )
    return Classification(
        category=Unclassified(
            component=fact.component,
            reason=f"no rule in any layer ({', '.join(LAYER_ORDER)}) matched: {checked}",
        ),
        matched=None,
    )
