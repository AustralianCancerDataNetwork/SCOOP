from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa
from omop_alchemy.cdm.model.vocabulary import Concept, Concept_Synonym

from scoop.vocabulary.gateway import (
    DefaultVocabularyGateway,
    FakeVocabularyGateway,
    VocabularyGateway,
    VocabularyMappers,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = ROOT / "scoop"


def test_default_gateway_constructs_mappers_lazily_and_caches_them():
    engine_calls: list[str] = []
    gateway = DefaultVocabularyGateway(
        CONFIG_ROOT,
        engine_factory=lambda: engine_calls.append("engine") or None,
    )

    assert gateway._mapper_cache == {}
    mappers = gateway.get_mappers()

    assert isinstance(mappers, VocabularyMappers)
    assert len(engine_calls) == 1
    assert gateway.get_mappers() is mappers
    assert len(engine_calls) == 1


def test_profile_map_does_not_own_routes_components_or_genomic_results():
    gateway = DefaultVocabularyGateway(CONFIG_ROOT, engine_factory=lambda: None)

    domains = set(gateway.get_mappers().concept_mapper.maps)

    assert "route" not in domains
    assert "drug" not in domains
    assert "procedure" not in domains
    assert "genomic_modifier" not in domains
    assert "variant_result" not in domains


def test_fake_gateway_can_supply_a_mapper_bundle_without_external_state():
    bundle = VocabularyMappers(
        concept_mapper=object(),
        condition_mapper=object(),
        modifier_mapper=object(),
        episode_mapper=object(),
        measurement_mapper=object(),
        observation_mapper=object(),
    )
    gateway = FakeVocabularyGateway(mapper_bundle=bundle)

    assert isinstance(gateway, VocabularyGateway)
    assert gateway.get_mappers() is bundle


def test_component_resolution_prefers_the_hemonc_cui_over_the_name_map():
    # HemOnc CUIs traverse to a standard concept in the vocabulary itself, so a
    # component carrying one must not fall through to a generic name lookup.
    gateway = DefaultVocabularyGateway(CONFIG_ROOT, engine_factory=lambda: None)
    gateway._hemonc = _StubHemOncResolver({(394, "Drug"): 45775965})

    assert gateway.resolve_component_concept("drug", "Pembrolizumab", 394) == 45775965


def test_component_resolution_falls_back_when_hemonc_has_no_mapping():
    # The fake supplies a name fallback without requiring a vocabulary database.
    # The production gateway performs the same fallback through OMOPConceptSource.
    gateway = FakeVocabularyGateway(
        concepts={
            ("drug", "Pembrolizumab"): 45775965,
            ("procedure", "External beam radiotherapy"): 4141448,
        },
        component_concepts={},
    )

    assert gateway.resolve_component_concept("drug", "Pembrolizumab", 394) == 45775965
    assert (
        gateway.resolve_component_concept("procedure", "External beam radiotherapy", 1608)
        == 4141448
    )


def test_route_resolution_is_a_separate_vocabulary_source():
    gateway = FakeVocabularyGateway(route_concepts={"intravenous": 4171047})

    assert gateway.resolve_route_concept("intravenous") == 4171047
    assert gateway.resolve_route_concept("ns") is None


def test_gateway_synonym_fallback_uses_schema_map_and_rejects_ambiguity(monkeypatch):
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS omop")
    routed = engine.execution_options(schema_translate_map={None: "omop"})
    Concept.__table__.create(routed)
    Concept_Synonym.__table__.create(routed)
    with routed.begin() as connection:
        connection.execute(
            Concept.__table__.insert(),
            [
                {
                    "concept_id": concept_id,
                    "concept_name": f"Drug {concept_id}",
                    "domain_id": "Drug",
                    "vocabulary_id": "RxNorm",
                    "concept_class_id": "Ingredient",
                    "standard_concept": "S",
                    "concept_code": str(concept_id),
                    "valid_start_date": date(2000, 1, 1),
                    "valid_end_date": date(2099, 12, 31),
                    "invalid_reason": None,
                }
                for concept_id in (10, 11)
            ],
        )
        connection.execute(
            Concept_Synonym.__table__.insert(),
            [
                {
                    "concept_id": concept_id,
                    "concept_synonym_name": synonym,
                    "language_concept_id": 4180186,
                }
                for concept_id, synonym in (
                    (10, "Only synonym"),
                    (10, "Shared synonym"),
                    (11, "Shared synonym"),
                )
            ],
        )

    class NoNameMatch:
        def lookup(self, _value):
            return None

    from omop_alchemy.toolkit.core import concepts

    monkeypatch.setattr(
        concepts,
        "make_concept_resolver",
        lambda *_args, **_kwargs: NoNameMatch(),
    )
    gateway = DefaultVocabularyGateway(engine_factory=lambda: routed)
    assert gateway.resolve_component_concept("drug", "Only synonym") == 10
    with pytest.raises(LookupError, match="is ambiguous"):
        gateway.resolve_component_concept("drug", "Shared synonym")

    engine.dispose()


class _StubHemOncResolver:
    """Stands in for the vocabulary walk so the test needs no OMOP database."""

    def __init__(self, mapping):
        self.mapping = mapping

    def resolve(self, cui, *, domain_id=None):
        return self.mapping.get((cui, domain_id))
