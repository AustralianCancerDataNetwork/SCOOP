from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Mapping, Protocol, Sequence, runtime_checkable

from ..spec import PhaseSpec

if TYPE_CHECKING:
    from ..candidates import RegimenCandidate


@runtime_checkable
class HemOncGateway(Protocol):
    """Injectable boundary for HemOnc-backed candidate retrieval."""

    def candidates_for_phase(
        self,
        phase_spec: PhaseSpec,
    ) -> Sequence["RegimenCandidate"]:
        """Return candidates for a declared phase without prescribing query mechanics."""


@dataclass
class FakeHemOncGateway:
    """In-memory gateway for isolated contract and later candidate tests."""

    candidates: Mapping[str, Sequence["RegimenCandidate"]] = field(default_factory=dict)

    def candidates_for_phase(self, phase_spec: PhaseSpec) -> list["RegimenCandidate"]:
        return list(self.candidates.get(phase_spec.source_phase_id, ()))
