from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from pydantic import Field
from .transforms import TransformableBase
from ..profiles.context import get_active_simulation_context
from ..selection.interventions import InterventionCatalog, Recipe

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
    drug_exposures: list[dict[str, Any]] = Field(default_factory=list)
    procedures: list[dict[str, Any]] = Field(default_factory=list)

    def roll_events(self):
        """Return event payloads explicitly produced by treatment resolution."""

        drug_exposures = [dict(event) for event in self.drug_exposures]
        procedures = [dict(event) for event in self.procedures]
        for phase in self.bundle_phase_summary:
            drug_exposures.extend(
                dict(event) for event in phase.get("drug_exposures", [])
            )
            procedures.extend(dict(event) for event in phase.get("procedures", []))
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
    resolver_type: str | None = None
    bundle_recipe_id: str | None = None


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
    registry: Any | None = None,
    intervention_catalog: InterventionCatalog | None = None,
    source_profile: str | None = None,
) -> GraphOutcome:
    if not graph_sample:
        raise ValueError("Graph sample is required to build a regimen outcome.")

    if intervention_catalog is None:
        context = get_active_simulation_context()
        if context is not None:
            intervention_catalog = getattr(context, "intervention_catalog", None)
    if intervention_catalog is None:
        if registry is not None:
            raise ValueError(
                "The legacy regimen registry is no longer supported; provide "
                "an InterventionCatalog from the active profile context."
            )
        if intervention_catalog is None:
            raise ValueError(
                "No intervention catalog was provided and no active profile context "
                "is available."
            )
    active_endpoints = extract_active_treatment_endpoints(graph_sample)
    if not active_endpoints:
        raise ValueError("No active treatment_episode endpoints found in graph sample.")

    undeclared = [
        endpoint
        for endpoint in active_endpoints
        if endpoint not in intervention_catalog.specs
    ]
    if undeclared:
        raise ValueError(
            f"Unmapped treatment endpoints in graph sample: {sorted(undeclared)}"
        )
    if len(set(active_endpoints)) != 1:
        raise ValueError(
            "Expected exactly one active treatment endpoint, "
            f"found {sorted(set(active_endpoints))}"
        )

    primary_category_id = active_endpoints[0]
    graph_endpoint = primary_category_id
    graph_alias_used = None
    spec = intervention_catalog.specs[primary_category_id]
    resolver_type = type(spec.resolution).__name__.lower()
    bundle_recipe_id = primary_category_id if isinstance(spec.resolution, Recipe) else None

    return GraphOutcome(
        primary_category_id=primary_category_id,
        graph_endpoint=graph_endpoint,
        graph_alias_used=graph_alias_used,
        source_profile=source_profile,
        active_endpoints=tuple(active_endpoints),
        modifiers=_extract_modifiers(graph_sample),
        raw_sample=graph_sample,
        resolver_type=resolver_type,
        bundle_recipe_id=bundle_recipe_id,
    )
