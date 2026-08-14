"""Configured OMOP engine access for vocabulary-backed mappers."""

from __future__ import annotations

from sqlalchemy.engine import Engine


def get_omop_engine() -> Engine:
    """Resolve and create the configured OMOP engine through oa-configurator."""

    from omop_alchemy.config import create_cdm_engine, get_cdm_context

    _, resolved = get_cdm_context()
    return create_cdm_engine(resolved)
