from pathlib import Path
from .schema.person import Person

__all__ = [
    "Person",
]

import importlib.metadata as importlib_metadata

__version__ = importlib_metadata.version(__name__)

THIS_PATH = Path(__file__).parent

