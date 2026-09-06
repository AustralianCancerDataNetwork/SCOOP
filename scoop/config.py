"""SCOOP's oa-configurator database backing definition."""

from __future__ import annotations

from typing import Annotated, ClassVar

from oa_configurator import (
    GenericDatabaseConfig,
    PackageConfigBase,
    RefTo,
    ResolvedDatabase,
    Resolver,
    load_stack_config,
)


class ScoopConfig(PackageConfigBase):
    """Typed view of SCOOP's shared stack configuration.

    HemOnc is represented as a generic database because its schema is owned by
    ``hemonc_alchemy`` rather than being an OMOP CDM database bundle.
    """

    tool_name: ClassVar[str] = "scoop"

    hemonc_db: Annotated[str, RefTo(GenericDatabaseConfig)] = "hemonc_db"
    test_hemonc_db: Annotated[
        str | None, RefTo(GenericDatabaseConfig, is_test=True)
    ] = None


def get_hemonc_context() -> tuple[ScoopConfig, ResolvedDatabase]:
    """Resolve SCOOP's configured HemOnc database without creating an engine."""

    try:
        stack = load_stack_config()
    except FileNotFoundError as exc:
        raise RuntimeError(
            "No SCOOP configuration found. Run `omop-config configure scoop` "
            "to set up the HemOnc database."
        ) from exc

    resolver = Resolver(stack)
    package_config = resolver.resolve_package_config(ScoopConfig)
    resolved = resolver.resolve_database(package_config.hemonc_db)
    return package_config, resolved


def create_hemonc_engine(resolved: ResolvedDatabase, **engine_kwargs):
    """Create an engine for an already-resolved HemOnc database."""

    return resolved.create_engine(**engine_kwargs)


__all__ = ["ScoopConfig", "create_hemonc_engine", "get_hemonc_context"]
