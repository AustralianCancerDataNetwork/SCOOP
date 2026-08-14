from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias

from ..protocols.spec import ProtocolSpec


class SequenceTemplate(StrEnum):
    """Observed bundle-level recipe labels from the pre-refactor registry."""

    ADJUVANT = "adjuvant"
    POSTOPERATIVE = "postoperative"
    CONCURRENT = "concurrent"
    SEQUENTIAL = "sequential"


class PhaseTiming(StrEnum):
    """Observed timing value attached to one recipe phase."""

    PRE = "pre"
    POST = "post"
    CONCURRENT = "concurrent"


@dataclass(frozen=True)
class PhaseRef:
    phase_id: str
    technique: str | None = None


@dataclass(frozen=True)
class ProtocolRef:
    protocol_id: str


@dataclass(frozen=True)
class Choice:
    intervention_ids: tuple[str, ...]


@dataclass(frozen=True)
class RecipeStep:
    reference: PhaseRef | ProtocolRef
    timing: PhaseTiming | None = None


@dataclass(frozen=True)
class Recipe:
    steps: tuple[RecipeStep, ...]
    sequence_template: SequenceTemplate | None = None


@dataclass(frozen=True)
class NonHemOnc:
    intervention_id: str
    label: str
    concept_domain: str | None = None
    concept_value: str | None = None


ResolutionForm: TypeAlias = PhaseRef | ProtocolRef | Choice | Recipe | NonHemOnc


@dataclass(frozen=True)
class InterventionSpec:
    intervention_id: str
    resolution: ResolutionForm
    requires: tuple[str, ...] = ()
    source_profile: str | None = None


@dataclass(frozen=True)
class ResolvedIntervention:
    intervention_id: str
    protocols: tuple[ProtocolSpec, ...] = ()
    recipe: Recipe | None = None
    non_hemonc: NonHemOnc | None = None
    technique: str | None = None
