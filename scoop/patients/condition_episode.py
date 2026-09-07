from __future__ import annotations
from typing import Any
from pydantic import Field, model_validator

from .episode_base import Episode_Base, Episode_Event, condition_start_date_from_source

class Condition_Episode(Episode_Base):
    """First-pass OMOP Episode wrapper for an initial condition presentation."""

    _episode_mapper_key = "overarching_condition_episode"
    _episode_label = "Condition_Episode"

    condition_occurrence_id: int = Field(exclude=True)
    condition_occurrence: Any | None = Field(
        default=None,
        exclude=True,
        repr=False,
    )

    @model_validator(mode="before")
    @classmethod
    def _attach_condition_context(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        condition = normalized.get("condition_occurrence")
        if condition is None:
            return normalized

        person_id = getattr(condition, "person_id", None)
        if person_id is not None:
            normalized.setdefault("person_id", person_id)

        condition_occurrence_id = getattr(condition, "condition_occurrence_id", None)
        if condition_occurrence_id is not None:
            normalized.setdefault("condition_occurrence_id", condition_occurrence_id)

        return normalized

    def _derive_episode_start_date(self):
        return condition_start_date_from_source(self.condition_occurrence)

    def _derive_episode_object_concept_id(self) -> int | None:
        condition = self.condition_occurrence
        if condition is None:
            return None
        if condition.condition_concept_id is not None:
            return condition.condition_concept_id
        return condition.condition_source_concept_id

    def _derive_episode_events(self) -> list[Episode_Event]:
        spec = self._episode_spec()
        event_field_concept_id = spec.event_field_concept_id("condition_occurrence")
        if event_field_concept_id is None:
            raise ValueError(
                "The overarching_condition_episode mapper must declare an "
                "episode_event_field_concept_id for condition_occurrence."
            )
        return [
            Episode_Event(
                episode_id=self.episode_id,
                event_id=self.condition_occurrence_id,
                episode_event_field_concept_id=event_field_concept_id,
            )
        ]
