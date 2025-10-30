from pydantic import BaseModel
from datetime import date
from .clinical_field_base import ClinicalFieldBase

class Observation(BaseModel):
    person_id: int
    observation_id: int | None = None
    observation_concept_id: int
    observation_date: date
    value_as_concept_id: int | None = None
    value_as_number: float | None = None
    source_value: str | None = None


class ObservationField(ClinicalFieldBase):
    record_class = Observation
    concept_field = "observation_concept_id"
    date_field = "observation_date"
    value_source_field = "value_source_value"

    def to_observation(self, person, value):
        return self.to_record(person, value)
