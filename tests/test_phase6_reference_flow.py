"""Phase 6B: freeze the effective NSCLC decision/bridge/assembly/selection flow.

This is the equivalence gate the 2026-08-15 migration code review's Finding F2
called for: the existing suite exercises `decide`, the bridge, `assemble`, and
`select` individually, but nothing ran a representative NSCLC state through
the complete chain and compared the whole result against a frozen reference.
That gap is closed here.

See tests/phase6_reference_flow.py for how the fixture is built and why (fully
documented in its module docstring), and tests/fixtures/nsclc_reference_flow.yaml
for the frozen expected output.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scoop.protocols.spec import RegistrySpec
from scoop.selection.bridge import DeclarativeInterventionBridge, resolve
from scoop.selection.choose import NoSelection, PatientContext, SelectedProtocol, select
from scoop.selection.interventions import InterventionCatalog

from phase6_reference_flow import (
    REFERENCE_SEED,
    build_endpoint_record,
    build_fake_gateway,
    build_reference_flow,
    fake_candidates_for_phase,
    load_nsclc_catalog,
    load_nsclc_registry,
)
from scoop.guidelines.sampling import GuidelineSample, decide
from scoop.protocols.candidates import assemble
from scoop.protocols.hemonc.gateway import FakeHemOncGateway

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "nsclc_reference_flow.yaml"


@pytest.fixture(scope="module")
def golden() -> dict:
    with GOLDEN_PATH.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@pytest.fixture(scope="module")
def live() -> dict:
    return build_reference_flow()


def test_reference_flow_matches_frozen_fixture(live, golden):
    """A diff here means effective decision-flow behavior changed.

    Per F2: this must never be "fixed" by silently regenerating the fixture.
    An intentional diff needs a reviewed, separately-authorized change that
    names the clinical/content decision, per the phased delivery plan's
    Phase 6 equivalence-before-enhancement rule.
    """

    assert live == golden


def test_every_declared_endpoint_is_covered(live):
    registry = load_nsclc_registry()
    catalog = load_nsclc_catalog(registry)
    declared = {spec.intervention_id for spec in catalog.specs.values() if not spec.internal}

    assert declared == set(live["endpoint_ids"])
    assert declared == set(live["endpoints"])


def test_selection_is_reproducible_for_the_reference_seed():
    registry = load_nsclc_registry()
    catalog = load_nsclc_catalog(registry)
    bridge = DeclarativeInterventionBridge(catalog)
    gateway = build_fake_gateway(registry)

    first = build_endpoint_record(
        "port_adjuvant_osimertinib",
        catalog=catalog,
        bridge=bridge,
        registry=registry,
        gateway=gateway,
        seed=REFERENCE_SEED,
    )
    second = build_endpoint_record(
        "port_adjuvant_osimertinib",
        catalog=catalog,
        bridge=bridge,
        registry=registry,
        gateway=gateway,
        seed=REFERENCE_SEED,
    )
    assert first == second


def test_candidate_ordering_is_load_bearing_for_selection():
    """Documents F7: seeded selection is only deterministic over stable order.

    `braf_intervention` has two declared component-list groups (dabrafenib,
    trametinib), so it assembles to two candidates. Reversing their order
    changes which one a fixed seed selects -- confirming that candidate order
    is part of today's effective behavior, not an incidental detail a refactor
    is free to change.
    """

    registry = load_nsclc_registry()
    phase_spec = registry.source_phases["braf_intervention"]
    assert len(phase_spec.component_requirements.component_lists) == 2

    catalog = load_nsclc_catalog(registry)
    bridge = DeclarativeInterventionBridge(catalog)

    forward = fake_candidates_for_phase("braf_intervention", phase_spec)
    reversed_ = list(reversed(forward))
    assert [c.name for c in forward] != [c.name for c in reversed_]

    context = {"condition_occurrence.biomarker": "<fixture:condition_occurrence.biomarker>"}
    decision = decide(
        GuidelineSample(
            values={"treatment_episode.braf_intervention": "braf_intervention", **context},
            source_profile="nsclc",
        )
    )
    resolved = resolve(bridge, decision, PatientContext(context))
    assert resolved.phase_refs[0].phase_id == "braf_intervention"

    from scoop.protocols.spec import ProtocolSpec

    def selected_name(candidates_in_order):
        gateway = FakeHemOncGateway({"braf_intervention": candidates_in_order})
        spec = ProtocolSpec(
            protocol_id="braf_intervention",
            source_phase_ids=["braf_intervention"],
            source_phases={"braf_intervention": phase_spec},
        )
        assembled = assemble(spec, gateway)
        result = select(assembled, PatientContext({}), REFERENCE_SEED)
        assert isinstance(result, SelectedProtocol)
        return result.phases[0].regimen.name

    assert selected_name(forward) != selected_name(reversed_)


def test_a_different_seed_can_change_a_multi_candidate_selection():
    registry = load_nsclc_registry()
    catalog = load_nsclc_catalog(registry)
    bridge = DeclarativeInterventionBridge(catalog)
    gateway = build_fake_gateway(registry)

    outcomes = {
        seed: build_endpoint_record(
            "braf_intervention",
            catalog=catalog,
            bridge=bridge,
            registry=registry,
            gateway=gateway,
            seed=seed,
        )["assembly"]["selection"]["regimens"][0]["regimen_name"]
        for seed in range(50)
    }
    assert len(set(outcomes.values())) > 1


def test_choice_alternatives_are_not_collapsed_to_one():
    """Open question recorded for the domain owner, not resolved here.

    Nothing in the current bridge/selection pipeline picks one alternative of
    a declared `Choice` -- every alternative is independently resolvable. This
    test pins that as today's actual behavior so a refactor that starts
    silently picking one is caught here rather than discovered downstream.
    """

    registry = load_nsclc_registry()
    catalog = load_nsclc_catalog(registry)
    bridge = DeclarativeInterventionBridge(catalog)

    from scoop.guidelines.sampling import GuidelineSample, decide

    decision = decide(
        GuidelineSample(
            values={
                "treatment_episode.met_intervention": "met_intervention",
                "condition_occurrence.biomarker": "<fixture:condition_occurrence.biomarker>",
            },
            source_profile="nsclc",
        )
    )
    resolved = resolve(
        bridge,
        decision,
        PatientContext({"condition_occurrence.biomarker": "<fixture:condition_occurrence.biomarker>"}),
    )
    assert len(resolved.alternatives) == 3
    assert {alt.phase_refs[0].phase_id for alt in resolved.alternatives} == {
        "met_tki",
        "met_ptdb",
        "met_ptdb_w_ici",
    }


def test_non_hemonc_endpoints_never_touch_assembly_or_selection(live):
    for intervention_id in ("surgery_only", "best_supportive_care"):
        record = live["endpoints"][intervention_id]
        assert record["resolved"]["form"] == "non_hemonc"
        assert "assembly" not in record
        assert "alternatives" not in record
        assert "steps" not in record
