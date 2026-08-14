"""Offline, typed loading of protocol registry specifications."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Final, Literal, Mapping, TYPE_CHECKING

from pydantic import (
    BaseModel,
    ConfigDict,
    BeforeValidator,
    Field,
    field_validator,
    model_validator,
)
from yaml import safe_dump, safe_load

if TYPE_CHECKING:
    from scoop.vocabulary.gateway import VocabularyGateway


class BroadCategory(StrEnum):
    CHEMOTHERAPY = "chemotherapy"
    RADIOTHERAPY = "radiotherapy"
    IMMUNOTHERAPY = "immunotherapy"
    SURGERY = "surgery"
    SUPPORTIVE = "supportive"


class CategoryRuleType(StrEnum):
    EXACT = "exact"
    MIN = "min"


RegimenList = list[int] | list[str]


class ConditionRef(BaseModel):
    """A condition declaration preserved until vocabulary resolution is requested."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    names: list[str] | None = None
    cuis: list[int] | None = None

    @model_validator(mode="after")
    def exactly_one_reference_form(self) -> "ConditionRef":
        if self.names is not None and self.cuis is not None:
            raise ValueError("ConditionRef must contain names or cuis, not both")
        if self.names is None and self.cuis is None:
            raise ValueError("ConditionRef must contain names or cuis")
        return self

    @classmethod
    def from_value(cls, value: Any) -> "ConditionRef":
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            return cls.model_validate(value)
        if isinstance(value, str):
            return cls(names=[value])
        if isinstance(value, list):
            if all(isinstance(item, str) for item in value):
                return cls(names=value)
            if all(isinstance(item, int) and not isinstance(item, bool) for item in value):
                return cls(cuis=value)
        raise TypeError("conditions must be a name/CUI or a homogeneous list")

    def resolve(self, gateway: "VocabularyGateway") -> tuple[int, ...]:
        """Resolve this declaration through an explicitly supplied gateway."""
        if self.names is not None:
            return tuple(gateway.resolve_condition_names(self.names))
        assert self.cuis is not None
        return tuple(gateway.resolve_condition_cuis(self.cuis))

    def to_value(self) -> list[str] | list[int]:
        """Return the compact list representation used by registry YAML."""
        return list(self.names) if self.names is not None else list(self.cuis or [])


def rescue_component_input(value: Any) -> Any:
    if value is None or value == []:
        return []
    if isinstance(value, str):
        return [[value]]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return [value]
    return value


ComponentLists = Annotated[list[list[str]], BeforeValidator(rescue_component_input)]


class CategoryRule(BaseModel):
    type: CategoryRuleType
    amount: int

    model_config = ConfigDict(extra="forbid")


DEFAULT_CATEGORY_RULES: Final[dict[BroadCategory, CategoryRule]] = {
    BroadCategory.CHEMOTHERAPY: CategoryRule(type=CategoryRuleType.EXACT, amount=0),
    BroadCategory.RADIOTHERAPY: CategoryRule(type=CategoryRuleType.EXACT, amount=0),
    BroadCategory.IMMUNOTHERAPY: CategoryRule(type=CategoryRuleType.EXACT, amount=0),
    BroadCategory.SURGERY: CategoryRule(type=CategoryRuleType.EXACT, amount=0),
}

EXCLUDE_RESTRICTION = CategoryRule(type=CategoryRuleType.EXACT, amount=0)


class ComponentRule(BaseModel):
    component_lists: ComponentLists = Field(default_factory=list)
    category_restrictions: dict[BroadCategory, CategoryRule] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    @field_validator("component_lists")
    @classmethod
    def reject_empty_component_lists(cls, value: list[list[str]]) -> list[list[str]]:
        if any(not components for components in value):
            raise ValueError("component_lists cannot contain empty lists")
        return value

    @classmethod
    def with_defaults(cls, raw: Mapping[str, Any] | None = None) -> "ComponentRule":
        """Apply the registry's explicit category defaulting rules."""
        raw_rule = cls.model_validate(raw or {})
        category_restrictions: dict[BroadCategory, CategoryRule]
        if raw_rule.category_restrictions:
            if set(raw_rule.category_restrictions) == set(DEFAULT_CATEGORY_RULES):
                category_restrictions = dict(raw_rule.category_restrictions)
            else:
                category_restrictions = {
                    category: CategoryRule.model_validate(rule)
                    for category, rule in DEFAULT_CATEGORY_RULES.items()
                }
                category_restrictions.update(raw_rule.category_restrictions)
        else:
            category_restrictions = {
                BroadCategory.CHEMOTHERAPY: CategoryRule(
                    type=CategoryRuleType.MIN,
                    amount=0,
                )
            }
        return cls(
            component_lists=raw_rule.component_lists,
            category_restrictions=category_restrictions,
        )

    def delivered_categories(self) -> set[BroadCategory] | None:
        if not self.category_restrictions:
            return None
        return {
            category
            for category, rule in self.category_restrictions.items()
            if rule != EXCLUDE_RESTRICTION
        }


class PhaseSpec(BaseModel):
    source_phase_id: str | None = None
    conditions: ConditionRef = Field(default_factory=lambda: ConditionRef(cuis=[]))
    component_requirements: ComponentRule = Field(default_factory=ComponentRule)
    study_restriction: Literal["must_have_rt"] | None = None
    curative_intent: bool = True
    regimens: RegimenList | None = Field(default_factory=list)
    type: str | None = None
    standalone: bool = True

    model_config = ConfigDict(extra="forbid")

    @field_validator("conditions", mode="before")
    @classmethod
    def parse_conditions(cls, value: Any) -> ConditionRef:
        return ConditionRef.from_value(value)

    @field_validator("regimens", mode="before")
    @classmethod
    def normalize_regimens(cls, value: Any) -> Any:
        if isinstance(value, (int, str)) and not isinstance(value, bool):
            return [value]
        return value

    @classmethod
    def generate(
        cls,
        phase_data: Mapping[str, Any],
        label: str,
        default_conditions: ConditionRef,
    ) -> "PhaseSpec":
        """Construct a phase, inheriting the registry's condition declaration."""
        data = dict(phase_data)
        data["conditions"] = ConditionRef.from_value(
            phase_data.get("conditions", default_conditions)
        )
        data["component_requirements"] = ComponentRule.with_defaults(
            phase_data.get("component_requirements")
        )
        data["source_phase_id"] = label
        return cls.model_validate(data)

    def delivered_categories(self) -> set[BroadCategory] | None:
        return self.component_requirements.delivered_categories()


class PhaseChoiceSpec(BaseModel):
    phase_id: str
    source_phase_ids: list[str] = Field(min_length=2)

    model_config = ConfigDict(extra="forbid")

    @field_validator("source_phase_ids")
    @classmethod
    def require_distinct_members(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("phase choice members must be distinct")
        return value


class OptionalPhaseCriteria(BaseModel):
    shared: Literal["component"]
    as_phase: str

    model_config = ConfigDict(extra="forbid")


class OptionalPhaseSpec(BaseModel):
    criteria: OptionalPhaseCriteria

    model_config = ConfigDict(extra="forbid")


class ProtocolSpec(BaseModel):
    protocol_id: str
    source_phase_ids: list[str]
    optional_phases: dict[str, OptionalPhaseSpec] | None = None

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def generate(cls, label: str, protocol_data: Mapping[str, Any]) -> "ProtocolSpec":
        unknown = set(protocol_data) - {"phases", "optional_phases"}
        if unknown:
            raise ValueError(
                f"protocol {label!r} has unknown field(s): {sorted(unknown)}"
            )
        return cls(
            protocol_id=label,
            source_phase_ids=protocol_data["phases"],
            optional_phases=protocol_data.get("optional_phases"),
        )


class RegistrySpec(BaseModel):
    label: str
    default_conditions: ConditionRef
    version: int | float
    source_phases: dict[str, PhaseSpec]
    union_phases: dict[str, PhaseChoiceSpec]
    protocols: dict[str, ProtocolSpec]

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def from_path(cls, path: str | Path) -> "RegistrySpec":
        """Load one registry without opening a database or resolving vocabulary."""
        path = Path(path)
        with path.open("r", encoding="utf-8") as handle:
            raw = safe_load(handle)

        if not isinstance(raw, dict) or len(raw) != 1:
            raise ValueError("registry YAML must contain exactly one profile mapping")
        label, registry_data = next(iter(raw.items()))
        if not isinstance(registry_data, dict):
            raise ValueError(f"registry {label!r} must contain a mapping")

        required = {"version", "conditions", "source_phases", "protocols"}
        missing = required - registry_data.keys()
        if missing:
            raise ValueError(f"registry {label!r} is missing: {sorted(missing)}")
        allowed = required | {"phase_combinations", "entries"}
        unknown = set(registry_data) - allowed
        if unknown:
            raise ValueError(
                f"registry {label!r} has unknown field(s): {sorted(unknown)}"
            )

        default_conditions = ConditionRef.from_value(registry_data["conditions"])
        source_phases = {
            phase_id: PhaseSpec.generate(
                phase_data or {}, phase_id, default_conditions
            )
            for phase_id, phase_data in registry_data["source_phases"].items()
        }
        protocols = {
            protocol_id: ProtocolSpec.generate(protocol_id, protocol_data)
            for protocol_id, protocol_data in registry_data["protocols"].items()
        }
        union_phases = {}
        for union_id, union_data in registry_data.get("phase_combinations", {}).items():
            unknown = set(union_data) - {"source_phases"}
            if unknown:
                raise ValueError(
                    f"phase choice {union_id!r} has unknown field(s): {sorted(unknown)}"
                )
            union_phases[union_id] = PhaseChoiceSpec(
                phase_id=union_id,
                source_phase_ids=union_data["source_phases"],
            )
        return cls.model_validate(
            {
                "label": label,
                "default_conditions": default_conditions,
                "version": registry_data["version"],
                "source_phases": source_phases,
                "union_phases": union_phases,
                "protocols": protocols,
            }
        )

    @model_validator(mode="after")
    def validate_phase_references(self) -> "RegistrySpec":
        declared = set(self.source_phases)
        for protocol_id, protocol in self.protocols.items():
            unknown = set(protocol.source_phase_ids) - declared
            if unknown:
                raise ValueError(
                    f"protocol {protocol_id!r} references unknown phase(s): "
                    f"{sorted(unknown)}"
                )
            for optional_id, optional in (protocol.optional_phases or {}).items():
                as_phase = optional.criteria.as_phase
                if as_phase not in declared:
                    raise ValueError(
                        f"protocol {protocol_id!r} optional phase {optional_id!r} "
                        f"references unknown phase {as_phase!r}"
                    )
        for choice_id, choice in self.union_phases.items():
            unknown = set(choice.source_phase_ids) - declared
            if unknown:
                raise ValueError(
                    f"phase choice {choice_id!r} references unknown phase(s): "
                    f"{sorted(unknown)}"
                )
        return self

    def to_dict(self) -> dict[str, dict[str, Any]]:
        """Return the canonical single-profile YAML structure."""
        data = self.model_dump(mode="json")
        data.pop("label")
        data.pop("default_conditions")
        data["conditions"] = self.default_conditions.to_value()

        for phase_id, phase in data["source_phases"].items():
            phase.pop("source_phase_id", None)
            phase["conditions"] = self.source_phases[phase_id].conditions.to_value()
            restrictions = phase["component_requirements"]["category_restrictions"]
            neutral_default = restrictions == {
                "chemotherapy": {"type": "min", "amount": 0}
            }
            if neutral_default:
                phase["component_requirements"]["category_restrictions"] = {}

        for protocol in data["protocols"].values():
            protocol.pop("protocol_id", None)
            protocol["phases"] = protocol.pop("source_phase_ids")

        phase_combinations = {
            choice_id: {"source_phases": choice["source_phase_ids"]}
            for choice_id, choice in data.pop("union_phases").items()
        }
        data["phase_combinations"] = phase_combinations
        return {self.label: data}

    def to_yaml(self) -> str:
        return safe_dump(self.to_dict(), sort_keys=False)
