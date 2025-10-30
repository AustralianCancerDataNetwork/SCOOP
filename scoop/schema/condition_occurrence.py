
from __future__ import annotations
from typing import TYPE_CHECKING, Any
from pydantic import BaseModel, Field
from datetime import date

from .transformable_object import TransformableBase
from ..configuration.object_config import ObjectConfig
from ..utils import concept_mapper, condition_id_seq

if TYPE_CHECKING:
    from .person import Person

def condition_from_config(config: ObjectConfig, person: Person, **overrides: Any) -> "Condition_Occurrence":
    """
    Sample from the configuration and return a Condition_Occurrence instance.
    Optional keyword args override sampled values.
    """
    sampled = config.sample()           # e.g. {"age": 72, "gender_concept_id": "male", ...}
    sampled.update(overrides)           # allow explicit overrides

    return Condition_Occurrence(person_id = person.person_id, **sampled)

gender_map = concept_mapper('sex')

class Condition_Occurrence(TransformableBase):

    ## identifier fields
    person_id: int | None = None


    ## measurement transformation fields - handled in measurement_mapper config
    morphology: str = "neither"  
    laterality: str = "right"
    lobe: str = "upper"

    # def __post_init__(self):
    #     self.full_name = f"{self.first_name} {self.last_name}"

    model_config = {
        "from_attributes": True  # allow .from_orm(Person instance)
    }

    # __base_transformations__ = {
    #     "year_of_birth": {
    #         "inputs": ["age"],
    #         "transform": lambda p: date.today().year - p.age,
    #         "drop": True,
    #     },
    #     "gender_concept_id": {
    #         "inputs": ["sex"],
    #         "transform": lambda p: gender_map(p.sex),
    #         "drop": True,
    #     }
    #     # todo: consider extending with mapper for race field
    # }

    # def model_dump(self, *args, **kwargs):
    #     """Assign sequential person_id if missing, then transform and dump."""
    #     if self.person_id is None:
    #         self.person_id = person_id_seq.next()
    #     return super().model_dump(*args, **kwargs)
    
