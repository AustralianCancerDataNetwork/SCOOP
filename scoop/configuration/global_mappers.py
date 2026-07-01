from __future__ import annotations

from ..utils.concept_map import ConceptMapper
from ..utils.condition_map import ConditionMapper
from ..utils.episode_map import EpisodeMapper
from ..utils.field_mapper import MeasurementMapper, ObservationMapper
from ..utils.modifier_map import ModifierMapper
from .database import get_omop_engine
from .paths import (
    CONDITION_MAP_PATH,
    CONCEPT_MAP_PATH,
    EPISODE_MAP_PATH,
    MEASUREMENT_MAP_PATH,
    MODIFIER_MAP_PATH,
    OBSERVATION_MAP_PATH,
)

_CONCEPT_MAP_PATH = CONCEPT_MAP_PATH
_MEASUREMENT_MAP_PATH = MEASUREMENT_MAP_PATH
_OBSERVATION_MAP_PATH = OBSERVATION_MAP_PATH
_CONDITION_MAP_PATH = CONDITION_MAP_PATH
_MODIFIER_MAP_PATH = MODIFIER_MAP_PATH
_EPISODE_MAP_PATH = EPISODE_MAP_PATH

try:
    concept_mapper = ConceptMapper(_CONCEPT_MAP_PATH)
except FileNotFoundError as e:
    raise RuntimeError(f"Concept map file not found: {_CONCEPT_MAP_PATH}") from e

try:
    modifier_mapper = ModifierMapper(
        _MODIFIER_MAP_PATH,
        engine=get_omop_engine(),
    )
except FileNotFoundError as e:
    raise RuntimeError(f"Modifier map file not found: {_MODIFIER_MAP_PATH}") from e

try:
    episode_mapper = EpisodeMapper(_EPISODE_MAP_PATH)
except FileNotFoundError as e:
    raise RuntimeError(f"Episode map file not found: {_EPISODE_MAP_PATH}") from e

try:
    measurement_mapper = MeasurementMapper(
        _MEASUREMENT_MAP_PATH,
        concept_mapper,
        modifier_mapper,
    )
except FileNotFoundError as e:
    raise RuntimeError(f"Measurement map file not found: {_MEASUREMENT_MAP_PATH}") from e

try:
    observation_mapper = ObservationMapper(_OBSERVATION_MAP_PATH, concept_mapper)
except FileNotFoundError as e:
    raise RuntimeError(f"Observation map file not found: {_OBSERVATION_MAP_PATH}") from e

try:
    condition_mapper = ConditionMapper(
        _CONDITION_MAP_PATH,
        engine=get_omop_engine(),
    )
except FileNotFoundError as e:
    raise RuntimeError(f"Condition map file not found: {_CONDITION_MAP_PATH}") from e


__all__ = [
    "concept_mapper",
    "measurement_mapper",
    "observation_mapper",
    "condition_mapper",
    "modifier_mapper",
    "episode_mapper",
]
