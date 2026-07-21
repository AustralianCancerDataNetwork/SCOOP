
import sqlalchemy as sa
import scoop.utils.variant_selection as vs
from scoop.utils.hemonc_helpers import (
    HemOncModels,
    _load_hemonc_models,
    get_sig_study_join,
)
from scoop.utils.treatment_selection import (
    CONCURRENT_CRT_REQUIREMENTS
)

def get_sig_objects(subquery, models: HemOncModels | None = None):
    models = models or _load_hemonc_models()
    return sa.select(models.Sigs).join(subquery, subquery.c.id==models.Sigs.id)

def rt_only_sigs(condition_cuis, models: HemOncModels | None = None):
    models = models or _load_hemonc_models()
    rt_only_sigs = (sa.select(models.Sigs)
    .join(models.Studies, get_sig_study_join(), full=True)
    .where(models.Studies.condition_cui.in_(condition_cuis),
           models.Sigs.class_field == "rad sig",
           models.Sigs.regimen == "Radiation therapy", # avoiding whole brain irradiation
           models.Sigs.variant_cui == None
          )      
    .distinct()
    ).subquery()

    return rt_only_sigs


def build_seq_crt_sigs_stmt(condition_cuis, models: HemOncModels | None = None):
    models = models or _load_hemonc_models()
    nsclc_rt_only_sigs = rt_only_sigs(condition_cuis)
    
    rt_sigs_studies = (
    sa.select(models.Studies)
    .join(models.Sigs, get_sig_study_join(), full=True)
    .where(models.Sigs.id == nsclc_rt_only_sigs.c.id)
    .distinct()
    ).subquery()
    
    rt_studies_sigs = (
    sa.select(models.Sigs)
    .join(models.Studies, get_sig_study_join(), full=True)
    .where(models.Studies.study==rt_sigs_studies.c.study)
    ).subquery()

    component_reqs = CONCURRENT_CRT_REQUIREMENTS
    
    conc_crt_var_cuis = vs.get_var_cuis_by_component(condition_cuis, component_reqs)
    conc_crt_sigs = (
    sa.select(models.Sigs)
        .join(conc_crt_var_cuis, conc_crt_var_cuis.c.variant_cui==models.Sigs.variant_cui)
    ).subquery()
    
    seq_crt_sigs = (
    sa.select(rt_studies_sigs)
        .where(rt_studies_sigs.c.id.not_in(sa.select(conc_crt_sigs.c.id)))
        .distinct().subquery()
    )
    return seq_crt_sigs


###############################################################################

def group_siglist_on_study(siglist):
    # This looks sensitive to order
    # TODO: add sorting siglist on sig study step
    grouped_list = []
    study_list = [siglist[0].study]
    sublist = []
    for sig in siglist:
        if sig.study != study_list[-1]:
            grouped_list.append(sublist)
            sublist = []
        sublist.append(sig)
        study_list.append(sig.study)
    if sig.study == study_list[-1]:
        grouped_list.append(sublist)
    return grouped_list