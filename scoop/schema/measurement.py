from pydantic import BaseModel, Field
from datetime import date
from .clinical_field_base import ClinicalFieldBase
from ..utils.id_management import measurement_id_seq
from .omop_export import as_datetime

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

    def to_omop_row(self):
        from omop_alchemy.cdm import model as omop_model

        payload = {
            "measurement_id": self.measurement_id,
            "person_id": self.person_id,
            "measurement_concept_id": self.measurement_concept_id,
            "measurement_date": self.measurement_date,
            "measurement_datetime": as_datetime(self.measurement_date),
            "value_source_value": self.value_source_value,
            "measurement_event_id": self.measurement_event_id,
            "meas_event_field_concept_id": self.meas_event_field_concept_id,
        }
        if self.unit_concept_id is not None:
            payload["unit_concept_id"] = self.unit_concept_id
        if self.value_as_number is not None:
            payload["value_as_number"] = self.value_as_number
        if self.value_as_concept_id is not None:
            payload["value_as_concept_id"] = self.value_as_concept_id

        return omop_model.Measurement(**payload)


class MeasurementField(ClinicalFieldBase):
    record_class = Measurement
    concept_field = "measurement_concept_id"
    date_field = "measurement_date"
    value_source_field = "value_source_value"

    def to_measurement(self, person, value):
        return self.to_record(person, value)
