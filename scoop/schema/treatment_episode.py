from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from pydantic import Field
from .transformable_object import TransformableBase
from ..configuration.profile_manager import get_active_simulation_context
# from ..configuration.regimen_registry import RegimenRegistry

class Treatment_Episode(TransformableBase):

    ## identifier fields
    person_id: int 
    condition_occurrence_id: int 
    regimen_type: str
    regimen_category_id: str
    graph_endpoint: str | None = None
    graph_alias_used: str | None = None
    registry_resolver_type: str | None = None
    source_profile: str | None = None
    required_modifiers: list[str] = Field(default_factory=list)
    decision_context: dict[str, Any] = Field(default_factory=dict)
    registry_notes: str | None = None
    bundle_recipe_id: str | None = None
    bundle_sequence_template: str | None = None
    bundle_phase_summary: list[dict[str, Any]] = Field(default_factory=list)

    def roll_events(self):
        """ Placeholder method to generate drug exposures and procedures for this treatment episode. """
        # In a real implementation, this would generate events based on the regimen details.
        drug_exposures = [{"drug_concept_id": 12345, "dose": "50mg", "frequency": "daily"}]
        procedures = [{"procedure_concept_id": 67890, "date": "2023-01-01"}]
        return drug_exposures, procedures


NON_REGISTRY_TREATMENT_STATE_NODES = {"resection_margin"}


@dataclass(frozen=True)
class GraphOutcome:
    primary_category_id: str
    graph_endpoint: str
    graph_alias_used: str | None
    source_profile: str | None
    active_endpoints: tuple[str, ...]
    modifiers: dict[str, Any]
    raw_sample: dict[str, Any]


def extract_active_treatment_endpoints(graph_sample: dict[str, Any]) -> list[str]:
    endpoints: list[str] = []
    for key, value in graph_sample.items():
        if not key.startswith("treatment_episode."):
            continue
        if value is None:
            continue
        endpoint = key.split(".", 1)[1]
        if endpoint in NON_REGISTRY_TREATMENT_STATE_NODES:
            continue
        endpoints.append(endpoint)
    return endpoints


def _extract_modifiers(graph_sample: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in graph_sample.items()
        if not key.startswith("treatment_episode.") and value is not None
    }


def build_graph_outcome(
    graph_sample: dict[str, Any],
    registry: None=None, #RegimenRegistry | None = None,
    source_profile: str | None = None,
) -> GraphOutcome:
    if not graph_sample:
        raise ValueError("Graph sample is required to build a regimen outcome.")

    if registry is None:
        context = get_active_simulation_context()
        if context is None or getattr(context, "regimen_registry", None) is None:
            raise ValueError(
                "No regimen registry was provided and no active profile context is available."
            )
        registry = context.regimen_registry
    active_endpoints = extract_active_treatment_endpoints(graph_sample)
    if not active_endpoints:
        raise ValueError("No active treatment_episode endpoints found in graph sample.")

    resolved: dict[str, list[str]] = {}
    unresolved: list[str] = []
    for endpoint in active_endpoints:
        try:
            canonical_id = registry.resolve_alias(endpoint)
        except KeyError:
            unresolved.append(endpoint)
            continue
        resolved.setdefault(canonical_id, []).append(endpoint)

    if unresolved:
        raise ValueError(f"Unmapped treatment endpoints in graph sample: {sorted(unresolved)}")
    if len(resolved) != 1:
        raise ValueError(
            "Expected exactly one active canonical regimen category, "
            f"found {sorted(resolved)} from endpoints {sorted(active_endpoints)}"
        )

    primary_category_id, raw_endpoints = next(iter(resolved.items()))
    graph_endpoint = raw_endpoints[0]
    graph_alias_used = graph_endpoint if graph_endpoint != primary_category_id else None

    return GraphOutcome(
        primary_category_id=primary_category_id,
        graph_endpoint=graph_endpoint,
        graph_alias_used=graph_alias_used,
        source_profile=source_profile,
        active_endpoints=tuple(active_endpoints),
        modifiers=_extract_modifiers(graph_sample),
        raw_sample=graph_sample,
    )
