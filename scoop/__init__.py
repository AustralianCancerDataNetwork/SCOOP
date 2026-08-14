from pathlib import Path
import importlib.metadata as importlib_metadata
import logging
import os
from dotenv import load_dotenv

__all__ = ["Person", "Condition_Occurrence", "Treatment_Episode"]
try:
    __version__ = importlib_metadata.version(__name__)
except importlib_metadata.PackageNotFoundError:
    __version__ = "0.0.0"
THIS_PATH = Path(__file__).parent

load_dotenv(dotenv_path=THIS_PATH / '.env')

_LOGLEVEL = os.getenv("SCOOP_LOGLEVEL", "INFO").upper()
_LOG_DIR = Path.home() / ".scoop/logs"

logger = logging.getLogger("scoop")

logging.basicConfig(
    level=_LOGLEVEL,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)

try:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(_LOG_DIR / "scoop.log")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    )
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)
    logger.debug("SCOOP logging initialized")
except OSError:
    logger.debug("Skipping file logging because the log directory is not writable.")

from .patients.person import Person  # noqa: E402
from .patients.condition_occurrence import Condition_Occurrence  # noqa: E402
from .patients.treatment_episode import Treatment_Episode  # noqa: E402
