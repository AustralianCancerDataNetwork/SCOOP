from .transformation_graph import ConditionalDiscreteDistribution
from .object_config import ObjectConfig
from pathlib import Path
from ..utils.concept_map import ConceptMapper
from ..utils.field_mapper import MeasurementMapper, ObservationMapper
from ..utils.config_loader import load_transformation_graph

_DISTRIBUTION_HOME = Path(__file__).resolve().parent / "profiles" / "lung"
_DISTRIBUTION_PATH = _DISTRIBUTION_HOME / "probabilistic_transformations.yaml"

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
    transformation_graph = load_transformation_graph(_DISTRIBUTION_PATH)
except FileNotFoundError as e:
    raise RuntimeError(f"Probabilistic distribution config file not found: {_DISTRIBUTION_PATH}") from e

__all__ = ["concept_mapper", "measurement_mapper", "observation_mapper", "transformation_graph"]