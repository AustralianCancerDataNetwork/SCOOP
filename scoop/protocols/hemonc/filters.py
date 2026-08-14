import sqlalchemy as sa
from scoop.protocols.hemonc.models import (
    HemOncModels,
    _load_hemonc_models,
    get_sig_study_join,
)

def get_rt_only_sigs(condition_cuis, models: HemOncModels | None = None):
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

def query_rt_inc_study_ids(condition_cuis, models: HemOncModels | None = None):
    models = models or _load_hemonc_models()
    rt_only_sigs = get_rt_only_sigs(condition_cuis)
    
    return (
    sa.select(models.Studies.id)
    .join(models.Sigs, get_sig_study_join(), full=True)
    .where(models.Sigs.id == rt_only_sigs.c.id)
    .distinct()
    )

    # exlcude conc crt studies