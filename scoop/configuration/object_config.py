from typing import Dict, Any
from pydantic import BaseModel, field_validator
from .transformation_graph import BaseDistribution

class ObjectConfig(BaseModel):
    """
    Holds an arbitrary set of distributions for sampling object attributes.
    Users can add new entries freely.
    """
    distributions: Dict[str, BaseDistribution]

    @field_validator("distributions", mode="before")
    @classmethod
    def _coerce_dict(cls, v: Any):
        # Allow plain dicts to be loaded and auto-validated later
        if not isinstance(v, dict):
            raise TypeError("distributions must be a mapping")
        return v

    def sample(self) -> dict[str, Any]:
        """Return a sampled attribute dict."""
        return {k: dist.sample() for k, dist in self.distributions.items()}
