from pathlib import Path
import importlib.metadata as importlib_metadata
import logging
import os
from dotenv import load_dotenv

__all__ = ["Person"]
__version__ = importlib_metadata.version(__name__)
THIS_PATH = Path(__file__).parent

# -------------------------------
# LOGGING SETUP (package-level)
# -------------------------------
load_dotenv(dotenv_path=THIS_PATH / '.env')

_LOGLEVEL = os.getenv("SCOOP_LOGLEVEL", "INFO").upper()
_LOG_DIR = Path.home() / ".scoop/logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("scoop")

logging.basicConfig(
    level=_LOGLEVEL,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)

file_handler = logging.FileHandler(
    _LOG_DIR / "scoop.log"
)

file_handler.setFormatter(
    logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
)

file_handler.setLevel(logging.DEBUG)

logger.addHandler(file_handler)
logger.debug("SCOOP logging initialized")

from .schema.person import Person  # noqa: E402