from dataclasses import dataclass
from typing import Any
import sqlalchemy as sa
import sqlalchemy.orm as so
import logging

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

    Variants: Any
    Sigs: Any
    Studies: Any
    Drugs: Any
    drugs_Canmed_major_classMap: Any
    drugs_Canmed_minor_classMap: Any
    variants_StudyMap: Any
    Conditions: Any
    StudyResults: Any


def _load_hemonc_models() -> HemOncModels:
    """Import and package the HemOnc SQLAlchemy models on demand."""

    try:
        from hemonc_import.final_model.relationships import Drugs, Sigs, Studies, Variants, Conditions
        from hemonc_import.final_model.entities import (
            drugs_Canmed_major_classMap,
            drugs_Canmed_minor_classMap,
            variants_StudyMap, 
            StudyResults
        )
    except ImportError as exc:
        raise ImportError(
            "hemonc_import is required to build executable HemOnc queries. "
            "Install it in the active environment before using these helpers."
        ) from exc

    return HemOncModels(
        Variants=Variants,
        Sigs=Sigs,
        Studies=Studies,
        Drugs=Drugs,
        drugs_Canmed_major_classMap=drugs_Canmed_major_classMap,
        drugs_Canmed_minor_classMap=drugs_Canmed_minor_classMap,
        variants_StudyMap=variants_StudyMap,
        Conditions=Conditions,
        StudyResults=StudyResults
    )



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
    models = models or _load_hemonc_models()
    results = (
        session.query(models.Conditions.condition_cui, models.Conditions.condition)
        .filter(models.Conditions.condition.in_(condition_names))
        .all()
    )
    found_names = {row.condition for row in results}
    missing = set(condition_names) - found_names
    if missing:
        logger.warning("No CUI found for condition(s): %s", missing)
    return [row.condition_cui for row in results]

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



