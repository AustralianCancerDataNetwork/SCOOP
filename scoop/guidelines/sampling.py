from __future__ import annotations

import random
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
    state_node_exemptions: frozenset[str] = frozenset()

    @property
    def as_dict(self) -> dict[str, object]:
        """Return a copy for callers that still consume the legacy mapping shape."""

        return dict(self.values)


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


class AmbiguousDecision(ValueError):
    """Raised when a guideline sample activates more than one intervention endpoint."""

    def __init__(self, endpoints: list[str] | tuple[str, ...]):
        self.endpoints = tuple(sorted(endpoints))
        super().__init__(
            "Ambiguous treatment decision; active endpoints: "
            + ", ".join(self.endpoints)
        )


def sample(
    graph: "TransformationGraph",
    seed: int,
    *,
    source_profile: str = "",
    initial_state: Mapping[str, object] | None = None,
    state_node_exemptions: frozenset[str] = frozenset(),
) -> GuidelineSample:
    """Sample one graph realization with an isolated, explicit random seed."""

    if graph is None:
        raise TypeError("sample() requires a TransformationGraph")
    values = graph.sample_one(
        initial_state=dict(initial_state or {}),
        rng=random.Random(seed),
    )
    return GuidelineSample(
        values=dict(values),
        source_profile=source_profile,
        seed=seed,
        state_node_exemptions=state_node_exemptions,
    )


def decide(sample: GuidelineSample) -> TreatmentDecision | NoDecision:
    """Extract one explicit treatment endpoint from a typed graph sample."""

    if not isinstance(sample, GuidelineSample):
        raise TypeError("decide() requires a GuidelineSample")

    endpoints = sorted(
        key.split(".", 1)[1]
        for key, value in sample.values.items()
        if key.startswith("treatment_episode.")
        and value is not None
        and key.split(".", 1)[1] not in sample.state_node_exemptions
    )
    if not endpoints:
        return NoDecision(
            reason="no active treatment endpoint",
            source_profile=sample.source_profile,
        )
    if len(endpoints) > 1:
        raise AmbiguousDecision(endpoints)

    endpoint = endpoints[0]
    modifiers = {
        key: value
        for key, value in sample.values.items()
        if not key.startswith("treatment_episode.") and value is not None
    }
    return TreatmentDecision(
        decision_id=endpoint,
        graph_endpoint=endpoint,
        modifiers=modifiers,
        source_profile=sample.source_profile,
        raw_sample=sample,
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
