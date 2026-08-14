"""Vocabulary gateway contracts and lazy mapper construction."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
import random
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from .database import get_omop_engine
from ..profiles.paths import (
    CONDITION_MAP_FILENAME,
    CONCEPT_MAP_FILENAME,
    EPISODE_MAP_FILENAME,
    MEASUREMENT_MAP_FILENAME,
    MODIFIER_MAP_FILENAME,
    OBSERVATION_MAP_FILENAME,
    CONFIG_ROOT,
    MAPPERS_DIRNAME,
)

if TYPE_CHECKING:
    from .condition_map import ConditionMapper
    from .concept_map import ConceptMapper
    from .episode_map import EpisodeMapper
    from .fields import MeasurementMapper, ObservationMapper
    from .modifier_map import ModifierMapper


@dataclass(frozen=True)
class VocabularyMappers:
    """All mapper families materialized for one vocabulary configuration."""

    concept_mapper: ConceptMapper
    condition_mapper: ConditionMapper
    modifier_mapper: ModifierMapper
    episode_mapper: EpisodeMapper
    measurement_mapper: MeasurementMapper
    observation_mapper: ObservationMapper


@runtime_checkable
class VocabularyGateway(Protocol):
    """Injectable boundary for vocabulary and concept resolution."""

    def resolve_condition_names(self, names: Sequence[str]) -> Sequence[int]:
        """Resolve declared condition names when a caller explicitly requests it."""

    def resolve_condition_cuis(self, cuis: Sequence[int]) -> Sequence[int]:
        """Resolve or validate declared condition CUIs."""

    def resolve_concept(self, domain: str, value: str) -> int:
        """Resolve one named concept in a declared domain."""

    def sample_modifier(self, label: str, seed: int) -> str:
        """Sample a modifier with an explicit seed."""

    def get_mappers(
        self,
        *,
        concept_path: Path | None = None,
        condition_path: Path | None = None,
        modifier_path: Path | None = None,
        episode_path: Path | None = None,
        measurement_path: Path | None = None,
        observation_path: Path | None = None,
    ) -> VocabularyMappers:
        """Return lazily materialized mapper families for the requested paths."""


class DefaultVocabularyGateway:
    """File-backed gateway with lazy, per-configuration mapper caches.

    Constructing the gateway is side-effect free. YAML files and the OMOP engine are
    touched only when ``get_mappers`` is called.
    """

    def __init__(
        self,
        base_dir: Path | None = None,
        *,
        engine_factory: Callable[[], Any] = get_omop_engine,
    ) -> None:
        self.base_dir = Path(base_dir) if base_dir is not None else CONFIG_ROOT
        self.engine_factory = engine_factory
        self._mapper_cache: dict[tuple[Path, ...], VocabularyMappers] = {}

    def _default_paths(self) -> tuple[Path, ...]:
        mapper_root = self.base_dir / MAPPERS_DIRNAME
        return (
            mapper_root / CONCEPT_MAP_FILENAME,
            mapper_root / CONDITION_MAP_FILENAME,
            mapper_root / MODIFIER_MAP_FILENAME,
            mapper_root / EPISODE_MAP_FILENAME,
            mapper_root / MEASUREMENT_MAP_FILENAME,
            mapper_root / OBSERVATION_MAP_FILENAME,
        )

    def get_mappers(
        self,
        *,
        concept_path: Path | None = None,
        condition_path: Path | None = None,
        modifier_path: Path | None = None,
        episode_path: Path | None = None,
        measurement_path: Path | None = None,
        observation_path: Path | None = None,
    ) -> VocabularyMappers:
        defaults = self._default_paths()
        requested = (
            concept_path,
            condition_path,
            modifier_path,
            episode_path,
            measurement_path,
            observation_path,
        )
        paths = tuple(
            Path(value) if value is not None else default
            for value, default in zip(requested, defaults)
        )
        cached = self._mapper_cache.get(paths)
        if cached is not None:
            return cached

        from .condition_map import ConditionMapper
        from .concept_map import ConceptMapper
        from .episode_map import EpisodeMapper
        from .fields import MeasurementMapper, ObservationMapper
        from .modifier_map import ModifierMapper

        concept_mapper = ConceptMapper(paths[0])
        engine = self.engine_factory()
        condition_mapper = ConditionMapper(paths[1], engine=engine)
        modifier_mapper = ModifierMapper(paths[2], engine=engine)
        episode_mapper = EpisodeMapper(paths[3])
        measurement_mapper = MeasurementMapper(
            paths[4], concept_mapper, modifier_mapper
        )
        observation_mapper = ObservationMapper(paths[5], concept_mapper)

        bundle = VocabularyMappers(
            concept_mapper=concept_mapper,
            condition_mapper=condition_mapper,
            modifier_mapper=modifier_mapper,
            episode_mapper=episode_mapper,
            measurement_mapper=measurement_mapper,
            observation_mapper=observation_mapper,
        )
        self._mapper_cache[paths] = bundle
        return bundle

    def resolve_condition_names(self, names: Sequence[str]) -> Sequence[int]:
        raise NotImplementedError(
            "Condition-name resolution belongs to the database-backed vocabulary "
            "gateway implementation."
        )

    def resolve_condition_cuis(self, cuis: Sequence[int]) -> Sequence[int]:
        return tuple(cuis)

    def resolve_concept(self, domain: str, value: str) -> int:
        return self.get_mappers().concept_mapper(domain)(value)

    def sample_modifier(self, label: str, seed: int) -> str:
        sampled = self.get_mappers().modifier_mapper(label).sample(
            label,
            rng=random.Random(seed),
        )
        return sampled.concept_code


@dataclass
class FakeVocabularyGateway:
    """Deterministic in-memory vocabulary gateway for isolated tests."""

    condition_names: Mapping[str, int] = field(default_factory=dict)
    concepts: Mapping[tuple[str, str], int] = field(default_factory=dict)
    modifiers: Mapping[str, str] = field(default_factory=dict)
    mapper_bundle: VocabularyMappers | None = None

    def resolve_condition_names(self, names: Sequence[str]) -> tuple[int, ...]:
        return tuple(self.condition_names[name] for name in names)

    def resolve_condition_cuis(self, cuis: Sequence[int]) -> tuple[int, ...]:
        return tuple(cuis)

    def resolve_concept(self, domain: str, value: str) -> int:
        return self.concepts[(domain, value)]

    def sample_modifier(self, label: str, seed: int) -> str:
        return self.modifiers[label]

    def get_mappers(self, **_: Any) -> VocabularyMappers:
        if self.mapper_bundle is None:
            raise RuntimeError("This fake gateway has no mapper bundle configured.")
        return self.mapper_bundle


@lru_cache(maxsize=1)
def get_default_vocabulary_gateway() -> DefaultVocabularyGateway:
    """Return the process default without constructing mappers at import time."""

    return DefaultVocabularyGateway()


def get_active_vocabulary_mappers() -> VocabularyMappers:
    """Return active profile mappers, falling back to the lazy default gateway."""

    from scoop.profiles.context import get_active_simulation_context

    context = get_active_simulation_context()
    if context is not None:
        mappers = getattr(context, "vocabulary_mappers", None)
        if mappers is not None:
            return mappers
        gateway = getattr(context, "vocabulary_gateway", None)
        if gateway is not None:
            return gateway.get_mappers()
    return get_default_vocabulary_gateway().get_mappers()


__all__ = [
    "DefaultVocabularyGateway",
    "FakeVocabularyGateway",
    "VocabularyGateway",
    "VocabularyMappers",
    "get_active_vocabulary_mappers",
    "get_default_vocabulary_gateway",
]
