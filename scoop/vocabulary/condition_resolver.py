from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .condition_map import ResolvedConditionConcept


def _normalized(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized or None


@dataclass(frozen=True)
class ConditionConceptResolver:
    name: str
    domains: tuple[str, ...]
    profile_prefixes: tuple[str, ...]

    def matches(self, condition: Any) -> bool:
        condition_domain = _normalized(getattr(condition, "condition_domain", None))
        if condition_domain in self.domains:
            return True

        source_profile = _normalized(getattr(condition, "source_profile", None))
        if source_profile is None:
            return False

        return any(source_profile.startswith(prefix) for prefix in self.profile_prefixes)

    def resolve_for_condition(self, condition: Any) -> ResolvedConditionConcept:
        raise NotImplementedError


_lung_condition_map = None


def _get_lung_condition_map():
    global _lung_condition_map
    if _lung_condition_map is None:
        from scoop.vocabulary.gateway import get_active_vocabulary_mappers

        _lung_condition_map = get_active_vocabulary_mappers().condition_mapper(
            "lung"
        )
    return _lung_condition_map


class LungConditionConceptResolver(ConditionConceptResolver):
    def __init__(self) -> None:
        super().__init__(
            name="lung",
            domains=("lung", "nsclc", "sclc"),
            profile_prefixes=("lung", "nsclc", "sclc"),
        )

    def resolve_for_condition(self, condition: Any) -> ResolvedConditionConcept:
        return _get_lung_condition_map().resolve(
            morphology=getattr(condition, "morphology", None),
            topography=getattr(condition, "topog", None),
        )


condition_concept_resolvers: tuple[ConditionConceptResolver, ...] = (
    LungConditionConceptResolver(),
    # TODO: bladder has no resolver here, so its condition_concept_id never
    # resolves -- every bladder case exports with a null concept id. Needs a
    # BladderConditionConceptResolver (morphology/topography -> OMOP concept)
    # once bladder's ICD-O mapping data is available. Tracked as a known gap
    # in tests/test_profile_cdm_completeness.py.
)


def get_condition_concept_resolver(condition: Any) -> ConditionConceptResolver | None:
    for resolver in condition_concept_resolvers:
        if resolver.matches(condition):
            return resolver
    return None
