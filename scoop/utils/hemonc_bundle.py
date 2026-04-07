from __future__ import annotations
"""Helpers for compiling and assembling non-variant HemOnc bundle recipes.

This module handles the bundle path that sits alongside the variant query
helpers in `hemonc_query.py`. It compiles phase-level queries, applies
lightweight Python-side filtering where cross-object metadata is awkward to
express in SQL, and assembles final bundle candidates from the phase results.
"""

from itertools import product
from typing import Any

import sqlalchemy as sa
import sqlalchemy.orm as so
from pydantic import BaseModel, Field

from ..configuration.regimen_registry import (
    RegimenRegistry,
    ResolvedBundlePhase,
    ResolvedHemOncBundle,
)
from .hemonc_query import HemOncModels, _load_hemonc_models, build_variant_statement


class BundlePhaseMatch(BaseModel):
    """Runtime representation of one candidate match for a bundle phase."""

    phase_id: str
    phase_type: str
    timing: str
    source_kind: str
    variant_cui: int | None = None
    sigs: list[Any] = Field(default_factory=list)
    label: str


def _sig_identifier(sig: Any) -> Any:
    """Return a stable tuple that can be used to deduplicate sig-backed phases."""

    return (
        getattr(sig, "id", None),
        getattr(sig, "sig_id", None),
        getattr(sig, "component", None),
        getattr(sig, "variant_cui", None),
    )


def _bundle_name(bundle: "AssembledTreatmentBundle") -> str:
    """Build a human-readable bundle label from its phase labels."""

    labels = [phase.label for phase in bundle.phases if phase.label]
    return " + ".join(labels) if labels else bundle.recipe_id


class AssembledBundlePhase(BaseModel):
    """Serializable summary of a single assembled bundle phase."""

    phase_id: str
    phase_type: str
    timing: str
    source_kind: str
    variant_cui: int | None = None
    sig_ids: list[Any] = Field(default_factory=list)
    component_names: list[str | None] = Field(default_factory=list)
    label: str


class AssembledTreatmentBundle(BaseModel):
    """Serializable bundle assembled from one candidate per recipe phase."""

    recipe_id: str
    assembly_mode: str
    sequence_template: str | None = None
    variant_cui: int | None = None
    phases: list[AssembledBundlePhase] = Field(default_factory=list)
    bundle_name: str = ""


def build_sig_component_statement(
    phase: ResolvedBundlePhase,
    models: HemOncModels | None = None,
) -> Any:
    """Build a coarse SQL query for sig rows matching the phase component terms.

    Condition-context filtering is intentionally left to Python so the live
    inspection path stays easier to reason about and less coupled to
    `hemonc_import`'s computed relationship helpers.
    """

    models = models or _load_hemonc_models()
    expressions = [models.Sigs.component.ilike(term) for term in phase.component_terms]
    return sa.select(models.Sigs).where(sa.or_(*expressions))


def build_bundle_phase_statement(
    phase: ResolvedBundlePhase,
    models: HemOncModels | None = None,
) -> Any:
    """Build the SQL statement used to fetch candidates for one bundle phase."""

    if phase.resolved_query is not None:
        return build_variant_statement(phase.resolved_query, models=models)
    if phase.source_type == "sig_component":
        return build_sig_component_statement(phase, models=models)
    raise ValueError(f"Unsupported bundle phase source type: {phase.source_type}")


def build_bundle_query_artifacts(
    resolved_bundle: ResolvedHemOncBundle,
    models: HemOncModels | None = None,
) -> dict[str, Any]:
    """Build inspectable phase-level SQL artifacts for a resolved bundle recipe."""

    models = models or _load_hemonc_models()
    return {
        "recipe_id": resolved_bundle.group_id,
        "assembly_mode": resolved_bundle.assembly_mode,
        "sequence_template": resolved_bundle.sequence_template,
        "phase_statements": {
            phase.phase_id: build_bundle_phase_statement(phase, models=models)
            for phase in resolved_bundle.phases
        },
    }


def build_bundle_query_artifacts_for_entry(
    registry: RegimenRegistry,
    entry_id: str,
    models: HemOncModels | None = None,
) -> dict[str, Any] | None:
    """Resolve a registry entry and build its bundle phase SQL artifacts."""

    resolved_bundle = registry.resolve_hemonc_bundle(entry_id)
    if resolved_bundle is None:
        return None
    return build_bundle_query_artifacts(resolved_bundle, models=models)


def _object_study_keys(obj: Any) -> set[str]:
    """Collect study identifiers exposed directly or indirectly on an ORM row."""

    keys: set[str] = set()
    study_objects = getattr(obj, "study_objects", None) or []
    for study in study_objects:
        study_name = getattr(study, "study", None)
        if study_name:
            keys.add(str(study_name))

    raw_study = getattr(obj, "study", None)
    if isinstance(raw_study, str):
        keys.update(token.strip() for token in raw_study.split("|") if token.strip())
    elif raw_study:
        try:
            keys.update(str(token) for token in raw_study if token)
        except TypeError:
            keys.add(str(raw_study))

    study_tokens = getattr(obj, "study_tokens", None) or []
    keys.update(str(token) for token in study_tokens if token)
    return keys


def _phase_match_study_keys(phase_match: BundlePhaseMatch) -> set[str]:
    """Collect study keys across all sigs contributing to a phase match."""

    study_keys: set[str] = set()
    for sig in phase_match.sigs:
        study_keys.update(_object_study_keys(sig))
    return study_keys


def _object_condition_cuis(obj: Any) -> set[int]:
    """Collect any condition CUIs discoverable on a variant or sig-like object."""

    condition_cuis: set[int] = set()

    direct_conditions = getattr(obj, "condition_objects", None) or []
    for condition in direct_conditions:
        condition_cui = getattr(condition, "condition_cui", None)
        if condition_cui is not None:
            condition_cuis.add(int(condition_cui))

    study_objects = getattr(obj, "study_objects", None) or []
    for study in study_objects:
        condition_cui = getattr(study, "condition_cui", None)
        if condition_cui is not None:
            condition_cuis.add(int(condition_cui))
        condition_object = getattr(study, "condition_object", None)
        if condition_object is not None:
            nested_cui = getattr(condition_object, "condition_cui", None)
            if nested_cui is not None:
                condition_cuis.add(int(nested_cui))

    variant = getattr(obj, "variant_context", None) or getattr(obj, "variant_object", None)
    if variant is not None and variant is not obj:
        condition_cuis.update(_object_condition_cuis(variant))

    return condition_cuis


def _record_matches_phase_condition_context(
    phase: ResolvedBundlePhase,
    record: Any,
) -> bool:
    """Return True when a candidate record is compatible with a phase condition set.

    This deliberately fails open when the record exposes no usable condition
    metadata, because the database query is only meant to generate candidates
    and not every relevant HemOnc row carries explicit condition links.
    """

    if not phase.resolved_condition_cuis:
        return True

    record_condition_cuis = _object_condition_cuis(record)
    if not record_condition_cuis:
        return True

    return not record_condition_cuis.isdisjoint(set(phase.resolved_condition_cuis))


def _filter_phase_records(
    phase: ResolvedBundlePhase,
    records: list[Any],
) -> list[Any]:
    """Apply Python-side phase filtering to coarse SQL candidate results."""

    return [
        record
        for record in records
        if _record_matches_phase_condition_context(phase, record)
    ]


def _requires_shared_study(left: BundlePhaseMatch, right: BundlePhaseMatch) -> bool:
    """Return True when two phase matches must share study provenance."""

    pair = {left.phase_type, right.phase_type}
    if pair != {"systemic", "radiotherapy"}:
        return False

    systemic = left if left.phase_type == "systemic" else right
    radiotherapy = left if left.phase_type == "radiotherapy" else right

    # Adjuvant post-CRT systemic phases are intentionally allowed to come
    # from separate evidence than the CRT backbone.
    if systemic.timing == "post" and radiotherapy.timing == "post":
        return False
    return True


def _phase_combo_is_compatible(phase_combo: tuple[BundlePhaseMatch, ...]) -> bool:
    """Apply lightweight cross-phase compatibility rules to a bundle candidate."""

    for index, left in enumerate(phase_combo):
        for right in phase_combo[index + 1 :]:
            if not _requires_shared_study(left, right):
                continue
            left_studies = _phase_match_study_keys(left)
            right_studies = _phase_match_study_keys(right)
            if left_studies and right_studies and left_studies.isdisjoint(right_studies):
                return False
    return True


def _phase_match_from_variant(phase: ResolvedBundlePhase, variant: Any) -> BundlePhaseMatch:
    """Convert a variant-backed phase candidate into a runtime match object."""

    return BundlePhaseMatch(
        phase_id=phase.phase_id,
        phase_type=phase.phase_type,
        timing=phase.timing,
        source_kind=phase.source_type,
        variant_cui=getattr(variant, "variant_cui", None),
        sigs=list(getattr(variant, "component_sigs", [])),
        label=getattr(variant, "regimen", None) or getattr(variant, "variant", None) or phase.phase_id,
    )


def _phase_match_from_sig(phase: ResolvedBundlePhase, sig: Any) -> BundlePhaseMatch:
    """Convert a sig-backed phase candidate into a runtime match object."""

    return BundlePhaseMatch(
        phase_id=phase.phase_id,
        phase_type=phase.phase_type,
        timing=phase.timing,
        source_kind=phase.source_type,
        variant_cui=getattr(sig, "variant_cui", None),
        sigs=[sig],
        label=getattr(sig, "component", None) or phase.phase_id,
    )


def assemble_bundle_matches(
    resolved_bundle: ResolvedHemOncBundle,
    phase_records: dict[str, list[Any]],
    *,
    enforce_phase_compatibility: bool = True,
) -> list[AssembledTreatmentBundle]:
    """Assemble final bundle candidates from per-phase candidate records."""

    phase_options: list[list[BundlePhaseMatch]] = []
    for phase in resolved_bundle.phases:
        records = phase_records.get(phase.phase_id, [])
        if phase.source_type == "sig_component":
            matches = [_phase_match_from_sig(phase, sig) for sig in records]
        else:
            matches = [_phase_match_from_variant(phase, variant) for variant in records]
        if not matches:
            return []
        phase_options.append(matches)

    bundles: list[AssembledTreatmentBundle] = []
    seen_signatures: set[tuple[Any, ...]] = set()
    for phase_combo in product(*phase_options):
        if enforce_phase_compatibility and not _phase_combo_is_compatible(phase_combo):
            continue
        bundle_phases = [
            AssembledBundlePhase(
                phase_id=phase_match.phase_id,
                phase_type=phase_match.phase_type,
                timing=phase_match.timing,
                source_kind=phase_match.source_kind,
                variant_cui=phase_match.variant_cui,
                sig_ids=[_sig_identifier(sig) for sig in phase_match.sigs],
                component_names=[getattr(sig, "component", None) for sig in phase_match.sigs],
                label=phase_match.label,
            )
            for phase_match in phase_combo
        ]
        signature = tuple(
            (
                phase.phase_id,
                phase.variant_cui,
                tuple(phase.sig_ids),
            )
            for phase in bundle_phases
        )
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        primary_variant_cui = next(
            (phase.variant_cui for phase in bundle_phases if phase.variant_cui is not None),
            None,
        )
        bundles.append(
            AssembledTreatmentBundle(
                recipe_id=resolved_bundle.group_id,
                assembly_mode=resolved_bundle.assembly_mode,
                sequence_template=resolved_bundle.sequence_template,
                variant_cui=primary_variant_cui,
                phases=bundle_phases,
                bundle_name=" + ".join(
                    phase.label for phase in bundle_phases if phase.label
                ) or resolved_bundle.group_id,
            )
        )
    return bundles


def execute_bundle_phase_query(
    session: so.Session,
    phase: ResolvedBundlePhase,
    models: HemOncModels | None = None,
) -> list[Any]:
    """Execute one bundle phase query and apply Python-side phase filtering."""

    stmt = build_bundle_phase_statement(phase, models=models)
    records = session.execute(stmt).unique().scalars().all()
    return _filter_phase_records(phase, records)


def execute_bundle_query(
    session: so.Session,
    resolved_bundle: ResolvedHemOncBundle,
    models: HemOncModels | None = None,
    *,
    enforce_phase_compatibility: bool = True,
) -> list[AssembledTreatmentBundle]:
    """Execute all phases of a resolved bundle recipe and assemble candidates."""

    phase_records = {
        phase.phase_id: execute_bundle_phase_query(session, phase, models=models)
        for phase in resolved_bundle.phases
    }
    return assemble_bundle_matches(
        resolved_bundle,
        phase_records,
        enforce_phase_compatibility=enforce_phase_compatibility,
    )


def execute_bundle_query_for_entry(
    session: so.Session,
    registry: RegimenRegistry,
    entry_id: str,
    models: HemOncModels | None = None,
    *,
    enforce_phase_compatibility: bool = True,
) -> list[AssembledTreatmentBundle]:
    """Resolve and execute a bundle recipe directly from a registry entry id."""

    resolved_bundle = registry.resolve_hemonc_bundle(entry_id)
    if resolved_bundle is None:
        raise ValueError(f"Registry entry '{entry_id}' does not resolve to a HemOnc bundle recipe.")
    return execute_bundle_query(
        session,
        resolved_bundle,
        models=models,
        enforce_phase_compatibility=enforce_phase_compatibility,
    )
