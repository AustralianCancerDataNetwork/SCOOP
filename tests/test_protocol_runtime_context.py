from pathlib import Path

from scoop.patients.treatment_episode import Treatment_Episode, build_graph_outcome
from scoop.profiles.manifest import load_profile
from scoop.vocabulary.gateway import DefaultVocabularyGateway


def _nsclc_context():
    root = Path(__file__).parents[1] / "scoop"
    return load_profile(
        "nsclc",
        base_dir=root,
        vocabulary_gateway=DefaultVocabularyGateway(
            root,
            engine_factory=lambda: None,
        ),
    )


def test_nsclc_profile_attaches_typed_registry_and_intervention_catalog():
    context = _nsclc_context()

    assert context.registry_spec is not None
    assert context.registry_spec.label == "nsclc"
    assert context.intervention_catalog is not None
    assert context.intervention_catalog.profile_id == "nsclc"


def test_graph_outcome_resolves_against_intervention_catalog():
    context = _nsclc_context()

    outcome = build_graph_outcome(
        {
            "treatment_episode.sbrt": True,
            "condition_occurrence.biomarker": "egfr",
        },
        intervention_catalog=context.intervention_catalog,
        source_profile="nsclc",
    )

    assert outcome.primary_category_id == "sbrt"
    assert outcome.resolver_type == "phaseref"
    assert outcome.graph_alias_used is None


def test_treatment_episode_does_not_emit_placeholder_events():
    episode = Treatment_Episode(
        person_id=1,
        condition_occurrence_id=2,
        regimen_type="unresolved",
        regimen_category_id="unresolved",
    )

    assert episode.roll_events() == ([], [])
