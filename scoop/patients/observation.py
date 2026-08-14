from pydantic import BaseModel, Field
from datetime import date
from .clinical_field_base import ClinicalFieldBase
from .ids import observation_id_seq
from ..export.omop import as_datetime

class Observation(BaseModel):
    person_id: int
    observation_id: int = Field(default_factory=observation_id_seq.next)
    observation_concept_id: int
    observation_date: date
    value_as_concept_id: int | None = None
    value_as_number: float | None = None
    observation_source_value: str | None = None

    def to_omop_row(self):
        from omop_alchemy.cdm import model as omop_model

        if self.value_as_number is None and self.value_as_concept_id is None:
            raise ValueError(
                "Observation OMOP export requires value_as_number or value_as_concept_id."
            )

        payload = {
            "observation_id": self.observation_id,
            "person_id": self.person_id,
            "observation_concept_id": self.observation_concept_id,
            "observation_date": self.observation_date,
            "observation_datetime": as_datetime(self.observation_date),
            "observation_source_value": self.observation_source_value,
        }
        if self.value_as_number is not None:
            payload["value_as_number"] = self.value_as_number
        if self.value_as_concept_id is not None:
            payload["value_as_concept_id"] = self.value_as_concept_id

        return omop_model.Observation(**payload)


class ObservationField(ClinicalFieldBase):
    record_class = Observation
    concept_field = "observation_concept_id"
    date_field = "observation_date"
    value_source_field = "observation_source_value"

    def to_observation(self, person, value):
        return self.to_record(person, value)
