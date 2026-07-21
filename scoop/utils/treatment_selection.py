from typing import Any
from ..configuration.protocol_registry import (
    ComponentRequirements,
    CategoryRestriction,
)
import sqlalchemy as sa
import sqlalchemy.orm as so
import pandas as pd
from functools import cached_property
import logging
from scoop.utils.hemonc_helpers import ( 
    _load_hemonc_models, HemOncModels, 
    condition_cuis_by_names, search_substring, _component_search_expressions,
    join_not_distinct, or_for_piped_col,
    get_sig_study_join, COMPONENT_CLASS_MAPS
    )

logger = logging.getLogger(__name__)


CONCURRENT_CRT_REQUIREMENTS = (
    ComponentRequirements(component_lists=[[]], 
    category_restrictions={"chemotherapy": CategoryRestriction(type="min", amount=1),
                           "radiotherapy": CategoryRestriction(type="min", amount=1)})
)

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


def get_sig_component_stmt(condition_cuis, models, non_curative=False):
    return (
    sa.select(
        models.Sigs.id,
        models.Sigs.regimen,
        models.Sigs.variant_cui,
        models.Sigs.component,
        models.Sigs.component_cui,
        models.Drugs.drug_inn,
        models.Drugs.main_class,
        models.drugs_Canmed_major_classMap.canmed_major_class,
        models.drugs_Canmed_minor_classMap.canmed_minor_class,
        models.Sigs.study,
        models.Sigs.phase
    )
    .outerjoin(models.Drugs, models.Sigs.component_cui == models.Drugs.drug_cui)
    .outerjoin(
        models.drugs_Canmed_major_classMap,
        models.drugs_Canmed_major_classMap.parent_id == models.Drugs.id,
    )
    .outerjoin(
        models.drugs_Canmed_minor_classMap,
        models.drugs_Canmed_minor_classMap.parent_id == models.Drugs.id,
    )
    .join(models.Studies, get_sig_study_join(), full=True)
    .where(models.Studies.study.in_(get_stmt_studies_by_curative_context(condition_cuis, models, non_curative)))
    )

def get_stmt_studies_by_curative_context(condition_cuis, models, non_curative = False):
    context_operator = getattr(models.StudyResults.context, "__eq__" if non_curative else "__ne__")
    return (
        sa.Select(models.Studies.study)
        .join(models.StudyResults, models.Studies.study==models.StudyResults.study)
        .where(sa.and_(models.Studies.condition_cui.in_(condition_cuis),
                       context_operator("Non-curative therapy") ))
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
    component_requirements: ComponentRequirements,
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
        component_cuis = sa.select(component_search_by_name(component_list, categorised_components))
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

def source_phase_to_sigdict(source_phase, session):
    base_df = get_base_sigs_df(source_phase, session)
    phase_filtered = subset_df_on_phase(base_df, source_phase.type)
    if phase_filtered.empty:
        return dict()
    return get_sigdict(phase_filtered)

def get_sigdict(df):
    grouped_df = df.fillna("None").groupby(["phase", "variant_cui", "study", "regimen"], sort=False)
    return grouped_df["Sigs"].agg(list).to_dict()


def get_siggroup_identifying_subquery(component_requirements, categorised, models=None):
    """
    Subquery for identifying groupings of sigs for potential sig bundles.
    
    Does not contain sig objects themselves.
    """
    models = models or _load_hemonc_models()
    return (
    sa.select(
        models.Studies.study.label('single_study'),
        # categorised.c.study.label('sig_study'),
        categorised.c.phase.label('matched_phase'),
        categorised.c.variant_cui.label('matched_variant_cui')
    )
    .join(models.Studies, or_for_piped_col(categorised.c.study, models.Studies.study))
    .group_by(models.Studies, categorised.c.phase, categorised.c.variant_cui)
    .having(sa.and_(*build_component_requirement_expressions(component_requirements, categorised)))
    .subquery()
)

def subquery_sigs_valid_groups(valid_groups_subquery, models):
    """
    Subquery which attaches sig info to selected sig groupings.
    """
    return (
    sa.select(
        # valid_groups_subquery.c.sig_study,
        valid_groups_subquery.c.single_study,
        valid_groups_subquery.c.matched_phase,
        valid_groups_subquery.c.matched_variant_cui,
        models.Sigs.variant_cui,
        models.Sigs.phase,
        models.Sigs.study,
        models.Sigs.component,
        models.Sigs.id,
        models.Sigs.regimen,
        # models.Studies.study,
        # models.StudyResults.context,
        # models.Studies.start,

    )
    .join(valid_groups_subquery, sa.and_(
        join_not_distinct(models.Sigs.phase, valid_groups_subquery.c.matched_phase),
        join_not_distinct(models.Sigs.variant_cui, valid_groups_subquery.c.matched_variant_cui),
        get_sig_study_join(to=valid_groups_subquery.c.single_study)
    ))
    # .join(models.Studies, get_sig_study_join())
    # .join(models.StudyResults, models.Studies.study==models.StudyResults.study)
).subquery()


def get_base_sigs_df(source_phase, session, models: HemOncModels | None = None):
    """
    Df of sigs from phase-varcui-study groupings meeting component & curative intent 
    """
    models = models or _load_hemonc_models()
    comp_reqs = source_phase.component_requirements
    condition_cuis = source_phase.condition_cuis
    components = get_sig_component_stmt(condition_cuis, models, 
                                        non_curative=(not source_phase.curative_intent))
    categorised = categorise_components(components)

    valid_groups_subquery = get_siggroup_identifying_subquery(comp_reqs, categorised)
    sigs_for_valid_groups_subquery= subquery_sigs_valid_groups(valid_groups_subquery, models)
    stmt = sa.select(sigs_for_valid_groups_subquery, models.Sigs).join(models.Sigs, models.Sigs.id==sigs_for_valid_groups_subquery.c.id)
    return pd.DataFrame(session.execute(stmt).unique())


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

