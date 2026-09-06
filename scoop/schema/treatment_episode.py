from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from pydantic import Field
from .transformable_object import TransformableBase

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
        """Return event payloads explicitly produced by treatment resolution.

        Event generation is deliberately data-driven.  The former implementation
        emitted hardcoded concept identifiers and dates, which made every
        treatment episode appear to have the same treatment.  Resolver and
        scheduler work can now populate these fields directly; until then an
        unresolved episode correctly emits no events.
        """

        drug_exposures = [dict(event) for event in self.drug_exposures]
        procedures = [dict(event) for event in self.procedures]

        # Permit bundle assemblers to attach event payloads to phase summaries
        # without requiring a second conversion step before rollout.
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
    source_profile: str | None = None,
) -> GraphOutcome:
    if not graph_sample:
        raise ValueError("Graph sample is required to build a regimen outcome.")

    active_endpoints = extract_active_treatment_endpoints(graph_sample)
    if not active_endpoints:
        raise ValueError("No active treatment_episode endpoints found in graph sample.")

    if len(set(active_endpoints)) != 1:
        raise ValueError(
            "Expected exactly one active treatment endpoint, "
            f"found {sorted(set(active_endpoints))}"
        )

    primary_category_id = active_endpoints[0]
    graph_endpoint = primary_category_id
    graph_alias_used = None
    registry_entry = None
    if registry is not None:
        registry_entry = getattr(registry, "entries", {}).get(primary_category_id)
        if registry_entry is None:
            raise ValueError(
                f"Graph endpoint '{primary_category_id}' is not declared in "
                f"protocol registry '{getattr(registry, 'label', 'unknown')}'."
            )

    resolver = getattr(registry_entry, "resolver", None)

    return GraphOutcome(
        primary_category_id=primary_category_id,
        graph_endpoint=graph_endpoint,
        graph_alias_used=graph_alias_used,
        source_profile=source_profile,
        active_endpoints=tuple(active_endpoints),
        modifiers=_extract_modifiers(graph_sample),
        raw_sample=graph_sample,
        resolver_type=getattr(resolver, "type", None),
        bundle_recipe_id=getattr(resolver, "group_id", None)
        if getattr(resolver, "type", None) == "bundle_recipe"
        else None,
    )
