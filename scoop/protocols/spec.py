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
from hemonc_alchemy.model.enums import Sigs_PhaseEnum

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


class ComponentCui(BaseModel):
    cui: int = Field(gt=0)
    name: str | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)


def _component_term(value: Any) -> Any:
    if isinstance(value, int) and not isinstance(value, bool):
        return {"cui": value}
    return value


ComponentTerm = Annotated[str | ComponentCui, BeforeValidator(_component_term)]


def component_term_label(term: ComponentTerm) -> str:
    if isinstance(term, str):
        return term
    return term.name or f"CUI {term.cui}"


def rescue_component_input(value: Any) -> Any:
    if value is None or value == []:
        return []
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        return [[value]]
    if isinstance(value, list) and all(
        isinstance(item, (str, int)) and not isinstance(item, bool)
        or isinstance(item, Mapping) and set(item) <= {"cui", "name"}
        for item in value
    ):
        return [value]
    return value


ComponentLists = Annotated[list[list[ComponentTerm]], BeforeValidator(rescue_component_input)]


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
    def reject_empty_component_lists(
        cls, value: list[list[ComponentTerm]]
    ) -> list[list[ComponentTerm]]:
        if any(not components for components in value):
            raise ValueError("component_lists cannot contain empty lists")
        return value

    @classmethod
    def with_defaults(cls, raw: Mapping[str, Any] | None = None) -> "ComponentRule":
        """Apply the registry's explicit category defaulting rules."""
        data = dict(raw or {})
        component_lists = data.get("component_lists")
        if isinstance(component_lists, list) and all(
            isinstance(item, Mapping) and set(item) == {"components"}
            for item in component_lists
        ):
            data["component_lists"] = [item["components"] for item in component_lists]

        category_restrictions = data.get("category_restrictions")
        if isinstance(category_restrictions, list):
            declared_categories = [item["category"] for item in category_restrictions]
            duplicates = sorted(
                {c for c in declared_categories if declared_categories.count(c) > 1}
            )
            if duplicates:
                raise ValueError(
                    f"category_restrictions has duplicate categor(y/ies): {duplicates}"
                )
            data["category_restrictions"] = {
                item["category"]: {
                    "type": item["type"],
                    "amount": item["amount"],
                }
                for item in category_restrictions
            }

        raw_rule = cls.model_validate(data)
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
    unknown_context_policy: Literal["exclude", "include", "phase_type"] = "exclude"
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

    @field_validator("type")
    @classmethod
    def validate_phase_type(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                Sigs_PhaseEnum(value)
            except ValueError as exc:
                allowed = ", ".join(phase.value for phase in Sigs_PhaseEnum)
                raise ValueError(
                    f"unknown HemOnc phase type {value!r}; expected one of: {allowed}"
                ) from exc
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
    selection_requires: tuple[str, ...] = ()
    source_phases: dict[str, PhaseSpec] = Field(default_factory=dict)
    phase_choices: dict[str, PhaseChoiceSpec] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    @field_validator("selection_requires")
    @classmethod
    def validate_selection_requires(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(not field for field in value):
            raise ValueError(
                "selection_requires must contain distinct, non-empty field names"
            )
        return value

    @classmethod
    def generate(
        cls,
        label: str,
        protocol_data: Mapping[str, Any],
        *,
        source_phases: Mapping[str, PhaseSpec] | None = None,
        phase_choices: Mapping[str, PhaseChoiceSpec] | None = None,
    ) -> "ProtocolSpec":
        unknown = set(protocol_data) - {
            "protocol_id",
            "source_phase_ids",
            "optional_phases",
            "selection_requires",
        }
        if unknown:
            raise ValueError(
                f"protocol {label!r} has unknown field(s): {sorted(unknown)}"
            )

        if protocol_data.get("protocol_id") != label:
            raise ValueError(f"protocol {label!r} has mismatched protocol_id")

        optional_phases: dict[str, OptionalPhaseSpec] | None = None
        if protocol_data.get("optional_phases") is not None:
            optional_phases = {}
            declared_ids = [
                item["phase_id"] for item in protocol_data["optional_phases"]
            ]
            duplicates = sorted(
                {phase_id for phase_id in declared_ids if declared_ids.count(phase_id) > 1}
            )
            if duplicates:
                raise ValueError(
                    f"protocol {label!r} has duplicate optional_phases "
                    f"phase_id(s): {duplicates}"
                )
            for optional_data in protocol_data["optional_phases"]:
                optional_data = dict(optional_data)
                optional_id = optional_data.pop("phase_id")
                optional_phases[optional_id] = OptionalPhaseSpec.model_validate(
                    optional_data
                )

        return cls(
            protocol_id=label,
            source_phase_ids=protocol_data["source_phase_ids"],
            optional_phases=optional_phases,
            selection_requires=tuple(protocol_data.get("selection_requires", ())),
            source_phases=dict(source_phases or {}),
            phase_choices=dict(phase_choices or {}),
        )

    @property
    def phase_specs(self) -> dict[str, PhaseSpec]:
        """Compatibility spelling for callers that use the old generator vocabulary."""

        return self.source_phases


def _apply_phase_context_policies(source_phases, policies):
    if not policies:
        return source_phases
    if not isinstance(policies, Mapping) or set(policies) != set(source_phases):
        raise ValueError("phase_context_policies must declare every source phase")
    for phase_id, policy in policies.items():
        if not isinstance(policy, Mapping) or set(policy) != {
            "curative_intent",
            "unknown_context_policy",
        }:
            raise ValueError(
                f"phase context policy for {phase_id!r} must declare "
                "curative_intent and unknown_context_policy"
            )
        if not isinstance(policy["curative_intent"], bool):
            raise ValueError(f"curative_intent for {phase_id!r} must be a boolean")
        if policy["unknown_context_policy"] not in {"exclude", "include", "phase_type"}:
            raise ValueError(f"unknown context policy for {phase_id!r} is invalid")
        source_phases[phase_id] = source_phases[phase_id].model_copy(update=policy)
    return source_phases


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

        if not isinstance(raw, dict):
            raise ValueError("registry YAML must contain a mapping")

        required = {
            "label",
            "version",
            "default_conditions",
            "source_phases",
            "union_phases",
            "protocols",
        }
        missing = required - raw.keys()
        if missing:
            raise ValueError(f"registry is missing: {sorted(missing)}")
        unknown = set(raw) - required - {"phase_context_policies"}
        if unknown:
            raise ValueError(
                f"registry has unknown field(s): {sorted(unknown)}"
            )

        label = raw["label"]
        default_conditions = ConditionRef.from_value(raw["default_conditions"])
        source_phases = {
            phase_id: PhaseSpec.generate(
                phase_data or {}, phase_id, default_conditions
            )
            for phase_id, phase_data in raw["source_phases"].items()
        }
        source_phases = _apply_phase_context_policies(
            source_phases, raw.get("phase_context_policies", {})
        )
        union_phases: dict[str, PhaseChoiceSpec] = {}
        for union_id, union_data in raw["union_phases"].items():
            unknown = set(union_data) - {"phase_id", "source_phase_ids"}
            if unknown:
                raise ValueError(
                    f"phase choice {union_id!r} has unknown field(s): {sorted(unknown)}"
                )
            if union_data.get("phase_id") != union_id:
                raise ValueError(
                    f"phase choice {union_id!r} has mismatched phase_id"
                )
            union_phases[union_id] = PhaseChoiceSpec.model_validate(union_data)
        protocols = {
            protocol_id: ProtocolSpec.generate(
                protocol_id,
                protocol_data,
                source_phases=source_phases,
                phase_choices=union_phases,
            )
            for protocol_id, protocol_data in raw["protocols"].items()
        }
        return cls.model_validate(
            {
                "label": label,
                "default_conditions": default_conditions,
                "version": raw["version"],
                "source_phases": source_phases,
                "union_phases": union_phases,
                "protocols": protocols,
            }
        )

    @model_validator(mode="after")
    def validate_phase_references(self) -> "RegistrySpec":
        declared = set(self.source_phases)
        choices = set(self.union_phases)
        for protocol_id, protocol in self.protocols.items():
            unknown = set(protocol.source_phase_ids) - (declared | choices)
            if unknown:
                raise ValueError(
                    f"protocol {protocol_id!r} references unknown phase(s): "
                    f"{sorted(unknown)}"
                )
            for optional_id, optional in (protocol.optional_phases or {}).items():
                as_phase = optional.criteria.as_phase
                if as_phase not in (declared | choices):
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

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical registry YAML structure."""
        data = self.model_dump(mode="json")

        for phase in data["source_phases"].values():
            requirements = phase["component_requirements"]
            requirements["component_lists"] = [
                {"components": components}
                for components in requirements["component_lists"]
            ]
            restrictions = requirements["category_restrictions"]
            if restrictions == {
                "chemotherapy": {"type": "min", "amount": 0}
            }:
                requirements["category_restrictions"] = []
            else:
                requirements["category_restrictions"] = [
                    {
                        "category": category,
                        "type": restriction["type"],
                        "amount": restriction["amount"],
                    }
                    for category, restriction in restrictions.items()
                ]

        for protocol in data["protocols"].values():
            protocol.pop("source_phases", None)
            protocol.pop("phase_choices", None)
            if protocol["optional_phases"] is not None:
                protocol["optional_phases"] = [
                    {"phase_id": phase_id, **optional}
                    for phase_id, optional in protocol["optional_phases"].items()
                ]

        return data

    def to_yaml(self) -> str:
        return safe_dump(self.to_dict(), sort_keys=False)
