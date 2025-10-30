from .person import Person
from .condition_occurrence import Condition_Occurrence

# Resolve forward references between the two
Person.model_rebuild()
Condition_Occurrence.model_rebuild()
