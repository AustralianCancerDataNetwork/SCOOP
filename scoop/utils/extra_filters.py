"""SCOOP-specific filters built on the current HemOnc toolkit."""

from __future__ import annotations

from typing import Any

from hemonc_alchemy.toolkit.analytics.treatment.filters import (
    standalone_radiation_sig_statement,
    studies_with_standalone_radiation_sigs_statement,
)

from .hemonc_helpers import HemOncModels


def get_rt_only_sigs(
    condition_cuis: list[int],
    models: HemOncModels | None = None,
) -> Any:
    """Return a subquery for standalone radiation sigs in a condition set."""

    del models
    return standalone_radiation_sig_statement(condition_cuis).subquery(
        "standalone_radiation_sigs"
    )


def query_rt_inc_study_ids(
    condition_cuis: list[int],
    models: HemOncModels | None = None,
) -> Any:
    """Return study IDs containing standalone radiation sigs.

    The optional ``models`` argument remains for SCOOP call compatibility;
    current model classes are owned by ``hemonc_alchemy``.
    """

    del models
    return studies_with_standalone_radiation_sigs_statement(condition_cuis)


__all__ = ["get_rt_only_sigs", "query_rt_inc_study_ids"]
