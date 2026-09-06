"""Adapters from SCOOP protocol phases to the HemOnc toolkit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
import sqlalchemy.orm as so

from hemonc_alchemy.toolkit.analytics.treatment.selection import (
    CategoryRequirement,
    ComponentRequirement,
    TreatmentSelectionSpec,
    build_variant_query_artifacts as _build_variant_query_artifacts,
    build_variant_statement as _build_variant_statement,
    select_variants,
)
from hemonc_alchemy.toolkit.core.conditions import (
    condition_cuis_by_names as _condition_cuis_by_names,
)

from ..configuration.protocol_registry import (
    ComponentRequirements,
    SourcePhaseYaml,
    apply_default_component_requirements,
)
from .hemonc_helpers import COMPONENT_CLASS_MAPS, HemOncModels, _load_hemonc_models


@dataclass(frozen=True)
class VariantQueryArtifacts:
    """SCOOP-shaped view of the current HemOnc query artifacts."""

    components_subquery: Any
    categorised_components_subquery: Any
    variant_cuis_subquery: Any
    variants_stmt: Any


def _category_mapping() -> dict[str, dict[str, str]]:
    """Return SCOOP's explicit broad-category policy for HemOnc fields."""

    return dict(COMPONENT_CLASS_MAPS)


def _condition_cuis(conditions: Any, session: so.Session | None) -> tuple[int, ...]:
    if isinstance(conditions, str):
        if session is None:
            raise TypeError("A session is required when conditions are names.")
        return tuple(_condition_cuis_by_names(session, conditions))
    values = tuple(conditions)
    if values and all(isinstance(value, str) for value in values):
        if session is None:
            raise TypeError("A session is required when conditions are names.")
        return tuple(_condition_cuis_by_names(session, values))
    return tuple(int(value) for value in values)


def _component_requirements(
    requirements: ComponentRequirements,
) -> tuple[ComponentRequirement, ...]:
    return tuple(
        ComponentRequirement.from_terms(terms)
        for terms in requirements.component_lists
        if terms
    )


def _category_requirements(
    requirements: ComponentRequirements,
) -> tuple[CategoryRequirement, ...]:
    return tuple(
        CategoryRequirement(
            category=category,
            amount=restriction.amount,
            match=restriction.type,
        )
        for category, restriction in requirements.category_restrictions.items()
    )


def _selection_spec(
    component_requirements: ComponentRequirements,
    condition_cuis: Any,
    *,
    regimens: tuple[str, ...] = (),
    phase: str | None = None,
    version_policy: str = "latest",
) -> TreatmentSelectionSpec:
    return TreatmentSelectionSpec.for_conditions(
        condition_cuis,
        component_requirements=_component_requirements(component_requirements),
        category_requirements=_category_requirements(component_requirements),
        regimens=regimens,
        phase=phase,
        version_policy=version_policy,
    )


def _query_spec(
    query: Any,
    *,
    condition_cuis: Any = None,
    session: so.Session | None = None,
) -> TreatmentSelectionSpec:
    """Normalize a protocol source phase or inline query description."""

    if hasattr(query, "component_requirements") and hasattr(query, "condition_cuis"):
        return _selection_spec(
            query.component_requirements,
            _condition_cuis(query.condition_cuis, session),
            regimens=tuple(query.regimens or ()),
            phase=(query.type if query.type and str(query.type).lower() != "none" else None),
        )

    if hasattr(query, "component_lists") and hasattr(query, "default_condition_cuis"):
        requirements = ComponentRequirements(
            component_lists=query.component_lists,
            category_restrictions=query.category_restrictions,
        )
        return _selection_spec(
            requirements,
            _condition_cuis(query.default_condition_cuis, session),
        )

    if condition_cuis is None:
        raise TypeError("condition_cuis is required for inline component requirements.")
    return _selection_spec(
        query,
        _condition_cuis(condition_cuis, session),
    )


def build_components_subquery(
    condition_cuis: list[int],
    models: HemOncModels | None = None,
) -> Any:
    """Build the current HemOnc flat component projection for a condition set."""

    del models
    spec = TreatmentSelectionSpec.for_conditions(condition_cuis)
    return _build_variant_query_artifacts(
        spec,
        category_mapping=_category_mapping(),
    ).components


def build_variant_cui_subquery(
    component_requirements: ComponentRequirements,
    condition_cuis: list[int],
    session: so.Session | None = None,
    models: HemOncModels | None = None,
) -> Any:
    """Build the current HemOnc matching-variant ID subquery."""

    del models
    spec = _selection_spec(
        component_requirements,
        _condition_cuis(condition_cuis, session),
    )
    return _build_variant_query_artifacts(
        spec,
        category_mapping=_category_mapping(),
    ).variant_ids


def get_variant_objects(
    subquery: Any,
    session: so.Session,
    models: HemOncModels | None = None,
) -> list[Any]:
    """Execute a variant ID subquery without discarding the model bundle."""

    model = (models or _load_hemonc_models()).Variants
    statement = sa.select(model).join(subquery, subquery.c.variant_id == model.id)
    return list(session.execute(statement).unique().scalars())


def build_variant_statement(
    component_requirements: Any,
    condition_cuis: Any = None,
    session: so.Session | None = None,
    models: HemOncModels | None = None,
) -> Any:
    """Build a current HemOnc variant statement from a SCOOP query object.

    ``models`` remains accepted for transition compatibility; the statement is
    deliberately built from the public ``hemonc_alchemy`` model and toolkit.
    """

    del models
    spec = _query_spec(
        component_requirements,
        condition_cuis=condition_cuis,
        session=session,
    )
    return _build_variant_statement(spec, category_mapping=_category_mapping())


def build_variant_query_artifacts(
    component_requirements: Any,
    condition_cuis: Any = None,
    models: HemOncModels | None = None,
) -> VariantQueryArtifacts:
    """Build inspectable current HemOnc artifacts for a SCOOP query."""

    del models
    spec = _query_spec(component_requirements, condition_cuis=condition_cuis)
    artifacts = _build_variant_query_artifacts(
        spec,
        category_mapping=_category_mapping(),
    )
    return VariantQueryArtifacts(
        components_subquery=artifacts.components,
        categorised_components_subquery=artifacts.categorized_components,
        variant_cuis_subquery=artifacts.variant_ids,
        variants_stmt=artifacts.variants,
    )


def execute_variant_query(
    session: so.Session,
    source_phase_yaml: SourcePhaseYaml,
    models: HemOncModels | None = None,
) -> list[Any]:
    """Execute a registry source phase through the HemOnc toolkit."""

    del models
    requirements = source_phase_yaml.component_requirements
    spec = _selection_spec(
        requirements,
        _condition_cuis(source_phase_yaml.condition_cuis, session),
        regimens=tuple(source_phase_yaml.regimens or ()),
        phase=(
            source_phase_yaml.type
            if source_phase_yaml.type and str(source_phase_yaml.type).lower() != "none"
            else None
        ),
    )
    return select_variants(
        session,
        spec,
        category_mapping=_category_mapping(),
    )


__all__ = [
    "HemOncModels",
    "VariantQueryArtifacts",
    "_load_hemonc_models",
    "apply_default_component_requirements",
    "build_components_subquery",
    "build_variant_cui_subquery",
    "build_variant_query_artifacts",
    "build_variant_statement",
    "execute_variant_query",
    "get_variant_objects",
]
