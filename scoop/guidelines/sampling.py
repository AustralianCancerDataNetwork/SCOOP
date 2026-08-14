from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping

from pydantic import BaseModel, field_validator
from .graph import BaseDistribution, TransformationGraph


@dataclass(frozen=True)
class GuidelineSample:
    """Typed boundary for one sampled guideline state."""

    values: Mapping[str, object]
    source_profile: str
    seed: int | None = None


@dataclass(frozen=True)
class TreatmentDecision:
    """The single treatment endpoint selected by a guideline sample."""

    decision_id: str
    graph_endpoint: str
    modifiers: Mapping[str, object]
    source_profile: str
    raw_sample: GuidelineSample


@dataclass(frozen=True)
class NoDecision:
    """Typed absence of a treatment decision."""

    reason: str
    source_profile: str | None = None


def sample(graph: "TransformationGraph", seed: int) -> GuidelineSample:
    """Declare the guideline sampling boundary without activating sampling yet."""

    raise NotImplementedError(
        "Guideline sampling is intentionally deferred to the Phase 4 port."
    )


def decide(sample: GuidelineSample) -> TreatmentDecision | NoDecision:
    """Declare the decision boundary without deriving a decision implicitly."""

    raise NotImplementedError(
        "Treatment decision extraction is intentionally deferred to Phase 5."
    )



class ObjectConfig(BaseModel):
    """
    Holds an arbitrary set of distributions for sampling object attributes.
    Users can add new entries freely.
    """
    distributions: Dict[str, BaseDistribution]

    # @field_validator("distributions", mode="before")
    # @classmethod
    # def _coerce_dict(cls, v: Any) -> Dict[str, Any]:
    #     # Allow plain dicts to be loaded and auto-validated later
    #     if not isinstance(v, dict):
    #         raise TypeError("distributions must be a mapping")
    #     return v

    @field_validator("distributions", mode="after")
    @classmethod
    def _validate_distributions(
        cls, v: Dict[str, BaseDistribution]
    ) -> Dict[str, BaseDistribution]:
        return v

    def sample(self) -> dict[str, Any]:
        """Return a sampled attribute dict."""
        return {k: dist.sample() for k, dist in self.distributions.items()}
