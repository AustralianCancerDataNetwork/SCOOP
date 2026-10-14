import sqlalchemy as sa
from hemonc_alchemy.toolkit.analytics.treatment.filters import (
    standalone_radiation_sig_statement,
    studies_with_standalone_radiation_sigs_statement,
)
from scoop.protocols.hemonc.models import HemOncModels

def get_rt_only_sigs(condition_cuis, models: HemOncModels | None = None):
    del models
    return standalone_radiation_sig_statement(condition_cuis).subquery(
        "standalone_radiation_sigs"
    )

def query_rt_inc_study_ids(condition_cuis, models: HemOncModels | None = None):
    del models
    return studies_with_standalone_radiation_sigs_statement(condition_cuis)
