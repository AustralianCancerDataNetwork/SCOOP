"""Explicit accessors for the vocabulary mapper bundle during migration."""

from __future__ import annotations

from pathlib import Path

from .gateway import (
    DefaultVocabularyGateway,
    VocabularyMappers,
    get_default_vocabulary_gateway,
)


def get_vocabulary_mappers(
    gateway: DefaultVocabularyGateway | None = None,
    *,
    base_dir: Path | None = None,
) -> VocabularyMappers:
    """Return lazily constructed mappers for legacy callers.

    The old module-level mapper singletons were removed. This function keeps the
    transitional access explicit while the remaining patient callers move to the
    gateway-owned bundle.
    """

    if gateway is not None:
        return gateway.get_mappers()
    if base_dir is None:
        return get_default_vocabulary_gateway().get_mappers()
    return DefaultVocabularyGateway(base_dir).get_mappers()


__all__ = ["get_vocabulary_mappers"]
