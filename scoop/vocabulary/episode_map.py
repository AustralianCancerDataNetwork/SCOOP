from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml


@dataclass(frozen=True)
class EpisodeSpec:
    episode_concept_id: int
    episode_type_concept_id: int
    episode_number: int | None = None
    episode_source_value: str | None = None
    episode_source_concept_id: int | None = None
    episode_event_field_concept_id: int | None = None
    episode_event_field_concept_ids: Mapping[str, int] = field(
        default_factory=dict
    )

    def event_field_concept_id(self, event_table: str | None = None) -> int | None:
        """Return the CDM Field concept for one event table.

        A treatment episode links to drug exposures *and* procedures, so the
        caller says which table the event_id belongs to. Falls back to the
        scalar default when the table is not declared.
        """

        if event_table is not None:
            declared = self.episode_event_field_concept_ids.get(event_table)
            if declared is not None:
                return declared
        return self.episode_event_field_concept_id


class EpisodeMapper:
    """Container for static episode defaults used by SCOOP schema objects."""

    def __init__(self, mapping: str | Path | dict[str, Any]):
        if isinstance(mapping, (str, Path)):
            with open(mapping, "r") as handle:
                mapping = yaml.safe_load(handle)

        if not isinstance(mapping, dict):
            raise ValueError("Episode mapping must be a dict or path to valid YAML.")

        self.maps: dict[str, EpisodeSpec] = {}
        for name, entry in mapping.items():
            if not isinstance(name, str) or not isinstance(entry, dict):
                raise ValueError("Episode mapping entries must be keyed by strings.")

            self.maps[name] = EpisodeSpec(
                episode_concept_id=int(entry["episode_concept_id"]),
                episode_type_concept_id=int(entry["episode_type_concept_id"]),
                episode_number=(
                    int(entry["episode_number"])
                    if entry.get("episode_number") is not None
                    else None
                ),
                episode_source_value=(
                    str(entry["episode_source_value"])
                    if entry.get("episode_source_value") is not None
                    else None
                ),
                episode_source_concept_id=(
                    int(entry["episode_source_concept_id"])
                    if entry.get("episode_source_concept_id") is not None
                    else None
                ),
                episode_event_field_concept_id=(
                    int(entry["episode_event_field_concept_id"])
                    if entry.get("episode_event_field_concept_id") is not None
                    else None
                ),
                episode_event_field_concept_ids=MappingProxyType(
                    {
                        str(table): int(concept_id)
                        for table, concept_id in dict(
                            entry.get("episode_event_field_concept_ids") or {}
                        ).items()
                    }
                ),
            )

    def __call__(self, name: str) -> EpisodeSpec:
        try:
            return self.maps[name]
        except KeyError as exc:
            raise KeyError(f"No episode map found for '{name}'.") from exc

    def __repr__(self) -> str:
        return f"<EpisodeMapper domains={list(self.maps)}>"
