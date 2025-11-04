
# from typing import Literal, Optional, Union, TypeVar, Generic, Tuple
from pydantic import BaseModel, Field
from pathlib import Path
from typing import Any, Dict
import yaml

from ..configuration.object_config import ObjectConfig
from ..configuration.conditional_distribution import ConditionalMatrixDistribution
from ..configuration.distribution_base import LabelledDistribution, NumericDistribution, DiscreteNumericDistribution


def load_config(config: str | dict[str, Any]) -> ObjectConfig:
    """Load a YAML config file or a pre-parsed dict."""
    if isinstance(config, (str, Path)):
        with open(config, "r") as f:
            config = yaml.safe_load(f)

    if not isinstance(config, dict):
        raise TypeError(f"Expected dict or YAML path, got {type(config)}")

    distributions = {}
    for key, entry in config.items():
        kind = entry.pop("type", None)
        if kind == "labelled":
            distributions[key] = LabelledDistribution(**entry)
        elif kind == "numeric":
            distributions[key] = NumericDistribution(**entry)
        elif kind == "discrete_numeric":
            distributions[key] = DiscreteNumericDistribution(**entry)
        else:
            raise ValueError(f"Unknown distribution type for {key}: {kind}")

    return ObjectConfig(distributions=distributions)

def load_conditional(config: str | dict[str, Any]) -> ConditionalMatrixDistribution:
    """Load a YAML config file or a pre-parsed dict."""
    if isinstance(config, (str, Path)):
        return ConditionalMatrixDistribution.from_yaml(config)

    if not isinstance(config, dict):
        raise TypeError(f"Expected dict or YAML path, got {type(config)}")
