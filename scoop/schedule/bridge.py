"""Turns an assigned regimen into a dated administration schedule.

Chains `assemble()` -> `select()` -> `schedule()`. Lives here rather than in
`patients/` because `schedule/` already depends on `selection/`, which
already depends on `protocols/` -- this just extends that chain by one link
instead of reversing it.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from ..protocols.candidates import assemble
from ..protocols.hemonc.gateway import HemOncGateway
from ..protocols.spec import ProtocolSpec, RegistrySpec
from ..selection.choose import NoSelection, PatientContext, select
from .rollout import ProtocolSchedule, ScheduleConfig, schedule

if TYPE_CHECKING:
    from ..patients.treatment_episode import Treatment_Episode


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
) -> ProtocolSchedule | None:
    """Turn an assigned regimen into dated administrations.

    Returns `None` -- not an error -- when there's nothing to schedule
    (a non-HemOnc intervention like surgery) or no HemOnc coverage for any
    candidate. Check `treatment_episode.registry_resolver_type` yourself if
    you need to tell those two cases apart.
    """

    schedule_config = config or ScheduleConfig()
    for spec in _protocol_specs_to_try(treatment_episode, registry):
        candidates = assemble(spec, gateway)
        context = PatientContext(values=treatment_episode.decision_context)
        selected = select(candidates, context, seed=seed)
        if isinstance(selected, NoSelection):
            continue
        return schedule(selected, start_date, seed, schedule_config)
    return None
