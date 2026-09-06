from __future__ import annotations

from dataclasses import dataclass
import random
from collections.abc import Mapping

from ..protocols.candidates import ProtocolCandidate, RegimenCandidate, _sort_candidates


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
    """Select one protocol and one regimen per phase reproducibly."""

    if not isinstance(context, PatientContext):
        raise TypeError("select() requires a PatientContext")
    if not candidates:
        return NoSelection("No protocol candidates were supplied")

    eligible: list[ProtocolCandidate] = []
    rejected: list[str] = []
    for candidate in candidates:
        required = tuple(candidate.selection_requires)
        missing = sorted(field for field in required if field not in context.values)
        if missing:
            rejected.append(
                f"{candidate.name}: missing declared selection input(s) {missing}"
            )
            continue
        if not candidate.phases:
            rejected.append(f"{candidate.name}: no phases were declared")
            continue
        if any(not phase.options for phase in candidate.phases):
            rejected.append(f"{candidate.name}: one or more phases have no candidates")
            continue
        eligible.append(candidate)

    if not eligible:
        reason = "No protocol candidate satisfies the declared selection context"
        if rejected:
            reason += ": " + "; ".join(rejected)
        return NoSelection(reason)

    rng = random.Random(seed)
    # Protocol and option order can originate in database row order or YAML
    # mapping order. Canonicalize both before using the seeded RNG so the same
    # seed means the same clinical alternative across equivalent inputs.
    eligible = sorted(eligible, key=lambda candidate: candidate.name)
    protocol = rng.choice(eligible)
    selected = tuple(
        SelectedRegimen(
            phase_id=phase.label or f"phase_{index}",
            regimen=rng.choice(_sort_candidates(phase.options)),
        )
        for index, phase in enumerate(protocol.phases, start=1)
    )
    return SelectedProtocol(protocol_id=protocol.name, phases=selected)
