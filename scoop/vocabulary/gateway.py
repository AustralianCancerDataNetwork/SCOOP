"""I/O-free vocabulary gateway contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence, runtime_checkable


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


@dataclass
class FakeVocabularyGateway:
    """Deterministic in-memory vocabulary gateway for isolated tests."""

    condition_names: Mapping[str, int] = field(default_factory=dict)
    concepts: Mapping[tuple[str, str], int] = field(default_factory=dict)
    modifiers: Mapping[str, str] = field(default_factory=dict)

    def resolve_condition_names(self, names: Sequence[str]) -> tuple[int, ...]:
        return tuple(self.condition_names[name] for name in names)

    def resolve_condition_cuis(self, cuis: Sequence[int]) -> tuple[int, ...]:
        return tuple(cuis)

    def resolve_concept(self, domain: str, value: str) -> int:
        return self.concepts[(domain, value)]

    def sample_modifier(self, label: str, seed: int) -> str:
        return self.modifiers[label]


__all__ = ["VocabularyGateway", "FakeVocabularyGateway"]
