"""Turns an assigned regimen into a dated administration schedule.

Chains `assemble()` -> `select()` -> `schedule()`. Lives here rather than in
`patients/` because `schedule/` already depends on `selection/`, which
already depends on `protocols/` -- this just extends that chain by one link
instead of reversing it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import logging
from typing import TYPE_CHECKING, Literal

from ..protocols.candidates import assemble
from ..protocols.hemonc.gateway import HemOncGateway
from ..protocols.spec import ProtocolSpec, RegistrySpec
from ..selection.choose import NoSelection, PatientContext, select
from .rollout import ProtocolSchedule, ScheduleConfig, schedule

if TYPE_CHECKING:
    from ..patients.treatment_episode import Treatment_Episode


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScheduleResolution:
    schedule: ProtocolSchedule | None
    status: Literal["scheduled", "non_hemonc", "no_selection", "no_candidates"]
    reason: str | None = None


def _single_phase_spec(phase_id: str, registry: RegistrySpec) -> ProtocolSpec | None:
    """Wrap a bare phase reference as a one-phase ProtocolSpec.

    The registry only declares multi-phase protocols; a plain phase
    reference has no protocol entry of its own to look up.
    """

    if phase_id not in registry.source_phases:
        return None
    return ProtocolSpec.generate(
        phase_id,
        {"protocol_id": phase_id, "source_phase_ids": [phase_id]},
        source_phases=registry.source_phases,
    )


def _protocol_specs_to_try(
    treatment_episode: "Treatment_Episode",
    registry: RegistrySpec,
) -> list[ProtocolSpec]:
    """Candidate ProtocolSpecs to try assembling, in priority order.

    `bundle_recipe_id` is only a real registry protocol id for a plain
    protocol reference; everything else (a single phase, a Choice's
    alternatives, a Recipe's steps) lives in `bundle_phase_summary` instead,
    tried in order until one has HemOnc coverage. A multi-step Recipe is not
    combined into one merged schedule -- each step is tried on its own and
    only the first that resolves gets scheduled.
    """

    if treatment_episode.bundle_recipe_id and not treatment_episode.bundle_phase_summary:
        protocol_id = treatment_episode.bundle_recipe_id
        spec = registry.protocols.get(protocol_id)
        return [spec] if spec is not None else []

    specs: list[ProtocolSpec] = []
    for step in treatment_episode.bundle_phase_summary:
        protocol_id = step.get("protocol_id")
        phase_id = step.get("phase_id")
        spec = None
        if protocol_id:
            spec = registry.protocols.get(protocol_id)
        elif phase_id:
            spec = _single_phase_spec(phase_id, registry)
        if spec is not None:
            specs.append(spec)
    return specs


def resolve_schedule_for_treatment(
    treatment_episode: "Treatment_Episode",
    registry: RegistrySpec,
    gateway: HemOncGateway,
    *,
    start_date: date,
    seed: int,
    config: ScheduleConfig | None = None,
) -> ScheduleResolution:
    """Turn an assigned regimen into dated administrations.

    The status distinguishes intentional non-HemOnc treatment from coverage gaps.
    """

    schedule_config = config or ScheduleConfig()
    specs = _protocol_specs_to_try(treatment_episode, registry)
    if not specs:
        status = (
            "non_hemonc"
            if treatment_episode.registry_resolver_type in {"nonhemonc", "non_hemonc"}
            else "no_candidates"
        )
        reason = (
            None
            if status == "non_hemonc"
            else "No registry protocol matched the treatment"
        )
        return ScheduleResolution(None, status, reason)

    had_candidates = False
    reasons: list[str] = []
    for spec in specs:
        candidates = assemble(spec, gateway)
        had_candidates |= any(
            phase.options
            for candidate in candidates
            for phase in candidate.phases
        )
        context = PatientContext(values=treatment_episode.decision_context)
        selected = select(candidates, context, seed=seed)
        if isinstance(selected, NoSelection):
            logger.info(
                "No schedule selected for %s: %s",
                spec.protocol_id,
                selected.reason,
            )
            reasons.append(selected.reason)
            continue
        return ScheduleResolution(
            schedule(selected, start_date, seed, schedule_config), "scheduled"
        )
    if not had_candidates:
        return ScheduleResolution(
            None,
            "no_candidates",
            "No HemOnc candidates matched the registry phases",
        )
    reason = "; ".join(dict.fromkeys(reasons)) or "No candidate passed selection"
    return ScheduleResolution(None, "no_selection", reason)
