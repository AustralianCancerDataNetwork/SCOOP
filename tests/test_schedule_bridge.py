import unittest
from datetime import date
from pathlib import Path

from scoop.patients.treatment_episode import Treatment_Episode
from scoop.protocols.candidates import RegimenCandidate
from scoop.protocols.hemonc.gateway import FakeHemOncGateway
from scoop.protocols.spec import RegistrySpec
from scoop.schedule.bridge import resolve_schedule_for_treatment
from scoop.schedule.rollout import ProtocolSchedule
from scoop.vocabulary.database import get_omop_engine
from test_schedule_rollout import StubSignature

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
    fields = dict(
        person_id=1,
        condition_occurrence_id=1,
        regimen_type="test",
        regimen_category_id="test",
        bundle_recipe_id=None,
        bundle_phase_summary=[],
        decision_context={},
    )
    fields.update(overrides)
    return Treatment_Episode(**fields)


class ScheduleBridgeUnitTests(unittest.TestCase):
    """Deterministic tests against FakeHemOncGateway -- no live DB needed."""

    @classmethod
    def setUpClass(cls):
        cls.registry = RegistrySpec.from_path(REGISTRY_PATH)

    def test_non_hemonc_resolution_returns_none_without_querying(self):
        # No bundle_recipe_id, no bundle_phase_summary -- e.g. surgery_only.
        treatment = _treatment_episode()
        gateway = FakeHemOncGateway(candidates={})

        result = resolve_schedule_for_treatment(
            treatment, self.registry, gateway,
            start_date=date(2021, 1, 1), seed=1,
        )

        self.assertIsNone(result)
        self.assertEqual(gateway.calls, [])

    def test_protocolref_with_full_coverage_produces_a_schedule(self):
        # port's resolution is `protocol: surgery_then_port`, which needs
        # both "surgery_only" and "port" phases to have candidates.
        treatment = _treatment_episode(bundle_recipe_id="surgery_then_port")
        gateway = FakeHemOncGateway(candidates={
            "surgery_only": [_candidate("Surgery", phase="surgery_only", component="Surgery")],
            "port": [_candidate("PORT", phase="port", component="External beam radiotherapy")],
        })

        result = resolve_schedule_for_treatment(
            treatment, self.registry, gateway,
            start_date=date(2021, 1, 1), seed=1,
        )

        self.assertIsInstance(result, ProtocolSchedule)

    def test_protocolref_missing_registry_entry_returns_none(self):
        treatment = _treatment_episode(bundle_recipe_id="not_a_real_protocol")
        gateway = FakeHemOncGateway(candidates={})

        result = resolve_schedule_for_treatment(
            treatment, self.registry, gateway,
            start_date=date(2021, 1, 1), seed=1,
        )

        self.assertIsNone(result)
        self.assertEqual(gateway.calls, [])

    def test_phaseref_with_coverage_produces_a_schedule(self):
        # sbrt's resolution is `phase: {id: definitive_rt, technique: sbrt}`.
        treatment = _treatment_episode(
            bundle_phase_summary=[{"phase_id": "definitive_rt", "source_type": "phaseref"}],
        )
        gateway = FakeHemOncGateway(candidates={
            "definitive_rt": [_candidate("SBRT", phase="definitive_rt", component="External beam radiotherapy")],
        })

        result = resolve_schedule_for_treatment(
            treatment, self.registry, gateway,
            start_date=date(2021, 1, 1), seed=1,
        )

        self.assertIsInstance(result, ProtocolSchedule)

    def test_phaseref_with_no_coverage_returns_none(self):
        treatment = _treatment_episode(
            bundle_phase_summary=[{"phase_id": "definitive_rt", "source_type": "phaseref"}],
        )
        gateway = FakeHemOncGateway(candidates={})

        result = resolve_schedule_for_treatment(
            treatment, self.registry, gateway,
            start_date=date(2021, 1, 1), seed=1,
        )

        self.assertIsNone(result)

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

        result = resolve_schedule_for_treatment(
            treatment, self.registry, gateway,
            start_date=date(2021, 1, 1), seed=1,
        )

        self.assertIsInstance(result, ProtocolSchedule)
        self.assertIn("ici_monotherapy", gateway.calls)
        self.assertIn("ici_w_chemo_nonsquamous", gateway.calls)

    def test_choice_with_no_alternative_covered_returns_none(self):
        treatment = _treatment_episode(
            bundle_phase_summary=[
                {"phase_id": "ici_monotherapy", "source_type": "phaseref"},
                {"phase_id": "ici_w_chemo_nonsquamous", "source_type": "phaseref"},
            ],
        )
        gateway = FakeHemOncGateway(candidates={})

        result = resolve_schedule_for_treatment(
            treatment, self.registry, gateway,
            start_date=date(2021, 1, 1), seed=1,
        )

        self.assertIsNone(result)


class ScheduleBridgeLiveHemOncTests(unittest.TestCase):
    """Integration tests against the real HemOnc database.

    Skips cleanly (not a failure) when that database isn't reachable.

    Deliberately shares one Session/SqlHemOncGateway across every test
    (set up once in setUpClass) rather than giving each test its own --
    reusing a gateway across calls is exactly what this suite guards
    against regressing (see SqlHemOncGateway._freeze_sig).
    """

    @classmethod
    def setUpClass(cls):
        try:
            with get_omop_engine().connect():
                pass
        except Exception as exc:  # noqa: BLE001 - reported verbatim in the skip reason
            raise unittest.SkipTest(
                f"No database reachable ({type(exc).__name__}: {exc}); this "
                "check needs both the OMOP vocabulary and HemOnc source "
                "databases to be up."
            )
        try:
            from sqlalchemy.orm import Session
            from scoop.config import get_hemonc_context, create_hemonc_engine
            from scoop.protocols.hemonc.gateway import SqlHemOncGateway

            _, resolved = get_hemonc_context()
            engine = create_hemonc_engine(resolved)
            cls.session = Session(engine)
            cls.gateway = SqlHemOncGateway(session=cls.session)
        except Exception as exc:  # noqa: BLE001 - reported verbatim in the skip reason
            raise unittest.SkipTest(
                f"No HemOnc database reachable ({type(exc).__name__}: {exc})."
            )
        cls.registry = RegistrySpec.from_path(REGISTRY_PATH)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "session"):
            cls.session.close()

    def test_non_hemonc_resolution_returns_none(self):
        treatment = _treatment_episode()

        result = resolve_schedule_for_treatment(
            treatment, self.registry, self.gateway,
            start_date=date(2021, 1, 1), seed=1,
        )

        self.assertIsNone(result)

    def test_partial_hemonc_coverage_returns_none_without_raising(self):
        # adjuvant_cisplatin_osimertinib needs both adjuvant_cisplatin_based
        # (real HemOnc coverage, confirmed separately) and
        # adjuvant_osimertinib (no candidates today) -- the whole protocol
        # should come back as no schedule, not an exception.
        treatment = _treatment_episode(bundle_recipe_id="adjuvant_cisplatin_osimertinib")

        result = resolve_schedule_for_treatment(
            treatment, self.registry, self.gateway,
            start_date=date(2021, 1, 1), seed=1,
        )

        self.assertIsNone(result)

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
            if result is not None:
                schedules.append((phase_id, result))

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
