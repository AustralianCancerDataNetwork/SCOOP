from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import random
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from omop_alchemy.cdm.model import Concept, Concept_Ancestor


@dataclass(frozen=True)
class ResolvedModifierConcept:
    concept_id: int
    concept_code: str
    concept_name: str
    vocabulary_id: str


class ModifierMap:
    """Resolve a coarse modifier label into a valid sampled OMOP descendant concept."""

    def __init__(
        self,
        *,
        engine: Engine,
        vocabulary_id: str,
        parent_concepts: dict[str, int],
        domain_id: str | None = None,
        concept_class_id: str | None = None,
        require_standard: bool = True,
        include_ancestor: bool = False,
    ) -> None:
        self.engine = engine
        self.vocabulary_id = vocabulary_id
        self.parent_concepts = {
            key.lower(): int(value) for key, value in parent_concepts.items()
        }
        self.domain_id = domain_id
        self.concept_class_id = concept_class_id
        self.require_standard = require_standard
        self.include_ancestor = include_ancestor
        self._candidate_cache: dict[str, list[ResolvedModifierConcept]] = {}
        self._concept_cache: dict[int, ResolvedModifierConcept] = {}

    def _concept_from_row(self, row: Concept) -> ResolvedModifierConcept:
        return ResolvedModifierConcept(
            concept_id=int(row.concept_id),
            concept_code=str(row.concept_code),
            concept_name=str(row.concept_name),
            vocabulary_id=str(row.vocabulary_id),
        )

    def concept_by_id(self, concept_id: int) -> ResolvedModifierConcept | None:
        cached = self._concept_cache.get(concept_id)
        if cached is not None:
            return cached

        stmt = select(Concept).where(
            Concept.concept_id == concept_id,
            Concept.invalid_reason.is_(None),
        )
        if self.vocabulary_id is not None:
            stmt = stmt.where(Concept.vocabulary_id == self.vocabulary_id)
        if self.domain_id is not None:
            stmt = stmt.where(Concept.domain_id == self.domain_id)
        if self.concept_class_id is not None:
            stmt = stmt.where(Concept.concept_class_id == self.concept_class_id)
        if self.require_standard:
            stmt = stmt.where(Concept.standard_concept == "S")

        with Session(self.engine) as session:
            row = session.execute(stmt).scalar_one_or_none()

        if row is None:
            return None

        concept = self._concept_from_row(row)
        self._concept_cache[concept_id] = concept
        return concept

    def candidates(self, label: str) -> list[ResolvedModifierConcept]:
        if not isinstance(label, str):
            raise KeyError(f"Modifier labels must be strings, got {type(label)!r}.")

        normalized_label = label.lower()
        cached = self._candidate_cache.get(normalized_label)
        if cached is not None:
            return cached

        try:
            ancestor_concept_id = self.parent_concepts[normalized_label]
        except KeyError as exc:
            raise KeyError(f"No modifier parent concept configured for '{label}'.") from exc

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
        if not self.include_ancestor:
            stmt = stmt.where(Concept.concept_id != ancestor_concept_id)
        if self.vocabulary_id is not None:
            stmt = stmt.where(Concept.vocabulary_id == self.vocabulary_id)
        if self.domain_id is not None:
            stmt = stmt.where(Concept.domain_id == self.domain_id)
        if self.concept_class_id is not None:
            stmt = stmt.where(Concept.concept_class_id == self.concept_class_id)
        if self.require_standard:
            stmt = stmt.where(Concept.standard_concept == "S")

        with Session(self.engine) as session:
            rows = session.execute(stmt).scalars().all()

        concepts = [self._concept_from_row(row) for row in rows]
        if not concepts:
            fallback = self.concept_by_id(ancestor_concept_id)
            if fallback is None:
                raise KeyError(
                    "Unable to resolve any valid modifier concepts for "
                    f"label={label!r}, ancestor_concept_id={ancestor_concept_id}."
                )
            concepts = [fallback]

        self._candidate_cache[normalized_label] = concepts
        return concepts

    def sample(
        self,
        label: str,
        *,
        rng: random.Random | None = None,
    ) -> ResolvedModifierConcept:
        chooser = rng or random
        return chooser.choice(self.candidates(label))


class ModifierMapper:
    """Container for named modifier resolution maps."""

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
            raise ValueError("Modifier mapping must be a dict or path to valid YAML.")

        self.engine = engine
        self.config_root = config_root
        self.maps: dict[str, ModifierMap] = {}
        for domain, config in mapping.items():
            if not isinstance(domain, str) or not isinstance(config, dict):
                raise ValueError("Modifier mapping entries must be keyed by strings.")

            self.maps[domain] = ModifierMap(
                engine=engine,
                vocabulary_id=str(config["vocabulary_id"]),
                parent_concepts={
                    str(key): int(value)
                    for key, value in dict(config["parent_concepts"]).items()
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
                require_standard=bool(config.get("require_standard", True)),
                include_ancestor=bool(config.get("include_ancestor", False)),
            )

    def __call__(self, domain: str) -> ModifierMap:
        try:
            return self.maps[domain]
        except KeyError as exc:
            raise KeyError(f"No modifier map found for domain '{domain}'.") from exc

    def __repr__(self) -> str:
        return f"<ModifierMapper domains={list(self.maps)}>"
