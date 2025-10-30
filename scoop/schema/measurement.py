from pydantic import BaseModel
from datetime import date

class Measurement(BaseModel):
    person_id: int
    measurement_id: int | None = None
    measurement_concept_id: int
    measurement_date: date
    value_as_concept_id: int | None = None
    value_as_number: float | None = None
    source_value: str | None = None


from datetime import date
from typing import Callable

class MeasurementField:
    """
    Describes how to turn an attribute into a Measurement record.

    Supports:
      - categorical → value_as_concept_id
      - numeric → value_as_number (+ optional unit)
    """

    def __init__(
        self,
        measurement_concept_id: int,
        value_map: Callable[[str | float | None], int | float | None],
        *,
        is_numeric: bool = False,
        unit_concept_id: int | None = None,
        source_field: str | None = None,
    ):
        self.measurement_concept_id = measurement_concept_id
        self.value_map = value_map
        self.is_numeric = is_numeric
        self.unit_concept_id = unit_concept_id
        self.source_field = source_field

    def to_measurement(self, person, value: str | float | None) -> Measurement:
        """Convert a single value into a Measurement instance."""
        mapped = self.value_map(value)
        if mapped is None:
            return None

        if self.is_numeric:
            return Measurement(
                person_id=person.person_id or 0,
                measurement_concept_id=self.measurement_concept_id,
                measurement_date=date.today(),
                value_as_number=float(mapped),
                unit_concept_id=self.unit_concept_id,
                value_source_value=str(value) if value is not None else None,
            )
        else:
            return Measurement(
                person_id=person.person_id or 0,
                measurement_concept_id=self.measurement_concept_id,
                measurement_date=date.today(),
                value_as_concept_id=int(mapped),
                value_source_value=str(value) if value is not None else None,
            )
