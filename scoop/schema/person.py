""" 
Person class and labelled distribution logic
"""

from pydantic import BaseModel, Field
from typing import Any
from datetime import date

from .transformable_object import TransformableBase
from ..configuration.object_config import ObjectConfig
from ..utils import concept_mapper, person_id_seq

def person_from_config(config: ObjectConfig, **overrides: Any) -> "Person":
    """
    Sample from the configuration and return a Person instance.
    Optional keyword args override sampled values.
    """
    sampled = config.sample()           # e.g. {"age": 72, "gender_concept_id": "male", ...}
    sampled.update(overrides)           # allow explicit overrides

    return Person(**sampled)

gender_map = concept_mapper('sex')

class Person(TransformableBase):

    ## identifier fields
    person_id: int | None = None
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

    condition: str = ""

    def __post_init__(self):
        self.full_name = f"{self.first_name} {self.last_name}"

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
        }
        # todo: consider extending with mapper for race field
    }

    def model_dump(self, *args, **kwargs):
        """Assign sequential person_id if missing, then transform and dump."""
        if self.person_id is None:
            self.person_id = person_id_seq.next()
        return super().model_dump(*args, **kwargs)
    
