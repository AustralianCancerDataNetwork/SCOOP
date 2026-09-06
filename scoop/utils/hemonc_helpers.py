"""SCOOP's small compatibility boundary for the HemOnc model.

SCOOP owns treatment-profile policy, but the model and reusable query
operations live in :mod:`hemonc_alchemy`. This module keeps the handful of
SCOOP-facing helpers that are still convenient while avoiding a dependency on
the removed ``hemonc_import.final_model`` package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
import sqlalchemy.orm as so

from hemonc_alchemy.model import (
    Conditions,
    Drugs,
    Sigs,
    Studies,
    StudyResults,
    Variants,
    drugs_Canmed_major_classMap,
    drugs_Canmed_minor_classMap,
    sigs_StudyMap,
    variants_StudyMap,
)
from hemonc_alchemy.toolkit.core.conditions import (
    condition_cuis_by_names as _condition_cuis_by_names,
)


# These are SCOOP policy mappings, not HemOnc model classifications. They
# remain here until each profile can provide its own category policy.
MAJOR_CLASS_MAP = {
    "Serine-threonine kinase inhibitor": "chemotherapy",
    "MEK inhibitor": "chemotherapy",
    "Immunostimulatory monoclonal antibody": "immunotherapy",
    "Antineoplastic monoclonal antibody": "immunotherapy",
    "Alkylating agent": "chemotherapy",
    "Microtubule inhibitor": "chemotherapy",
    "Tyrosine kinase inhibitor": "chemotherapy",
    "Topoisomerase inhibitor": "chemotherapy",
    "Antimetabolite": "chemotherapy",
    "Emesis prevention": "supportive",
    "Chemotherapy protective agent": "supportive",
    "Growth factor": "supportive",
    "Antibody-drug conjugate": "immunotherapy",
}

MAIN_CLASS_MAP = {
    "TRK inhibitor": "chemotherapy",
    "KRAS inhibitor": "chemotherapy",
    "Anti-EGFR-HGFR bispecific antibody": "immunotherapy",
    "Anthracycline": "chemotherapy",
    "Multikinase inhibitor": "chemotherapy",
    "MET inhibitor": "chemotherapy",
    "Steroid": "supportive",
    "Antihistamine": "supportive",
    "H2 blocker": "supportive",
    "Opioid receptor agonist": "supportive",
    "Replacement therapy": "supportive",
    "Anti-PD-1-VEGF bispecific antibody": "immunotherapy",
    "Dopamine receptor antagonist": "supportive",
    "Fluoroquinolone": "supportive",
    "H2-receptor antagonist": "supportive",
    "ROS1 inhibitor": "chemotherapy",
    "RET inhibitor": "chemotherapy",
    "Anti-DLL3-CD3 bispecific antibody": "immunotherapy",
}

COMPONENT_MAP = {
    "Bleomycin": "supportive",
    "Granulocyte colony-stimulating factor": "supportive",
    "Serotonin 5-HT3 antagonist": "supportive",
    "Steroid": "supportive",
    "External beam radiotherapy": "radiotherapy",
    "Surgery": "surgery",
    "Emesis prevention": "supportive",
}

COMPONENT_CLASS_MAPS = {
    "canmed_major_class": MAJOR_CLASS_MAP,
    "main_class": MAIN_CLASS_MAP,
    "component": COMPONENT_MAP,
}
COMPONENT_STR_COLS = [
    "canmed_major_class",
    "canmed_minor_class",
    "main_class",
    "component",
]


@dataclass(frozen=True)
class HemOncModels:
    """SCOOP-local model bundle backed by current HemOnc entities."""

    Variants: Any = Variants
    Sigs: Any = Sigs
    Studies: Any = Studies
    Drugs: Any = Drugs
    drugs_Canmed_major_classMap: Any = drugs_Canmed_major_classMap
    drugs_Canmed_minor_classMap: Any = drugs_Canmed_minor_classMap
    variants_StudyMap: Any = variants_StudyMap
    sigs_StudyMap: Any = sigs_StudyMap
    Conditions: Any = Conditions
    StudyResults: Any = StudyResults


def _load_hemonc_models() -> HemOncModels:
    """Return the current public HemOnc model classes."""

    return HemOncModels()


def condition_cuis_by_names(
    condition_names: list[str] | str,
    session: so.Session,
    models: HemOncModels | None = None,
) -> list[int]:
    """Resolve exact condition names using the HemOnc toolkit."""

    # ``models`` remains accepted for source compatibility while the current
    # toolkit intentionally owns the generated entity classes.
    del models
    return _condition_cuis_by_names(session, condition_names)


def search_substring(substring: str, table: Any, column_names=COMPONENT_STR_COLS):
    """Return a union selecting rows whose text columns contain a term."""

    pattern = f"%{substring}%"
    statements = [
        sa.select(getattr(table.c, column_name)).where(
            getattr(table.c, column_name).ilike(pattern)
        )
        for column_name in column_names
    ]
    return sa.union(*statements)


def _component_search_expressions(
    search_terms: str | list[str], table: Any
) -> list[Any]:
    """Build ILIKE expressions across projected component text columns."""

    terms = [search_terms] if isinstance(search_terms, str) else search_terms
    return [
        getattr(table.c, column_name).ilike(
            term if any(marker in term for marker in ("%", "_")) else f"%{term}%"
        )
        for term in terms
        for column_name in COMPONENT_STR_COLS
    ]


def join_not_distinct(col1: Any, col2: Any) -> Any:
    """Compare nullable SQL columns with equality semantics for NULLs."""

    return sa.or_(col1 == col2, sa.and_(col1.is_(None), col2.is_(None)))


__all__ = [
    "COMPONENT_CLASS_MAPS",
    "COMPONENT_STR_COLS",
    "HemOncModels",
    "_load_hemonc_models",
    "_component_search_expressions",
    "condition_cuis_by_names",
    "join_not_distinct",
    "search_substring",
]
