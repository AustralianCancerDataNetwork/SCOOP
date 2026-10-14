from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from ..guidelines.sampling import GuidelineSample
from .condition_occurrence import Condition_Occurrence
from .person import Person
from .treatment_episode import Treatment_Episode


@dataclass(frozen=True)
class PatientBuildInput:
    """Explicit input to the patient construction stages."""

    sample: GuidelineSample
    person_overrides: Mapping[str, object] = field(default_factory=dict)
    condition_overrides: Mapping[str, object] = field(default_factory=dict)
    treatment_overrides: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PatientBuildOutput:
    """Typed output boundary for explicit patient construction."""

    person: Person
    condition_occurrence: Condition_Occurrence
    treatment_episode: Treatment_Episode | None = None
