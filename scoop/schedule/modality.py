from enum import StrEnum


class Modality(StrEnum):
    """Explicit delivery axis for later schedule implementations."""

    SYSTEMIC = "systemic"
    RADIOTHERAPY = "radiotherapy"
