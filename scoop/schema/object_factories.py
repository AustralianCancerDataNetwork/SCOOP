# holding all these steps separately so that pydantic classes can build in the appropriate order
from __future__ import annotations
from typing import Any
import logging

from .person import Person
from .condition_occurrence import Condition_Occurrence
from .treatment_episode import Treatment_Episode

from ..configuration import ObjectConfig, ConditionalMatrixDistribution
from ..configuration import direct_config, conditional_config

logger = logging.getLogger(__name__)

def person_from_config(**overrides) -> "Person":
    """
    Sample from the configuration and return a Person instance.
    Optional keyword args override sampled values.
    """
    sampled = direct_config.sample()  
    logger.debug(f"Person from config [{sampled}]")
    if overrides:
        sampled.update(overrides)  # allow explicit overrides
        logger.debug(f"Person with overrides [{sampled}]")
    return Person(**sampled)


def make_condition_occurrence(person: Person, **overrides) -> "Condition_Occurrence":
    """
    Sample from the configuration and return a Condition_Occurrence instance.
    Optional keyword args override sampled values.
    """
    if conditional_config is None:
        raise ValueError("Conditional configuration not loaded.")
    sampled = direct_config.sample() | conditional_config.sample('morphology', context_obj={"person": person})
    logger.debug(f"Condition from config [{sampled}]")
    if overrides:
        sampled.update(overrides)
        logger.debug(f"Condition with overrides [{sampled}]")
    return Condition_Occurrence(person_id=person.person_id, **sampled)


def assign_regimen(condition_occurrence: Condition_Occurrence, **overrides: Any) -> "Treatment_Episode":
    """
    Assign a relevant, clinically plausible regimen based on the Condition_Occurrence & Person parameters.
    """
    # Simple logic for demonstration; real logic would be more complex
    if condition_occurrence.morphology == "type_a":
        regimen_type = "chemo_only"
    else: 
        regimen_type = "chemo_radiation"
    logger.debug(f"Assigning regimen '{regimen_type}' for Condition[{condition_occurrence.condition_occurrence_id}]")
    return Treatment_Episode(
        regimen_type=regimen_type, 
        condition_occurrence_id=condition_occurrence.condition_occurrence_id, 
        person_id=condition_occurrence.person_id, 
        **overrides)