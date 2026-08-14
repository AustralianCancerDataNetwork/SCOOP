
from __future__ import annotations
from datetime import date
from typing import TYPE_CHECKING, Any
from pydantic import Field, model_validator
from .transforms import TransformableBase
from scoop.vocabulary.gateway import get_active_vocabulary_mappers
from .ids import condition_id_seq
from ..schedule.timing import random_date
from ..vocabulary.condition_resolver import get_condition_concept_resolver
from ..export.omop import OmopExportBundle, as_datetime
if TYPE_CHECKING:
    from .build import assign_regimen
    from .condition_episode import Condition_Episode

def _resolved_year_of_diagnosis(condition: "Condition_Occurrence") -> int | None:
    if condition.year_of_diagnosis is not None:
        return int(condition.year_of_diagnosis)
    if condition.years_since_diagnosis is not None:
        return date.today().year - int(condition.years_since_diagnosis)
    return None


def _condition_start_date(condition: "Condition_Occurrence") -> date | None:
    resolved_year = _resolved_year_of_diagnosis(condition)
    if resolved_year is None:
        return None
    return random_date(resolved_year)


def _stage_label(condition: "Condition_Occurrence") -> str | None:
    for label in (condition.cancer_stage, condition.stage):
        if isinstance(label, str) and label:
            return label
    return None


def _build_metastasis_measurement(condition: "Condition_Occurrence") -> "Measurement | None":
    if _stage_label(condition) != "stage_iv":
        return None

    sampled_metastasis = get_active_vocabulary_mappers().modifier_mapper(
        "metastasis"
    ).sample("stage_iv")
    return Measurement(
        person_id=condition.person_id,
        measurement_concept_id=sampled_metastasis.concept_id,
        measurement_date=date.today(),
        measurement_event_id=condition.condition_occurrence_id,
        meas_event_field_concept_id=1147127,
        value_source_value=sampled_metastasis.concept_code,
    )


def _resolved_condition_concepts(
    condition: "Condition_Occurrence",
) -> dict[str, int | str] | None:
    resolver = get_condition_concept_resolver(condition)
    if resolver is None:
        return None

    resolved = resolver.resolve_for_condition(condition)
    return {
        "condition_concept_id": resolved.concept_id,
        "condition_source_concept_id": resolved.concept_id,
        "condition_source_value": resolved.concept_code,
    }


class Condition_Occurrence(TransformableBase):

    ## identifier fields
    person_id: int 
    condition_occurrence_id: int = Field(default_factory=condition_id_seq.next)
    condition_concept_id: int | None = None
    condition_source_concept_id: int | None = None
    condition_source_value: str | None = None

    year_of_diagnosis: int | None = None
    years_since_diagnosis: int | None = None
    condition_start_date: date | None = None

    person: 'Person | None' = None
    regimen: 'Treatment_Episode | None' = None
    overarching_episode: 'Condition_Episode | None' = Field(
        default=None,
        exclude=True,
        repr=False,
    )
    condition_domain: str | None = None

    ## measurement transformation fields - handled in measurement_mapper config
    morphology: str = "neither"  
    laterality: str = "right"
    topog: str = "upper"
    stage: str = "stage_ii"  
    cancer_stage: str | None = None
    biomarker: str | None = None
    pdl1: str | None = None
    graph_sample: dict[str, Any] | None = Field(default=None, exclude=True)
    source_profile: str | None = None
    auto_assign_regimen: bool = Field(default=True, exclude=True, repr=False)

    def _has_resolved_condition_concept(self) -> bool:
        return (
            self.condition_concept_id is not None
            and self.condition_source_concept_id is not None
            and self.condition_source_value is not None
        )

    def _resolve_condition_concept(self):
        self._apply_named_persisted_bundle_transform("condition_concept_resolution")
        return self

    def _create_overarching_condition_episode(self):
        if (
            self.overarching_episode is None
            and (
                self.condition_concept_id is not None
                or self.condition_source_concept_id is not None
            )
        ):
            from .condition_episode import Condition_Episode

            self.overarching_episode = Condition_Episode(
                condition_occurrence=self,
            )
        return self

    def _assign_treatment(self):
        from .build import assign_regimen
        if self.regimen is None and self.auto_assign_regimen:
            self.regimen = assign_regimen(condition_occurrence=self)
        return self

    @model_validator(mode="after")
    def _finalize_condition_dependencies(self):
        # managed all post-creation dependencies in one place to ensure
        # correct ordering and avoid multiple passes over the data
        self._resolve_condition_concept()
        self._create_overarching_condition_episode()
        self._assign_treatment()
        return self

    def __post_init__(self):
        if self.regimen:
            drug_exposures, procedures = self.regimen.roll_events()

    def to_omop_row(self):
        from omop_alchemy.cdm import model as omop_model

        return omop_model.Condition_Occurrence(
            condition_occurrence_id=self.condition_occurrence_id,
            person_id=self.person_id,
            condition_concept_id=self.condition_concept_id,
            condition_start_date=self.condition_start_date,
            condition_start_datetime=as_datetime(self.condition_start_date),
            condition_source_value=self.condition_source_value,
            condition_source_concept_id=self.condition_source_concept_id,
        )

    def to_omop_bundle(self, *, include_related: bool = False) -> OmopExportBundle:
        _, measurements, observations, _ = self._collect_emitted_outputs()
        bundle = OmopExportBundle(
            condition_occurrences=[self.to_omop_row()],
            measurements=[
                measurement.to_omop_row()
                for measurement in measurements
                if measurement is not None
            ],
            observations=[
                observation.to_omop_row()
                for observation in observations
                if observation is not None
            ],
        )
        if include_related and self.overarching_episode is not None:
            bundle.merge(self.overarching_episode.to_omop_bundle())
        return bundle

    model_config = {
        "from_attributes": True  
    }

    def __init__(self, **data: Any):
        """
        Allow initialization with either a Person or a person_id.
        """
        person = data.pop("person", None)
        super().__init__(**data)
        if person is not None:
            # fill person_id automatically
            self.person = person
            self.person_id = getattr(person, "person_id", self.person_id)

    __persisted_transforms__ = {
        "year_of_diagnosis": {
            "inputs": ["year_of_diagnosis", "years_since_diagnosis"],
            "transform": _resolved_year_of_diagnosis,
            "when_missing": True,
        },
        "condition_start_date": {
            "inputs": ["year_of_diagnosis", "years_since_diagnosis"],
            "transform": _condition_start_date,
            "when_missing": True,
        },
    }

    __persisted_bundle_transforms__ = {
        "condition_concept_resolution": {
            "inputs": ["morphology", "topog", "condition_domain", "source_profile"],
            "outputs": [
                "condition_concept_id",
                "condition_source_concept_id",
                "condition_source_value",
            ],
            "transform": _resolved_condition_concepts,
            "when_missing": True,
        },
    }

    __base_transformations__ = {
        "metastasis_measurement": {
            "inputs": ["cancer_stage", "stage"],
            "transform": _build_metastasis_measurement,
            "drop": False,
        },
    }
    

from .person import Person
from .condition_episode import Condition_Episode
from .treatment_episode import Treatment_Episode
from .measurement import Measurement
Condition_Occurrence.model_rebuild()
