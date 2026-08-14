from pathlib import Path
import importlib.metadata as importlib_metadata
import logging
import os

__all__ = ["Person", "Condition_Occurrence", "Treatment_Episode"]
try:
    __version__ = importlib_metadata.version(__name__)
except importlib_metadata.PackageNotFoundError:
    __version__ = "0.0.0"
_LOGLEVEL = os.getenv("SCOOP_LOGLEVEL", "INFO").upper()

logger = logging.getLogger("scoop")

logging.basicConfig(
    level=_LOGLEVEL,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)

def configure_file_logging(log_dir: Path | None = None) -> None:
    """Opt into file logging without filesystem I/O during package import."""

    target_dir = log_dir or (Path.home() / ".scoop" / "logs")
    target_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(target_dir / "scoop.log")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    )
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)
    logger.debug("SCOOP file logging enabled")

from .patients.person import Person  # noqa: E402
from .patients.condition_occurrence import Condition_Occurrence  # noqa: E402
from .patients.treatment_episode import Treatment_Episode  # noqa: E402
