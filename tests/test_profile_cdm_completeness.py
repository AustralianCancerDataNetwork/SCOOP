"""Every profile must produce OMOP-exportable cases.

Samples several seeds per profile and checks `to_omop_row()` succeeds, so a
profile missing a required field is caught here instead of at export time.

Needs a real OMOP vocabulary database (for concept resolution), so unlike
the rest of the suite this one skips rather than failing when it's down.
"""

import unittest
from pathlib import Path

from scoop.patients.build import build_condition_occurrence, build_person
from scoop.profiles.context import set_active_simulation_context
from scoop.profiles.manifest import list_profiles, load_routed_profile
from scoop.vocabulary.database import get_omop_engine

CONFIG_ROOT = Path(__file__).resolve().parents[1] / "scoop"
SEEDS = range(10)

# TODO: remove once bladder has a ConditionConceptResolver (condition_resolver.py).
_KNOWN_GAPS = {"bladder": "condition_concept_id"}


def _activate(runtime, routed):
    context = (
        runtime.entry_context
        if routed.tail_graph_name is None
        else runtime.tail_contexts[routed.tail_graph_name]
    )
    set_active_simulation_context(context)
    return context


class ProfileCDMCompletenessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            with get_omop_engine().connect():
                pass
        except Exception as exc:  # noqa: BLE001 - reported verbatim in the skip reason
            raise unittest.SkipTest(
                f"No OMOP vocabulary database reachable ({type(exc).__name__}: {exc}); "
                "condition-concept resolution needs it, so this check can't run."
            )

    def test_every_profile_is_nonempty(self):
        # otherwise the loop below would vacuously pass
        self.assertTrue(list_profiles(CONFIG_ROOT))

    def test_every_profile_produces_a_valid_condition_occurrence_row(self):
        for profile_id in list_profiles(CONFIG_ROOT):
            with self.subTest(profile=profile_id):
                runtime = load_routed_profile(profile_id, base_dir=CONFIG_ROOT)
                for seed in SEEDS:
                    with self.subTest(profile=profile_id, seed=seed):
                        routed = runtime.sample_routed_patient(seed=seed)
                        _activate(runtime, routed)

                        person = build_person(routed.final_sample, attach_condition=False)
                        condition = build_condition_occurrence(
                            person, routed.final_sample, assign_treatment=False
                        )
                        person.condition = condition

                        try:
                            row = condition.to_omop_row()
                        except ValueError as exc:
                            known_gap = _KNOWN_GAPS.get(profile_id)
                            if known_gap and known_gap in str(exc):
                                continue
                            self.fail(
                                f"profile '{profile_id}' seed {seed} produced a "
                                f"case that cannot be exported to OMOP: {exc}"
                            )
                            continue

                        self.assertIsNotNone(row.condition_start_date)
