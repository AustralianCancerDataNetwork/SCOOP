from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any


def as_datetime(value: date | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, time.min)


@dataclass
class OmopExportBundle:
    person: Any | None = None
    condition_occurrences: list[Any] = field(default_factory=list)
    episodes: list[Any] = field(default_factory=list)
    episode_events: list[Any] = field(default_factory=list)
    measurements: list[Any] = field(default_factory=list)
    observations: list[Any] = field(default_factory=list)

    def merge(self, other: "OmopExportBundle") -> "OmopExportBundle":
        if self.person is None:
            self.person = other.person
        self.condition_occurrences.extend(other.condition_occurrences)
        self.episodes.extend(other.episodes)
        self.episode_events.extend(other.episode_events)
        self.measurements.extend(other.measurements)
        self.observations.extend(other.observations)
        return self
