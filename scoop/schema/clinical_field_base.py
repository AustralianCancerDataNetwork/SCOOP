# clinical_field_base.py
from datetime import date
from typing import Callable, Type, Any
from pydantic import BaseModel

class ClinicalFieldBase:
    """
    Base class for MeasurementField / ObservationField.
    Handles conversion of an attribute to a clinical record (Measurement or Observation).
    """

    record_class: Type[BaseModel]           # e.g. Measurement or Observation
    concept_field: str                      # e.g. 'measurement_concept_id' or 'observation_concept_id'
    date_field: str                         # e.g. 'measurement_date' or 'observation_date'
    value_source_field: str                 # e.g. 'value_source_value' or 'observation_source_value'

    def __init__(
        self,
        concept_id: int,
        value_map: Callable[[Any], int | float | None],
        *,
        is_numeric: bool = False,
        unit_concept_id: int | None = None,
        source_field: str | None = None,
    ):
        self.concept_id = concept_id
        self.value_map = value_map
        self.is_numeric = is_numeric
        self.unit_concept_id = unit_concept_id
        self.source_field = source_field

    def to_record(self, person, value: Any):
        """Convert a single value into a Measurement/Observation instance."""
        mapped = self.value_map(value)
        if mapped is None:
            return None

        base_kwargs = {
            "person_id": person.person_id or 0,
            self.concept_field: self.concept_id,
            self.date_field: date.today(),
            "unit_concept_id": self.unit_concept_id,
            self.value_source_field: str(value) if value is not None else None,
        }

        if self.is_numeric:
            base_kwargs["value_as_number"] = float(mapped)
        else:
            base_kwargs["value_as_concept_id"] = int(mapped)

        return self.record_class(**base_kwargs)
