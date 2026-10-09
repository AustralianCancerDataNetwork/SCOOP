from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from hemonc_alchemy.toolkit.analytics.treatment.classification import (
    RAD_SIG_CLASS_VALUE,
    sig_class_value,
)
from hemonc_alchemy.toolkit.analytics.treatment.scheduling import (
    TimedEvent,
    anchor_blocks,
    group_into_blocks,
    schedule_events,
)
from hemonc_alchemy.toolkit.analytics.treatment.scheduling.tokens import Day

from ..selection.choose import SelectedProtocol
from ..selection.interventions import PhaseTiming
from .sampling import sample_phase
from .systemic import BaseRandomSampler, Component

logger = logging.getLogger(__name__)
_CHOICE_STREAM_SALT = 0x5EEDC01C


class UnresolvedScheduleError(ValueError):
    """A selected source instruction could not be assigned a calendar date."""

    def __init__(self, *, sig_id: int | None, phase_id: str, reason: str) -> None:
        self.sig_id = sig_id
        self.phase_id = phase_id
        self.reason = reason
        super().__init__(
            f"Unresolved schedule for sig {sig_id!r} in phase {phase_id!r}: {reason}"
        )


@dataclass(frozen=True)
class Administration:
    """One dated source-based delivery."""

    date: date
    component: Component
    phase_id: str
    source_sig_id: int | None = None
    timing_status: str = field(default="resolved", kw_only=True)
    elapsed_day: int | None = field(default=None, kw_only=True)


@dataclass(frozen=True)
class SystemicAdministration(Administration):
    dose: float | None = None
    dose_unit: str | None = None
    dose_unit_cui: int | None = None
    route: str | None = None
    route_group: str | None = field(default=None, kw_only=True)
    cycle_number: int | None = 1


@dataclass(frozen=True)
class RadiationAdministration(Administration):
    fraction_number: int | None = None
    fraction_dose: float | None = None
    dose_unit: str | None = None
    technique: str | None = None


@dataclass(frozen=True)
class ScheduleConfig:
    """Explicit rollout configuration for reproducible schedule generation."""

    max_cycles: int | None = None
    within_component_continue_probability: float | None = None
    within_cycleset_continue_probability: float | None = None
    optional_cycle_probability: float | None = None

    def __post_init__(self) -> None:
        if self.max_cycles is not None and self.max_cycles < 1:
            raise ValueError("max_cycles must be positive when provided")
        for name in (
            "within_component_continue_probability",
            "within_cycleset_continue_probability",
            "optional_cycle_probability",
        ):
            value = getattr(self, name)
            if value is not None and not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")


@dataclass(frozen=True)
class ProtocolSchedule:
    administrations: tuple[Administration, ...] = field(default_factory=tuple)
    selected_protocol: SelectedProtocol | None = None
    skipped_phases: tuple[str, ...] = field(default_factory=tuple)
    phase_start_dates: dict[str, date] = field(default_factory=dict)


def schedule(
    selected_protocol: SelectedProtocol,
    start_date: date,
    seed: int,
    config: ScheduleConfig | None = None,
) -> ProtocolSchedule:
    """Roll selected HemOnc signatures into typed administration records.

    HemOnc owns each phase's cycle dates. SCOOP owns patient sampling and
    recipe sequencing (including the existing last-delivery-plus-one rule).
    """

    if not isinstance(selected_protocol, SelectedProtocol):
        raise TypeError("schedule() requires a SelectedProtocol")
    if not isinstance(start_date, (date, datetime)):
        raise TypeError("start_date must be a date or datetime")
    config = config or ScheduleConfig()

    administrations: list[Administration] = []
    skipped_phases: list[str] = []
    phase_start_dates: dict[str, date] = {}
    has_timing = any(
        selected.timing is not None for selected in selected_protocol.phases
    )
    cursor_date = start_date.date() if isinstance(start_date, datetime) else start_date
    concurrent_anchor = cursor_date
    for phase_index, selected in enumerate(selected_protocol.phases, start=1):
        sigs = list(selected.regimen.sigs)
        phase_id = selected.phase_id
        if not sigs:
            skipped_phases.append(phase_id)
            continue
        if not all(_is_schedulable_signature(sig) for sig in sigs):
            raise ValueError(
                f"Selected phase {phase_id!r} contains signatures without "
                "the released scheduling fields"
            )

        phase_start = cursor_date
        if has_timing and selected.timing is PhaseTiming.CONCURRENT:
            phase_start = concurrent_anchor
        phase_start_dates[phase_id] = phase_start
        deliveries = _phase_schedule(
            sigs,
            phase_start=phase_start,
            seed=seed + phase_index,
            config=config,
        )
        dose_sampler = BaseRandomSampler(seed=seed + phase_index)
        phase_administrations = [
            _administration_from_event(event, phase_id=phase_id, sampler=dose_sampler)
            for event in deliveries
        ]
        if has_timing and deliveries and selected.timing is not PhaseTiming.CONCURRENT:
            cursor_date = max(
                item.date for item in phase_administrations
            ) + timedelta(days=1)
            concurrent_anchor = cursor_date
        administrations.extend(phase_administrations)

    administrations.sort(
        key=lambda administration: (
            administration.date,
            administration.phase_id,
            administration.component.name,
            administration.source_sig_id or -1,
        )
    )
    return ProtocolSchedule(
        administrations=tuple(administrations),
        selected_protocol=selected_protocol,
        skipped_phases=tuple(skipped_phases),
        phase_start_dates=phase_start_dates,
    )


def _is_schedulable_signature(signature: Any) -> bool:
    required = (
        "timing_sequence",
        "alldays",
        "cycle_length_lb",
        "cycle_length_ub",
        "cycle_length_unit",
        "step_number",
        "component",
        "component_cui",
        "drug_object",
        "phase",
        "phase_step",
        "portion",
        "route",
        "frequency",
        "doseminnum",
        "dosemaxnum",
        "doseunit",
    )
    return all(hasattr(signature, field) for field in required)


def _phase_schedule(
    signatures: list[Any],
    *,
    phase_start: date,
    seed: int,
    config: ScheduleConfig,
) -> list[TimedEvent]:
    events = schedule_events(SimpleNamespace(component_sigs=signatures))
    choice_rng = random.Random(seed ^ _CHOICE_STREAM_SALT)
    events = [
        replace(
            event,
            days=tuple(dict.fromkeys((
                *event.days,
                *(Day(choice_rng.choice(choice.options)) for choice in event.choices),
            ))),
            choices=(),
        )
        for event in events
    ]
    anchored = anchor_blocks(group_into_blocks(events))
    # Decay is an analytical intensity tail, never another administration.
    return sample_phase(anchored, phase_start=phase_start, seed=seed, config=config)


def _administration_from_event(
    event: TimedEvent,
    *,
    phase_id: str,
    sampler: BaseRandomSampler,
) -> Administration:
    if event.calendar_date is None or event.timing_status.startswith("unresolved:"):
        reason = (
            event.timing_status.removeprefix("unresolved:").strip()
            if event.timing_status.startswith("unresolved:")
            else "no calendar date was produced"
        )
        raise UnresolvedScheduleError(
            sig_id=getattr(event.schedule_event.sig, "id", None),
            phase_id=phase_id,
            reason=reason or "timing could not be resolved",
        )
    source = event.schedule_event
    sig = source.sig
    common = {
        "date": event.calendar_date,
        "elapsed_day": event.elapsed_day,
        "timing_status": event.timing_status,
        "component": Component.from_sig(sig),
        "phase_id": phase_id,
        "source_sig_id": getattr(sig, "id", None),
    }
    dose = None
    if source.dose_min is not None and source.dose_max is not None:
        try:
            lower, upper = float(source.dose_min), float(source.dose_max)
        except (TypeError, ValueError):
            logger.warning(
                "Nonnumeric dose bounds for sig %s; retaining a null dose",
                getattr(sig, "id", None),
            )
        else:
            dose = sampler._get_value_in_range(lower, upper)
    if sig_class_value(sig) == RAD_SIG_CLASS_VALUE:
        return RadiationAdministration(
            **common,
            fraction_dose=dose,
            dose_unit=source.dose_unit,
        )
    return SystemicAdministration(
        **common,
        dose=dose,
        dose_unit=source.dose_unit,
        dose_unit_cui=getattr(sig, "doseunit_cui", None),
        route=getattr(sig.route, "value", sig.route),
        route_group=source.route_group,
        cycle_number=event.cycle_number,
    )
