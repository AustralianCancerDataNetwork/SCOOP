from pathlib import Path

import pytest
import sqlalchemy as sa
from oa_configurator import (
    ConnectionConfig,
    GenericDatabaseConfig,
    Resolver,
    StackConfig,
)

from scoop.config import ScoopConfig, create_hemonc_engine, get_hemonc_context


def stack_with_hemonc_databases() -> StackConfig:
    return StackConfig.for_session(
        connections={
            "hemonc": ConnectionConfig(
                dialect="sqlite",
                database_name=":memory:",
            ),
            "hemonc_test": ConnectionConfig(
                dialect="sqlite",
                database_name=":memory:",
                test_only=True,
            ),
        },
        databases={
            "hemonc_db": GenericDatabaseConfig(
                connection="hemonc",
                schema_name=None,
            ),
            "test_hemonc_db": GenericDatabaseConfig(
                connection="hemonc_test",
                schema_name=None,
            ),
        },
        tools={
            "scoop": {
                "hemonc_db": "hemonc_db",
                "test_hemonc_db": "test_hemonc_db",
            }
        },
    )


def test_scoop_config_resolves_production_and_test_database_references():
    config = stack_with_hemonc_databases()

    resolved = ScoopConfig.validate_candidate(config)

    assert resolved.hemonc_db == "hemonc_db"
    assert resolved.test_hemonc_db == "test_hemonc_db"


def test_get_hemonc_context_resolves_without_creating_an_engine(monkeypatch):
    config = stack_with_hemonc_databases()
    monkeypatch.setattr("scoop.config.load_stack_config", lambda: config)

    package_config, resolved = get_hemonc_context()

    assert isinstance(package_config, ScoopConfig)
    assert package_config.hemonc_db == "hemonc_db"
    assert resolved.name == "hemonc_db"
    assert resolved.connection.safe_url.startswith("sqlite://")


def test_create_hemonc_engine_uses_resolved_database():
    config = stack_with_hemonc_databases()
    resolved = Resolver(config).resolve_database("hemonc_db")

    engine = create_hemonc_engine(resolved)
    try:
        assert isinstance(engine, sa.Engine)
    finally:
        engine.dispose()


def test_missing_stack_config_has_an_actionable_error(monkeypatch):
    def missing_config():
        raise FileNotFoundError(Path("missing-config.toml"))

    monkeypatch.setattr("scoop.config.load_stack_config", missing_config)

    with pytest.raises(RuntimeError, match="omop-config configure scoop"):
        get_hemonc_context()
