""" 
Person class and labelled distribution logic
"""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Any
from pydantic import Field, model_validator
from datetime import date
from scoop.patients.transforms import TransformableBase
from scoop.vocabulary.gateway import get_active_vocabulary_mappers
from scoop.patients.ids import person_id_seq
from ..export.omop import OmopExportBundle
if TYPE_CHECKING:
    from .build import make_condition_occurrence

logger = logging.getLogger(__name__)


def _gender_concept_id(value: str) -> int:
    return get_active_vocabulary_mappers().concept_mapper("sex")(value)


def _race_concept_id(value: str) -> int:
    """SCOOP deliberately does not infer race; emit OMOP's unknown sentinel."""

    return 0


def _ethnicity_concept_id(value: str) -> int:
    """SCOOP deliberately does not infer ethnicity; emit OMOP's unknown sentinel."""

    return 0


class Person(TransformableBase):

    ## identifier fields
    person_id: int = Field(default_factory=person_id_seq.next)
    ## simple transformed fields
    sex: str
    age: int
    # SCOOP does not sample race or ethnicity. These fields are retained for
    # source compatibility, but their OMOP concept IDs intentionally remain 0.
    race: str = "unknown"
    ethnicity: str = "unknown"

    ## measurement transformation fields - handled in measurement_mapper config
    smoking_history: str = "never"  
    ecog: int = 2 # todo: enforce appropriate range
    weight: float = 75.0
    variant: str = "none"
    graph_sample: dict[str, Any] | None = Field(default=None, exclude=True, repr=False)
    auto_create_condition: bool = Field(default=True, exclude=True, repr=False)

    condition: 'Condition_Occurrence | None' = None

    @model_validator(mode="after")
    def _attach_condition(self):
        """
        Runs after Person is initialised. Automatically attach a Condition_Occurrence.
        """
        logger.debug(f"Initialised Person[{self.person_id}] age={self.age} sex={self.sex}")
        from .build import make_condition_occurrence  
        if self.condition is None and self.auto_create_condition:
            self.condition = make_condition_occurrence(person=self, graph_sample=self.graph_sample)
            logger.debug(f"Attached Condition_Occurrence to Person[{self.person_id}]")
        return self

    model_config = {
        "from_attributes": True  # allow .from_orm(Person instance)
    }

    __base_transformations__ = {
        "year_of_birth": {
            "inputs": ["age"],
            "transform": lambda p: date.today().year - p.age,
            "drop": True,
        },
        "gender_concept_id": {
            "inputs": ["sex"],
            "transform": lambda p: _gender_concept_id(p.sex),
            "drop": True,
        },
        "race_concept_id": {
            "inputs": ["race"],
            "transform": lambda p: _race_concept_id(p.race),
            "drop": True,
        },
        "ethnicity_concept_id": {
            "inputs": ["ethnicity"],
            "transform": lambda p: _ethnicity_concept_id(p.ethnicity),
            "drop": True,
        },
    }

    def to_omop_row(self):
        from omop_alchemy.cdm import model as omop_model

        return omop_model.Person(
            person_id=self.person_id,
            year_of_birth=date.today().year - self.age,
            gender_concept_id=_gender_concept_id(self.sex),
            race_concept_id=_race_concept_id(self.race),
            ethnicity_concept_id=_ethnicity_concept_id(self.ethnicity),
            gender_source_value=self.sex,
            race_source_value=self.race,
            ethnicity_source_value=self.ethnicity,
        )

    def to_omop_bundle(self, *, include_related: bool = False) -> OmopExportBundle:
        _, measurements, observations, _ = self._collect_emitted_outputs()
        bundle = OmopExportBundle(
            person=self.to_omop_row(),
            measurements=[measurement.to_omop_row() for measurement in measurements if measurement is not None],
            observations=[observation.to_omop_row() for observation in observations if observation is not None],
        )
        if include_related and self.condition is not None:
            bundle.merge(self.condition.to_omop_bundle(include_related=True))
        return bundle

    
from scoop.patients.condition_occurrence import Condition_Occurrence
Person.model_rebuild()
