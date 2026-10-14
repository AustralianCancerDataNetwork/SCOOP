import json
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from live_db import LiveDatabaseTestCase
from test_schedule_rollout import StubSignature

from scoop.patients.treatment_episode import Treatment_Episode
from scoop.protocols.candidates import RegimenCandidate
from scoop.protocols.hemonc.gateway import FakeHemOncGateway
from scoop.protocols.spec import ProtocolSpec, RegistrySpec
from scoop.schedule.bridge import resolve_schedule_for_treatment
from scoop.schedule.rollout import (
    ProtocolSchedule,
    ScheduleConfig,
    SystemicAdministration,
)

REGISTRY_PATH = (
    Path(__file__).resolve().parents[1]
    / "scoop" / "profiles" / "nsclc" / "nsclc_protocol_registry.yaml"
)


def _candidate(name: str, *, phase: str, component: str) -> RegimenCandidate:
    # `schedule()` needs real schedulable signature fields (dose, route,
    # cycle timing, ...), not just a name -- StubSignature (from
    # test_schedule_rollout.py) already provides a minimal valid one rather
    # than reinventing that fixture here.
    signature = StubSignature(component)
    return RegimenCandidate(
        name=name,
        variant_cui=1,
        sigs=[signature],
        regimen=name,
        components={component},
        phase=phase,
        source_phase_id=phase,
    )


def _treatment_episode(**overrides) -> Treatment_Episode:
    fields = {
        "person_id": 1,
        "condition_occurrence_id": 1,
        "regimen_type": "test",
        "regimen_category_id": "test",
        "bundle_recipe_id": None,
        "bundle_phase_summary": [],
        "decision_context": {},
    }
    fields.update(overrides)
    return Treatment_Episode(**fields)


def _registry_requiring_morphology(registry: RegistrySpec, phase_id: str) -> RegistrySpec:
    protocol = ProtocolSpec.generate(
        phase_id,
        {
            "protocol_id": phase_id,
            "source_phase_ids": [phase_id],
            "selection_requires": ["condition_occurrence.morphology"],
        },
        source_phases=registry.source_phases,
    )
    return registry.model_copy(
        update={"protocols": {**registry.protocols, phase_id: protocol}}
    )


def _run_nsclc_candidate(candidate, registry, phase_id, decision_context):
    gateway = FakeHemOncGateway(candidates={phase_id: [candidate]})
    treatment = _treatment_episode(
        bundle_recipe_id=phase_id,
        decision_context=decision_context,
    )
    result = resolve_schedule_for_treatment(
        treatment,
        registry,
        gateway,
        start_date=date(2026, 1, 1),
        seed=11,
        config=ScheduleConfig(max_cycles=3),
    )
    if result.schedule is not None:
        treatment.attach_schedule(result.schedule)
    return result, treatment.roll_events(), gateway.calls


class ScheduleBridgeUnitTests(unittest.TestCase):
    """Deterministic tests against FakeHemOncGateway -- no live DB needed."""

    @classmethod
    def setUpClass(cls):
        cls.registry = RegistrySpec.from_path(REGISTRY_PATH)

    def resolve(self, treatment, gateway):
        return resolve_schedule_for_treatment(
            treatment,
            self.registry,
            gateway,
            start_date=date(2021, 1, 1),
            seed=1,
        )

    def test_non_hemonc_resolution_is_explicit_without_querying(self):
        # No bundle_recipe_id, no bundle_phase_summary -- e.g. surgery_only.
        treatment = _treatment_episode(registry_resolver_type="nonhemonc")
        gateway = FakeHemOncGateway(candidates={})

        result = self.resolve(treatment, gateway)

        self.assertEqual(result.status, "non_hemonc")
        self.assertEqual(gateway.calls, [])

    def test_protocolref_with_full_coverage_produces_a_schedule(self):
        # port's resolution is `protocol: surgery_then_port`, which needs
        # both "surgery_only" and "port" phases to have candidates.
        treatment = _treatment_episode(bundle_recipe_id="surgery_then_port")
        gateway = FakeHemOncGateway(candidates={
            "surgery_only": [_candidate("Surgery", phase="surgery_only", component="Surgery")],
            "port": [_candidate("PORT", phase="port", component="External beam radiotherapy")],
        })

        result = self.resolve(treatment, gateway)

        self.assertIsInstance(result.schedule, ProtocolSchedule)

    def test_phaseref_coverage_controls_schedule_resolution(self):
        treatment = _treatment_episode(
            bundle_phase_summary=[{"phase_id": "definitive_rt", "source_type": "phaseref"}],
        )
        scenarios = (
            ("uncovered", {}, "no_candidates"),
            (
                "covered",
                {
                    "definitive_rt": [
                        _candidate(
                            "SBRT",
                            phase="definitive_rt",
                            component="External beam radiotherapy",
                        )
                    ]
                },
                "scheduled",
            ),
        )
        for name, candidates, expected_status in scenarios:
            with self.subTest(name=name):
                result = self.resolve(
                    treatment, FakeHemOncGateway(candidates=candidates)
                )
                self.assertEqual(result.status, expected_status)
                self.assertEqual(result.schedule is not None, expected_status == "scheduled")

    def test_choice_falls_through_to_the_first_alternative_with_coverage(self):
        # ici_monotherapy_or_ici_with_chemotherapy_nonsquamous is a Choice
        # between ici_monotherapy and ici_w_chemo_nonsquamous. Only the
        # second has candidates here -- the adapter should skip the first
        # (NoSelection) and succeed on the second, not give up.
        treatment = _treatment_episode(
            bundle_phase_summary=[
                {"phase_id": "ici_monotherapy", "source_type": "phaseref"},
                {"phase_id": "ici_w_chemo_nonsquamous", "source_type": "phaseref"},
            ],
        )
        gateway = FakeHemOncGateway(candidates={
            "ici_w_chemo_nonsquamous": [
                _candidate("Pembrolizumab + chemo", phase="ici_w_chemo_nonsquamous", component="Pembrolizumab"),
            ],
        })

        result = self.resolve(treatment, gateway)

        self.assertIsInstance(result.schedule, ProtocolSchedule)
        self.assertIn("ici_monotherapy", gateway.calls)
        self.assertIn("ici_w_chemo_nonsquamous", gateway.calls)

    def test_uncovered_protocol_references_report_no_candidates(self):
        scenarios = (
            (
                "unknown protocol",
                _treatment_episode(bundle_recipe_id="not_a_real_protocol"),
                [],
            ),
            (
                "uncovered choice",
                _treatment_episode(
                    bundle_phase_summary=[
                        {"phase_id": "ici_monotherapy", "source_type": "phaseref"},
                        {"phase_id": "ici_w_chemo_nonsquamous", "source_type": "phaseref"},
                    ]
                ),
                ["ici_monotherapy", "ici_w_chemo_nonsquamous"],
            ),
        )
        for name, treatment, expected_calls in scenarios:
            with self.subTest(name=name):
                gateway = FakeHemOncGateway(candidates={})
                result = self.resolve(treatment, gateway)
                self.assertEqual(result.status, "no_candidates")
                self.assertEqual(gateway.calls, expected_calls)


def test_schedule_bridge_returns_dated_events_for_a_source_day_choice():
    registry = RegistrySpec.from_path(REGISTRY_PATH)
    candidate = _candidate(
        "SBRT", phase="definitive_rt", component="External beam radiotherapy"
    )
    candidate.sigs[0].alldays = "1,8|9"
    treatment = _treatment_episode(
        bundle_phase_summary=[
            {"phase_id": "definitive_rt", "source_type": "phaseref"}
        ],
    )
    gateway = FakeHemOncGateway(candidates={"definitive_rt": [candidate]})

    result = resolve_schedule_for_treatment(
        treatment,
        registry,
        gateway,
        start_date=date(2026, 1, 1),
        seed=11,
        config=ScheduleConfig(max_cycles=1),
    )

    assert isinstance(result.schedule, ProtocolSchedule)
    assert len(result.schedule.administrations) == 2
    assert result.schedule.administrations[0].date == date(2026, 1, 1)
    assert result.schedule.administrations[1].date in {date(2026, 1, 8), date(2026, 1, 9)}


def test_case_builder_attaches_explicit_schedule_outcome(monkeypatch):
    import scoop.patients.build as builder

    registry = RegistrySpec.from_path(REGISTRY_PATH)
    person = SimpleNamespace(person_id=7, condition=None)
    condition = SimpleNamespace(
        person_id=7,
        condition_occurrence_id=9,
        condition_start_date=date(2026, 1, 1),
        regimen=None,
    )
    treatment = _treatment_episode(registry_resolver_type="nonhemonc")
    gateway = FakeHemOncGateway()
    monkeypatch.setattr(builder, "build_person", lambda *args, **kwargs: person)
    monkeypatch.setattr(
        builder,
        "build_condition_occurrence",
        lambda *args, **kwargs: condition,
    )
    monkeypatch.setattr(builder, "assign_regimen", lambda *args, **kwargs: treatment)
    monkeypatch.setattr(
        builder,
        "get_active_simulation_context",
        lambda: SimpleNamespace(registry_spec=registry),
    )

    case = builder.build_case_bundle(
        {"fixture": True},
        schedule_options=builder.ScheduleBuildOptions(gateway=gateway, seed=19),
    )

    assert case.treatment_episode.schedule_status == "non_hemonc"
    assert case.treatment_episode.schedule_seed == 19
    assert gateway.calls == []


class ScheduleBridgeLiveHemOncTests(LiveDatabaseTestCase):
    """Integration tests against the real HemOnc database.

    Skips cleanly (not a failure) when that database isn't reachable.

    Deliberately shares one Session/SqlHemOncGateway across every test
    (set up once in setUpClass) rather than giving each test its own --
    reusing a gateway across calls is exactly what this suite guards
    against regressing (see SqlHemOncGateway._freeze_sig).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.registry = RegistrySpec.from_path(REGISTRY_PATH)

    def test_live_protocol_without_full_phase_coverage_has_no_candidates(self):
        treatment = _treatment_episode(bundle_recipe_id="adjuvant_cisplatin_osimertinib")

        result = resolve_schedule_for_treatment(
            treatment, self.registry, self.gateway,
            start_date=date(2021, 1, 1), seed=1,
        )

        self.assertEqual(result.status, "no_candidates")
        self.assertIsNone(result.schedule)

    def test_at_least_one_real_nsclc_phase_schedules_successfully(self):
        # HemOnc coverage per phase can drift, so try several rather than
        # hardcoding one that's covered today.
        candidate_phase_ids = [
            "platinum_doublet_3rd_gen_agent",
            "platinum_doublet_nonsquamous",
            "ici_monotherapy",
            "adjuvant_cisplatin_based",
        ]
        schedules = []
        for phase_id in candidate_phase_ids:
            treatment = _treatment_episode(
                bundle_phase_summary=[{"phase_id": phase_id, "source_type": "phaseref"}],
            )
            result = resolve_schedule_for_treatment(
                treatment, self.registry, self.gateway,
                start_date=date(2021, 1, 1), seed=1,
            )
            if result.schedule is not None:
                schedules.append((phase_id, result.schedule))

        self.assertTrue(
            schedules,
            f"expected at least one of {candidate_phase_ids} to have real "
            "HemOnc coverage and produce a schedule; none did",
        )
        phase_id, schedule_result = schedules[0]
        self.assertTrue(schedule_result.administrations)

    def test_many_sequential_calls_through_one_shared_gateway_do_not_corrupt(self):
        phase_ids = [
            "platinum_doublet_3rd_gen_agent",
            "platinum_doublet_nonsquamous",
            "ici_monotherapy",
            "adjuvant_cisplatin_based",
            "adjuvant_osimertinib",
            "ici_w_chemo_nonsquamous",
            "ici_w_chemo_squamous",
            "adjuvant_cisplatin_based",  # deliberately repeated
        ]
        for phase_id in phase_ids:
            treatment = _treatment_episode(
                bundle_phase_summary=[{"phase_id": phase_id, "source_type": "phaseref"}],
            )
            # Not asserting a particular outcome here (some of these phases
            # have no HemOnc coverage) -- only that none of them raise.
            resolve_schedule_for_treatment(
                treatment, self.registry, self.gateway,
                start_date=date(2021, 1, 1), seed=1,
            )


def test_real_nsclc_candidate_snapshot_through_registry_bridge():
    fixture = json.loads(
        (
            Path(__file__).parent / "fixtures/nsclc_pembrolizumab_schedule.json"
        ).read_text()
    )
    payload = fixture["candidate"]
    payload["sigs"] = [SimpleNamespace(**sig) for sig in payload["sigs"]]
    candidate = RegimenCandidate.model_validate(payload)
    phase_id = fixture["phase_id"]
    registry = RegistrySpec.from_path(REGISTRY_PATH)
    gateway = FakeHemOncGateway(candidates={phase_id: [candidate]})
    treatment = _treatment_episode(
        bundle_phase_summary=[{"phase_id": phase_id, "source_type": "phaseref"}]
    )
    result = resolve_schedule_for_treatment(
        treatment,
        registry,
        gateway,
        start_date=date(2026, 1, 1),
        seed=11,
        config=ScheduleConfig(max_cycles=3),
    )
    assert result.schedule.selected_protocol.phases[0].regimen.variant_cui == 133537
    assert [a.date for a in result.schedule.administrations] == [
        date(2026, 1, 1),
        date(2026, 1, 22),
        date(2026, 2, 12),
    ]
    assert [a.cycle_number for a in result.schedule.administrations] == [1, 2, 3]
    assert all(
        isinstance(a, SystemicAdministration)
        and a.source_sig_id == 20450
        and a.dose == 200
        for a in result.schedule.administrations
    )
    assert gateway.calls == [phase_id]


def test_nsclc_selected_treatment_emits_reproducible_context_events():
    fixture = json.loads(
        (
            Path(__file__).parent / "fixtures/nsclc_pembrolizumab_schedule.json"
        ).read_text()
    )
    payload = fixture["candidate"]
    payload["sigs"] = [SimpleNamespace(**sig) for sig in payload["sigs"]]
    candidate = RegimenCandidate.model_validate(payload)
    phase_id = fixture["phase_id"]
    registry = _registry_requiring_morphology(
        RegistrySpec.from_path(REGISTRY_PATH), phase_id
    )
    missing_context = _run_nsclc_candidate(candidate, registry, phase_id, {})
    first = _run_nsclc_candidate(
        candidate,
        registry,
        phase_id,
        {"condition_occurrence.morphology": "nonsquamous"},
    )
    second = _run_nsclc_candidate(
        candidate,
        registry,
        phase_id,
        {"condition_occurrence.morphology": "nonsquamous"},
    )
    result, (drugs, procedures), gateway_calls = first
    assert missing_context[0].status == "no_selection"
    assert result.schedule is not None
    selected_candidate = result.schedule.selected_protocol.phases[0].regimen
    signature = selected_candidate.sigs[0]
    assert first == second
    assert gateway_calls == [phase_id]
    assert not procedures
    assert len(drugs) == 3
    assert {event["drug_source_value"] for event in drugs} == {signature.component}
    assert {event["source_sig_id"] for event in drugs} == {signature.id}
    assert {event["component_hemonc_cui"] for event in drugs} == {
        signature.component_cui
    }
    assert all(event["dose"] == 200 for event in drugs)
    assert all(
        not key.endswith("_concept_id")
        for event in drugs + procedures
        for key in event
    )
