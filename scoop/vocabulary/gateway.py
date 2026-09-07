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


# Administered component kind -> the OMOP domain a HemOnc "Maps to" edge must
# land in. This is runtime vocabulary resolution, not profile configuration.
COMPONENT_OMOP_DOMAINS = {
    "drug": "Drug",
    "procedure": "Procedure",
}

# OMOP's route-of-administration hierarchy is vocabulary data, not a SCOOP
# profile choice.  Keep the semantic anchor here and let omop-alchemy expand it
# against the connected vocabulary release.
ROUTE_OF_ADMINISTRATION_PARENT_CONCEPT_ID = 4106215


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

    def resolve_component_concept(
        self,
        domain: str,
        value: str,
        cui: int | None = None,
    ) -> int:
        """Resolve an administered component, preferring its HemOnc CUI."""

    def resolve_route_concept(self, value: str | None) -> int | None:
        """Resolve a route below OMOP's route-of-administration parent."""

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
        self._engine: Any | None = None
        self._engine_loaded = False
        self._hemonc: Any | None = None
        self._omop_resolvers: dict[str, Any] = {}
        self._route_resolver: Any | None = None
        self._route_resolver_loaded = False

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
        engine = self._get_engine()
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

    def _get_engine(self) -> Any | None:
        """Materialize one vocabulary engine for all gateway resolvers."""

        if not self._engine_loaded:
            self._engine = self.engine_factory()
            self._engine_loaded = True
        return self._engine

    def resolve_condition_names(self, names: Sequence[str]) -> Sequence[int]:
        raise NotImplementedError(
            "Condition-name resolution belongs to the database-backed vocabulary "
            "gateway implementation."
        )

    def resolve_condition_cuis(self, cuis: Sequence[int]) -> Sequence[int]:
        return tuple(cuis)

    def resolve_concept(self, domain: str, value: str) -> int:
        return self.get_mappers().concept_mapper(domain)(value)

    def _hemonc_resolver(self):
        from .hemonc import HemOncConceptResolver

        if self._hemonc is None:
            self._hemonc = HemOncConceptResolver(engine=self._get_engine())
        return self._hemonc

    def resolve_component_concept(
        self,
        domain: str,
        value: str,
        cui: int | None = None,
    ) -> int:
        """Resolve an administered component to a standard OMOP concept.

        The HemOnc CUI is authoritative when the vocabulary carries a "Maps to"
        edge for it, so it is tried first.  A name lookup is the only fallback;
        it is built from OMOP vocabulary rows rather than a SCOOP-maintained
        table of component concept IDs.
        """

        omop_domain = COMPONENT_OMOP_DOMAINS.get(domain)
        if cui is not None and omop_domain is not None:
            resolved = self._hemonc_resolver().resolve(cui, domain_id=omop_domain)
            if resolved:
                return resolved

        resolved = self._resolve_omop_domain_concept(domain, value)
        if resolved is None:
            raise LookupError(
                f"No standard OMOP {omop_domain or domain!r} concept resolved for "
                f"{value!r}; provide a valid HemOnc CUI or vocabulary term."
            )
        return resolved

    def _resolve_omop_domain_concept(
        self, domain: str, value: str | None
    ) -> int | None:
        omop_domain = COMPONENT_OMOP_DOMAINS.get(domain)
        if omop_domain is None or not value:
            return None

        resolver = self._omop_resolvers.get(omop_domain)
        if resolver is None:
            engine = self._get_engine()
            if engine is None:
                return None
            from omop_alchemy.toolkit.core.concepts import make_concept_resolver
            from sqlalchemy.orm import Session

            with Session(engine) as session:
                resolver = make_concept_resolver(
                    session,
                    name=f"scoop.{omop_domain.lower()}.component",
                    domain_id=omop_domain,
                    unknown=None,
                    include_synonyms=True,
                    require_active=True,
                )
            self._omop_resolvers[omop_domain] = resolver
        return resolver.lookup(value)

    def resolve_route_concept(self, value: str | None) -> int | None:
        """Resolve an OMOP route using the released vocabulary hierarchy."""

        if not value or value.strip().lower() in {"ns", "not specified"}:
            return None
        if not self._route_resolver_loaded:
            engine = self._get_engine()
            self._route_resolver_loaded = True
            if engine is not None:
                from omop_alchemy.toolkit.core.concepts import make_concept_resolver
                from sqlalchemy.orm import Session

                with Session(engine) as session:
                    self._route_resolver = make_concept_resolver(
                        session,
                        name="scoop.route_of_administration",
                        unknown=None,
                        parents=[ROUTE_OF_ADMINISTRATION_PARENT_CONCEPT_ID],
                        include_synonyms=True,
                        require_active=True,
                    )
        if self._route_resolver is None:
            return None
        return self._route_resolver.lookup(value)

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
    route_concepts: Mapping[str, int] = field(default_factory=dict)
    modifiers: Mapping[str, str] = field(default_factory=dict)
    # (domain, hemonc_cui) -> concept_id, standing in for the "Maps to" walk.
    component_concepts: Mapping[tuple[str, int], int] = field(default_factory=dict)
    mapper_bundle: VocabularyMappers | None = None

    def resolve_condition_names(self, names: Sequence[str]) -> tuple[int, ...]:
        return tuple(self.condition_names[name] for name in names)

    def resolve_condition_cuis(self, cuis: Sequence[int]) -> tuple[int, ...]:
        return tuple(cuis)

    def resolve_concept(self, domain: str, value: str) -> int:
        return self.concepts[(domain, value)]

    def resolve_component_concept(
        self,
        domain: str,
        value: str,
        cui: int | None = None,
    ) -> int:
        if cui is not None:
            resolved = self.component_concepts.get((domain, int(cui)))
            if resolved:
                return resolved
        return self.resolve_concept(domain, value)

    def resolve_route_concept(self, value: str | None) -> int | None:
        if not value or value.strip().lower() in {"ns", "not specified"}:
            return None
        return self.route_concepts.get(value.lower())

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
