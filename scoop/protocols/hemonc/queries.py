import logging
import pandas as pd
import sqlalchemy as sa
import sqlalchemy.orm as so
from functools import cached_property
from typing import Any
from hemonc_alchemy.model import (
    Drugs,
    Sigs,
    Studies,
    StudyResults,
    drugs_Canmed_major_classMap,
    drugs_Canmed_minor_classMap,
    sigs_StudyMap,
)
from hemonc_alchemy.model.enums import (
    Sigs_PhaseEnum,
    Studies_IntentEnum,
    StudyResults_IntentEnum,
)
from hemonc_alchemy.toolkit.core.sigs import SigSelectionSpec, sig_search_statement
from scoop.protocols.hemonc.models import ( 
    _load_hemonc_models, HemOncModels, 
    condition_cuis_by_names, search_substring, _component_search_expressions,
    join_not_distinct, COMPONENT_CLASS_MAPS
    )
from scoop.protocols.hemonc.filters import query_rt_inc_study_ids
from scoop.protocols.spec import ComponentCui, ComponentTerm


logger = logging.getLogger(__name__)


def show_conditions(substring: str, session: so.Session, 
                    models: HemOncModels | None = None) -> pd.DataFrame:
    models = models or _load_hemonc_models()
    pattern = f"%{substring}%"
    return pd.DataFrame(
            session.execute(sa.select(models.Conditions.condition, 
                                      models.Conditions.condition_cui)
                            .where(models.Conditions.condition.ilike(pattern))
        ))


def component_search_by_name(search_terms: str | list[str], table: Any) -> Any:
    """Build a subquery selecting component CUIs matching the search terms."""
    return (
        sa.select(table.c.component_cui)
        .where(sa.or_(*_component_search_expressions(search_terms, table)))
        .distinct()
        .subquery()
    )


def component_search_by_terms(terms: list[ComponentTerm], table: Any) -> Any:
    """Match named components by text and bound components by HemOnc CUI."""
    expressions = []
    names = [term for term in terms if isinstance(term, str)]
    cuis = [term.cui for term in terms if isinstance(term, ComponentCui)]
    if names:
        expressions.extend(_component_search_expressions(names, table))
    if cuis:
        expressions.append(table.c.component_cui.in_(cuis))
    return (
        sa.select(table.c.component_cui)
        .where(sa.or_(*expressions))
        .distinct()
        .subquery()
    )


def hemonc_subsumes_descendant_cuis(
    session: so.Session,
    root_cuis: list[str | int],
    *,
    max_depth: int = 32,
) -> set[str]:
    """Walk valid HemOnc ``Subsumes`` edges with cycle and depth guards."""
    if max_depth < 1:
        raise ValueError("max_depth must be positive")
    concept, relation = omop_vocabulary_tables(session)
    root_codes = {str(cui) for cui in root_cuis}
    root_ids = set(
        session.scalars(
            sa.select(concept.c.concept_id).where(
                concept.c.concept_code.in_(root_codes),
                concept.c.vocabulary_id == "HemOnc",
                sa.func.nullif(sa.func.trim(concept.c.invalid_reason), "").is_(None),
            )
        )
    )
    if not root_ids:
        raise LookupError(f"No HemOnc concepts resolve for {root_cuis!r}.")
    visited = set(root_ids)
    frontier = set(root_ids)
    for depth in range(max_depth + 1):
        statement = (
            sa.select(relation.c.concept_id_2)
            .select_from(relation)
            .join(concept, concept.c.concept_id == relation.c.concept_id_2)
            .where(
                relation.c.concept_id_1.in_(frontier),
                relation.c.relationship_id == "Subsumes",
                concept.c.vocabulary_id == "HemOnc",
                sa.func.nullif(sa.func.trim(relation.c.invalid_reason), "").is_(None),
                sa.func.nullif(sa.func.trim(concept.c.invalid_reason), "").is_(None),
            )
        )
        next_ids = set(session.scalars(statement)) - visited
        if not next_ids:
            break
        if depth == max_depth:
            raise RuntimeError(
                f"HemOnc Subsumes hierarchy exceeds depth limit {max_depth}."
            )
        visited.update(next_ids)
        frontier = next_ids
    descendants = visited - root_ids
    if not descendants:
        return set()
    return set(
        session.scalars(
            sa.select(concept.c.concept_code).where(
                concept.c.concept_id.in_(descendants),
                concept.c.vocabulary_id == "HemOnc",
            )
        )
    )


def omop_vocabulary_tables(session: so.Session) -> tuple[sa.Table, sa.Table]:
    """Reflect the separate OMOP vocabulary tables used by HemOnc."""
    bind = session.get_bind()
    metadata = sa.MetaData()
    try:
        concept = sa.Table("concept", metadata, schema="omop", autoload_with=bind)
        relation = sa.Table(
            "concept_relationship", metadata, schema="omop", autoload_with=bind
        )
    except sa.exc.NoSuchTableError as exc:
        raise RuntimeError("The OMOP vocabulary tables are unavailable.") from exc
    return concept, relation


def get_sig_component_stmt(
    condition_cuis,
    models,
    non_curative=False,
    unknown_context_policy="exclude",
    phase_type=None,
):
    stmt = sig_search_statement(
        SigSelectionSpec.for_conditions(condition_cuis)
    ).outerjoin(Drugs, Drugs.drug_cui == Sigs.component_cui).outerjoin(
        drugs_Canmed_major_classMap,
        drugs_Canmed_major_classMap.parent_id == Drugs.id,
    ).outerjoin(
        drugs_Canmed_minor_classMap,
        drugs_Canmed_minor_classMap.parent_id == Drugs.id,
    )
    context_studies = get_stmt_studies_by_curative_context(
        condition_cuis,
        models=models,
        non_curative=non_curative,
        unknown_context_policy=unknown_context_policy,
        phase_type=phase_type,
    )
    return stmt.with_only_columns(
        Sigs.id,
        Sigs.regimen,
        Sigs.variant_cui,
        Sigs.component,
        Sigs.component_cui,
        Drugs.drug_inn,
        Drugs.main_class,
        drugs_Canmed_major_classMap.canmed_major_class,
        drugs_Canmed_minor_classMap.canmed_minor_class,
        Studies.study.label("study"),
        Sigs.phase,
        maintain_column_froms=True,
    ).where(Studies.study.in_(context_studies))


def _intent_predicates(studies, results, intent_map, non_curative):
    target_study_intent = (
        Studies_IntentEnum.NON_TO_CURATIVE_INTENT
        if non_curative
        else Studies_IntentEnum.CURATIVE_INTENT
    )
    target_result_intent = (
        StudyResults_IntentEnum.NON_TO_CURATIVE_INTENT
        if non_curative
        else StudyResults_IntentEnum.CURATIVE_INTENT
    )
    has_study_intent = sa.exists(
        sa.select(1)
        .select_from(intent_map)
        .where(
            intent_map.parent_id == studies.id,
            intent_map.intent != Studies_IntentEnum.UNSPECIFIED_INTENT,
        )
        .correlate(studies)
    )
    study_intent_match = sa.exists(
        sa.select(1)
        .select_from(intent_map)
        .where(
            intent_map.parent_id == studies.id,
            intent_map.intent == target_study_intent,
        )
        .correlate(studies)
    )
    result_intent_match = sa.exists(
        sa.select(1)
        .select_from(results)
        .where(
            results.study == studies.study,
            results.intent == target_result_intent,
        )
        .correlate(studies)
    )
    has_result_intent = sa.exists(
        sa.select(1)
        .select_from(results)
        .where(
            results.study == studies.study,
            results.intent != StudyResults_IntentEnum.UNSPECIFIED_INTENT,
        )
        .correlate(studies)
    )
    classified_match = sa.or_(
        study_intent_match,
        sa.and_(sa.not_(has_study_intent), result_intent_match),
    )
    return classified_match, has_study_intent, has_result_intent


def _unknown_intent_predicate(
    studies, models, has_any_intent, unknown_context_policy, phase_type,
):
    if unknown_context_policy == "include":
        return sa.not_(has_any_intent)
    elif unknown_context_policy == "phase_type" and phase_type:
        phase = Sigs_PhaseEnum(phase_type)
        sigs = models.Sigs
        sig_studies = models.sigs_StudyMap
        phase_studies = (
            sa.select(sig_studies.study)
            .join(sigs, sig_studies.parent_id == sigs.id)
            .where(sigs.phase == phase)
        )
        return sa.and_(
            sa.not_(has_any_intent),
            studies.study.in_(phase_studies),
        )
    return sa.false()


def get_stmt_studies_by_curative_context(
    condition_cuis,
    models=None,
    non_curative=False,
    unknown_context_policy="exclude",
    phase_type=None,
):
    models = models or _load_hemonc_models()
    studies = models.Studies
    classified_match, has_study_intent, has_result_intent = _intent_predicates(
        studies, models.StudyResults, models.studies_IntentMap, non_curative
    )
    has_any_intent = sa.or_(has_study_intent, has_result_intent)
    unknown_match = _unknown_intent_predicate(
        studies,
        models,
        has_any_intent,
        unknown_context_policy,
        phase_type,
    )
    return (
        sa.select(studies.study)
        .where(
            sa.and_(
                studies.condition_cui.in_(condition_cuis),
                sa.or_(classified_match, unknown_match),
            )
        )
    )


def _category_mapping_expressions(components: Any) -> list[Any]:
    """Build SQL expressions mapping raw HemOnc classes into broad categories."""
    expressions: list[Any] = []
    for column_name, mapping in COMPONENT_CLASS_MAPS.items():
        column = getattr(components.c, column_name)
        expressions.append(
            sa.case(
                *[(column == source_value, mapped_value) for source_value, mapped_value in mapping.items()]
            )
        )
    return expressions


def categorise_components(components: Any) -> Any:
    """Annotate component rows with derived broad-category labels."""
    return (
        sa.select(
            components.c.variant_cui,
            components.c.canmed_major_class,
            components.c.canmed_minor_class,
            components.c.main_class,
            components.c.component,
            components.c.component_cui,
            components.c.study,
            components.c.phase,
            sa.func.coalesce(*_category_mapping_expressions(components)).label("broad_category"),
        )
        .distinct()
        .subquery()
    )

class ComponentSubquery:
    def __init__(self, conditions: list, session=None, models: HemOncModels | None = None):
        models = models or _load_hemonc_models()
        self.models = models
        if isinstance(conditions[0], str):
            if not session:
                raise TypeError("Session must be supplied if conditions list is not CUIs")
            self.condition_cuis = condition_cuis_by_names(conditions, session)
        else:
            self.condition_cuis = conditions
        self.sigs_stmt= get_sig_component_stmt(self.condition_cuis, self.models)
        self.sigs_query = self.sigs_stmt.subquery()
        self._df_cache = {}
        
    @cached_property
    def variant_query(self):
        # this query isn't particularly useful since sigs has variant_cui
        # but I wanted to know I could attach the variant model consistently
        return ( sa.select(
        self.models.Variants.variant_cui,
        self.sigs_query)
        .join(self.models.Variants, self.sigs_query.c.variant_cui == self.models.Variants.variant_cui)
        # without below you can get variants from other conditions
        # which use the same sigs (I learnt this through experience)
        .join(self.models.variants_StudyMap)
        .join(self.models.Studies, self.models.variants_StudyMap.study == self.models.Studies.study)
        .where(self.models.Variants.study_items.any(self.models.Studies.condition_cui.in_(self.condition_cuis)))
        ).subquery()


    def get_dataframe(self, session: so.Session, subquery_name="sigs_query", categorise = False):
        cache_key = (subquery_name, categorise)
        if cache_key not in self._df_cache:
            subquery = getattr(self, subquery_name)
            stmt = sa.select(subquery)
            if categorise:
                category_expr = sa.func.coalesce(
                    *_category_mapping_expressions(subquery)
                    ).label("broad_category")
                stmt = stmt.add_columns(category_expr)
            df = pd.DataFrame(session.execute(stmt).unique())
            self._df_cache[cache_key] = df.dropna(how='all', inplace=True)
        else:
            df = self._df_cache[cache_key]
        return df


class ConditionDevTools:
    def __init__(self, conditions, session=None):
        self.components = ComponentSubquery(conditions, session)
        self.conditions=conditions, 
    
    def show_component_with_substring(self, session: so.Session, substring: str):
        print(session.scalars(search_substring(substring, self.components.sigs_query)).all())


def build_component_requirement_expressions(
    component_requirements: Any,
    categorised_components: Any,
) -> list[Any]:
    """Translate component requirements into SQL HAVING clauses."""
    expressions: list[Any] = []
    expressions += build_category_expressions(component_requirements.category_restrictions, categorised_components)
    expressions += build_component_expressions(component_requirements.component_lists, categorised_components)
    return expressions


def build_category_expressions(category_restrictions, categorised_components):
    """
    Broad category sqlachemy expressions to apply to grouped sig info.
    """
    expressions = []
    for category, restriction in category_restrictions.items():
        category_count = sa.func.sum(
            sa.case((categorised_components.c.broad_category == category, 1), else_=0)
        )
        if restriction.type == "exact":
            expressions.append(category_count == restriction.amount)
        elif restriction.type == "min":
            expressions.append(category_count >= restriction.amount)
    return expressions


def build_component_expressions(component_lists, categorised_components):
    """
    Component cui expressions to apply to grouped sig info.
    """
    expressions = []
    for component_list in component_lists:
        component_cuis = sa.select(
            component_search_by_terms(component_list, categorised_components)
        )
        expressions.append(
            sa.func.sum(
                sa.case(
                    (categorised_components.c.component_cui.in_(component_cuis), 1),
                    else_=0,
                )
            )
            >= 1
        )
    return expressions


def phase_spec_to_sigdict(
    phase_spec,
    session,
    *,
    condition_cuis: list[int] | None = None,
    models: HemOncModels | None = None,
):
    """Execute the phase query and return the legacy grouped SIG shape.

    Condition references are resolved by the gateway before this function is called;
    keeping that resolution outside the query helper avoids a hidden database lookup
    in candidate assembly.
    """

    base_df = get_base_sigs_df(
        phase_spec,
        session,
        condition_cuis=condition_cuis,
        models=models,
    )
    phase_filtered = subset_df_on_phase(base_df, phase_spec.type)
    sig_deduped = phase_filtered.drop_duplicates(subset=["id"])
    if sig_deduped.empty:
        return {}
    return get_sigdict(sig_deduped)


def get_sigdict(df):
    grouped_df = df.fillna("None").groupby(["phase", "variant_cui", "study", "regimen", "variant"], sort=False)
    return grouped_df["Sigs"].agg(list).to_dict()


def get_siggroup_identifying_subquery(component_requirements, categorised, models=None):
    """
    Subquery for identifying groupings of sigs for potential sig bundles.
    
    Does not contain sig objects themselves.
    """
    del models
    return (
    sa.select(
        categorised.c.study.label('single_study'),
        categorised.c.phase.label('matched_phase'),
        categorised.c.variant_cui.label('matched_variant_cui')
    )
    .select_from(categorised)
    .group_by(categorised.c.study, categorised.c.phase, categorised.c.variant_cui)
    .having(sa.and_(*build_component_requirement_expressions(component_requirements, categorised)))
    .subquery()
)


def subquery_sigs_valid_groups(valid_groups_subquery, models):
    """
    Subquery which attaches sig info to selected sig groupings.
    """
    return (
    sa.select(
        valid_groups_subquery.c.single_study,
        valid_groups_subquery.c.matched_phase,
        valid_groups_subquery.c.matched_variant_cui,
        Sigs.variant_cui,
        Sigs.phase,
        sigs_StudyMap.study.label("study"),
        Sigs.component,
        Sigs.id,
        Sigs.regimen,
        Sigs.variant,
    )
    .select_from(Sigs)
    .join(sigs_StudyMap, sigs_StudyMap.parent_id == Sigs.id)
    .join(valid_groups_subquery, sa.and_(
        join_not_distinct(Sigs.phase, valid_groups_subquery.c.matched_phase),
        join_not_distinct(Sigs.variant_cui, valid_groups_subquery.c.matched_variant_cui),
        sigs_StudyMap.study == valid_groups_subquery.c.single_study,
    ))
).subquery()


def get_base_sigs_df(
    phase_spec,
    session,
    models: HemOncModels | None = None,
    *,
    condition_cuis: list[int] | None = None,
):
    """
    Df of sigs from phase-varcui-study groupings meeting component & curative intent 
    """
    models = models or _load_hemonc_models()
    comp_reqs = phase_spec.component_requirements
    condition_cuis = (
        list(condition_cuis)
        if condition_cuis is not None
        else list(getattr(phase_spec, "condition_cuis", ()))
    )
    components = get_sig_component_stmt(condition_cuis, models, 
                                        non_curative=(not phase_spec.curative_intent),
                                        unknown_context_policy=phase_spec.unknown_context_policy,
                                        phase_type=phase_spec.type)
    components = subset_components_by_study_stmt(phase_spec.study_restriction,
                                                 condition_cuis, components)
    components = subset_components_stmt_by_regimens(components, phase_spec.regimens)
    categorised = categorise_components(components)
    valid_groups_subquery = get_siggroup_identifying_subquery(comp_reqs, categorised)
    sigs_for_valid_groups_subquery= subquery_sigs_valid_groups(valid_groups_subquery, models)
    stmt = sa.select(sigs_for_valid_groups_subquery, models.Sigs).join(models.Sigs, models.Sigs.id==sigs_for_valid_groups_subquery.c.id)
    return pd.DataFrame(session.execute(stmt).unique())


def subset_components_by_study_stmt(study_restriction, condition_cuis, 
                                    components_stmt: sa.Select, models = None) -> sa.Select:
    models = models or _load_hemonc_models()
    if study_restriction and study_restriction == "must_have_rt":
        subsetting_stmt = query_rt_inc_study_ids(condition_cuis)
        return components_stmt.where(models.Studies.id.in_(subsetting_stmt))
    return components_stmt

def subset_components_stmt_by_regimens(components_stmt: sa.Select, regimens: list[str], models=None) -> sa.Select:
    models = models or _load_hemonc_models()
    if regimens:
        return components_stmt.where(models.Sigs.regimen.in_(regimens))
    return components_stmt

def subset_df_on_phase(df, target_phase):
    """Returns df subsetted on phase as closely as possible"""
    try:
        if target_phase:
            subset = df[df["phase"].str.lower()==target_phase.lower()]
        else:
            subset = df[df["phase"].isna()]
        if len(subset) > 0:
            return subset
        na_phases = df[df["phase"].isna()]
        if len(na_phases) > 0:
            # TODO: create some less important logging here
            return na_phases
        else:
            # TODO: create some kind of warning here
            return df
    except KeyError: # empty df case
            # TODO: logging
        return df
