"""Resolve HemOnc component CUIs to standard OMOP concepts.

A scheduled administration carries the HemOnc CUI of its component. That CUI is
the ``concept_code`` of the corresponding HemOnc concept in the OMOP vocabulary,
which in turn carries a "Maps to" edge to a standard RxNorm ingredient. Walking
that edge is strictly better than a hand-maintained name table: it stays correct
as HemOnc grows, and it cannot disagree with the vocabulary the exported rows
will be queried against.

A HemOnc CUI is *not* an OMOP concept_id and must never be written into a
``*_concept_id`` column directly.

Not every HemOnc component has a "Maps to" edge -- radiotherapy components and
BCG are modelled in HemOnc's Drug domain with no standard equivalent -- so
callers may then perform an OMOP vocabulary name lookup or report the component
as unresolved. No HemOnc component IDs are duplicated in SCOOP configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

HEMONC_VOCABULARY_ID = "HemOnc"
MAPS_TO = "Maps to"


@dataclass
class HemOncConceptResolver:
    """Look up standard OMOP concepts for HemOnc CUIs, memoised per engine."""

    engine: Any
    _cache: dict[tuple[int, str | None], int | None] = field(
        default_factory=dict, repr=False
    )

    def resolve(self, cui: int | None, *, domain_id: str | None = None) -> int | None:
        """Return the standard concept_id for a HemOnc CUI, or None.

        ``domain_id`` constrains the target, so a drug lookup cannot land on a
        procedure concept (or vice versa) if HemOnc ever maps across domains.
        """

        if cui is None or self.engine is None:
            return None

        key = (int(cui), domain_id)
        if key in self._cache:
            return self._cache[key]

        resolved = self._query(int(cui), domain_id)
        self._cache[key] = resolved
        return resolved

    def _query(self, cui: int, domain_id: str | None) -> int | None:
        from omop_alchemy.cdm.model import Concept, Concept_Relationship

        source = Concept.__table__.alias("hemonc_source")
        target = Concept.__table__.alias("standard_target")
        relationship = Concept_Relationship.__table__

        stmt = (
            select(target.c.concept_id)
            .select_from(
                source.join(
                    relationship,
                    source.c.concept_id == relationship.c.concept_id_1,
                ).join(
                    target,
                    relationship.c.concept_id_2 == target.c.concept_id,
                )
            )
            .where(
                source.c.vocabulary_id == HEMONC_VOCABULARY_ID,
                source.c.concept_code == str(cui),
                relationship.c.relationship_id == MAPS_TO,
                relationship.c.invalid_reason.is_(None),
                target.c.standard_concept == "S",
                target.c.invalid_reason.is_(None),
            )
            .limit(1)
        )
        if domain_id is not None:
            stmt = stmt.where(target.c.domain_id == domain_id)

        with Session(self.engine) as session:
            return session.execute(stmt).scalar_one_or_none()


__all__ = ["HEMONC_VOCABULARY_ID", "HemOncConceptResolver"]
