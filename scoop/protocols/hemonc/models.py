from dataclasses import dataclass
from typing import Any
import sqlalchemy as sa
import sqlalchemy.orm as so
import logging
from hemonc_alchemy.model import (
    Conditions,
    Drugs,
    Sigs,
    Studies,
    StudyResults,
    drugs_Canmed_major_classMap,
    drugs_Canmed_minor_classMap,
    sigs_StudyMap,
    variants_StudyMap,
    Variants,
)
from hemonc_alchemy.toolkit.core.conditions import (
    condition_cuis_by_names as _condition_cuis_by_names,
)

logger = logging.getLogger(__name__)

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

COMPONENT_STR_COLS = ["canmed_major_class", "canmed_minor_class", "main_class", "component"]

@dataclass(frozen=True)
class HemOncModels:
    """Container for the SQLAlchemy entities used by the query helpers."""

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
    """Return the current public HemOnc SQLAlchemy model bundle."""

    return HemOncModels()



def search_substring(substring, table, column_names=COMPONENT_STR_COLS):
    pattern = f"%{substring}%"
    statements = []

    for col_name in column_names:
        column = getattr(table.c, col_name)
        statements.append(sa.select(column).where(column.ilike(pattern)))
    stmt = sa.union(*statements)

    return stmt


def or_for_piped_col(from_col, to_col):
    return sa.or_(
        (from_col==to_col),
        from_col.like(sa.func.concat(to_col, '|%')),
        from_col.like(sa.func.concat('%|', to_col, '|%')), 
        from_col.like(sa.func.concat('%|', to_col)))


def get_sig_study_join(models: HemOncModels | None = None, to="studies_table"):
    models = models or _load_hemonc_models()
    if to == "studies_table":
        to_col = models.Studies.study
    else:
        to_col = to
    return or_for_piped_col(models.Sigs.study, to_col)


def condition_cuis_by_names(condition_names: list[str], session: so.Session,
                           models: HemOncModels | None = None) -> list[int]:
    del models
    return _condition_cuis_by_names(session, condition_names)

def _component_search_expressions(search_terms: str | list[str], table: Any) -> list[Any]:
    """Build `ILIKE` expressions across all supported component text columns."""

    if isinstance(search_terms, str):
        search_terms = [search_terms]
    expressions: list[Any] = []
    for search_term in search_terms:
        for column_name in COMPONENT_STR_COLS:
            expressions.append(getattr(table.c, column_name).ilike(search_term))
    return expressions


def join_not_distinct(col1, col2):
    return sa.or_(
        col1 == col2,
        sa.and_(col1.is_(None), col2.is_(None))
    )


