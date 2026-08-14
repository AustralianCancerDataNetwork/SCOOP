from pydantic import BaseModel
from abc import ABC, abstractmethod
from scoop.protocols.spec import SourcePhaseYaml


class ResolvedPhase(BaseModel):
    phase_id: str
    phase_type: str # i.e. sig.phase

    # Strictly for debugging/history-viewing
    # NOT for building logic
    origin: SourcePhaseYaml
    pass


class SigbackedPhase(ABC, ResolvedPhase):
    # change to this approach? sigbundles = list #List[SigBundle]
    sql_artifacts: str

    @classmethod
    @abstractmethod
    def _create_sigbundles(cls, SourcePhaseYaml, session):
        pass


class VariantPhase(SigbackedPhase):
    """
    Applies when HemOncKB regimen variants are defined.

    Note: this is not based on attempted search, but on being the type of 
    situation where a regimen variant would be defined.
    
    I.e. at least one systemic component.
    """
    @classmethod
    def _create_sigbundles(cls, SourcePhaseYaml, session):
        pass


class NonVariantPhase(SigbackedPhase):
    """
    Applies when HemOncKB regimen variants are not defined.

    Note: this is not based on attempted search, but on being the type of 
    situation where a regimen variant would not be defined.

    I.e. when no concurrent systemic component
    """
    @classmethod
    def _create_sigbundles(cls, SourcePhaseYaml, session):
        pass


class NonSigbackedPhase(ResolvedPhase):
    """
    Used whenever the HemOncKB is not/cannot be used for operationalising.
    """
    pass


class SurgeryPhase(NonSigbackedPhase):
    # until better differentiation based on pt these all lobectomies?
    pass


class NullPhase(NonSigbackedPhase):
    """
    Placeholder phase for no implementation
    """
    # No implementation of Best Supportive Care atm. 
    pass