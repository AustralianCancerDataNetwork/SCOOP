# holding all these steps separately so that pydantic classes can build in the appropriate order
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import logging

from .person import Person
from .condition_occurrence import Condition_Occurrence
from .condition_episode import Condition_Episode
from .episode_base import Episode_Base
from .treatment_episode import Treatment_Episode, build_graph_outcome

from ..configuration.profile_manager import get_active_simulation_context

logger = logging.getLogger(__name__)


MAX_GRAPH_SAMPLE_ATTEMPTS = 25


@dataclass(frozen=True)
class CaseBundle:
    graph_sample: dict[str, Any]
    person: Person
    condition_occurrence: Condition_Occurrence
    treatment_episode: Treatment_Episode | None


def _active_profile_name() -> str:
    context = get_active_simulation_context()
    return getattr(context, "profile_name", "unknown_profile")


def _active_profile_id() -> str:
    context = get_active_simulation_context()
    return getattr(context, "profile_id", "unknown_profile")


def _sample_graph_state(require_regimen: bool = False, **overrides: Any) -> dict[str, Any]:
    context = get_active_simulation_context()
    if context is None or getattr(context, "graph", None) is None:
        raise RuntimeError(
            "No active simulation context is available. Load a profile before sampling."
        )
    last_error: Exception | None = None
    last_sampled: dict[str, Any] | None = None

    for _ in range(MAX_GRAPH_SAMPLE_ATTEMPTS if require_regimen else 1):
        try:
            sampled = context.graph.sample_one()
            last_sampled = sampled
            if overrides:
                sampled.update(overrides)
            if not require_regimen:
                return sampled
            build_graph_outcome(graph_sample=sampled, source_profile=_active_profile_name())
            return sampled
        except Exception as exc:
            last_error = exc

    if last_sampled is not None:
        logger.debug(
            "Falling back to the last sampled graph state without a registry-backed regimen endpoint: %s",
            last_error,
        )
        return last_sampled

    raise ValueError(
        "Unable to sample a registry-backed treatment endpoint from the active profile graph."
    ) from last_error


def _build_person_fields(graph_sample: dict[str, Any]) -> dict[str, Any]:
    return {
        "sex": graph_sample.get("person.sex", "female"),
        "age": graph_sample.get("person.age", 70),
        "smoking_history": graph_sample.get("person.smoking_history", "never"),
        "weight": graph_sample.get("person.weight", 75.0),
        "graph_sample": graph_sample,
    }


def _build_condition_fields(graph_sample: dict[str, Any], person: Person) -> dict[str, Any]:
    return {
        "person_id": person.person_id,
        "year_of_diagnosis": graph_sample.get("person.year_of_diagnosis"),
        "years_since_diagnosis": graph_sample.get("person.years_since_diagnosis"),
        "morphology": graph_sample.get("condition_occurrence.morphology", "neither"),
        "laterality": graph_sample.get("condition_occurrence.laterality", "right"),
        "topog": graph_sample.get("condition_occurrence.topog", "upper"),
        "stage": graph_sample.get("condition_occurrence.cancer_stage", "stage_ii"),
        "cancer_stage": graph_sample.get("condition_occurrence.cancer_stage"),
        "biomarker": graph_sample.get("condition_occurrence.biomarker"),
        "pdl1": graph_sample.get("condition_occurrence.pdl1"),
        "pdl1_binary": graph_sample.get("condition_occurrence.pdl1_binary"),
        "graph_sample": graph_sample,
        "condition_domain": _active_profile_id(),
        "source_profile": _active_profile_name(),
    }


def build_person(
    graph_sample: dict[str, Any],
    *,
    attach_condition: bool = False,
    **overrides: Any,
) -> Person:
    """Build a Person from an existing graph sample."""
    person_fields = _build_person_fields(graph_sample)
    person_fields["auto_create_condition"] = attach_condition
    if overrides:
        person_fields.update(overrides)
    return Person(**person_fields)


def build_condition_occurrence(
    person: Person,
    graph_sample: dict[str, Any],
    *,
    assign_treatment: bool = False,
    **overrides: Any,
) -> Condition_Occurrence:
    """Build a Condition_Occurrence from an existing graph sample."""
    condition_fields = _build_condition_fields(graph_sample, person)
    condition_fields["auto_assign_regimen"] = assign_treatment
    if overrides:
        condition_fields.update(overrides)
    return Condition_Occurrence(**condition_fields)


def build_condition_episode(
    condition_occurrence: Condition_Occurrence,
    **overrides: Any,
) -> Condition_Episode:
    """Build one top-level OMOP Episode from an existing Condition_Occurrence."""
    return Episode_Base.from_object(condition_occurrence, **overrides)


def build_case_bundle(
    graph_sample: dict[str, Any] | None = None,
    *,
    require_regimen_endpoint: bool = False,
    include_treatment_episode: bool = False,
    person_overrides: dict[str, Any] | None = None,
    condition_overrides: dict[str, Any] | None = None,
    treatment_overrides: dict[str, Any] | None = None,
) -> CaseBundle:
    """Build a Person/Condition/Treatment bundle from one graph sample."""
    sampled = graph_sample or _sample_graph_state(require_regimen=require_regimen_endpoint)
    person = build_person(
        sampled,
        attach_condition=False,
        **(person_overrides or {}),
    )
    condition = build_condition_occurrence(
        person,
        sampled,
        assign_treatment=False,
        **(condition_overrides or {}),
    )
    person.condition = condition

    treatment_episode: Treatment_Episode | None = None
    if include_treatment_episode:
        treatment_episode = assign_regimen(condition, **(treatment_overrides or {}))
        condition.regimen = treatment_episode

    return CaseBundle(
        graph_sample=sampled,
        person=person,
        condition_occurrence=condition,
        treatment_episode=treatment_episode,
    )

def person_from_config(**overrides) -> "Person":
    """
    Sample from the configuration and return a Person instance.
    Optional keyword args override sampled values.
    """
    sampled = _sample_graph_state(require_regimen=False)
    person_fields = _build_person_fields(sampled)
    logger.debug(f"Person from graph sample [{person_fields}]")
    if overrides:
        person_fields.update(overrides)
        logger.debug(f"Person with overrides [{person_fields}]")
    return Person(**person_fields)


def make_condition_occurrence(person: Person, graph_sample: dict[str, Any] | None = None, **overrides) -> "Condition_Occurrence":
    """
    Sample from the configuration and return a Condition_Occurrence instance.
    Optional keyword args override sampled values.
    """
    sampled = graph_sample or _sample_graph_state(require_regimen=False)
    condition_fields = _build_condition_fields(sampled, person)
    logger.debug(f"Condition from graph sample [{condition_fields}]")
    if overrides:
        condition_fields.update(overrides)
        logger.debug(f"Condition with overrides [{condition_fields}]")
    return Condition_Occurrence(**condition_fields)


def assign_regimen(condition_occurrence: Condition_Occurrence, **overrides: Any) -> "Treatment_Episode":
    """
    Assign a relevant, clinically plausible regimen based on the Condition_Occurrence & Person parameters.
    """
    graph_sample = condition_occurrence.graph_sample
    if not graph_sample:
        regimen_type = overrides.pop("regimen_type", "registry_unresolved")
        logger.debug(
            "Assigning unresolved regimen for Condition[%s] because no graph sample was attached.",
            condition_occurrence.condition_occurrence_id,
        )
        return Treatment_Episode(
            regimen_type=regimen_type,
            regimen_category_id=regimen_type,
            condition_occurrence_id=condition_occurrence.condition_occurrence_id,
            person_id=condition_occurrence.person_id,
            source_profile=condition_occurrence.source_profile,
            registry_resolver_type="none",
            decision_context={},
            **overrides,
        )

    try:
        context = get_active_simulation_context()
        outcome = build_graph_outcome(
            graph_sample=graph_sample,
            registry=getattr(context, "protocol_registry", None),
            source_profile=condition_occurrence.source_profile or _active_profile_name(),
        )
    except ValueError as exc:
        logger.debug(
            "Unable to resolve graph sample to a canonical regimen for Condition[%s]: %s",
            condition_occurrence.condition_occurrence_id,
            exc,
        )
        return Treatment_Episode(
            regimen_type="registry_unresolved",
            regimen_category_id="registry_unresolved",
            graph_endpoint=None,
            graph_alias_used=None,
            registry_resolver_type="none",
            source_profile=condition_occurrence.source_profile or _active_profile_name(),
            required_modifiers=[],
            decision_context={
                key: value
                for key, value in graph_sample.items()
                if value is not None and not key.startswith("treatment_episode.")
            },
            registry_notes=str(exc),
            condition_occurrence_id=condition_occurrence.condition_occurrence_id,
            person_id=condition_occurrence.person_id,
            **overrides,
        )

    logger.debug(
        "Assigning regimen '%s' for Condition[%s] from graph endpoint '%s'",
        outcome.primary_category_id,
        condition_occurrence.condition_occurrence_id,
        outcome.graph_endpoint,
    )
    return Treatment_Episode(
        regimen_type=outcome.primary_category_id,
        regimen_category_id=outcome.primary_category_id,
        graph_endpoint=outcome.graph_endpoint,
        graph_alias_used=outcome.graph_alias_used,
        registry_resolver_type=outcome.resolver_type,
        source_profile=outcome.source_profile,
        required_modifiers=[],
        decision_context=outcome.modifiers,
        registry_notes=None,
        bundle_recipe_id=outcome.bundle_recipe_id,
        condition_occurrence_id=condition_occurrence.condition_occurrence_id,
        person_id=condition_occurrence.person_id,
        **overrides,
    )
