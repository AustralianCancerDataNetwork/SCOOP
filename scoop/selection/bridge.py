from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..guidelines.sampling import TreatmentDecision
from .choose import PatientContext
from .interventions import (
    Choice,
    InterventionCatalog,
    InterventionSpec,
    NonHemOnc,
    PhaseRef,
    ProtocolRef,
    Recipe,
    ResolvedIntervention,
)


@runtime_checkable
class InterventionBridge(Protocol):
    """Pure declaration boundary for decision-to-intervention resolution."""

    def resolve(
        self,
        decision: TreatmentDecision,
        context: PatientContext,
    ) -> ResolvedIntervention:
        """Resolve a decision using only its declared intervention context."""


class DeclarativeInterventionBridge:
    """Resolve a validated intervention catalog without external lookups."""

    def __init__(self, catalog: InterventionCatalog):
        self.catalog = catalog

    def resolve(
        self,
        decision: TreatmentDecision,
        context: PatientContext,
    ) -> ResolvedIntervention:
        if not isinstance(decision, TreatmentDecision):
            raise TypeError("resolve() requires a TreatmentDecision")
        if not isinstance(context, PatientContext):
            raise TypeError("resolve() requires a PatientContext")

        spec = self.catalog.specs.get(decision.decision_id)
        if spec is None and decision.graph_endpoint != decision.decision_id:
            spec = self.catalog.specs.get(decision.graph_endpoint)
        if spec is None:
            raise ValueError(
                f"No intervention declaration for decision {decision.decision_id!r} "
                f"in profile {decision.source_profile!r}"
            )

        missing = [field for field in spec.requires if field not in context.values]
        if missing:
            raise ValueError(
                f"Intervention {spec.intervention_id!r} requires undeclared context "
                f"value(s): {sorted(missing)}"
            )
        return self._resolve_spec(spec, depth=0)

    def _resolve_spec(
        self,
        spec: InterventionSpec,
        *,
        depth: int,
    ) -> ResolvedIntervention:
        if depth > self.catalog.max_choice_depth:
            raise ValueError(
                f"Intervention choice depth exceeds the configured bound "
                f"({self.catalog.max_choice_depth})"
            )

        resolution = spec.resolution
        if isinstance(resolution, PhaseRef):
            return ResolvedIntervention(
                intervention_id=spec.intervention_id,
                phase_refs=(resolution,),
                technique=resolution.technique,
            )
        if isinstance(resolution, ProtocolRef):
            protocol = self.catalog.protocols.get(resolution.protocol_id)
            if protocol is None:
                raise ValueError(
                    f"Intervention {spec.intervention_id!r} references unknown "
                    f"protocol {resolution.protocol_id!r}"
                )
            return ResolvedIntervention(
                intervention_id=spec.intervention_id,
                protocols=(protocol,),
            )
        if isinstance(resolution, Choice):
            alternatives = tuple(
                self._resolve_spec(self.catalog.specs[member], depth=depth + 1)
                for member in resolution.intervention_ids
            )
            return ResolvedIntervention(
                intervention_id=spec.intervention_id,
                alternatives=alternatives,
            )
        if isinstance(resolution, Recipe):
            return ResolvedIntervention(
                intervention_id=spec.intervention_id,
                recipe=resolution,
            )
        if isinstance(resolution, NonHemOnc):
            return ResolvedIntervention(
                intervention_id=spec.intervention_id,
                non_hemonc=resolution,
            )
        raise TypeError(
            f"Unsupported intervention resolution for {spec.intervention_id!r}"
        )


def resolve(
    bridge: InterventionBridge,
    decision: TreatmentDecision,
    context: PatientContext,
) -> ResolvedIntervention:
    """Resolve through an explicitly supplied pure intervention bridge."""

    if bridge is None:
        raise TypeError("resolve() requires an InterventionBridge")
    return bridge.resolve(decision, context)
