from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..protocols.candidates import ProtocolCandidate, RegimenCandidate


@dataclass(frozen=True)
class PatientContext:
    """Explicit, inspectable context available to bridge and selection stages."""

    values: Mapping[str, object]


@dataclass(frozen=True)
class SelectedRegimen:
    phase_id: str
    regimen: RegimenCandidate


@dataclass(frozen=True)
class SelectedProtocol:
    protocol_id: str
    phases: tuple[SelectedRegimen, ...]


@dataclass(frozen=True)
class NoSelection:
    reason: str


def select(
    candidates: list[ProtocolCandidate],
    context: PatientContext,
    seed: int,
) -> SelectedProtocol | NoSelection:
    """Declare the selection boundary without choosing a regimen yet."""

    raise NotImplementedError(
        "Candidate selection is intentionally deferred to Phase 5."
    )
