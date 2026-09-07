# clinical_field_base.py
from datetime import date
from typing import Callable, Type, Any
from pydantic import BaseModel

from enum import Enum


class ClinicalFieldType(Enum):
    CATEGORICAL = "categorical"  # write into value_as_concept_id
    NUMERIC = "numeric"          # write into value_as_number
    VALUE = "value"              # write into value_as_concept_id (EAV-style)


class ClinicalFieldBase:

    """
    Base class for MeasurementField / ObservationField.

    Supports three modes:
        - categorical: mapped → concept code → value_as_concept_id
        - numeric:     mapped → float → value_as_number
        - value(EAV):  entity-level concept_id identifies "what", mapped stores the "value"
                       → mapped → value_as_concept_id
    """

    record_class: Type[BaseModel]           # e.g. Measurement or Observation
    concept_field: str                      # e.g. 'measurement_concept_id' or 'observation_concept_id'
    date_field: str                         # e.g. 'measurement_date' or 'observation_date'
    value_source_field: str                 # e.g. 'value_source_value' or 'observation_source_value'
    type_concept_field: str                 # e.g. 'measurement_type_concept_id'

    def __init__(
        self,
        concept_id: int | None,
        value_map: Callable[[Any], int | float | None],
        *,
        concept_map: Callable[[Any], int] | None = None,
        field_type: ClinicalFieldType = ClinicalFieldType.CATEGORICAL,
        unit_concept_id: int | None = None,
        type_concept_id: int | None = None,
        source_field: str | None = None,
        event_id_attr: str | None = None,
        event_field_concept_id: int | None = None,
        drop: bool = True,
    ):
        if concept_id is None and concept_map is None:
            raise ValueError(
                "A clinical field needs either a fixed concept_id or a "
                "concept_map to resolve one from the value."
            )
        self.concept_id = concept_id
        self.concept_map = concept_map
        self.value_map = value_map
        self.field_type = field_type
        self.unit_concept_id = unit_concept_id
        self.type_concept_id = type_concept_id
        self.source_field = source_field
        self.event_id_attr = event_id_attr
        self.event_field_concept_id = event_field_concept_id
        self.drop = drop

    def _resolved_concept_id(self, value: Any) -> int | None:
        """The entity concept for one value, or None when there is no concept.

        Fields whose attribute names the assay rather than the test (a gene, say)
        carry a concept_map instead of a fixed id; an unmapped value resolves to
        0 there, which means "no OMOP concept" and drops the row.
        """

        if self.concept_map is None:
            return self.concept_id
        resolved = self.concept_map(value)
        return int(resolved) or None

    def to_record(self, person, value: Any):
        """Convert a single value into a Measurement/Observation instance."""
        concept_id = self._resolved_concept_id(value)
        if concept_id is None:
            return None
        mapped = self.value_map(value)
        if mapped is None:
            return None
        if self.field_type != ClinicalFieldType.NUMERIC and int(mapped) == 0:
            return None

        base_kwargs = {
            "person_id": person.person_id or 0,
            self.concept_field: concept_id,
            self.date_field: date.today(),
            "unit_concept_id": self.unit_concept_id,
            self.type_concept_field: self.type_concept_id,
            self.value_source_field: str(value) if value is not None else None,
        }
        if self.event_id_attr is not None:
            base_kwargs["measurement_event_id"] = getattr(person, self.event_id_attr, None)
            base_kwargs["meas_event_field_concept_id"] = self.event_field_concept_id

        if self.field_type == ClinicalFieldType.NUMERIC:
            base_kwargs["value_as_number"] = float(mapped)

        elif self.field_type == ClinicalFieldType.VALUE:
            # EAV structure: entity concept stored in concept_field, mapped value stored separately
            base_kwargs["value_as_concept_id"] = int(mapped)

        else:  # CATEGORICAL
            base_kwargs["value_as_concept_id"] = int(mapped)

        return self.record_class(**base_kwargs)
