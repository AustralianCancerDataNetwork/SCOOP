from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel, ConfigDict

from scoop.protocols.spec import PhaseSpec


SYSTEMIC_CATEGORIES = frozenset({"chemotherapy", "immunotherapy"})


def _positive_categories(phase_spec: PhaseSpec) -> set[str]:
    return {
        category.value
        for category, rule in phase_spec.component_requirements.category_restrictions.items()
        if rule.amount > 0
    }


def _has_explicit_components(phase_spec: PhaseSpec) -> bool:
    return bool(phase_spec.component_requirements.component_lists)


def _has_systemic_component(phase_spec: PhaseSpec) -> bool:
    categories = _positive_categories(phase_spec)
    return bool(categories & SYSTEMIC_CATEGORIES) or _has_explicit_components(phase_spec)


def _has_sigbacked_component(phase_spec: PhaseSpec) -> bool:
    categories = _positive_categories(phase_spec)
    return bool(categories - {"surgery", "supportive"}) or _has_explicit_components(phase_spec)


def _is_surgery_only(phase_spec: PhaseSpec) -> bool:
    categories = _positive_categories(phase_spec)
    return "surgery" in categories and not _has_systemic_component(phase_spec) and not (
        categories - {"surgery"}
    )


def _is_null(phase_spec: PhaseSpec) -> bool:
    return not (
        _positive_categories(phase_spec) - {"supportive"}
    ) and not _has_explicit_components(phase_spec)


def _is_nonvariant_sig_backed(phase_spec: PhaseSpec) -> bool:
    return _has_sigbacked_component(phase_spec) and not _has_systemic_component(
        phase_spec
    )


class PhaseBinding(BaseModel):
    phase_id: str
    phase_type: str | None = None

    # Strictly for debugging/history-viewing
    # NOT for building logic
    origin: PhaseSpec
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


class SigbackedBinding(PhaseBinding):
    """Binding for phases whose candidates come from the HemOnc gateway."""

    sql_artifacts: str | None = None

    @classmethod
    def _create_candidates(cls, phase_spec: PhaseSpec, gateway):
        """Fetch and normalize candidates through the injected gateway.

        Binding decides *which kind* of phase is eligible for HemOnc-backed
        resolution. Query construction remains owned by the gateway, so this
        helper does not accept a SQLAlchemy session or inspect database models.
        """

        if not isinstance(phase_spec, PhaseSpec):
            raise TypeError("_create_candidates() requires a PhaseSpec")
        if not hasattr(gateway, "candidates_for_phase"):
            raise TypeError(
                "_create_candidates() requires a gateway with "
                "candidates_for_phase()"
            )
        from .candidates import _group_candidates

        return _group_candidates(
            list(gateway.candidates_for_phase(phase_spec)),
            source_phase_id=phase_spec.source_phase_id or "",
        )


class VariantBinding(SigbackedBinding):
    """
    Applies when HemOncKB regimen variants are defined.

    Note: this is not based on attempted search, but on being the type of 
    situation where a regimen variant would be defined.
    
    I.e. at least one systemic component.
    """
class NonVariantBinding(SigbackedBinding):
    """
    Applies when HemOncKB regimen variants are not defined.

    Note: this is not based on attempted search, but on being the type of 
    situation where a regimen variant would not be defined.

    I.e. when no concurrent systemic component
    """
class NonSigbackedBinding(PhaseBinding):
    """
    Used whenever the HemOncKB is not/cannot be used for operationalising.
    """
class SurgeryBinding(NonSigbackedBinding):
    """Surgery-only phase; procedure modeling is outside HemOnc selection."""


class NullBinding(NonSigbackedBinding):
    """
    Placeholder phase for no implementation
    """
    # No implementation of Best Supportive Care atm.


@dataclass(frozen=True)
class BindingRule:
    """One declared shape-to-binding rule."""

    name: str
    matches: Callable[[PhaseSpec], bool]
    binding_type: type[PhaseBinding]


BINDING_RULES: tuple[BindingRule, ...] = (
    BindingRule(
        "systemic_sig_backed",
        _has_systemic_component,
        VariantBinding,
    ),
    BindingRule(
        "nonvariant_sig_backed",
        _is_nonvariant_sig_backed,
        NonVariantBinding,
    ),
    BindingRule("surgery_only", _is_surgery_only, SurgeryBinding),
    BindingRule("nothing_operationalisable", _is_null, NullBinding),
)


def bind(phase_spec: PhaseSpec) -> PhaseBinding:
    """Classify a phase from its declaration, without querying or using provenance."""

    if not isinstance(phase_spec, PhaseSpec):
        raise TypeError("bind() requires a PhaseSpec")

    for rule in BINDING_RULES:
        if rule.matches(phase_spec):
            return rule.binding_type(
                phase_id=phase_spec.source_phase_id or "",
                phase_type=phase_spec.type,
                origin=phase_spec,
            )

    raise ValueError(
        f"No binding rule matched phase {phase_spec.source_phase_id!r}"
    )
