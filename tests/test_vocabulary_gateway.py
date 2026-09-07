from pathlib import Path

from scoop.vocabulary.gateway import (
    DefaultVocabularyGateway,
    FakeVocabularyGateway,
    VocabularyMappers,
    VocabularyGateway,
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


class _StubHemOncResolver:
    """Stands in for the vocabulary walk so the test needs no OMOP database."""

    def __init__(self, mapping):
        self.mapping = mapping

    def resolve(self, cui, *, domain_id=None):
        return self.mapping.get((cui, domain_id))
