""""
In development. Script for reading protocol registry.
"""
from __future__ import annotations
from typing import Annotated, Literal, Final, Any
from pydantic import BaseModel, Field, BeforeValidator, ValidationInfo
from yaml import safe_load
from scoop.utils.hemonc_helpers import condition_cuis_by_names

BroadCategory = Literal["chemotherapy", "radiotherapy", "immunotherapy", 
                        "surgery", "supportive",]

def rescue_input_to_list(input):
    if isinstance(input, (int, str)):
        return [input]
    return input


StringOrCuiList = Annotated[list[int] | list[str], BeforeValidator(rescue_input_to_list)]
# I thought it was a bad idea but consider bringing back the old condition validation approach...
# don't want to be passing convert checks everywhere
# ConditionList = StringOrCuiList
# TODO: Standardise like conditions
RegimenList = StringOrCuiList


def condition_db_validation(condition_input: Any, info: ValidationInfo) -> Any:
    context = info.context or {}
    session = context.get("session")
    if session:
        condition_cuis = rescue_condition_input(condition_input, session)
    return condition_cuis


def rescue_condition_input(condition_input, session):
    if isinstance(condition_input, str):
        condition_cuis = condition_cuis_by_names([condition_input], session)
    elif isinstance(condition_input, list) and all(isinstance(value, str) for value in condition_input):
            condition_cuis = condition_cuis_by_names(condition_input, session)
    else:
        condition_cuis = condition_input
    return condition_cuis

ConditionCuis = Annotated[list[int], BeforeValidator(condition_db_validation)]


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
    study_restriction: Literal["must_have_rt"] | None
    curative_intent: bool = True
    regimens: RegimenList | None = Field(default_factory=list)
    type: str | None
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
        conditions = phase_data.get("conditions", default_conditions)
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
    # is this really the best structure?
    optional_phases: dict[str, dict[Literal["criteria"], OptionalPhaseCriteria]] | None

    @classmethod
    def generate(cls, label:str, protocol_data:dict):
        phases = protocol_data["phases"]
        optional_phases = protocol_data.get("optional_phases", None)
        return cls(protocol_id=label, source_phase_ids=phases,  
                   optional_phases=optional_phases)
    

class RegistryYaml(BaseModel):
    label: str
    default_conditions: ConditionCuis
    version: float
    source_phases: dict[str, SourcePhaseYaml]
    union_phases:  dict[str, PhaseUnionYaml]
    protocols:     dict[str, ProtocolYaml]

    @classmethod
    def from_yaml_path(cls, yaml_path, session):
        with yaml_path.open("r", encoding="utf-8") as handle:
            raw = safe_load(handle)

        label, = raw.keys()
        registry_data = raw[label]
        default_conditions = registry_data["conditions"]
        source_phases = {
            label: SourcePhaseYaml.generate(source_phase, label, default_conditions, session) 
            for label, source_phase in registry_data["source_phases"].items()
            }
        protocols = {
            label: ProtocolYaml.generate(label, protocol_data) 
            for label, protocol_data in raw["nsclc"]["protocols"].items()
            }
        phase_unions = {
            label: PhaseUnionYaml(phase_id=label, source_phase_ids=union_data["source_phases"])
            for label, union_data in raw["nsclc"]["phase_combinations"].items()
        }
        data = {
                "label": label,
                "default_conditions": default_conditions, 
                "version": registry_data["version"],
                "source_phases": source_phases,
                "union_phases": phase_unions,
                "protocols": protocols,
        }
        return cls.model_validate(
            data, context = {"session": session}
        )



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