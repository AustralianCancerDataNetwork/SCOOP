from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping, Protocol, Sequence, runtime_checkable

from ..spec import PhaseSpec

if TYPE_CHECKING:
    from ..candidates import RegimenCandidate


@runtime_checkable
class HemOncGateway(Protocol):
    """Injectable boundary for HemOnc-backed candidate retrieval."""

    def candidates_for_phase(
        self,
        phase_spec: PhaseSpec,
    ) -> Sequence["RegimenCandidate"]:
        """Return candidates for a declared phase without prescribing query mechanics."""


@dataclass
class FakeHemOncGateway:
    """In-memory gateway for isolated contract and later candidate tests."""

    candidates: Mapping[str, Sequence["RegimenCandidate"]] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list, init=False)
    _cache: dict[str, tuple["RegimenCandidate", ...]] = field(
        default_factory=dict, init=False, repr=False
    )

    def candidates_for_phase(self, phase_spec: PhaseSpec) -> list["RegimenCandidate"]:
        phase_id = phase_spec.source_phase_id or ""
        if phase_id not in self._cache:
            self.calls.append(phase_id)
            self._cache[phase_id] = tuple(self.candidates.get(phase_id, ()))
        return list(self._cache[phase_id])


@dataclass
class SqlHemOncGateway:
    """Database-backed gateway for the existing HemOnc query implementation.

    Query construction and execution are deliberately kept behind this object. The
    candidate layer therefore only sees ``candidates_for_phase`` and can be tested
    entirely with ``FakeHemOncGateway``.
    """

    session: Any
    models: Any | None = None
    _cache: dict[str, tuple["RegimenCandidate", ...]] = field(
        default_factory=dict, init=False, repr=False
    )

    def candidates_for_phase(self, phase_spec: PhaseSpec) -> list["RegimenCandidate"]:
        phase_id = phase_spec.source_phase_id or ""
        if phase_id not in self._cache:
            from ..candidates import get_regimen_candidate_list
            from .models import condition_cuis_by_names
            from .queries import phase_spec_to_sigdict

            conditions = phase_spec.conditions
            if conditions.cuis is not None:
                condition_cuis = list(conditions.cuis)
            elif conditions.names:
                condition_cuis = condition_cuis_by_names(
                    conditions.names,
                    self.session,
                    models=self.models,
                )
            else:
                condition_cuis = []
            sigdict = phase_spec_to_sigdict(
                phase_spec,
                self.session,
                condition_cuis=condition_cuis,
                models=self.models,
            )
            self._cache[phase_id] = tuple(get_regimen_candidate_list(sigdict))
        return list(self._cache[phase_id])
