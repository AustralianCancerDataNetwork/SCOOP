import random
from abc import ABC, abstractmethod
from typing import Generic, TypeVar, Union
from pydantic import BaseModel, Field, field_validator, ValidationError

T = TypeVar("T")


class BaseDistribution(BaseModel, ABC):
    """Abstract base for all distributions."""

    @abstractmethod
    def sample(self):
        """Return a sampled value."""
        raise NotImplementedError


class LabelledDistribution(BaseDistribution, Generic[T]):
    """Discrete labelled distribution with probability constraints."""

    probs: dict[T, float] = Field(
        ..., description="Mapping of label → probability (must sum to 1.0)"
    )

    # --- validation ---
    @field_validator("probs")
    @classmethod
    def _validate_probs_sum_to_one(cls, v: dict[T, float]) -> dict[T, float]:
        total = sum(v.values())
        if not abs(total - 1.0) < 1e-6:
            raise ValueError(f"Probabilities must sum to 1.0 (got {total})")
        for label, p in v.items():
            if p < 0:
                raise ValueError(f"Negative probability for {label}: {p}")
        return v

    # --- sampling ---
    def sample(self) -> T:
        labels, weights = zip(*self.probs.items())
        return random.choices(labels, weights=weights, k=1)[0]


class NumericDistribution(BaseDistribution):
    """
    Base class for numeric random variables.
    Subclasses define parameterisation and sampling logic.
    """

    mean: float = Field(0, description="Mean or central tendency parameter")
    sd: float | None = Field(None, gt=0, description="Standard deviation or scale parameter")
    min_value: float | None = Field(None, description="Lower bound (optional)")
    max_value: float | None = Field(None, description="Upper bound (optional)")
    as_int: bool = Field(False, description="Return as int if True")

    def _clip(self, x: float) -> float:
        """Apply optional min/max bounds."""
        if self.min_value is not None:
            x = max(self.min_value, x)
        if self.max_value is not None:
            x = min(self.max_value, x)
        return x

    def sample(self) -> float | int:
        """
        Default Sampling logic:
        - If sd is provided → Gaussian(mean, sd) + clipping
        - If sd is missing or zero → Uniform(min_value, max_value)
        """
        if not self.sd:  # sd is None or zero
            if self.min_value is None or self.max_value is None:
                raise ValueError(
                    "Uniform sampling requires min_value and max_value when sd is not provided."
                )
            val = random.uniform(self.min_value, self.max_value)
        else:       
            val = random.gauss(self.mean, self.sd)
        val = self._clip(val)
        return int(val) if self.as_int else val


Number = Union[int, float]

class DiscreteNumericDistribution(LabelledDistribution[Number]):
    """
    Discrete numeric distribution — like LabelledDistribution but returns numeric types.
    Useful for ordinal numeric scales (e.g. ECOG).
    """

    probs: dict[Number, float] = Field(
        ..., description="Mapping of numeric value → probability (must sum to 1.0)"
    )

    @field_validator("probs")
    @classmethod
    def _validate_numeric_keys(cls, v: dict[Number, float]) -> dict[Number, float]:
        # Enforce numeric keys (ints or floats)
        for k in v.keys():
            if not isinstance(k, (int, float)):
                raise TypeError(f"All keys must be numeric (got {type(k)} for {k})")
        return super()._validate_probs_sum_to_one(v)

    def sample(self) -> Number:
        """Sample a numeric value from the discrete probability distribution."""
        values, weights = zip(*self.probs.items())
        return random.choices(values, weights=weights, k=1)[0]

