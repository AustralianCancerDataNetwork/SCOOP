from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

from .systemic import Component
from .modality import Modality
from ..selection.choose import SelectedProtocol
from ..vocabulary.gateway import VocabularyGateway

if TYPE_CHECKING:
    pass


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
    """Explicit rollout configuration; execution is deferred to a later phase."""

    max_cycles: int | None = None
    within_component_continue_probability: float | None = None
    within_cycleset_continue_probability: float | None = None
    optional_cycle_probability: float | None = None


@dataclass(frozen=True)
class ProtocolSchedule:
    administrations: tuple[Administration, ...] = field(default_factory=tuple)
    selected_protocol: "SelectedProtocol | None" = None


def schedule(
    selected_protocol: "SelectedProtocol",
    start_date: date,
    seed: int,
    config: ScheduleConfig | None = None,
) -> ProtocolSchedule:
    """Declare the schedule boundary without rolling out any administrations."""

    raise NotImplementedError(
        "Protocol scheduling is intentionally deferred to Phase 7."
    )
