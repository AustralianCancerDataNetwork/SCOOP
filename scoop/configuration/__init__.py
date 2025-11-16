from .conditional_distribution import ConditionalMatrixDistribution
from .object_config import ObjectConfig
from pathlib import Path
from ..utils.concept_map import ConceptMapper
from ..utils.field_mapper import MeasurementMapper, ObservationMapper
from ..utils.config_loader import load_config, load_conditional

_DISTRIBUTION_HOME = Path(__file__).resolve().parent / "baseline"
_DIRECT_DISTRIBUTION_PATH = _DISTRIBUTION_HOME / "direct_distributions.yaml"
_CONDITIONAL_DISTRIBUTION_PATH = _DISTRIBUTION_HOME / "conditional_distributions.yaml"

_MAPPER_HOME = Path(__file__).resolve().parent / "mappers"
_CONCEPT_MAP_PATH = _MAPPER_HOME / "concept_maps.yaml"
_MEASUREMENT_MAP_PATH = _MAPPER_HOME / "measurement_mappers.yaml"
_OBSERVATION_MAP_PATH = _MAPPER_HOME / "observation_mappers.yaml"

try:
    concept_mapper = ConceptMapper(_CONCEPT_MAP_PATH)
except FileNotFoundError as e:
    raise RuntimeError(f"Concept map file not found: {_CONCEPT_MAP_PATH}") from e
try:
    measurement_mapper = MeasurementMapper(_MEASUREMENT_MAP_PATH, concept_mapper)
except FileNotFoundError as e:
    raise RuntimeError(f"Measurement map file not found: {_MEASUREMENT_MAP_PATH}") from e
try:
    observation_mapper = ObservationMapper(_OBSERVATION_MAP_PATH, concept_mapper)
except FileNotFoundError as e:
    raise RuntimeError(f"Observation map file not found: {_OBSERVATION_MAP_PATH}") from e

try: 
    direct_config = load_config(_DIRECT_DISTRIBUTION_PATH)
except FileNotFoundError as e:
    raise RuntimeError(f"Direct distribution config file not found: {_DIRECT_DISTRIBUTION_PATH}") from e
try:
    conditional_config = load_conditional(_CONDITIONAL_DISTRIBUTION_PATH)
except FileNotFoundError as e:
    raise RuntimeError(f"Conditional distribution config file not found: {_CONDITIONAL_DISTRIBUTION_PATH}") from e


__all__ = ["concept_mapper", "measurement_mapper", "observation_mapper", "conditional_config", "direct_config"]