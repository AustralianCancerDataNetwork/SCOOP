""" 
Person class and labelled distribution logic
"""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Any
from pydantic import Field, model_validator
from datetime import date
from scoop.schema.transformable_object import TransformableBase
from scoop.configuration import concept_mapper
from scoop.utils.id_management import person_id_seq
from .omop_export import OmopExportBundle
if TYPE_CHECKING:
    from .object_factories import make_condition_occurrence

logger = logging.getLogger(__name__)

gender_map = concept_mapper('sex')

class Person(TransformableBase):

    ## identifier fields
    person_id: int = Field(default_factory=person_id_seq.next)
    ## direct mapped fields (because not implemented)
    race_concept_id: int = 0
    ethnicity_concept_id: int = 0
    ## simple transformed fields
    sex: str
    age: int

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
        from .object_factories import make_condition_occurrence  
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
            "transform": lambda p: gender_map(p.sex),
            "drop": True,
        },
        # todo: consider extending with mapper for race field
    }

    def to_omop_row(self):
        from omop_alchemy.cdm import model as omop_model

        return omop_model.Person(
            person_id=self.person_id,
            year_of_birth=date.today().year - self.age,
            gender_concept_id=gender_map(self.sex),
            race_concept_id=self.race_concept_id,
            ethnicity_concept_id=self.ethnicity_concept_id,
            gender_source_value=self.sex,
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

    
from scoop.schema.condition_occurrence import Condition_Occurrence
Person.model_rebuild()
