
from __future__ import annotations
from typing import TYPE_CHECKING, Any
from pydantic import Field, model_validator
from .transformable_object import TransformableBase
from ..utils.id_management import condition_id_seq
if TYPE_CHECKING:
    from .object_factories import assign_regimen

class Condition_Occurrence(TransformableBase):

    ## identifier fields
    person_id: int 
    condition_occurrence_id: int = Field(default_factory=condition_id_seq.next)

    year_of_diagnosis: int | None = None
    years_since_diagnosis: int | None = None

    person: 'Person | None' = None
    regimen: 'Treatment_Episode | None' = None

    ## measurement transformation fields - handled in measurement_mapper config
    morphology: str = "neither"  
    laterality: str = "right"
    lobe: str = "upper"
    stage: str = "stage_ii"  

    # todo 0. create all the necessary fields to populate condition_occurrence instance + associated regimen
    # todo 1. create conditional (?+ regular) configuration sampler entries for these fields
    # todo 2. implement logic to assign regimen based on condition + person attributes
    # todo 3. implement measurement transformations for condition fields
    # todo 4. implement logic to generate drug_exposure + procedure_occurrence events from regimen
    # todo 5. implement overarching condition episode + associated events
    # todo 6. model dump logic to apply transforms for this and all child elements

    @model_validator(mode="after")
    def _assign_treatment(self):
        from .object_factories import assign_regimen
        if self.regimen is None:
            self.regimen = assign_regimen(condition_occurrence=self)
        return self

    def __post_init__(self):
        if self.regimen:
            drug_exposures, procedures = self.regimen.roll_events()

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
        
    

from .person import Person
from .treatment_episode import Treatment_Episode
Condition_Occurrence.model_rebuild()