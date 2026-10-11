"""Database-backed vocabulary checks for regimen registries."""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Sequence

import sqlalchemy as sa
import yaml
from sqlalchemy.orm import Session

from hemonc_alchemy.model import (
    Drugs,
    Sigs,
    Studies,
    drugs_Canmed_major_classMap,
    drugs_Canmed_minor_classMap,
    sigs_StudyMap,
)

from scoop.config import create_hemonc_engine, get_hemonc_context
from scoop.profiles.paths import PROFILES_ROOT
from scoop.protocols.spec import ComponentCui, ComponentTerm, RegistrySpec

from .models import (
    COMPONENT_CLASS_MAPS,
    COMPONENT_STR_COLS,
)
from .queries import hemonc_subsumes_descendant_cuis, omop_vocabulary_tables

_DEFAULT_ALLOWLIST = Path(__file__).with_name("class_membership_allowlist.yaml")


@dataclass(frozen=True)
class MembershipAcknowledgement:
    cui: int
    reason: str
    reviewed: date


@dataclass
class RegistryLintReport:
    checked_drugs: int = 0
    checked_classes: int = 0
    checked_category_keys: int = 0
    checked_family_components: int = 0
    class_memberships: dict[str, tuple[int, int]] = field(default_factory=dict)
    class_divergences: dict[str, tuple[tuple[int, ...], tuple[int, ...]]] = field(
        default_factory=dict
    )
    class_concept_names: dict[int, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _component_rows(session: Session, condition_cuis: set[int] | None = None):
    statement = (
        sa.select(
            Sigs.id.label("sig_id"),
            Sigs.component_cui,
            Sigs.component,
            Drugs.main_class,
            drugs_Canmed_major_classMap.canmed_major_class,
            drugs_Canmed_minor_classMap.canmed_minor_class,
        )
        .select_from(Sigs)
    )
    if condition_cuis is not None:
        statement = (
            statement.join(sigs_StudyMap, sigs_StudyMap.parent_id == Sigs.id)
            .join(Studies, Studies.study == sigs_StudyMap.study)
            .where(Studies.condition_cui.in_(condition_cuis))
        )
    statement = statement.outerjoin(Drugs, Drugs.drug_cui == Sigs.component_cui)
    statement = statement.outerjoin(
        drugs_Canmed_major_classMap,
        drugs_Canmed_major_classMap.parent_id == Drugs.id,
    ).outerjoin(
        drugs_Canmed_minor_classMap,
        drugs_Canmed_minor_classMap.parent_id == Drugs.id,
    )
    return session.execute(statement).mappings().all()


def _registry_terms(registry: RegistrySpec) -> set[ComponentTerm]:
    return {
        term
        for phase in registry.source_phases.values()
        for component_list in phase.component_requirements.component_lists
        for term in component_list
    }


def _class_concepts(session: Session, names: set[str]):
    concept, _ = omop_vocabulary_tables(session)
    return session.execute(
        sa.select(
            concept.c.concept_name,
            concept.c.concept_code,
            concept.c.concept_id,
        ).where(
            concept.c.vocabulary_id == "HemOnc",
            concept.c.concept_class_id == "Component Class",
            concept.c.concept_name.in_(names),
            sa.func.nullif(sa.func.trim(concept.c.invalid_reason), "").is_(None),
        )
    ).mappings().all()


def _drug_cui_counts(session: Session) -> dict[int, int]:
    return dict(
        session.execute(
            sa.select(Drugs.drug_cui, sa.func.count())
            .group_by(Drugs.drug_cui)
        ).all()
    )


def _source_values(session: Session) -> dict[str, set[str]]:
    columns = {
        "canmed_major_class": drugs_Canmed_major_classMap.canmed_major_class,
        "canmed_minor_class": drugs_Canmed_minor_classMap.canmed_minor_class,
        "main_class": Drugs.main_class,
        "component": Sigs.component,
    }
    values: dict[str, set[str]] = {}
    for name, column in columns.items():
        statement = sa.select(column).where(column.is_not(None)).distinct()
        values[name] = set(session.scalars(statement))
    return values


def _family_condition_cuis(session: Session, registry: RegistrySpec) -> set[int]:
    from .models import condition_cuis_by_names

    cuis: set[int] = set()
    for phase in registry.source_phases.values():
        conditions = phase.conditions
        if conditions.cuis is not None:
            cuis.update(conditions.cuis)
        elif conditions.names:
            cuis.update(condition_cuis_by_names(conditions.names, session))
    return cuis


def _index_component_rows(rows):
    matched: dict[str, set[tuple[int, int]]] = defaultdict(set)
    ids_by_cui: dict[int, set[int]] = defaultdict(set)
    for row in rows:
        sig_id = int(row["sig_id"])
        cui = int(row["component_cui"])
        ids_by_cui[cui].add(sig_id)
        for column in COMPONENT_STR_COLS:
            if (value := row[column]) is not None:
                matched[str(value).casefold()].add((sig_id, cui))
    return matched, ids_by_cui


def _check_label_membership(name, cui, matched, ids_by_cui, report):
    rows = matched[name.casefold()]
    text_cuis = {matched_cui for _, matched_cui in rows}
    if text_cuis != {cui}:
        report.errors.append(
            f"Component label {name!r} matches CUIs {sorted(text_cuis)}, expected only {cui}."
        )
    text_ids = {sig_id for sig_id, _ in rows}
    cui_ids = ids_by_cui[cui]
    if text_ids != cui_ids:
        report.errors.append(
            f"Component label {name!r} changes matched signature set: "
            f"text={len(text_ids)}, CUI={len(cui_ids)}."
        )


def _check_drug_reference(term, drug_counts, matched, ids_by_cui, report):
    report.checked_drugs += 1
    if isinstance(term, ComponentCui):
        cui = term.cui
        if drug_counts.get(cui, 0) != 1:
            report.errors.append(
                f"CUI {cui} resolves to {drug_counts.get(cui, 0)} drugs.drug_cui rows."
            )
        if term.name:
            _check_label_membership(term.name, cui, matched, ids_by_cui, report)
        return
    text_cuis = {cui for _, cui in matched[term.casefold()]}
    if len(text_cuis) != 1:
        report.errors.append(
            f"Drug name {term!r} matches {len(text_cuis)} component CUIs: {sorted(text_cuis)}."
        )
        return
    cui = next(iter(text_cuis))
    if drug_counts.get(cui, 0) != 1:
        report.errors.append(
            f"Drug name {term!r} maps to CUI {cui}, which resolves to "
            f"{drug_counts.get(cui, 0)} drugs.drug_cui rows."
        )
    _check_label_membership(term, cui, matched, ids_by_cui, report)


def _check_class_reference(term, classes, source_values, report):
    matches = classes.get(term, [])
    if not matches:
        return False
    report.checked_classes += 1
    if len(matches) != 1:
        report.errors.append(
            f"Class {term!r} resolves to {len(matches)} HemOnc Component Class concepts."
        )
    if not any(
        term in source_values[column]
        for column in ("canmed_major_class", "canmed_minor_class", "main_class")
    ):
        report.errors.append(
            f"Class {term!r} is absent from the matcher CANMED/main_class values."
        )
    return True


def _check_class_memberships(session, names, classes, matched, ids_by_cui, report):
    for name in sorted(names):
        class_row = classes[name][0]
        text_cuis = {cui for _, cui in matched[name.casefold()]}
        descendant_codes = hemonc_subsumes_descendant_cuis(
            session, [class_row["concept_code"]]
        )
        hierarchy_cuis = {
            int(code) for code in descendant_codes if code.isdecimal()
        } & set(ids_by_cui)
        report.class_memberships[name] = (len(text_cuis), len(hierarchy_cuis))
        if text_cuis != hierarchy_cuis:
            report.class_divergences[name] = (
                tuple(sorted(text_cuis - hierarchy_cuis)),
                tuple(sorted(hierarchy_cuis - text_cuis)),
            )


def _parse_allowance(entry, seen, class_name, side):
    if not isinstance(entry, dict) or set(entry) != {"cui", "reason", "reviewed"}:
        raise ValueError("Allowlist entries require cui, reason, and reviewed fields.")
    cui, reason, reviewed = entry["cui"], entry["reason"], entry["reviewed"]
    if not isinstance(cui, int) or cui in seen:
        raise ValueError(f"Allowlist CUIs must be unique integers for {class_name!r}.{side}.")
    if not isinstance(reason, str) or not reason.strip() or "\n" in reason:
        raise ValueError(f"Allowlist reason must be one non-empty line for CUI {cui}.")
    if isinstance(reviewed, date):
        reviewed_date = reviewed
    elif isinstance(reviewed, str):
        try:
            reviewed_date = date.fromisoformat(reviewed)
        except ValueError as exc:
            raise ValueError(f"Allowlist review date must be ISO format for CUI {cui}.") from exc
        if reviewed_date.isoformat() != reviewed:
            raise ValueError(f"Allowlist review date must be ISO format for CUI {cui}.")
    else:
        raise ValueError(f"Allowlist review date must be ISO format for CUI {cui}.")
    seen.add(cui)
    return MembershipAcknowledgement(cui, reason.strip(), reviewed_date)


def _parse_allowlist_class(class_name, sides):
    if not isinstance(class_name, str) or not class_name or not isinstance(sides, dict):
        raise ValueError("Allowlist class entries must map class names to side mappings.")
    if set(sides) - {"text_only", "hierarchy_only"}:
        raise ValueError(f"Unknown allowlist side for class {class_name!r}.")
    parsed = {"text_only": [], "hierarchy_only": []}
    for side, entries in sides.items():
        if not isinstance(entries, list):
            raise ValueError(f"Allowlist {class_name!r}.{side} must be a list.")
        seen: set[int] = set()
        parsed[side] = [
            _parse_allowance(entry, seen, class_name, side) for entry in entries
        ]
    return parsed


def _load_class_allowlist(path: Path) -> dict[str, dict[str, list[MembershipAcknowledgement]]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict) or set(raw) != {"classes"} or not isinstance(raw["classes"], dict):
        raise ValueError("Class membership allowlist must contain a 'classes' mapping.")
    return {
        class_name: _parse_allowlist_class(class_name, sides)
        for class_name, sides in raw["classes"].items()
    }


def _hemonc_concept_names(session: Session, cuis: set[int]) -> dict[int, str]:
    if not cuis:
        return {}
    concept, _ = omop_vocabulary_tables(session)
    vocabulary_rows = session.execute(
        sa.select(concept.c.concept_code, concept.c.concept_name).where(
            concept.c.vocabulary_id == "HemOnc",
            concept.c.concept_code.in_([str(cui) for cui in cuis]),
            sa.func.nullif(sa.func.trim(concept.c.invalid_reason), "").is_(None),
        )
    ).all()
    names = {int(code): name for code, name in vocabulary_rows if str(code).isdecimal()}
    drug_rows = session.execute(
        sa.select(Drugs.drug_cui, Drugs.drug).where(Drugs.drug_cui.in_(cuis))
    ).all()
    names.update({int(cui): name for cui, name in drug_rows if cui is not None})
    return names


def _check_membership_allowlist(session, divergences, allowlist, report):
    acknowledged = {
        (class_name, side, item.cui)
        for class_name, sides in allowlist.items()
        for side, entries in sides.items()
        for item in entries
    }
    divergent = {
        (class_name, side, cui)
        for class_name, (text_only, hierarchy_only) in divergences.items()
        for side, cuis in (("text_only", text_only), ("hierarchy_only", hierarchy_only))
        for cui in cuis
    }
    cuis = {cui for _, _, cui in acknowledged | divergent}
    report.class_concept_names.update(_hemonc_concept_names(session, cuis))
    for class_name, side, cui in sorted(divergent):
        name = report.class_concept_names.get(cui)
        if name is None:
            report.errors.append(f"Divergent CUI {cui} for {class_name!r} is absent from the HemOnc vocabulary.")
        if (class_name, side, cui) not in acknowledged:
            report.errors.append(f"Unacknowledged {side.replace('_', '-')} CUI {cui} for class {class_name!r}.")
    for class_name, side, cui in sorted(acknowledged - divergent):
        name = report.class_concept_names.get(cui)
        state = f" ({name})" if name else " (not present in HemOnc vocabulary)"
        report.errors.append(f"Stale allowlist entry: {class_name!r} {side.replace('_', '-')} CUI {cui}{state}.")


def _check_category_keys(source_values, report):
    for column, mapping in COMPONENT_CLASS_MAPS.items():
        missing = set(mapping) - source_values[column]
        report.checked_category_keys += len(mapping)
        if missing:
            report.errors.append(
                f"{column} mapping keys absent with exact spelling: {sorted(missing)}."
            )


def _check_family_categories(session, registry, report):
    condition_cuis = _family_condition_cuis(session, registry)
    if not condition_cuis:
        report.errors.append("No HemOnc condition CUIs resolved for the registry.")
        return
    rows = _component_rows(session, condition_cuis)
    report.checked_family_components = len({int(row["component_cui"]) for row in rows})
    unmapped = {
        (int(row["component_cui"]), row["component"])
        for row in rows
        if not any(row[column] in mapping for column, mapping in COMPONENT_CLASS_MAPS.items())
    }
    if unmapped:
        report.errors.append(
            f"NSCLC-family components without a broad category: {sorted(unmapped, key=str)}."
        )


def lint_registry_vocabulary(
    session: Session,
    registry: RegistrySpec,
    allowlist_path: Path = _DEFAULT_ALLOWLIST,
) -> RegistryLintReport:
    report = RegistryLintReport()
    terms = _registry_terms(registry)
    matched, ids_by_cui = _index_component_rows(_component_rows(session))
    source_values = _source_values(session)
    class_rows = _class_concepts(
        session, {term for term in terms if isinstance(term, str)}
    )
    classes_by_name = defaultdict(list)
    for row in class_rows:
        classes_by_name[row["concept_name"]].append(row)
    drug_counts = _drug_cui_counts(session)
    class_names: set[str] = set()
    for term in sorted(terms, key=str):
        if isinstance(term, str) and _check_class_reference(
            term, classes_by_name, source_values, report
        ):
            class_names.add(term)
        else:
            _check_drug_reference(term, drug_counts, matched, ids_by_cui, report)
    _check_class_memberships(
        session, class_names, classes_by_name, matched, ids_by_cui, report
    )
    allowlist = _load_class_allowlist(allowlist_path)
    _check_membership_allowlist(session, report.class_divergences, allowlist, report)
    _check_category_keys(source_values, report)
    _check_family_categories(session, registry, report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check the NSCLC regimen registry against the live HemOnc vocabulary."
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=PROFILES_ROOT / "nsclc" / "nsclc_protocol_registry.yaml",
    )
    parser.add_argument("--class-allowlist", type=Path, default=_DEFAULT_ALLOWLIST)
    args = parser.parse_args(argv)
    registry = RegistrySpec.from_path(args.registry)
    _, resolved = get_hemonc_context()
    engine = create_hemonc_engine(resolved)
    try:
        with Session(engine) as session:
            report = lint_registry_vocabulary(session, registry, args.class_allowlist)
    finally:
        engine.dispose()
    for name, (text_count, hierarchy_count) in report.class_memberships.items():
        print(f"Class {name}: text={text_count} hierarchy={hierarchy_count}")
    for name, (text_only, hierarchy_only) in report.class_divergences.items():
        for side, cuis in (("text-only", text_only), ("hierarchy-only", hierarchy_only)):
            for cui in cuis:
                concept_name = report.class_concept_names.get(cui, "<not found>")
                print(f"DIVERGENCE: {name}: {side} CUI {cui} — {concept_name}")
    print(
        f"Checked {report.checked_drugs} drug references, "
        f"{report.checked_classes} class references, {report.checked_category_keys} "
        f"mapping keys, and {report.checked_family_components} NSCLC-family components."
    )
    for error in report.errors:
        print(f"ERROR: {error}")
    if report.ok:
        print("Registry vocabulary lint passed.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
