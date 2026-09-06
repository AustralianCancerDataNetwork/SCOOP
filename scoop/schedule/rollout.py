from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from datetime import datetime
from typing import Any

from .systemic import Component
from .modality import Modality
from ..selection.choose import SelectedProtocol

@dataclass(frozen=True)
class Administration:
    """Common identity for one fixed delivery record."""

    date: date
    component: Component
    phase_id: str
    source_sig_id: int | None = None


@dataclass(frozen=True)
class SystemicAdministration(Administration):
    dose: float | None = None
    dose_unit: str | None = None
    dose_unit_cui: int | None = None
    route: str | None = None
    cycle_number: int = 1


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
    selected_protocol: "SelectedProtocol | None" = None
    skipped_phases: tuple[str, ...] = field(default_factory=tuple)


def schedule(
    selected_protocol: "SelectedProtocol",
    start_date: date,
    seed: int,
    config: ScheduleConfig | None = None,
) -> ProtocolSchedule:
    """Roll selected HemOnc signatures into typed administration records.

    The current selected-protocol contract carries phase signatures but not
    recipe timing. Consequently each selected phase is scheduled from the
    supplied start date; cross-phase sequencing remains an explicit follow-up
    once timing is represented on ``SelectedProtocol``.
    """

    if not isinstance(selected_protocol, SelectedProtocol):
        raise TypeError("schedule() requires a SelectedProtocol")
    if not isinstance(start_date, (date, datetime)):
        raise TypeError("start_date must be a date or datetime")
    config = config or ScheduleConfig()

    administrations: list[Administration] = []
    skipped_phases: list[str] = []
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

        phase_schedule = _phase_schedule(sigs, seed=seed + phase_index)
        deliveries = phase_schedule.get_scheduled_dates(
            start_date,
            max_cycles=config.max_cycles,
            within_component_continue_probability=(
                config.within_component_continue_probability
            ),
            within_cycleset_continue_probability=(
                config.within_cycleset_continue_probability
            ),
            optional_cycle_probability=config.optional_cycle_probability,
        )
        for delivered_at, component_parts in deliveries.items():
            for component_part in component_parts:
                administrations.append(
                    _administration_from_part(
                        delivered_at,
                        component_part,
                        phase_id=phase_id,
                    )
                )

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
    )


def _is_schedulable_signature(signature: Any) -> bool:
    required = (
        "timing_sequence",
        "alldays",
        "cycle_length_lb",
        "cycle_length_ub",
        "cycle_length_unit",
        "cyclesigs",
        "step_number",
        "component",
        "component_cui",
    )
    return all(hasattr(signature, field) for field in required)


def _phase_schedule(signatures: list[Any], *, seed: int):
    from .systemic import PhaseSchedule

    return PhaseSchedule.from_component_sigs(signatures, seed=seed)


def _administration_from_part(delivered_at: Any, component_part: Any, *, phase_id: str):
    delivered_date = (
        delivered_at.date()
        if isinstance(delivered_at, datetime)
        else delivered_at.to_pydatetime().date()
        if hasattr(delivered_at, "to_pydatetime")
        else delivered_at
    )
    dose, dose_unit = _dose_details(component_part)
    component = component_part.component
    common = {
        "date": delivered_date,
        "component": component,
        "phase_id": phase_id,
        "source_sig_id": component_part.source_sig_id,
    }
    if _is_radiation(component_part):
        return RadiationAdministration(
            **common,
            fraction_number=None,
            fraction_dose=dose,
            dose_unit=dose_unit,
            technique=None,
        )
    return SystemicAdministration(
        **common,
        dose=dose,
        dose_unit=dose_unit,
        dose_unit_cui=(
            component_part.dose_info.doseunit_cui
            if component_part.dose_info is not None
            else None
        ),
        route=component_part.route,
        cycle_number=1,
    )


def _dose_details(component_part: Any) -> tuple[float | None, str | None]:
    if component_part.duration is not None:
        return component_part.duration.get_duration(), component_part.duration.durationunit
    if component_part.dose_info is not None:
        return component_part.dose_info.get_dose(), component_part.dose_info.doseunit
    return None, None


def _is_radiation(component_part: Any) -> bool:
    text = " ".join(
        str(value or "")
        for value in (
            component_part.component.name,
            component_part.role,
            component_part.class_field,
        )
    ).lower()
    return "radiotherap" in text or "radiation" in text
