from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from omop_alchemy.cdm.model import Concept, Concept_Ancestor


@dataclass(frozen=True)
class ResolvedConditionConcept:
    concept_id: int
    concept_code: str
    concept_name: str
    vocabulary_id: str


class ConditionMap:
    """Resolve a morphology/topography pair into a concrete OMOP condition concept."""

    def __init__(
        self,
        *,
        engine: Engine,
        vocabulary_id: str,
        default_morphology_code: str,
        default_topography_code: str,
        morphology_codes: dict[str, str],
        topography_codes: dict[str, str],
        domain_id: str | None = None,
        concept_class_id: str | None = None,
    ) -> None:
        self.engine = engine
        self.vocabulary_id = vocabulary_id
        self.default_morphology_code = default_morphology_code
        self.default_topography_code = default_topography_code
        self.domain_id = domain_id
        self.concept_class_id = concept_class_id
        self.morphology_codes = {
            key.lower(): value for key, value in morphology_codes.items()
        }
        self.topography_codes = {
            key.lower(): value for key, value in topography_codes.items()
        }
        self._concept_cache: dict[str, ResolvedConditionConcept] = {}

    def _morphology_code(self, value: str | None) -> str:
        if not isinstance(value, str):
            return self.default_morphology_code
        return self.morphology_codes.get(value.lower(), self.default_morphology_code)

    def _topography_code(self, value: str | None) -> str:
        if not isinstance(value, str):
            return self.default_topography_code
        return self.topography_codes.get(value.lower(), self.default_topography_code)

    def _concept_from_row(self, row: Concept) -> ResolvedConditionConcept:
        return ResolvedConditionConcept(
            concept_id=int(row.concept_id),
            concept_code=str(row.concept_code),
            concept_name=str(row.concept_name),
            vocabulary_id=str(row.vocabulary_id),
        )

    def concept_by_code(self, concept_code: str) -> ResolvedConditionConcept | None:
        cached = self._concept_cache.get(concept_code)
        if cached is not None:
            return cached

        stmt = select(Concept).where(
            Concept.concept_code == concept_code,
            Concept.vocabulary_id == self.vocabulary_id,
            Concept.invalid_reason.is_(None),
        )
        if self.domain_id is not None:
            stmt = stmt.where(Concept.domain_id == self.domain_id)
        if self.concept_class_id is not None:
            stmt = stmt.where(Concept.concept_class_id == self.concept_class_id)

        with Session(self.engine) as session:
            row = session.execute(stmt).scalar_one_or_none()

        if row is None:
            return None

        concept = self._concept_from_row(row)
        self._concept_cache[concept_code] = concept
        return concept

    def descendants_of_concept(self, ancestor_concept_id: int) -> list[ResolvedConditionConcept]:
        stmt = (
            select(Concept)
            .join(
                Concept_Ancestor,
                Concept.concept_id == Concept_Ancestor.descendant_concept_id,
            )
            .where(
                Concept_Ancestor.ancestor_concept_id == ancestor_concept_id,
                Concept.invalid_reason.is_(None),
            )
        )
        if self.vocabulary_id is not None:
            stmt = stmt.where(Concept.vocabulary_id == self.vocabulary_id)
        if self.domain_id is not None:
            stmt = stmt.where(Concept.domain_id == self.domain_id)
        if self.concept_class_id is not None:
            stmt = stmt.where(Concept.concept_class_id == self.concept_class_id)

        with Session(self.engine) as session:
            rows = session.execute(stmt).scalars().all()

        return [self._concept_from_row(row) for row in rows]

    def resolve(
        self,
        *,
        morphology: str | None,
        topography: str | None = None,
    ) -> ResolvedConditionConcept:
        morphology_code = self._morphology_code(morphology)
        topography_code = self._topography_code(topography)

        candidate_codes = [
            f"{morphology_code}-{topography_code}",
            f"{morphology_code}-{self.default_topography_code}",
            f"{self.default_morphology_code}-{self.default_topography_code}",
        ]
        for candidate_code in candidate_codes:
            concept = self.concept_by_code(candidate_code)
            if concept is not None:
                return concept

        raise KeyError(
            "Unable to resolve condition concept for "
            f"morphology={morphology!r}, topography={topography!r}."
        )


class ConditionMapper:
    """Container for named condition resolution maps."""

    def __init__(
        self,
        mapping: str | Path | dict[str, Any],
        *,
        engine: Engine,
        base_dir: Path | None = None,
    ):
        if isinstance(mapping, (str, Path)):
            mapping_path = Path(mapping)
            with mapping_path.open() as handle:
                mapping = yaml.safe_load(handle)
            config_root = mapping_path.parent
        else:
            config_root = base_dir or Path.cwd()

        if not isinstance(mapping, dict):
            raise ValueError("Condition mapping must be a dict or path to valid YAML.")

        self.engine = engine
        self.config_root = config_root
        self.maps: dict[str, ConditionMap] = {}
        for domain, config in mapping.items():
            if not isinstance(domain, str) or not isinstance(config, dict):
                raise ValueError("Condition mapping entries must be keyed by strings.")

            self.maps[domain] = ConditionMap(
                engine=engine,
                vocabulary_id=str(config["vocabulary_id"]),
                default_morphology_code=str(config["default_morphology_code"]),
                default_topography_code=str(config["default_topography_code"]),
                morphology_codes={
                    str(key): str(value)
                    for key, value in dict(config["morphology_codes"]).items()
                },
                topography_codes={
                    str(key): str(value)
                    for key, value in dict(config["topography_codes"]).items()
                },
                domain_id=(
                    str(config["domain_id"])
                    if config.get("domain_id") is not None
                    else None
                ),
                concept_class_id=(
                    str(config["concept_class_id"])
                    if config.get("concept_class_id") is not None
                    else None
                ),
            )

    def __call__(self, domain: str) -> ConditionMap:
        try:
            return self.maps[domain]
        except KeyError as exc:
            raise KeyError(f"No condition map found for domain '{domain}'.") from exc

    def __repr__(self) -> str:
        return f"<ConditionMapper domains={list(self.maps)}>"
