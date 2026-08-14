from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..guidelines.sampling import TreatmentDecision
from .choose import PatientContext
from .interventions import InterventionSpec, ResolvedIntervention


@runtime_checkable
class InterventionBridge(Protocol):
    """Pure declaration boundary for decision-to-intervention resolution."""

    def resolve(
        self,
        decision: TreatmentDecision,
        context: PatientContext,
    ) -> ResolvedIntervention:
        """Resolve a decision using only its declared intervention context."""


def resolve(
    bridge: InterventionBridge,
    decision: TreatmentDecision,
    context: PatientContext,
) -> ResolvedIntervention:
    """Declare the bridge entry point without invoking an implicit legacy cascade."""

    raise NotImplementedError(
        "Intervention resolution is intentionally deferred to Phase 5."
    )
