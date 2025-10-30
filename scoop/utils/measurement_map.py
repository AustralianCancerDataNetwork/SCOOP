import yaml
from pathlib import Path
from typing import Any
from .concept_map import ConceptMapper
from ..schema.measurement import MeasurementField

class MeasurementMapper:
    """Singleton-style container for multiple MeasurementField definitions."""

    def __init__(self, mapping: str | Path | dict[str, Any], concept_mapper: ConceptMapper):
        if isinstance(mapping, (str, Path)):
            with open(mapping, "r") as f:
                mapping = yaml.safe_load(f)

        self.measurements: dict[str, MeasurementField] = {}

        for name, entry in mapping.items():
            measurement_concept_id = entry["measurement_concept_id"]
            type_ = entry.get("type", "categorical")
            unit_concept_id = entry.get("unit_concept_id")
            value_map_name = entry.get("value_map")

            if value_map_name:
                value_map = concept_mapper(value_map_name)
            else:
                value_map = float if type_ == "numeric" else lambda x: x

            field = MeasurementField(
                measurement_concept_id=measurement_concept_id,
                value_map=value_map,
                is_numeric=(type_ == "numeric"),
                unit_concept_id=unit_concept_id,
                source_field=name,
            )
            self.measurements[name] = field

    def __call__(self, name: str) -> MeasurementField:
        """Return a MeasurementField for a given attribute name."""
        try:
            return self.measurements[name]
        except KeyError as e:
            raise KeyError(f"No measurement definition found for '{name}'") from e

    def __getitem__(self, name: str) -> MeasurementField:
        return self(name)

    def __repr__(self):
        return f"<MeasurementMapper n={len(self.measurements)}>"
