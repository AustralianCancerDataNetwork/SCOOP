"""Helpers for turning resolved HemOnc variant queries into SQLAlchemy queries.

This module covers the variant-backed path only. It normalizes component and
category requirements, builds SQL expressions against the `hemonc_import`
schema, and optionally executes the resulting variant query.
"""
from __future__ import annotations
from typing import Any
import sqlalchemy as sa
import sqlalchemy.orm as so
from dataclasses import dataclass

from ..spec import (
    ComponentRequirements,
    SourcePhaseYaml,
    ConditionCuis,
)
from scoop.protocols.hemonc.models import (
    HemOncModels,
    _load_hemonc_models,
    condition_cuis_by_names,

)
from scoop.protocols.hemonc.queries import (
    categorise_components,
    get_sig_component_stmt,
    build_component_requirement_expressions
)
import logging

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class VariantQueryArtifacts:
    """Intermediate SQL artifacts for inspecting how a variant query was built."""

    # resolved_query: ResolvedHemOncQuery
    # source_phase_yaml: SourcePhaseYaml
    components_subquery: Any
    categorised_components_subquery: Any
    variant_cuis_subquery: Any
    variants_stmt: Any

def build_components_subquery(condition_cuis: list[int], models: HemOncModels | None = None) -> Any:
    """Build the base component-level subquery for a condition set."""

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
        .join(models.variants_StudyMap)
        .join(models.Studies, models.variants_StudyMap.study == models.Studies.study)
        .where(models.Variants.study_items.any(models.Studies.condition_cui.in_(condition_cuis)))
        .subquery()
    )



def convert_conditions(conditions: list, session: so.Session):
    if isinstance(conditions[0], str):
        condition_cuis = condition_cuis_by_names(conditions, session)
    else:
        condition_cuis = conditions
    return condition_cuis

def build_variant_cui_subquery(
    component_requirements: ComponentRequirements,
    condition_cuis: ConditionCuis,
    session: so.Session,
    models: HemOncModels | None = None,
) -> Any:
    """Build the subquery that identifies matching variant CUIs."""

    condition_cuis = convert_conditions(condition_cuis, session)

    components = build_components_subquery(condition_cuis, models=models)
    categorised = categorise_components(components)
    return (
        sa.select(categorised.c.variant_cui)
        .group_by(categorised.c.variant_cui) # TODO: test for nonvar path with study+phase grouping
        .having(*build_component_requirement_expressions(component_requirements, categorised))
        .subquery()
    )

def get_variant_objects(subquery, session, models: HemOncModels | None = None):
    models: HemOncModels | None = None,
    stmt_full = (
        sa.select(models.Variants)
        .join(subquery, models.Variants.variant_cui == subquery.c.variant_cui)
    )
    chosen_variants = session.execute(stmt_full).unique().scalars().all()
    return chosen_variants

def build_variant_statement(
    component_requirements: ComponentRequirements,
    condition_cuis: ConditionCuis,
    session: so.Session,
    models: HemOncModels | None = None,
) -> Any:
    """Build the final SQLAlchemy statement returning matching variant rows."""
    
    models = models or _load_hemonc_models()
    variant_cuis = build_variant_cui_subquery(component_requirements, condition_cuis, 
                                              models=models, session=session)
    return sa.select(models.Variants).join(
        variant_cuis, models.Variants.variant_cui == variant_cuis.c.variant_cui
    )



def build_variant_query_artifacts(
    component_requirements: ComponentRequirements,
    condition_cuis: ConditionCuis,
    models: HemOncModels | None = None,
) -> VariantQueryArtifacts:
    """Build inspectable intermediate SQL artifacts for a resolved variant query."""
    models = models or _load_hemonc_models()
    components = get_sig_component_stmt(condition_cuis, models=models)
    categorised = categorise_components(components)
    variant_cuis = (
        sa.select(categorised.c.variant_cui)
        .group_by(categorised.c.variant_cui)
        .having(
            *build_component_requirement_expressions(
                component_requirements,
                categorised,
            )
        )
        .subquery()
    )
    return VariantQueryArtifacts(
        components_subquery=components,
        categorised_components_subquery=categorised,
        variant_cuis_subquery=variant_cuis,
        variants_stmt=sa.select(models.Variants).join(
            variant_cuis, models.Variants.variant_cui == variant_cuis.c.variant_cui
        ),
    )


def build_variant_statement_for_entry():
    pass

def execute_variant_query(
    session: so.Session,
    source_phase_yaml: SourcePhaseYaml,
    models: HemOncModels | None = None,
) -> list[Any]:
    """Execute a resolved variant query and return ORM variant rows."""
    # TODO: Change approach, split up build_variant_statement
    stmt = build_variant_statement(source_phase_yaml.component_requirements,
                                   source_phase_yaml.conditions, models=models, session=session)
    return session.execute(stmt).unique().scalars().all()


def execute_variant_query_for_entry():
    pass
