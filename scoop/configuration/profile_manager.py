from __future__ import annotations
from typing import Dict, Any, Callable, Optional
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)
_ACTIVE_SIMULATION_CONTEXT: Any | None = None

def set_active_simulation_context(context: Any) -> None:
    global _ACTIVE_SIMULATION_CONTEXT
    _ACTIVE_SIMULATION_CONTEXT = context

def get_active_simulation_context() -> Any | None:
    return _ACTIVE_SIMULATION_CONTEXT


class CancerProfile(BaseModel):
    """
    Defines a disease-specific profile with:
    - distributions for sampling Person attributes
    - logic to derive Condition_Occurrence
    - logic to derive Treatment_Episode
    - logic to derive Treatment_Response
    """

    name: str
    distributions: Dict[str, Any]  # validated into ObjectConfig later
    condition_logic: Callable[[dict], dict]
    treatment_logic: Callable[[dict, dict], dict]

    def sample_person(self, overrides: Optional[dict] = None):
        """Sample distributions → Person fields"""
        from scoop.schema.person import Person

        base = {k: dist.sample() for k, dist in self.distributions.items()}
        if overrides:
            base.update(overrides)

        return Person(**base)

    def derive_condition(self, person_fields: dict):
        """Apply disease logic → Condition fields"""
        return self.condition_logic(person_fields)

    def derive_treatment(self, person_fields: dict, condition_fields: dict):
        """Apply treatment decision logic → Treatment fields"""
        return self.treatment_logic(person_fields, condition_fields)

    def generate_case(self, overrides: Optional[dict] = None):
        """
        Full case synthesis:
        Person → Condition → Treatment
        """
        person = self.sample_person(overrides)
        p_fields = person.model_dump()

        cond_fields = self.derive_condition(p_fields)

        from scoop.schema.condition_occurrence import Condition_Occurrence
        condition = Condition_Occurrence(person=person, **cond_fields)

        treat_fields = self.derive_treatment(p_fields, cond_fields)

        from scoop.schema.treatment_episode import Treatment_Episode
        regimen = Treatment_Episode(
            person_id=person.person_id,
            condition_occurrence_id=condition.condition_occurrence_id,
            **treat_fields
        )

        # attach where needed:
        person.condition = condition
        condition.regimen = regimen
        return person
