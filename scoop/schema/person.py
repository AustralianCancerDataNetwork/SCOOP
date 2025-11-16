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

    condition: 'Condition_Occurrence | None' = None

    @model_validator(mode="after")
    def _attach_condition(self):
        """
        Runs after Person is initialized. Automatically attach a Condition_Occurrence.
        """
        logger.debug(f"Initialised Person[{self.person_id}] age={self.age} sex={self.sex}")
        from .object_factories import make_condition_occurrence  
        if self.condition is None:
            self.condition = make_condition_occurrence(person=self)
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

    
from scoop.schema.condition_occurrence import Condition_Occurrence
Person.model_rebuild()