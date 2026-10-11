"""Builder for the Phase 6B NSCLC decision-flow reference fixture.

This module is test infrastructure, not a test module itself (no ``test_``
prefix). It walks the *current* NSCLC decision -> bridge -> assembly ->
selection pipeline for every declared graph endpoint and produces one frozen,
plain-data snapshot per endpoint. `tests/test_phase6_reference_flow.py` diffs a
freshly built snapshot against the checked-in golden file
(`tests/fixtures/nsclc_reference_flow.yaml`), so an unreviewed behavioral
change anywhere in the chain shows up as a failing test with a readable diff,
per Finding F2 of `_design/migration_code_review_2026-08-15.md`.

Design decisions recorded here, not silently assumed:

- Explicit state, not sampling. Every fixture patient state is an explicit
  ``GuidelineSample`` constructed directly (one active `treatment_episode.*`
  endpoint plus placeholder values for every context field the endpoint's
  intervention declares as `requires`). No transformation graph is sampled.
  This is deliberate: `resolve()`/`select()` currently branch only on field
  *presence*, never on field *value* (see the migration review's F2/6B note
  on `selection_requires`), so a placeholder string is sufficient to exercise
  every code path without inventing a clinical interpretation of what value
  a field like `condition_occurrence.biomarker` should hold.
- Fake HemOnc rows are derived mechanically from each source phase's own
  declared `component_requirements.component_lists` in the registry: one
  synthetic `RegimenCandidate` per declared inner list, with that
  candidate's `components` populated from each term's display label. Nothing is
  invented; a phase with N declared component-list groups gets exactly N
  fake candidates, and a phase with zero declared groups gets exactly one
  placeholder candidate with no components, clearly named
  `<phase_id>::no-declared-components` so it reads as a structural stand-in
  rather than a clinical fact.
- A `Choice` resolution is not collapsed to one alternative. Nothing in the
  current pipeline selects among Choice alternatives (this is itself an open
  question -- see the accompanying report), so every alternative is resolved,
  assembled, and (independently) run through `select()`, and all of them are
  recorded.
- `Recipe` steps are resolved and assembled per step (each step's own
  `PhaseRef`/`ProtocolRef`), but no timeline/rollout semantics are invented;
  that is explicitly out of scope until Phase 7 per the phased delivery plan.
- `NonHemOnc` endpoints are recorded as-is and are not run through
  `assemble()`/`select()` at all, per BRG-9: they never touch the HemOnc
  gateway by design.
- One fixed seed is used everywhere selection happens, so the frozen
  selection outcome is reproducible byte-for-byte on every run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scoop.guidelines.sampling import (
    AmbiguousDecision,
    GuidelineSample,
    NoDecision,
    TreatmentDecision,
    decide,
)
from scoop.protocols.candidates import (
    NoCandidates,
    OptionalPhaseOmitted,
    ProtocolCandidate,
    RegimenCandidate,
    SharedComponent,
    assemble,
)
from scoop.protocols.hemonc.gateway import FakeHemOncGateway
from scoop.protocols.spec import (
    PhaseSpec,
    ProtocolSpec,
    RegistrySpec,
    component_term_label,
)
from scoop.selection.bridge import DeclarativeInterventionBridge, resolve
from scoop.selection.choose import NoSelection, PatientContext, SelectedProtocol, select
from scoop.selection.interventions import (
    Choice,
    InterventionCatalog,
    NonHemOnc,
    PhaseRef,
    ProtocolRef,
    Recipe,
    ResolvedIntervention,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
NSCLC_DIR = REPO_ROOT / "scoop" / "profiles" / "nsclc"
REGISTRY_PATH = NSCLC_DIR / "nsclc_protocol_registry.yaml"
INTERVENTIONS_PATH = NSCLC_DIR / "nsclc_interventions.yaml"

# Fixed across the whole matrix so every selection outcome is reproducible.
# Value carries no meaning beyond "recorded once and never changed casually";
# changing it is itself a fixture-invalidating action and must be deliberate.
REFERENCE_SEED = 20260815


def load_nsclc_registry() -> RegistrySpec:
    return RegistrySpec.from_path(REGISTRY_PATH)


def load_nsclc_catalog(registry: RegistrySpec) -> InterventionCatalog:
    # Mirrors scoop.profiles.manifest.ProfileLoader.load(): the catalog is
    # loaded with the registry's own protocols so ProtocolRef resolution and
    # reference validation see the real ProtocolSpec objects, not empty ones.
    return InterventionCatalog.from_path(INTERVENTIONS_PATH, protocols=registry.protocols)


def fake_candidates_for_phase(phase_id: str, phase_spec: PhaseSpec) -> list[RegimenCandidate]:
    """Derive deterministic fake HemOnc rows straight from the declared spec."""

    groups = phase_spec.component_requirements.component_lists
    if not groups:
        return [
            RegimenCandidate(
                name=f"{phase_id}::no-declared-components",
                variant_cui=9000,
                sigs=[f"{phase_id}-sig-placeholder"],
                regimen=phase_id,
                components=set(),
                phase=phase_id,
                study="fixture-study",
                variant="placeholder",
                source_phase_id=phase_id,
            )
        ]
    return [
        RegimenCandidate(
            name=f"{phase_id}::group{index}",
            variant_cui=9000 + index,
            sigs=[f"{phase_id}-sig-{index}"],
            regimen=phase_id,
            components=sorted(component_term_label(term) for term in group),
            phase=phase_id,
            study="fixture-study",
            variant=f"group{index}",
            source_phase_id=phase_id,
        )
        for index, group in enumerate(groups, start=1)
    ]


def build_fake_gateway(registry: RegistrySpec) -> FakeHemOncGateway:
    return FakeHemOncGateway(
        {
            phase_id: fake_candidates_for_phase(phase_id, phase_spec)
            for phase_id, phase_spec in registry.source_phases.items()
        }
    )


def _summarize_reference(reference: PhaseRef | ProtocolRef) -> dict[str, Any]:
    if isinstance(reference, PhaseRef):
        return {"kind": "phase", "phase_id": reference.phase_id, "technique": reference.technique}
    return {"kind": "protocol", "protocol_id": reference.protocol_id}


def summarize_resolved(resolved: ResolvedIntervention) -> dict[str, Any]:
    if resolved.non_hemonc is not None:
        non_hemonc = resolved.non_hemonc
        return {
            "form": "non_hemonc",
            "label": non_hemonc.label,
            "concept_domain": non_hemonc.concept_domain,
            "concept_value": non_hemonc.concept_value,
        }
    if resolved.recipe is not None:
        recipe = resolved.recipe
        return {
            "form": "recipe",
            "sequence_template": recipe.sequence_template.value if recipe.sequence_template else None,
            "steps": [
                {
                    "timing": step.timing.value if step.timing else None,
                    "reference": _summarize_reference(step.reference),
                }
                for step in recipe.steps
            ],
        }
    if resolved.alternatives:
        return {
            "form": "choice",
            "alternatives": [summarize_resolved(alt) for alt in resolved.alternatives],
        }
    if resolved.protocols:
        return {"form": "protocol", "protocol_ids": [p.protocol_id for p in resolved.protocols]}
    if resolved.phase_refs:
        return {
            "form": "phase",
            "phase_ids": [ref.phase_id for ref in resolved.phase_refs],
            "technique": resolved.phase_refs[0].technique,
        }
    raise AssertionError(f"resolved intervention {resolved.intervention_id!r} has no recognized form")


def summarize_fan_out(reason: SharedComponent | OptionalPhaseOmitted | None) -> Any:
    if reason is None:
        return None
    if isinstance(reason, SharedComponent):
        return {"shared_component": reason.component}
    return {"optional_phase_omitted": reason.phase_id}


def summarize_candidates(candidates: list[ProtocolCandidate]) -> list[dict[str, Any]]:
    return [
        {
            "name": candidate.name,
            "fan_out_reason": summarize_fan_out(candidate.fan_out_reason),
            "phases": [
                {
                    "index": phase.index,
                    "label": phase.label,
                    "conditions": phase.conditions,
                    "no_candidates": phase.no_candidates.phase_id if phase.no_candidates else None,
                    "options": [
                        {
                            "name": option.name,
                            "components": sorted(option.components),
                            "source_phase_id": option.source_phase_id,
                            "variant": option.variant,
                        }
                        for option in phase.options
                    ],
                }
                for phase in candidate.phases
            ],
        }
        for candidate in candidates
    ]


def summarize_selection(result: SelectedProtocol | NoSelection) -> dict[str, Any]:
    if isinstance(result, NoSelection):
        return {"outcome": "no_selection", "reason": result.reason}
    return {
        "outcome": "selected",
        "protocol_id": result.protocol_id,
        "regimens": [
            {"phase_id": regimen.phase_id, "regimen_name": regimen.regimen.name}
            for regimen in result.phases
        ],
    }


def assemble_and_select(
    reference: PhaseRef | ProtocolRef,
    *,
    registry: RegistrySpec,
    gateway: FakeHemOncGateway,
    context: PatientContext,
    seed: int,
) -> dict[str, Any]:
    if isinstance(reference, PhaseRef):
        phase_spec = registry.source_phases[reference.phase_id]
        protocol_spec = ProtocolSpec(
            protocol_id=reference.phase_id,
            source_phase_ids=[reference.phase_id],
            source_phases={reference.phase_id: phase_spec},
        )
        target = reference.phase_id
    else:
        protocol_spec = registry.protocols[reference.protocol_id]
        target = reference.protocol_id

    candidates = assemble(protocol_spec, gateway)
    return {
        "target": target,
        "candidates": summarize_candidates(candidates),
        "selection": summarize_selection(select(candidates, context, seed)),
    }


def _walk(
    resolved: ResolvedIntervention,
    *,
    registry: RegistrySpec,
    gateway: FakeHemOncGateway,
    context: PatientContext,
    seed: int,
) -> dict[str, Any]:
    record: dict[str, Any] = {"resolved": summarize_resolved(resolved)}
    if resolved.non_hemonc is not None:
        return record
    if resolved.alternatives:
        record["alternatives"] = [
            _walk(alt, registry=registry, gateway=gateway, context=context, seed=seed)
            for alt in resolved.alternatives
        ]
        return record
    if resolved.recipe is not None:
        record["steps"] = [
            assemble_and_select(
                step.reference, registry=registry, gateway=gateway, context=context, seed=seed
            )
            for step in resolved.recipe.steps
        ]
        return record
    if resolved.protocols:
        reference = ProtocolRef(resolved.protocols[0].protocol_id)
    elif resolved.phase_refs:
        reference = resolved.phase_refs[0]
    else:
        raise AssertionError(f"resolved intervention {resolved.intervention_id!r} has no recognized form")
    record["assembly"] = assemble_and_select(
        reference, registry=registry, gateway=gateway, context=context, seed=seed
    )
    return record


def build_endpoint_record(
    intervention_id: str,
    *,
    catalog: InterventionCatalog,
    bridge: DeclarativeInterventionBridge,
    registry: RegistrySpec,
    gateway: FakeHemOncGateway,
    seed: int,
) -> dict[str, Any]:
    spec = catalog.specs[intervention_id]
    context_values = {field: f"<fixture:{field}>" for field in spec.requires}
    sample = GuidelineSample(
        values={
            f"treatment_episode.{intervention_id}": intervention_id,
            **context_values,
        },
        source_profile="nsclc",
        state_node_exemptions=catalog.state_node_exemptions,
    )
    decision = decide(sample)
    assert isinstance(decision, TreatmentDecision)
    context = PatientContext(context_values)
    resolved = resolve(bridge, decision, context)
    record = {
        "decision": {
            "decision_id": decision.decision_id,
            "graph_endpoint": decision.graph_endpoint,
        },
        **_walk(resolved, registry=registry, gateway=gateway, context=context, seed=seed),
    }
    return record


def build_no_decision_case() -> dict[str, Any]:
    sample = GuidelineSample(values={}, source_profile="nsclc")
    result = decide(sample)
    assert isinstance(result, NoDecision)
    return {"outcome": "no_decision", "reason": result.reason}


def build_ambiguous_decision_case(endpoint_a: str, endpoint_b: str) -> dict[str, Any]:
    sample = GuidelineSample(
        values={
            f"treatment_episode.{endpoint_a}": endpoint_a,
            f"treatment_episode.{endpoint_b}": endpoint_b,
        },
        source_profile="nsclc",
    )
    try:
        decide(sample)
    except AmbiguousDecision as exc:
        return {"outcome": "ambiguous", "endpoints": list(exc.endpoints)}
    raise AssertionError("expected AmbiguousDecision")


def build_state_node_exemption_case(catalog: InterventionCatalog, real_endpoint: str) -> dict[str, Any]:
    """A declared state-node exemption must not be counted as a second decision."""

    exempted = next(iter(catalog.state_node_exemptions))
    sample = GuidelineSample(
        values={
            f"treatment_episode.{real_endpoint}": real_endpoint,
            f"treatment_episode.{exempted}": "state-value",
        },
        source_profile="nsclc",
        state_node_exemptions=catalog.state_node_exemptions,
    )
    decision = decide(sample)
    assert isinstance(decision, TreatmentDecision)
    return {
        "outcome": "decided_despite_exempted_state_node",
        "exempted_node": exempted,
        "decision_id": decision.decision_id,
    }


def build_reference_flow() -> dict[str, Any]:
    registry = load_nsclc_registry()
    catalog = load_nsclc_catalog(registry)
    bridge = DeclarativeInterventionBridge(catalog)
    gateway = build_fake_gateway(registry)

    endpoint_ids = sorted(
        spec.intervention_id for spec in catalog.specs.values() if not spec.internal
    )
    endpoints = {
        intervention_id: build_endpoint_record(
            intervention_id,
            catalog=catalog,
            bridge=bridge,
            registry=registry,
            gateway=gateway,
            seed=REFERENCE_SEED,
        )
        for intervention_id in endpoint_ids
    }

    special_cases = {
        "no_decision": build_no_decision_case(),
        "ambiguous_decision": build_ambiguous_decision_case(endpoint_ids[0], endpoint_ids[1]),
        "state_node_exemption": build_state_node_exemption_case(catalog, endpoint_ids[0]),
    }

    return {
        "seed": REFERENCE_SEED,
        "endpoint_ids": endpoint_ids,
        "endpoints": endpoints,
        "special_cases": special_cases,
    }
