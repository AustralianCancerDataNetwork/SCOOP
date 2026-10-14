import unittest

import pandas as pd

from scoop.schedule.systemic import DoseRule, PhaseSchedule

SAMPLE_DATE = pd.to_datetime("2020-01-01")
NUM_SCHEDULES_TO_TRY = 40

class StubSigDosing:
    def __init__(self, min_val, max_val, unit="mg", cui=12345):
        self.doseminnum = min_val
        self.dosemaxnum = max_val
        self.doseunit = unit
        self.doseunit_cui = cui

class StubSig:
    def __init__(self, cycle_length_lb=21, cycle_length_ub=21, 
                 cycle_length_unit="day", timing_sequence="1,2,3,4", 
                 alldays = "1", cyclesigs = "21 day cycles for 4 cycles", 
                 portion = "", doseminnum = 5, dosemaxnum = 5, 
                 doseunit = "mg/mL min", doseunit_cui=12, 
                 component= "ComponentName", component_cui = 1234, drug_object=None,
                 durationminnum = None, durationmaxnum = None, durationunit = None, 
                 durationunit_cui = None, route="", route_cui=123, 
                 frequency="", frequency_cui=12345, component_role = "",
                 step_number = "1 of 1"):
        self.cycle_length_lb = cycle_length_lb
        self.cycle_length_ub = cycle_length_ub
        self.cycle_length_unit = cycle_length_unit 
        self.timing_sequence = timing_sequence
        self.alldays = alldays
        self.cyclesigs = cyclesigs
        self.portion = portion
        self.doseminnum = doseminnum
        self.dosemaxnum = dosemaxnum
        self.doseunit = doseunit
        self.doseunit_cui = doseunit_cui
        self.drug_object = drug_object
        self.component = component
        self.component_cui = component_cui
        self.durationminnum = durationminnum
        self.durationmaxnum = durationmaxnum
        self.durationunit = durationunit
        self.durationunit_cui = durationunit_cui
        self.route= route
        self.route_cui= route_cui
        self.frequency= frequency
        self.frequency_cui= frequency_cui
        self.component_role=component_role
        self.step_number = step_number



class StandardDoseTests(unittest.TestCase):
    def test_constant_dose_skips_prng_allocation(self):
        """Verify that constant bounds instantly return the value without building the PRNG."""
        sig = StubSigDosing(5.0, 5.0)
        tool = DoseRule.from_sig(sig, seed=42)
        # Laziness check
        self.assertIsNone(tool._rng)
        self.assertEqual(tool.get_dose(), 5.0)
        # No random behaviour requested
        self.assertIsNone(tool._rng)

    def test_seeded_determinism(self):
        """Verify that matching seeds guarantee identical random sequences."""
        sig = StubSigDosing(10.0, 50.0)
        tool_a = DoseRule.from_sig(sig, seed=2026)
        tool_b = DoseRule.from_sig(sig, seed=2026)
        sequence_a = [tool_a.get_dose() for _ in range(5)]
        sequence_b = [tool_b.get_dose() for _ in range(5)]
        self.assertListEqual(sequence_a, sequence_b)

    def test_unseeded_independence(self):
        """Verify that omitting seeds produces different output streams."""
        sig = StubSigDosing(10.0, 50.0)
        tool_a = DoseRule.from_sig(sig)
        tool_b = DoseRule.from_sig(sig)
        sequence_a = [tool_a.get_dose() for _ in range(5)]
        sequence_b = [tool_b.get_dose() for _ in range(5)]
        self.assertNotEqual(sequence_a, sequence_b)

    def test_auto_rescue_inverted_bounds_raises_warning(self):
        """Verify that inverted bounds raise a RuntimeWarning and return valid numbers."""
        # Intentional mixup: low is 100.0, high is 20.0
        sig = StubSigDosing(100.0, 20.0)
        tool = DoseRule.from_sig(sig, seed=123)
        
        # Use pytest.warns to capture the expected RuntimeWarning
        with self.assertWarns(RuntimeWarning):
            dose = tool.get_dose()
            
        # The returned value must be bounded within the corrected boundaries [20.0, 100.0]
        self.assertTrue(20.0 <= dose <= 100.0)

    def test_actual_seed_retrieval_and_auditability(self):
        """Verify we can fetch the generated seed on unseeded runs for debugging loops."""
        sig = StubSigDosing(1.0, 10.0)
        tool = DoseRule.from_sig(sig)  # seed=None
        # A concrete seed integer must exist even before evaluation
        actual_seed = tool._engine_seed
        self.assertIsInstance(actual_seed, int)
        # Verify that creating a new tool manually with that captured seed replays the stream
        dose_from_first_run = tool.get_dose()
        replay_tool = DoseRule.from_sig(sig, seed=actual_seed)
        self.assertEqual(replay_tool.get_dose(), dose_from_first_run)


class CycleSchedulingTests(unittest.TestCase):
    def test_cycle_set_order(self):
        earlier_sig = StubSig(component="Earlier component", timing_sequence="1,2,3")
        later_sig = StubSig(component="Later component", timing_sequence="4,5,6")
        also_late_sig = StubSig(component="Ongoing component", timing_sequence="4,(+1)")
        unsorted_sig_list = [later_sig, earlier_sig, also_late_sig]
        systemic_phase = PhaseSchedule.from_component_sigs(unsorted_sig_list)
        first_cycle_set = systemic_phase.cycle_sets[0]
        second_cycle_set = systemic_phase.cycle_sets[1]
        self.assertEqual(first_cycle_set.sig_schedules[0].component_part.component.name,
                         "Earlier component")
        self.assertEqual(len(systemic_phase.cycle_sets), 2)
        self.assertEqual(len(second_cycle_set.sig_schedules), 2)

    def test_sole_component_ongoing_not_exceed_cycleset(self):
        set_cycles_sig = StubSig(component="Set component", timing_sequence="1,2,3")
        ongoing_sig = StubSig(component="Ongoing component", timing_sequence="1,(+1)")
        systemic_phase = PhaseSchedule.from_component_sigs([set_cycles_sig, ongoing_sig])

        for _ in range(20):
            dates_len = len(systemic_phase.get_scheduled_dates(SAMPLE_DATE))
            with self.subTest():
                self.assertLessEqual(dates_len, 3)
            systemic_phase.reset()

    def _assert_any_date_len_gt(self, systemic_phase, int):
        date_lengths = set()
        for _ in range(NUM_SCHEDULES_TO_TRY):
            date_lengths.add(len(systemic_phase.get_scheduled_dates(SAMPLE_DATE)))
            systemic_phase.reset()
        self.assertGreater(max(date_lengths), int)

    def test_sole_component_ongoing_not_limited_other_cycleset(self):
        set_cycles_sig = StubSig(component="Set component", timing_sequence="1,2,3")
        ongoing_sig = StubSig(component="Ongoing component", timing_sequence="4,(+1)")
        systemic_phase = PhaseSchedule.from_component_sigs([set_cycles_sig, ongoing_sig])
        self._assert_any_date_len_gt(systemic_phase, 4)

    def test_monotherapy_ongoing_can_continue(self):
        ongoing_sig = StubSig(component="Ongoing component", timing_sequence="1,(+1)")
        systemic_phase = PhaseSchedule.from_component_sigs([ongoing_sig])
        self._assert_any_date_len_gt(systemic_phase, 1)

    def test_all_ongoing_cycleset_can_continue(self):
        ongoing_sig = StubSig(component="One ongoing component", timing_sequence="4,(+1)")
        other_ongoing_sig = StubSig(component="Another ongoing component", timing_sequence="4,(+1)")
        set_cycles_sig = StubSig(component="Set component", timing_sequence="1,2,3")
        siglist = [set_cycles_sig, ongoing_sig, other_ongoing_sig]
        systemic_phase = PhaseSchedule.from_component_sigs(siglist)
        self._assert_any_date_len_gt(systemic_phase, 4)

    def test_optional_cycles_sometimes_rolled(self):
        ...

    def test_reset_equivalent_to_new_instantiation(self):
        ...

# These are commented out because they reflect functionality not built yet
# class DaySchedulingTests(unittest.TestCase):
    # def test_day_ongoing_not_beyond_cycle_len(self):
    #     ...

    # def test_day_ongoing_possible(self):
    #     ...

    # like discontinue flag for cycleevents & cyclesets
    # def test_day_ongoing_monotonicity(self):
    #     ...

        

if __name__ == '__main__':
    unittest.main()
