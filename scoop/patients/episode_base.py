from __future__ import annotations

from datetime import date, datetime
from typing import Any, ClassVar

from pydantic import BaseModel, Field, model_validator

from scoop.vocabulary.gateway import get_active_vocabulary_mappers

from ..export.omop import OmopExportBundle, as_datetime
from .ids import episode_id_seq
from .transforms import TransformableBase


def _source_value(source: Any, *names: str) -> Any:
    if source is None:
        return None

    for name in names:
        if isinstance(source, dict) and name in source and source[name] is not None:
            return source[name]

        value = getattr(source, name, None)
        if value is not None:
            return value

    return None


def _coerce_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def condition_start_date_from_source(condition: Any | None) -> date:
    if condition is not None:
        condition_start_date = _source_value(condition, "condition_start_date")
        if condition_start_date is not None:
            return condition_start_date
    return date.today()

class Episode_Event(BaseModel):
    """Episode-to-event link row for the OMOP episode_event table."""

    episode_id: int
    event_id: int
    episode_event_field_concept_id: int

    def to_omop_row(self):
        from omop_alchemy.cdm import model as omop_model

        return omop_model.Episode_Event(
            episode_id=self.episode_id,
            event_id=self.event_id,
            episode_event_field_concept_id=self.episode_event_field_concept_id,
        )


class Episode_Base(TransformableBase):
    """Shared OMOP episode fields and default-population logic."""

    _episode_mapper_key: ClassVar[str]
    _episode_label: ClassVar[str] = "Episode"
    _requires_object_concept_id: ClassVar[bool] = True

    person_id: int
    episode_id: int = Field(default_factory=episode_id_seq.next)
    episode_parent_id: int | None = None
    episode_start_date: date | None = None
    episode_start_datetime: datetime | None = None
    episode_end_date: date | None = None
    episode_end_datetime: datetime | None = None
    episode_number: int | None = None
    episode_source_value: str | None = None
    episode_concept_id: int | None = None
    episode_object_concept_id: int | None = None
    episode_type_concept_id: int | None = None
    episode_source_concept_id: int | None = None
    episode_events: list[Episode_Event] = Field(default_factory=list)
    episode_object: Any | None = Field(
        default=None,
        exclude=True,
        repr=False,
    )

    model_config = {
        "from_attributes": True,
    }

    __persisted_transforms__ = {
        "episode_concept_id": {
            "transform": lambda episode: episode._episode_spec().episode_concept_id,
            "when_missing": True,
        },
        "episode_type_concept_id": {
            "transform": lambda episode: episode._episode_spec().episode_type_concept_id,
            "when_missing": True,
        },
        "episode_number": {
            "transform": lambda episode: episode._episode_spec().episode_number,
            "when_missing": True,
        },
        "episode_source_value": {
            "transform": lambda episode: episode._episode_spec().episode_source_value,
            "when_missing": True,
        },
        "episode_parent_id": {
            "transform": lambda episode: episode._derive_episode_parent_id(),
            "when_missing": True,
        },
        "episode_start_date": {
            "transform": lambda episode: episode._derive_episode_start_date(),
            "when_missing": True,
        },
        "episode_start_datetime": {
            "transform": lambda episode: as_datetime(episode.episode_start_date),
            "when_missing": True,
        },
        "episode_end_datetime": {
            "transform": lambda episode: as_datetime(episode.episode_end_date),
            "when_missing": True,
        },
        "episode_object_concept_id": {
            "transform": lambda episode: episode._derive_episode_object_concept_id(),
            "when_missing": True,
        },
        "episode_source_concept_id": {
            "transform": lambda episode: episode._episode_spec().episode_source_concept_id,
            "when_missing": True,
        },
    }

    @classmethod
    def _episode_spec(cls):
        return get_active_vocabulary_mappers().episode_mapper(cls._episode_mapper_key)

    @classmethod
    def from_object(cls, source_object: Any, **kwargs: Any) -> "Episode_Base":
        from .condition_episode import Condition_Episode
        from .condition_occurrence import Condition_Occurrence

        if isinstance(source_object, Condition_Occurrence):
            return Condition_Episode(
                condition_occurrence=source_object,
                **kwargs,
            )

        if source_object.__class__.__name__ == "Procedure_Occurrence":
            raise NotImplementedError(
                "Procedure-backed episode dispatch is not implemented yet."
            )

        raise TypeError(
            "Episode_Base.from_object expected a Condition_Occurrence or "
            f"Procedure_Occurrence-like object, got {type(source_object).__name__}."
        )

    def _derive_episode_parent_id(self) -> int | None:
        return self.episode_parent_id

    def _derive_episode_start_date(self) -> date | None:
        return self.episode_start_date

    def _derive_episode_object_concept_id(self) -> int | None:
        return self.episode_object_concept_id

    def _derive_episode_events(self) -> list[Episode_Event]:
        return list(self.episode_events)

    def to_omop_row(self):
        from omop_alchemy.cdm import model as omop_model

        return omop_model.Episode(
            episode_id=self.episode_id,
            episode_parent_id=self.episode_parent_id,
            episode_start_date=self.episode_start_date,
            episode_start_datetime=self.episode_start_datetime,
            episode_end_date=self.episode_end_date,
            episode_end_datetime=self.episode_end_datetime,
            episode_number=self.episode_number,
            episode_source_value=self.episode_source_value,
            episode_concept_id=self.episode_concept_id,
            episode_object_concept_id=self.episode_object_concept_id,
            episode_type_concept_id=self.episode_type_concept_id,
            episode_source_concept_id=self.episode_source_concept_id,
            person_id=self.person_id,
        )

    def to_omop_bundle(self) -> OmopExportBundle:
        return OmopExportBundle(
            episodes=[self.to_omop_row()],
            episode_events=[event.to_omop_row() for event in self.episode_events],
        )

    @model_validator(mode="after")
    def _populate_episode_defaults(self):
        if self._requires_object_concept_id and self.episode_object_concept_id is None:
            raise ValueError(
                f"{self._episode_label} requires a resolved episode_object_concept_id."
            )

        if not self.episode_events:
            self.episode_events = self._derive_episode_events()

        return self
