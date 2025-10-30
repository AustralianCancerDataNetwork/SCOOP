from pathlib import Path
from .concept_map import ConceptMapper, ConceptMap
from .measurement_map import MeasurementMapper
from .id_management import Sequence


_MAPPER_HOME = Path(__file__).resolve().parent / "mappers"
_CONCEPT_MAP_PATH = _MAPPER_HOME / "concept_maps.yaml"
_MEASUREMENT_MAP_PATH = _MAPPER_HOME / "measurement_mappers.yaml"

try:
    concept_mapper = ConceptMapper(_CONCEPT_MAP_PATH)
except FileNotFoundError as e:
    raise RuntimeError(f"Concept map file not found: {_CONCEPT_MAP_PATH}") from e

try:
    measurement_mapper = MeasurementMapper(_MEASUREMENT_MAP_PATH, concept_mapper)
except FileNotFoundError as e:
    raise RuntimeError(f"Measurement map file not found: {_MEASUREMENT_MAP_PATH}") from e

# Global singleton sequences
person_id_seq = Sequence(1)
measurement_id_seq = Sequence(1)


__all__ = ["concept_mapper", "ConceptMapper", "ConceptMap", "person_id_seq", "measurement_id_seq", "measurement_mapper", "MeasurementMapper"]