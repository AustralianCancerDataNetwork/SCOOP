from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


DEFAULT_OMOP_DATABASE_URL = "postgresql+psycopg2://omop:omop@localhost:5432/omop"


def omop_database_url() -> str:
    """Return the configured OMOP database URL."""

    return os.getenv("SCOOP_OMOP_DATABASE_URL", DEFAULT_OMOP_DATABASE_URL)


_ENGINE: Engine | None = None


def get_omop_engine() -> Engine:
    """Create or return the shared SQLAlchemy engine for OMOP vocabulary access."""

    global _ENGINE
    if _ENGINE is None:
        _ENGINE = create_engine(omop_database_url(), pool_pre_ping=True)
    return _ENGINE
