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
