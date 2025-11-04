# holding all these steps separately so that pydantic classes can build in the appropriate order
from __future__ import annotations
from typing import Any

from .person import Person
from .condition_occurrence import Condition_Occurrence
from .treatment_episode import Treatment_Episode

from ..configuration import ObjectConfig, ConditionalMatrixDistribution
from ..utils import person_config, condition_config


def person_from_config(**overrides) -> "Person":
    """
    Sample from the configuration and return a Person instance.
    Optional keyword args override sampled values.
    """

    sampled = person_config.sample()  # e.g. {"age": 72, "gender_concept_id": "male", ...}
    sampled.update(overrides)  # allow explicit overrides
    return Person(**sampled)


def make_condition_occurrence(person: Person, **overrides) -> "Condition_Occurrence":
    """
    Sample from the configuration and return a Condition_Occurrence instance.
    Optional keyword args override sampled values.
    """
    sampled = condition_config.sample('morphology', context_obj={"person": person})      
    print(overrides)    
    sampled.update(overrides)          
    return Condition_Occurrence(person_id = person.person_id, **sampled)


def assign_regimen(condition_occurrence: Condition_Occurrence, **overrides: Any) -> "Treatment_Episode":
    """
    Assign a relevant, clinically plausible regimen based on the Condition_Occurrence & Person parameters.
    """
    # Simple logic for demonstration; real logic would be more complex
    if condition_occurrence.morphology == "type_a":
        regimen_type = "chemo_only"
        print(regimen_type)
    