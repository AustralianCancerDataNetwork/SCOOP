"""Typed loading and validation for SCOOP protocol registries."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, Field, BeforeValidator, ValidationInfo, model_validator
from yaml import safe_load

from scoop.utils.hemonc_helpers import condition_cuis_by_names

BroadCategory = Literal[
    "chemotherapy",
    "radiotherapy",
    "immunotherapy",
    "surgery",
    "supportive",
]

def rescue_input_to_list(value):
    if isinstance(value, (int, str)):
        return [value]
    return value


StringOrCuiList = Annotated[list[int] | list[str], BeforeValidator(rescue_input_to_list)]
RegimenList = StringOrCuiList


def condition_db_validation(condition_input: Any, info: ValidationInfo) -> Any:
    context = info.context or {}
    session = context.get("session")
    if session:
        return rescue_condition_input(condition_input, session)
    return condition_input


def rescue_condition_input(condition_input, session):
    if isinstance(condition_input, str):
        condition_cuis = condition_cuis_by_names([condition_input], session)
    elif isinstance(condition_input, list) and all(isinstance(value, str) for value in condition_input):
            condition_cuis = condition_cuis_by_names(condition_input, session)
    else:
        condition_cuis = condition_input
    return condition_cuis

# Condition names are intentionally retained when a database session is not
# available.  Resolution to CUIs belongs at query execution time, not at YAML
# loading time.  This allows profile validation and CLI inspection without a
# live HemOnc database.
ConditionCuis = Annotated[list[int] | list[str], BeforeValidator(condition_db_validation)]


def rescue_component_input(component_input):
    if isinstance(component_input, list) and all(isinstance(value, str) for value in component_input):
        component_lists = [component_input]
    elif isinstance(component_input, str):
        component_lists = [[component_input]]
    else:
        component_lists=component_input
    return component_lists

ComponentLists = Annotated[list[list[str]], BeforeValidator(rescue_component_input)]


def check_phases_are_subset(v: list[str] | None | Literal["all", "none"], info) -> list[str]:
    if v == "all":
        v = info.data.get("source_phase_ids", [])
    if v == "none":
        v = None
    if v:
        source_phase_ids = set(info.data.get("source_phase_ids", []))
        if not set(v).issubset(source_phase_ids):
            raise ValueError(f"{info.field_name} must be a subset of source_phases")
    return v

PhaseSubset = Annotated[list[str]| None, BeforeValidator(check_phases_are_subset)]


# Class definitions ###########################################################

class CategoryRestriction(BaseModel):
    type: Literal["exact", "min"]
    amount: int

    
class ComponentRequirements(BaseModel):
    component_lists: ComponentLists = Field(default_factory=list)
    category_restrictions: dict[BroadCategory, CategoryRestriction] = Field(default_factory=dict)
    model_config = {"extra": "forbid"}

# This doesn't use EXCLUDE_RESTRICTION because that would require changing
# apply_default_component_requirements()
DEFAULT_CATEGORY_RESTRICTIONS: Final[dict[BroadCategory, CategoryRestriction]] = {
    "chemotherapy":  {"type": "exact", "amount": 0},
    "radiotherapy":  {"type": "exact", "amount": 0},
    "immunotherapy": {"type": "exact", "amount": 0},
    "surgery":       {"type": "exact", "amount": 0},
    # NOTE: "supportive" intentionally excluded
}

EXCLUDE_RESTRICTION = CategoryRestriction(type='exact', amount=0)

# BAD IDEA: WE WANT TO KNOW WHETHER VARIANTABLE OR NOT
# class RegimenSpecifier(BaseModel):
#     component_requirements: Optional[ComponentRequirements] = None
#     regimens: Optional[RegimenList] = None

#     @model_validator(mode="after")
#     def _has_at_least_one(self):
#         if not self.component_requirements and not self.regimens:
#             raise ValueError("Must have component requirements or regimen list.")
#         return self

class SourcePhaseYaml(BaseModel):
    source_phase_id: str | None = None
    condition_cuis: ConditionCuis = Field(default_factory=list)
    component_requirements: ComponentRequirements = Field(default_factory=dict)
    study_restriction: Literal["must_have_rt"] | None = None
    curative_intent: bool = True
    regimens: RegimenList | None = Field(default_factory=list)
    type: str | None = None
    standalone: bool = True

    model_config = {"extra": "forbid"}

    @classmethod
    def generate(cls,
        phase_data: dict,
        label: str,
        default_conditions: ConditionCuis,
        session
        ) -> SourcePhaseYaml:
        """
        Construct a SourcePhaseYaml, inheriting treatment-group default
        conditions when not explicitly provided.
        """
        conditions = rescue_input_to_list(
            phase_data.get("conditions", default_conditions)
        )
        if not conditions:
            conditions = default_conditions
        data = {"condition_cuis": conditions,
            "component_requirements":apply_default_component_requirements(phase_data.get("component_requirements")),
            # "optional_reqs":phase_data.get("optional_reqs"),
            "study_restriction":phase_data.get("study_restriction"),
            "source_phase_id":label,
            "type":phase_data.get("type"),
            "regimens":phase_data.get("regimens"),
            "curative_intent": phase_data.get("curative_intent", True),
            "standalone": phase_data.get("standalone", True)}
        return cls.model_validate(
            data, context = {"session": session}
        )
    
    def categorise(self):
        broad_categories = None
        category_restrictions = self.component_requirements.category_restrictions
        if category_restrictions:
            broad_categories = {broad_category for broad_category in category_restrictions 
                                if category_restrictions[broad_category] != EXCLUDE_RESTRICTION}
        return broad_categories


class PhaseUnionYaml(BaseModel):
    phase_id: str
    source_phase_ids: list[str]

    model_config = {"extra": "forbid"}


class OptionalPhaseCriteria(BaseModel):
    shared: Literal["component"]
    as_phase: str # should be a phase_id in source_phase_ids


class ProtocolYaml(BaseModel):
    protocol_id: str
    source_phase_ids: list[str]
    optional_phases: dict[str, dict[Literal["criteria"], OptionalPhaseCriteria]] | None = None

    @classmethod
    def generate(cls, label:str, protocol_data:dict):
        phases = protocol_data["phases"]
        optional_phases = protocol_data.get("optional_phases", None)
        return cls(
            protocol_id=label,
            source_phase_ids=phases,
            optional_phases=optional_phases,
        )


class ResolverYaml(BaseModel):
    """Resolver declaration attached to a protocol entry."""

    type: str
    group_id: str | None = None
    members: list[str] = Field(default_factory=list)

    # Registry evolution should not make the loader unusable when a new
    # resolver-specific field is added.  Resolver implementations can validate
    # their own parameters after this structural load has completed.
    model_config = {"extra": "allow"}


class ProtocolEntryYaml(BaseModel):
    """Protocol endpoint metadata used by graph outcome normalization."""

    public: bool = True
    stage_scope: list[str] = Field(default_factory=list)
    clinical_group: str | None = None
    aliases: list[str] = Field(default_factory=list)
    graph_sources: list[str] = Field(default_factory=list)
    exploratory_sources: list[str] = Field(default_factory=list)
    required_modifiers: list[str] = Field(default_factory=list)
    resolver: ResolverYaml
    notes: str | None = None

    model_config = {"extra": "allow"}
    

class RegistryYaml(BaseModel):
    label: str
    disease: str | None = None
    default_conditions: ConditionCuis = Field(default_factory=list)
    version: float
    condition_sets: dict[str, list[int]] = Field(default_factory=dict)
    hemonc_groups: dict[str, Any] = Field(default_factory=dict)
    hemonc_bundle_groups: dict[str, Any] = Field(default_factory=dict)
    source_phases: dict[str, SourcePhaseYaml] = Field(default_factory=dict)
    union_phases: dict[str, PhaseUnionYaml] = Field(default_factory=dict)
    protocols: dict[str, ProtocolYaml] = Field(default_factory=dict)
    entries: dict[str, ProtocolEntryYaml] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_phase_references(self) -> "RegistryYaml":
        """Reject protocol references to undeclared source phases early."""

        source_phase_ids = set(self.source_phases)
        for protocol_id, protocol in self.protocols.items():
            missing = set(protocol.source_phase_ids) - source_phase_ids
            if missing:
                raise ValueError(
                    f"Protocol '{protocol_id}' references unknown source phases: "
                    f"{sorted(missing)}"
                )
            for optional_id, optional in (protocol.optional_phases or {}).items():
                criteria = optional.get("criteria")
                if criteria is None:
                    continue
                if criteria.as_phase not in protocol.source_phase_ids:
                    raise ValueError(
                        f"Protocol '{protocol_id}' optional phase '{optional_id}' "
                        f"refers to unknown phase '{criteria.as_phase}'."
                    )

        for union_id, union in self.union_phases.items():
            missing = set(union.source_phase_ids) - source_phase_ids
            if missing:
                raise ValueError(
                    f"Phase union '{union_id}' references unknown source phases: "
                    f"{sorted(missing)}"
                )
        return self

    @classmethod
    def from_yaml_path(cls, yaml_path: Path | str, session=None) -> "RegistryYaml":
        """Load one protocol registry without requiring a live database.

        Condition names remain names when ``session`` is omitted.  A session can
        still be supplied for callers that want eager CUI resolution.
        """

        path = Path(yaml_path)
        with path.open("r", encoding="utf-8") as handle:
            raw = safe_load(handle) or {}

        if len(raw) != 1:
            raise ValueError(
                f"Protocol registry '{path}' must contain exactly one root profile."
            )

        label, registry_data = next(iter(raw.items()))
        if not isinstance(registry_data, dict):
            raise ValueError(f"Protocol registry root '{label}' must be a mapping.")

        default_conditions = registry_data.get("conditions", [])
        source_phases = {
            phase_label: SourcePhaseYaml.generate(
                source_phase,
                phase_label,
                default_conditions,
                session,
            )
            for phase_label, source_phase in registry_data.get("source_phases", {}).items()
        }
        protocols = {
            protocol_label: ProtocolYaml.generate(protocol_label, protocol_data)
            for protocol_label, protocol_data in registry_data.get("protocols", {}).items()
        }
        phase_unions = {
            union_label: PhaseUnionYaml(
                phase_id=union_label,
                source_phase_ids=union_data["source_phases"],
            )
            for union_label, union_data in registry_data.get("phase_combinations", {}).items()
        }
        canonical_label = registry_data.get("disease", label)
        data = {
            "label": canonical_label,
            "disease": registry_data.get("disease"),
            "default_conditions": default_conditions,
            "version": registry_data["version"],
            "condition_sets": registry_data.get("condition_sets", {}),
            "hemonc_groups": registry_data.get("hemonc_groups", {}),
            "hemonc_bundle_groups": registry_data.get("hemonc_bundle_groups", {}),
            "source_phases": source_phases,
            "union_phases": phase_unions,
            "protocols": protocols,
            "entries": registry_data.get("entries", {}),
        }
        return cls.model_validate(data, context={"session": session})


def load_protocol_registry(
    yaml_path: Path | str,
    session=None,
) -> RegistryYaml:
    """Load and validate a protocol registry from a YAML path."""

    return RegistryYaml.from_yaml_path(yaml_path, session=session)



def apply_default_component_requirements(
    raw_component_requirements: dict | None,
) -> ComponentRequirements:
    """
    Apply component requirement defaults during YAML generation.

    Rules:
    1. If ANY category_restrictions are provided by the user:
       - Apply full default exclusions
       - Overlay user-provided restrictions
    2. If NO category_restrictions are provided:
       - Do NOT apply defaults
       - Add a neutral anchoring restriction:
         chemotherapy min 0
    """
    raw_component_requirements = raw_component_requirements or {}

    raw_category_restrictions = raw_component_requirements.get("category_restrictions")

    if raw_category_restrictions:
        category_restrictions = {
            k: CategoryRestriction(**v)
            for k, v in DEFAULT_CATEGORY_RESTRICTIONS.items()
        }

        for k, v in raw_category_restrictions.items():
            category_restrictions[k] = CategoryRestriction(**v)

    else:
        category_restrictions = {
            "chemotherapy": CategoryRestriction(type="min", amount=0)
        }

    return ComponentRequirements(
        component_lists=raw_component_requirements.get("component_lists", []),
        category_restrictions=category_restrictions,
    )
