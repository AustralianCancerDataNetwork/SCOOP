from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class EpisodeSpec:
    episode_concept_id: int
    episode_type_concept_id: int
    episode_number: int | None = None
    episode_source_value: str | None = None
    episode_event_field_concept_id: int | None = None


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
                episode_event_field_concept_id=(
                    int(entry["episode_event_field_concept_id"])
                    if entry.get("episode_event_field_concept_id") is not None
                    else None
                ),
            )

    def __call__(self, name: str) -> EpisodeSpec:
        try:
            return self.maps[name]
        except KeyError as exc:
            raise KeyError(f"No episode map found for '{name}'.") from exc

    def __repr__(self) -> str:
        return f"<EpisodeMapper domains={list(self.maps)}>"
