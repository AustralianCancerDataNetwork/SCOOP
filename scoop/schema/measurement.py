from pydantic import BaseModel, Field
from datetime import date
from .clinical_field_base import ClinicalFieldBase
from ..utils.id_management import measurement_id_seq

class Measurement(BaseModel):
    person_id: int
    measurement_id: int = Field(default_factory=measurement_id_seq.next)
    measurement_concept_id: int
    measurement_date: date
    measurement_event_id: int | None = None
    meas_event_field_concept_id: int | None = None
    unit_concept_id: int | None = None
    value_as_concept_id: int | None = None
    value_as_number: float | None = None
    value_source_value: str | None = None


class MeasurementField(ClinicalFieldBase):
    record_class = Measurement
    concept_field = "measurement_concept_id"
    date_field = "measurement_date"
    value_source_field = "value_source_value"

    def to_measurement(self, person, value):
        return self.to_record(person, value)
