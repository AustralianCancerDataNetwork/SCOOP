from itertools import count
from threading import Lock

class Sequence:
    """Thread-safe integer sequence generator."""
    def __init__(self, start: int = 1):
        self._counter = count(start)
        self._lock = Lock()

    def next(self) -> int:
        with self._lock:
            return next(self._counter)

    def reset(self, start: int = 1):
        with self._lock:
            self._counter = count(start)

# Global singleton sequences & configurations
person_id_seq = Sequence(1)
measurement_id_seq = Sequence(1)
condition_id_seq = Sequence(1)
observation_id_seq = Sequence(1)
episode_id_seq = Sequence(1)
drug_exposure_id_seq = Sequence(1)
procedure_occurrence_id_seq = Sequence(1)
