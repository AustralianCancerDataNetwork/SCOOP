"""Shared category vocabulary for component classification.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..spec import BroadCategory

__all__ = ["BroadCategory", "Unclassified"]


@dataclass(frozen=True)
class Unclassified:
    """A component that no declared rule, in any layer, assigned a category.

    This is a value, never `None` and never a silent SQL `NULL` (CLS-1): a
    caller that receives an `Unclassified` knows exactly which component was
    unrecognised and can decide how to treat it, rather than the component
    quietly contributing zero to every category count the way a `NULL`
    `broad_category` does in the legacy SQL path.
    """

    component: str | None
    reason: str
