from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
import sqlalchemy.orm as so

from ..configuration.regimen_registry import (
    CategoryRestriction,
    RegimenRegistry,
    ResolvedHemOncQuery,
)


MAJOR_CLASS_MAP = {
    "Serine-threonine kinase inhibitor": "chemotherapy",
    "MEK inhibitor": "chemotherapy",
    "Immunostimulatory monoclonal antibody": "immunotherapy",
    "Antineoplastic monoclonal antibody": "immunotherapy",
    "Alkylating agent": "chemotherapy",
    "Microtubule inhibitor": "chemotherapy",
    "Tyrosine kinase inhibitor": "chemotherapy",
    "Topoisomerase inhibitor": "chemotherapy",
    "Antimetabolite": "chemotherapy",
    "Emesis prevention": "supportive",
    "Chemotherapy protective agent": "supportive",
    "Growth factor": "supportive",
    "Antibody-drug conjugate": "immunotherapy",
}

MAIN_CLASS_MAP = {
    "TRK inhibitor": "chemotherapy",
    "KRAS inhibitor": "chemotherapy",
    "Anti-EGFR-HGFR bispecific antibody": "immunotherapy",
    "Anthracycline": "chemotherapy",
    "Multikinase inhibitor": "chemotherapy",
    "MET inhibitor": "chemotherapy",
    "Steroid": "supportive",
    "Antihistamine": "supportive",
    "H2 blocker": "supportive",
    "Opioid receptor agonist": "supportive",
    "Replacement therapy": "supportive",
    "Anti-PD-1-VEGF bispecific antibody": "immunotherapy",
    "Dopamine receptor antagonist": "supportive",
    "Fluoroquinolone": "supportive",
    "H2-receptor antagonist": "supportive",
    "ROS1 inhibitor": "chemotherapy",
    "RET inhibitor": "chemotherapy",
    "Anti-DLL3-CD3 bispecific antibody": "immunotherapy",
}

COMPONENT_MAP = {
    "Bleomycin": "supportive",
    "Granulocyte colony-stimulating factor": "supportive",
    "Serotonin 5-HT3 antagonist": "supportive",
    "Steroid": "supportive",
    "External beam radiotherapy": "radiotherapy",
    "Surgery": "surgery",
    "Emesis prevention": "supportive",
}

DEFAULT_COMPONENT_REQUIREMENTS = {
    "component_lists": [],
    "category_restrictions": {
        "radiotherapy": {"type": "exact", "amount": 0},
        "surgery": {"type": "exact", "amount": 0},
        "chemotherapy": {"type": "exact", "amount": 0},
        "immunotherapy": {"type": "exact", "amount": 0},
    },
}

COMPONENT_STR_COLS = ["canmed_major_class", "canmed_minor_class", "main_class", "component"]


@dataclass(frozen=True)
class HemOncModels:
    Variants: Any
    Sigs: Any
    Studies: Any
    Drugs: Any
    drugs_Canmed_major_classMap: Any
    drugs_Canmed_minor_classMap: Any


@dataclass(frozen=True)
class HemOncQueryArtifacts:
    resolved_query: ResolvedHemOncQuery
    components_subquery: Any
    categorised_components_subquery: Any
    variant_cuis_subquery: Any
    variants_stmt: Any


def _load_hemonc_models() -> HemOncModels:
    try:
        from hemonc_import.final_model.relationships import Drugs, Sigs, Studies, Variants
        from hemonc_import.final_model.entities import (
            drugs_Canmed_major_classMap,
            drugs_Canmed_minor_classMap,
        )
    except ImportError as exc:
        raise ImportError(
            "hemonc_import is required to build executable HemOnc queries. "
            "Install it in the active environment before using these helpers."
        ) from exc

    return HemOncModels(
        Variants=Variants,
        Sigs=Sigs,
        Studies=Studies,
        Drugs=Drugs,
        drugs_Canmed_major_classMap=drugs_Canmed_major_classMap,
        drugs_Canmed_minor_classMap=drugs_Canmed_minor_classMap,
    )


def apply_default_component_requirements(
    component_requirements: dict[str, Any],
) -> dict[str, Any]:
    merged = deepcopy(DEFAULT_COMPONENT_REQUIREMENTS)
    merged["component_lists"] = list(component_requirements.get("component_lists", ()))
    merged["category_restrictions"].update(
        {
            category: _normalise_category_restriction(requirement)
            for category, requirement in component_requirements.get(
                "category_restrictions", {}
            ).items()
        }
    )
    return merged


def _normalise_category_restriction(requirement: Any) -> dict[str, Any]:
    if isinstance(requirement, CategoryRestriction):
        return {"type": requirement.type, "amount": requirement.amount}
    if isinstance(requirement, dict):
        return requirement
    if hasattr(requirement, "type") and hasattr(requirement, "amount"):
        return {
            "type": getattr(requirement, "type"),
            "amount": getattr(requirement, "amount"),
        }
    raise TypeError(
        "Category restriction must be a dict-like object or expose 'type' and 'amount'."
    )


def _component_search_expressions(search_terms: str | list[str], table: Any) -> list[Any]:
    if isinstance(search_terms, str):
        search_terms = [search_terms]
    expressions: list[Any] = []
    for search_term in search_terms:
        for column_name in COMPONENT_STR_COLS:
            expressions.append(getattr(table.c, column_name).ilike(search_term))
    return expressions


def component_search_by_name(search_terms: str | list[str], table: Any) -> Any:
    return (
        sa.select(table.c.component_cui)
        .where(sa.or_(*_component_search_expressions(search_terms, table)))
        .distinct()
        .subquery()
    )


def _category_mapping_expressions(components: Any) -> list[Any]:
    class_maps = {
        "canmed_major_class": MAJOR_CLASS_MAP,
        "main_class": MAIN_CLASS_MAP,
        "component": COMPONENT_MAP,
    }
    expressions: list[Any] = []
    for column_name, mapping in class_maps.items():
        column = getattr(components.c, column_name)
        expressions.append(
            sa.case(
                *[(column == source_value, mapped_value) for source_value, mapped_value in mapping.items()]
            )
        )
    return expressions


def build_components_subquery(condition_cuis: list[int], models: HemOncModels | None = None) -> Any:
    models = models or _load_hemonc_models()
    return (
        sa.select(
            models.Variants.variant_cui,
            models.Sigs.component,
            models.Sigs.component_cui,
            models.Drugs.drug_inn,
            models.Drugs.main_class,
            models.drugs_Canmed_major_classMap.canmed_major_class,
            models.drugs_Canmed_minor_classMap.canmed_minor_class,
        )
        .join(models.Variants.component_sigs)
        .outerjoin(models.Drugs, models.Sigs.component_cui == models.Drugs.drug_cui)
        .outerjoin(
            models.drugs_Canmed_major_classMap,
            models.drugs_Canmed_major_classMap.parent_id == models.Drugs.id,
        )
        .outerjoin(
            models.drugs_Canmed_minor_classMap,
            models.drugs_Canmed_minor_classMap.parent_id == models.Drugs.id,
        )
        .where(models.Variants.study_objects.any(models.Studies.condition_cui.in_(condition_cuis)))
        .subquery()
    )


def categorise_components(components: Any) -> Any:
    return (
        sa.select(
            components.c.variant_cui,
            components.c.canmed_major_class,
            components.c.canmed_minor_class,
            components.c.main_class,
            components.c.component,
            components.c.component_cui,
            sa.func.coalesce(*_category_mapping_expressions(components)).label("broad_category"),
        )
        .distinct()
        .subquery()
    )


def build_component_requirement_expressions(
    component_requirements: dict[str, Any],
    categorised_components: Any,
) -> list[Any]:
    normalised = apply_default_component_requirements(component_requirements)
    expressions: list[Any] = []

    for category, requirement in normalised["category_restrictions"].items():
        category_count = sa.func.sum(
            sa.case((categorised_components.c.broad_category == category, 1), else_=0)
        )
        if requirement["type"] == "exact":
            expressions.append(category_count == requirement["amount"])
        elif requirement["type"] == "min":
            expressions.append(category_count >= requirement["amount"])
        else:
            raise ValueError(f"Unsupported category restriction type: {requirement['type']}")

    for component_list in normalised["component_lists"]:
        component_cuis = sa.select(component_search_by_name(component_list, categorised_components))
        expressions.append(
            sa.func.sum(
                sa.case(
                    (categorised_components.c.component_cui.in_(component_cuis), 1),
                    else_=0,
                )
            )
            >= 1
        )

    return expressions


def build_variant_cui_subquery(
    resolved_query: ResolvedHemOncQuery,
    models: HemOncModels | None = None,
) -> Any:
    component_requirements = {
        "component_lists": resolved_query.component_lists,
        "category_restrictions": resolved_query.category_restrictions,
    }
    components = build_components_subquery(resolved_query.default_condition_cuis, models=models)
    categorised = categorise_components(components)
    return (
        sa.select(categorised.c.variant_cui)
        .group_by(categorised.c.variant_cui)
        .having(*build_component_requirement_expressions(component_requirements, categorised))
        .subquery()
    )


def build_variant_statement(
    resolved_query: ResolvedHemOncQuery,
    models: HemOncModels | None = None,
) -> Any:
    models = models or _load_hemonc_models()
    variant_cuis = build_variant_cui_subquery(resolved_query, models=models)
    return sa.select(models.Variants).join(
        variant_cuis, models.Variants.variant_cui == variant_cuis.c.variant_cui
    )


def build_variant_query_artifacts(
    resolved_query: ResolvedHemOncQuery,
    models: HemOncModels | None = None,
) -> HemOncQueryArtifacts:
    models = models or _load_hemonc_models()
    components = build_components_subquery(resolved_query.default_condition_cuis, models=models)
    categorised = categorise_components(components)
    variant_cuis = (
        sa.select(categorised.c.variant_cui)
        .group_by(categorised.c.variant_cui)
        .having(
            *build_component_requirement_expressions(
                {
                    "component_lists": resolved_query.component_lists,
                    "category_restrictions": resolved_query.category_restrictions,
                },
                categorised,
            )
        )
        .subquery()
    )
    return HemOncQueryArtifacts(
        resolved_query=resolved_query,
        components_subquery=components,
        categorised_components_subquery=categorised,
        variant_cuis_subquery=variant_cuis,
        variants_stmt=sa.select(models.Variants).join(
            variant_cuis, models.Variants.variant_cui == variant_cuis.c.variant_cui
        ),
    )


def build_variant_statement_for_entry(
    registry: RegimenRegistry,
    entry_id: str,
    models: HemOncModels | None = None,
) -> Any:
    resolved_query = registry.resolve_hemonc_query(entry_id)
    if resolved_query is None:
        raise ValueError(f"Registry entry '{entry_id}' does not resolve to a HemOnc query.")
    return build_variant_statement(resolved_query, models=models)


def execute_variant_query(
    session: so.Session,
    resolved_query: ResolvedHemOncQuery,
    models: HemOncModels | None = None,
) -> list[Any]:
    stmt = build_variant_statement(resolved_query, models=models)
    return session.execute(stmt).unique().scalars().all()


def execute_variant_query_for_entry(
    session: so.Session,
    registry: RegimenRegistry,
    entry_id: str,
    models: HemOncModels | None = None,
) -> list[Any]:
    resolved_query = registry.resolve_hemonc_query(entry_id)
    if resolved_query is None:
        raise ValueError(f"Registry entry '{entry_id}' does not resolve to a HemOnc query.")
    return execute_variant_query(session, resolved_query, models=models)
