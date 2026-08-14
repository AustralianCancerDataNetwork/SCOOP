from pydantic import BaseModel
from abc import ABC, abstractmethod
from scoop.protocols.spec import PhaseSpec


class PhaseBinding(BaseModel):
    phase_id: str
    phase_type: str # i.e. sig.phase

    # Strictly for debugging/history-viewing
    # NOT for building logic
    origin: PhaseSpec
    pass


class SigbackedBinding(ABC, PhaseBinding):
    # change to this approach? candidates = list[RegimenCandidate]
    sql_artifacts: str

    @classmethod
    @abstractmethod
    def _create_candidates(cls, phase_spec, session):
        pass


class VariantBinding(SigbackedBinding):
    """
    Applies when HemOncKB regimen variants are defined.

    Note: this is not based on attempted search, but on being the type of 
    situation where a regimen variant would be defined.
    
    I.e. at least one systemic component.
    """
    @classmethod
    def _create_candidates(cls, phase_spec, session):
        pass


class NonVariantBinding(SigbackedBinding):
    """
    Applies when HemOncKB regimen variants are not defined.

    Note: this is not based on attempted search, but on being the type of 
    situation where a regimen variant would not be defined.

    I.e. when no concurrent systemic component
    """
    @classmethod
    def _create_candidates(cls, phase_spec, session):
        pass


class NonSigbackedBinding(PhaseBinding):
    """
    Used whenever the HemOncKB is not/cannot be used for operationalising.
    """
    pass


class SurgeryBinding(NonSigbackedBinding):
    # until better differentiation based on pt these all lobectomies?
    pass


class NullBinding(NonSigbackedBinding):
    """
    Placeholder phase for no implementation
    """
    # No implementation of Best Supportive Care atm. 
    pass
