import pytest #TODO: change to unittests
from scoop.utils.scheduling import StandardDoseInfo


class MockSig:
    def __init__(self, min_val, max_val, unit="mg", cui=12345):
        self.doseminnum = min_val
        self.dosemaxnum = max_val
        self.doseunit = unit
        self.doseunit_cui = cui


def test_constant_dose_skips_prng_allocation():
    """Verify that constant bounds instantly return the value without building the PRNG."""
    sig = MockSig(5.0, 5.0)
    tool = StandardDoseInfo.from_sig(sig, seed=42)
    # Laziness check
    assert tool._rng is None
    assert tool.get_dose() == 5.0
    # No random behaviour requested
    assert tool._rng is None


def test_seeded_determinism():
    """Verify that matching seeds guarantee identical random sequences."""
    sig = MockSig(10.0, 50.0)

    tool_a = StandardDoseInfo.from_sig(sig, seed=2026)
    tool_b = StandardDoseInfo.from_sig(sig, seed=2026)
    
    sequence_a = [tool_a.get_dose() for _ in range(5)]
    sequence_b = [tool_b.get_dose() for _ in range(5)]
    
    assert sequence_a == sequence_b


def test_unseeded_independence():
    """Verify that omitting seeds produces different output streams."""
    sig = MockSig(10.0, 50.0)
    
    tool_a = StandardDoseInfo.from_sig(sig)
    tool_b = StandardDoseInfo.from_sig(sig)
    
    sequence_a = [tool_a.get_dose() for _ in range(5)]
    sequence_b = [tool_b.get_dose() for _ in range(5)]
    
    assert sequence_a != sequence_b


def test_auto_rescue_inverted_bounds_raises_warning():
    """Verify that inverted bounds raise a RuntimeWarning and return valid numbers."""
    # Intentional mixup: low is 100.0, high is 20.0
    sig = MockSig(100.0, 20.0)
    tool = StandardDoseInfo.from_sig(sig, seed=123)
    
    # Use pytest.warns to capture the expected RuntimeWarning
    with pytest.warns(RuntimeWarning):
        dose = tool.get_dose()
        
    # The returned value must be bounded within the corrected boundaries [20.0, 100.0]
    assert 20.0 <= dose <= 100.0


def test_actual_seed_retrieval_and_auditability():
    """Verify we can fetch the generated seed on unseeded runs for debugging loops."""
    sig = MockSig(1.0, 10.0)
    tool = StandardDoseInfo.from_sig(sig)  # seed=None
    
    # A concrete seed integer must exist even before evaluation
    actual_seed = tool._engine_seed
    assert isinstance(actual_seed, int)
    
    # Verify that creating a new tool manually with that captured seed replays the stream
    dose_from_first_run = tool.get_dose()
    
    replay_tool = StandardDoseInfo.from_sig(sig, seed=actual_seed)
    assert replay_tool.get_dose() == dose_from_first_run
