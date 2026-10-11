from __future__ import annotations

from datetime import date

import sqlalchemy as sa
import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from scoop.protocols.hemonc import registry_lint
from scoop.config import create_hemonc_engine, get_hemonc_context
from scoop.profiles.paths import PROFILES_ROOT
from scoop.protocols.hemonc.queries import component_search_by_terms
from scoop.protocols.hemonc.registry_lint import (
    MembershipAcknowledgement,
    RegistryLintReport,
    _check_membership_allowlist,
    lint_registry_vocabulary,
)
from scoop.protocols.spec import (
    ComponentCui,
    ComponentRule,
    RegistrySpec,
    component_term_label,
)


def test_component_lists_accept_cui_references_with_labels():
    requirements = ComponentRule.model_validate(
        {"component_lists": [[{"name": "fixture drug", "cui": 987654321}]]}
    )
    term = requirements.component_lists[0][0]

    assert term == ComponentCui(name="fixture drug", cui=987654321)
    assert component_term_label(term) == "fixture drug"


def test_nsclc_drugs_are_cui_bound_and_class_labels_stay_strings():
    registry = RegistrySpec.from_path(
        PROFILES_ROOT / "nsclc" / "nsclc_protocol_registry.yaml"
    )
    terms = {
        term
        for phase in registry.source_phases.values()
        for group in phase.component_requirements.component_lists
        for term in group
    }
    classes = {
        "Immunostimulatory monoclonal antibody",
        "Platinum agent",
        "Taxane",
    }

    assert {term for term in terms if isinstance(term, str)} == classes
    assert all(isinstance(term, ComponentCui) for term in terms - classes)
    assert not {"capamatinib", "geftinib", "reptrectinib"} & {
        term for term in terms if isinstance(term, str)
    }


def test_cui_component_search_uses_only_the_bound_cui():
    table = sa.table(
        "components",
        sa.column("component_cui"),
        sa.column("component"),
        sa.column("canmed_major_class"),
        sa.column("canmed_minor_class"),
        sa.column("main_class"),
    )

    statement = component_search_by_terms(
        [ComponentCui(cui=987654321, name="fixture drug")],
        table,
    )
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)

    assert "component_cui IN" in sql
    assert "ILIKE" not in sql
    assert [987654321] in compiled.params.values()


def _fixture_membership_report(monkeypatch, *, names, divergence, allowlist):
    monkeypatch.setattr(registry_lint, "_hemonc_concept_names", lambda _s, _c: names)
    report = RegistryLintReport()
    _check_membership_allowlist(None, divergence, allowlist, report)
    return report


def test_class_membership_divergence_fails_without_allowlist_and_allowed_passes(monkeypatch):
    cui = 987654321
    divergence = {"Fixture class": ((cui,), ())}
    names = {cui: "Fixture concept"}
    rejected = _fixture_membership_report(
        monkeypatch, names=names, divergence=divergence, allowlist={}
    )
    allowlist = {
        "Fixture class": {
            "text_only": [MembershipAcknowledgement(cui, "Reviewed fixture difference.", date(2026, 10, 9))],
            "hierarchy_only": [],
        }
    }
    accepted = _fixture_membership_report(
        monkeypatch, names=names, divergence=divergence, allowlist=allowlist
    )

    assert rejected.class_concept_names[cui] == "Fixture concept"
    assert any("Unacknowledged text-only CUI 987654321" in error for error in rejected.errors)
    assert accepted.errors == []


@pytest.mark.parametrize(
    "names",
    [{987654321: "Fixture concept"}, {}],
    ids=["no-longer-divergent", "missing-from-vocabulary"],
)
def test_stale_class_membership_allowlist_entry_fails(monkeypatch, names):
    cui = 987654321
    allowlist = {
        "Fixture class": {
            "text_only": [MembershipAcknowledgement(cui, "Reviewed fixture difference.", date(2026, 10, 9))],
            "hierarchy_only": [],
        }
    }
    report = _fixture_membership_report(
        monkeypatch, names=names, divergence={}, allowlist=allowlist
    )

    assert any("Stale allowlist entry" in error for error in report.errors)


def test_registry_vocabulary_lint_when_hemonc_database_is_available():
    try:
        _, settings = get_hemonc_context()
        engine = create_hemonc_engine(settings)
        with engine.connect() as connection:
            connection.execute(sa.text("SELECT 1"))
    except Exception as exc:
        engine.dispose() if "engine" in locals() else None
        pytest.skip(f"HemOnc database unavailable: {type(exc).__name__}: {exc}")

    try:
        registry = RegistrySpec.from_path(
            PROFILES_ROOT / "nsclc" / "nsclc_protocol_registry.yaml"
        )
        with Session(engine) as session:
            report = lint_registry_vocabulary(session, registry)
        expected_errors = set()
        for class_name, (text_only, hierarchy_only) in report.class_divergences.items():
            for side, cuis in (("text_only", text_only), ("hierarchy_only", hierarchy_only)):
                for cui in cuis:
                    expected_errors.add(
                        f"Unacknowledged {side.replace('_', '-')} CUI {cui} "
                        f"for class {class_name!r}."
                    )
                    assert cui in report.class_concept_names
        assert set(report.errors) == expected_errors
    finally:
        engine.dispose()
