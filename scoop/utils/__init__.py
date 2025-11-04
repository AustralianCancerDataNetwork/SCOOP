from pathlib import Path
from .concept_map import ConceptMapper, ConceptMap
from .field_mapper import MeasurementMapper, ObservationMapper
from .id_management import Sequence
from .config_loader import load_config, load_conditional


_DISTRIBUTION_HOME = Path(__file__).resolve().parent / "distributions"
_PERSON_DISTRIBUTION_PATH = _DISTRIBUTION_HOME / "person_distributions.yaml"
_CONDITION_DISTRIBUTION_PATH = _DISTRIBUTION_HOME / "diagnosis_conditional.yaml"

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
    person_config = load_config(_PERSON_DISTRIBUTION_PATH)
except FileNotFoundError as e:
    raise RuntimeError(f"Person distribution config file not found: {_PERSON_DISTRIBUTION_PATH}") from e
try:
    condition_config = load_conditional(_CONDITION_DISTRIBUTION_PATH)
except FileNotFoundError as e:
    raise RuntimeError(f"Condition distribution config file not found: {_CONDITION_DISTRIBUTION_PATH}") from e

# Global singleton sequences & configurations
person_id_seq = Sequence(1)
measurement_id_seq = Sequence(1)
condition_id_seq = Sequence(1)

__all__ = ["concept_mapper", "ConceptMapper", "ConceptMap", "person_id_seq", "measurement_id_seq", 
           "condition_id_seq", "measurement_mapper", "MeasurementMapper", "observation_mapper", 
           "ObservationMapper", "condition_config", "person_config", "load_config", "load_conditional"]