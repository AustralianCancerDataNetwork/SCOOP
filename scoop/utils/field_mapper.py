# clinical_mapper_base.py
import yaml
from pathlib import Path
from typing import Any, Type, Callable, TypeAlias
from .concept_map import ConceptMapper
from ..schema.clinical_field_base import ClinicalFieldType

ValueMap: TypeAlias = Callable[[Any], Any]

class ClinicalMapperBase:
    """
    Generic YAML loader and container for clinical field definitions.
    Subclasses (MeasurementMapper, ObservationMapper) must set:
      - record_name (for logging / repr)
      - field_class (e.g. MeasurementField, ObservationField)
    """

    record_name: str = "clinical_field"
    field_class: Type[Any]

    def __init__(self, mapping: str | Path | dict[str, Any], concept_mapper: ConceptMapper):
        
        if isinstance(mapping, (str, Path)):
            with open(mapping, "r") as f:
                mapping = yaml.safe_load(f)

        self.fields: dict[str, Any] = {}
        if not isinstance(mapping, dict):
            raise ValueError("Mapping must be a dict or path to valid YAML file")
        
        for name, entry in mapping.items():
            concept_id_key = f"{self.record_name}_concept_id" if self.record_name != "clinical_field" else "concept_id"
            concept_id = entry.get(concept_id_key)
            if concept_id is None:
                raise ValueError(f"Missing {concept_id_key} for field '{name}'")

            type_ = entry.get("type", "categorical")
            try:
                field_type = ClinicalFieldType(type_)
            except ValueError:
                raise ValueError(f"Unknown clinical field type '{type_}' for field {name}")

            unit_concept_id = entry.get("unit_concept_id")
            value_map_name = entry.get("value_map")
            value_map: ValueMap
            
            if value_map_name:
                value_map = concept_mapper(value_map_name)
            else:
                value_map = float if type_ == "numeric" else lambda x: x

            field = self.field_class(
                concept_id=concept_id,
                value_map=value_map,
                field_type=field_type,
                unit_concept_id=unit_concept_id,
                source_field=name,
            )
            self.fields[name] = field

    def __call__(self, name: str):
        """Return a Field object for the given source name."""
        try:
            return self.fields[name]
        except KeyError as e:
            raise KeyError(f"No {self.record_name} definition found for '{name}'") from e

    def __getitem__(self, name: str):
        return self(name)

    def __repr__(self):
        return f"<{self.__class__.__name__} n={len(self.fields)}>"

from ..schema.measurement import MeasurementField
from ..schema.observation import ObservationField

class MeasurementMapper(ClinicalMapperBase):
    record_name = "measurement"
    field_class = MeasurementField

class ObservationMapper(ClinicalMapperBase):
    record_name = "observation"
    field_class = ObservationField
