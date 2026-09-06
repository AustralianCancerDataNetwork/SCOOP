"""SCOOP adapters for released HemOnc variant-selection APIs."""

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

from ..spec import ConditionRef, ComponentRule, PhaseSpec
from .models import HemOncModels, _load_hemonc_models


@dataclass(frozen=True)
class VariantQueryArtifacts:
    """SCOOP-shaped view of the current HemOnc query artifacts."""

    components_subquery: Any
    categorised_components_subquery: Any
    variant_cuis_subquery: Any
    variants_stmt: Any


def _category_mapping() -> dict[str, dict[str, str]]:
    """Return SCOOP policy mappings for the toolkit category projection."""

    from .models import COMPONENT_CLASS_MAPS

    return dict(COMPONENT_CLASS_MAPS)


def _condition_cuis(
    conditions: ConditionRef | str | list[str] | list[int] | tuple[str, ...] | tuple[int, ...],
    session: so.Session | None,
) -> tuple[int, ...]:
    """Resolve a protocol condition declaration into HemOnc CUIs."""

    if isinstance(conditions, ConditionRef):
        if conditions.cuis is not None:
            return tuple(int(value) for value in conditions.cuis)
        conditions = conditions.names or []
    if isinstance(conditions, str):
        conditions = [conditions]
    values = tuple(conditions)
    if values and all(isinstance(value, str) for value in values):
        if session is None:
            raise TypeError("A session is required when conditions are names.")
        return tuple(_condition_cuis_by_names(session, list(values)))
    return tuple(int(value) for value in values)


def _component_requirements(
    requirements: ComponentRule,
) -> tuple[ComponentRequirement, ...]:
    return tuple(
        ComponentRequirement.from_terms(terms)
        for terms in requirements.component_lists
        if terms
    )


def _category_requirements(
    requirements: ComponentRule,
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
    requirements: ComponentRule,
    condition_cuis: tuple[int, ...],
    *,
    regimens: tuple[str | int, ...] = (),
    phase: str | None = None,
) -> TreatmentSelectionSpec:
    return TreatmentSelectionSpec.for_conditions(
        condition_cuis,
        component_requirements=_component_requirements(requirements),
        category_requirements=_category_requirements(requirements),
        regimens=regimens,
        phase=phase,
    )


def _query_spec(
    query: PhaseSpec | ComponentRule,
    *,
    condition_cuis: Any = None,
    session: so.Session | None = None,
) -> TreatmentSelectionSpec:
    """Adapt a reorg protocol object to the package-owned selection spec."""

    if isinstance(query, PhaseSpec):
        return _selection_spec(
            query.component_requirements,
            _condition_cuis(query.conditions, session),
            regimens=tuple(query.regimens or ()),
            phase=query.type if query.type and str(query.type).lower() != "none" else None,
        )
    if condition_cuis is None:
        raise TypeError("condition_cuis is required for component requirements.")
    return _selection_spec(
        query,
        _condition_cuis(condition_cuis, session),
    )


def build_components_subquery(
    condition_cuis: list[int] | tuple[int, ...],
    models: HemOncModels | None = None,
) -> Any:
    """Build the toolkit component projection for a condition set."""

    del models
    spec = TreatmentSelectionSpec.for_conditions(condition_cuis)
    return _build_variant_query_artifacts(
        spec,
        category_mapping=_category_mapping(),
    ).components


def build_variant_cui_subquery(
    component_requirements: ComponentRule,
    condition_cuis: ConditionRef | list[int] | list[str] | tuple[int, ...] | tuple[str, ...],
    session: so.Session | None = None,
    models: HemOncModels | None = None,
) -> Any:
    """Build the toolkit subquery selecting matching variant IDs."""

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
    """Execute a variant-ID subquery without discarding model identity."""

    model = (models or _load_hemonc_models()).Variants
    statement = sa.select(model).join(subquery, subquery.c.variant_id == model.id)
    return list(session.execute(statement).unique().scalars())


def build_variant_statement(
    component_requirements: ComponentRule | PhaseSpec,
    condition_cuis: Any = None,
    session: so.Session | None = None,
    models: HemOncModels | None = None,
) -> Any:
    """Build a current HemOnc variant statement from a SCOOP query object."""

    del models
    spec = _query_spec(
        component_requirements,
        condition_cuis=condition_cuis,
        session=session,
    )
    return _build_variant_statement(spec, category_mapping=_category_mapping())


def build_variant_query_artifacts(
    component_requirements: ComponentRule | PhaseSpec,
    condition_cuis: Any = None,
    session: so.Session | None = None,
    models: HemOncModels | None = None,
) -> VariantQueryArtifacts:
    """Build inspectable current HemOnc artifacts for a SCOOP query."""

    del models
    spec = _query_spec(
        component_requirements,
        condition_cuis=condition_cuis,
        session=session,
    )
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
    phase_spec: PhaseSpec,
    models: HemOncModels | None = None,
) -> list[Any]:
    """Execute a protocol phase through the released HemOnc toolkit."""

    del models
    spec = _query_spec(phase_spec, session=session)
    return select_variants(
        session,
        spec,
        category_mapping=_category_mapping(),
    )


def build_variant_statement_for_entry(
    phase_spec: PhaseSpec,
    *,
    session: so.Session | None = None,
    models: HemOncModels | None = None,
) -> Any:
    """Build a variant statement for a typed protocol phase entry."""

    return build_variant_statement(phase_spec, session=session, models=models)


def execute_variant_query_for_entry(
    session: so.Session,
    phase_spec: PhaseSpec,
    models: HemOncModels | None = None,
) -> list[Any]:
    """Execute a typed protocol phase entry."""

    return execute_variant_query(session, phase_spec, models=models)


__all__ = [
    "HemOncModels",
    "VariantQueryArtifacts",
    "_load_hemonc_models",
    "build_components_subquery",
    "build_variant_cui_subquery",
    "build_variant_query_artifacts",
    "build_variant_statement",
    "build_variant_statement_for_entry",
    "execute_variant_query",
    "execute_variant_query_for_entry",
    "get_variant_objects",
]
