from sqlalchemy.dialects import postgresql
from pathlib import Path

from scoop.configuration.config_loader import load_profile
from scoop.configuration.protocol_registry import SourcePhaseYaml
from scoop.configuration.protocol_registry import load_protocol_registry
from scoop.schema.treatment_episode import build_graph_outcome
from scoop.schema.treatment_episode import Treatment_Episode
from scoop.utils.extra_filters import get_rt_only_sigs, query_rt_inc_study_ids
from scoop.utils.treatment_selection import get_sig_component_stmt
from scoop.utils.variant_selection import (
    build_variant_query_artifacts,
    build_variant_statement,
)


def _source_phase() -> SourcePhaseYaml:
    return SourcePhaseYaml.model_validate(
        {
            "condition_cuis": [46104],
            "component_requirements": {
                "component_lists": [["pembrolizumab"]],
                "category_restrictions": {
                    "immunotherapy": {"type": "exact", "amount": 1},
                },
            },
            "study_restriction": None,
            "type": "adjuvant",
        }
    )


def test_protocol_phase_builds_current_variant_statement():
    statement = build_variant_statement(_source_phase())
    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "variants" in sql
    assert "variants_study" in sql
    assert "ranked_variants" in sql


def test_protocol_phase_exposes_inspectable_query_artifacts():
    artifacts = build_variant_query_artifacts(_source_phase())

    assert artifacts.categorised_components_subquery.name == "categorized_components"
    assert artifacts.variant_cuis_subquery.name == "matching_variant_ids"
    assert "variants" in str(
        artifacts.components_subquery.compile(dialect=postgresql.dialect())
    )


def test_sig_and_radiotherapy_helpers_use_normalized_study_links():
    for statement in (
        get_sig_component_stmt([46104]),
        get_rt_only_sigs([46104]),
        query_rt_inc_study_ids([46104]),
    ):
        sql = str(statement.compile(dialect=postgresql.dialect()))
        assert "sigs" in sql
        assert "study" in sql


def test_sig_component_projection_joins_projected_drug_fields():
    sql = str(
        get_sig_component_stmt([46104]).compile(dialect=postgresql.dialect())
    )

    assert "LEFT OUTER JOIN drugs ON drugs.drug_cui = sigs.component_cui" in sql
    assert "LEFT OUTER JOIN drugs_canmed_major_class" in sql
    assert "LEFT OUTER JOIN drugs_canmed_minor_class" in sql
    assert ", drugs," not in sql


def test_graph_outcome_uses_current_direct_endpoint_model():
    outcome = build_graph_outcome(
        {"treatment_episode.example": "example"},
        source_profile="nsclc",
    )

    assert outcome.primary_category_id == "example"
    assert outcome.active_endpoints == ("example",)


def test_nsclc_protocol_registry_loads_without_database_session():
    registry = load_protocol_registry(
        Path("scoop/configuration/profiles/nsclc/nsclc_protocol_registry.yaml")
    )

    assert registry.label == "nsclc"
    assert registry.default_conditions == [
        "Lung cancer",
        "Non-small cell lung cancer",
    ]
    assert "egfr_intervention" in registry.entries
    assert registry.entries["egfr_intervention"].resolver.type == "component_query"


def test_nsclc_profile_attaches_protocol_registry_to_runtime():
    context = load_profile(
        "nsclc",
        base_dir=Path("scoop/configuration"),
    )

    assert context.protocol_registry is not None
    assert context.protocol_registry.label == "nsclc"


def test_graph_outcome_uses_protocol_entry_metadata():
    registry = load_protocol_registry(
        Path("scoop/configuration/profiles/nsclc/nsclc_protocol_registry.yaml")
    )
    outcome = build_graph_outcome(
        {"treatment_episode.egfr_intervention": "egfr_intervention"},
        registry=registry,
        source_profile="nsclc",
    )

    assert outcome.resolver_type == "component_query"
    assert outcome.bundle_recipe_id is None


def test_treatment_episode_emits_only_explicit_events():
    episode = Treatment_Episode(
        person_id=1,
        condition_occurrence_id=2,
        regimen_type="unresolved",
        regimen_category_id="unresolved",
    )

    assert episode.roll_events() == ([], [])


def test_treatment_episode_rolls_events_from_bundle_phases():
    episode = Treatment_Episode(
        person_id=1,
        condition_occurrence_id=2,
        regimen_type="example",
        regimen_category_id="example",
        bundle_phase_summary=[
            {
                "phase_id": "systemic",
                "drug_exposures": [{"drug_concept_id": 123}],
                "procedures": [{"procedure_concept_id": 456}],
            }
        ],
    )

    assert episode.roll_events() == (
        [{"drug_concept_id": 123}],
        [{"procedure_concept_id": 456}],
    )
