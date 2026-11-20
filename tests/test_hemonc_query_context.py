from types import SimpleNamespace

import pandas as pd
import sqlalchemy as sa
from hemonc_alchemy.model.enums import (
    Sigs_PhaseEnum,
    Studies_IntentEnum,
    StudyResults_IntentEnum,
)

from scoop.protocols.hemonc.queries import (
    _one_row_per_sig,
    build_category_expressions,
    get_stmt_studies_by_curative_context,
)
from scoop.protocols.spec import BroadCategory, CategoryRule, CategoryRuleType


def _context_models():
    base = sa.orm.declarative_base()

    class Studies(base):
        __tablename__ = "studies"
        id = sa.Column(sa.Integer, primary_key=True)
        study = sa.Column(sa.String, nullable=False)
        substudy = sa.Column(sa.String, nullable=True)
        condition_cui = sa.Column(sa.Integer, nullable=False)

    class StudiesIntentMap(base):
        __tablename__ = "studies_intent"
        parent_id = sa.Column(sa.Integer, primary_key=True)
        intent = sa.Column(sa.Enum(Studies_IntentEnum), primary_key=True)

    class StudyResults(base):
        __tablename__ = "study_results"
        id = sa.Column(sa.Integer, primary_key=True)
        study = sa.Column(sa.String, nullable=False)
        intent = sa.Column(sa.Enum(StudyResults_IntentEnum), nullable=False)

    class Sigs(base):
        __tablename__ = "sigs"
        id = sa.Column(sa.Integer, primary_key=True)
        phase = sa.Column(sa.Enum(Sigs_PhaseEnum))

    class SigsStudyMap(base):
        __tablename__ = "sigs_study"
        parent_id = sa.Column(sa.Integer, primary_key=True)
        study = sa.Column(sa.String, primary_key=True)

    engine = sa.create_engine("sqlite://")
    base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            Studies.__table__.insert(),
            [
                {"id": 1, "study": "primary-curative", "substudy": None, "condition_cui": 101},
                {"id": 2, "study": "result-curative", "substudy": None, "condition_cui": 101},
                {"id": 3, "study": "result-non-curative", "substudy": None, "condition_cui": 101},
                {"id": 4, "study": "no-intent", "substudy": None, "condition_cui": 101},
                {"id": 5, "study": "unspecified-primary", "substudy": None, "condition_cui": 101},
                {"id": 6, "study": "preventive-primary", "substudy": None, "condition_cui": 101},
                {"id": 7, "study": "unspecified-only", "substudy": None, "condition_cui": 101},
                {"id": 8, "study": "unspecified-result", "substudy": None, "condition_cui": 101},
                {"id": 9, "study": "phase-match", "substudy": None, "condition_cui": 101},
                {"id": 10, "study": "phase-mismatch", "substudy": None, "condition_cui": 101},
                {"id": 11, "study": "other-condition", "substudy": None, "condition_cui": 202},
                {"id": 12, "study": "result-substudy", "substudy": "arm-a", "condition_cui": 101},
                {"id": 13, "study": "phase-substudy", "substudy": "arm-b", "condition_cui": 101},
            ],
        )
        connection.execute(
            StudiesIntentMap.__table__.insert(),
            [
                {"parent_id": 1, "intent": Studies_IntentEnum.CURATIVE_INTENT},
                {"parent_id": 5, "intent": Studies_IntentEnum.UNSPECIFIED_INTENT},
                {"parent_id": 6, "intent": Studies_IntentEnum.PREVENTIVE_INTENT},
                {"parent_id": 7, "intent": Studies_IntentEnum.UNSPECIFIED_INTENT},
            ],
        )
        connection.execute(
            StudyResults.__table__.insert(),
            [
                {
                    "id": 1,
                    "study": "primary-curative",
                    "intent": StudyResults_IntentEnum.NON_TO_CURATIVE_INTENT,
                },
                {
                    "id": 2,
                    "study": "result-curative",
                    "intent": StudyResults_IntentEnum.CURATIVE_INTENT,
                },
                {
                    "id": 3,
                    "study": "result-non-curative",
                    "intent": StudyResults_IntentEnum.NON_TO_CURATIVE_INTENT,
                },
                {
                    "id": 4,
                    "study": "unspecified-primary",
                    "intent": StudyResults_IntentEnum.CURATIVE_INTENT,
                },
                {
                    "id": 5,
                    "study": "unspecified-result",
                    "intent": StudyResults_IntentEnum.UNSPECIFIED_INTENT,
                },
                {
                    "id": 6,
                    "study": "result-substudy--arm-a",
                    "intent": StudyResults_IntentEnum.CURATIVE_INTENT,
                },
            ],
        )
        connection.execute(
            Sigs.__table__.insert(),
            [
                {"id": 1, "phase": Sigs_PhaseEnum.ADJUVANT},
                {"id": 2, "phase": Sigs_PhaseEnum.INDUCTION},
            ],
        )
        connection.execute(
            SigsStudyMap.__table__.insert(),
            [
                {"parent_id": 1, "study": "phase-match"},
                {"parent_id": 2, "study": "phase-mismatch"},
                {"parent_id": 1, "study": "phase-substudy--arm-b"},
            ],
        )
    models = SimpleNamespace(
        Studies=Studies,
        StudyResults=StudyResults,
        Sigs=Sigs,
        sigs_StudyMap=SigsStudyMap,
        studies_IntentMap=StudiesIntentMap,
    )
    return engine, models


def _studies(connection, models, **kwargs):
    statement = get_stmt_studies_by_curative_context([101], models=models, **kwargs)
    return set(connection.execute(statement).scalars())


def test_study_intent_is_primary_and_result_intent_is_fallback():
    engine, models = _context_models()
    with engine.connect() as connection:
        assert _studies(connection, models) == {1, 2, 5, 12}
        assert _studies(connection, models, non_curative=True) == {
            3
        }
    engine.dispose()


def test_unknown_policy_requires_absent_or_unspecified_intent():
    engine, models = _context_models()
    with engine.connect() as connection:
        assert _studies(connection, models, unknown_context_policy="include") == {
            1, 2, 4, 5, 7, 8, 9, 10, 12, 13
        }
        assert _studies(
            connection,
            models,
            unknown_context_policy="phase_type",
            phase_type="adjuvant",
        ) == {1, 2, 5, 9, 12, 13}
    engine.dispose()


def test_canmed_map_multiplicity_does_not_inflate_category_counts():
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    components = sa.Table(
        "components",
        metadata,
        sa.Column("component_cui", sa.Integer),
        sa.Column("broad_category", sa.String),
    )
    metadata.create_all(engine)
    requirement = {
        BroadCategory.CHEMOTHERAPY: CategoryRule(
            type=CategoryRuleType.EXACT,
            amount=1,
        )
    }
    expression = build_category_expressions(requirement, components)[0]

    with engine.begin() as connection:
        connection.execute(
            components.insert(),
            [
                {"component_cui": 123, "broad_category": "chemotherapy"}
                for _ in range(12)
            ],
        )
    statement = (
        sa.select(sa.literal(1))
        .select_from(components)
        .group_by(sa.literal(1))
        .having(expression)
    )
    with engine.connect() as connection:
        assert connection.execute(statement).first() is not None

    with engine.begin() as connection:
        connection.execute(
            components.insert(),
            {"component_cui": 456, "broad_category": "chemotherapy"},
        )
    with engine.connect() as connection:
        assert connection.execute(statement).first() is None
    engine.dispose()


def test_shared_sig_keeps_the_same_study_whatever_the_row_order():
    rows = pd.DataFrame(
        [
            {"id": 1, "single_study": "B", "study": "B", "matched_phase": None, "matched_variant_cui": 7},
            {"id": 1, "single_study": "A", "study": "A", "matched_phase": None, "matched_variant_cui": 7},
            {"id": 2, "single_study": "A", "study": "A", "matched_phase": None, "matched_variant_cui": 7},
            {"id": 1, "single_study": "C", "study": "C", "matched_phase": None, "matched_variant_cui": 7},
            {"id": 3, "single_study": "C", "study": "C", "matched_phase": None, "matched_variant_cui": 7},
        ]
    )

    picks = {
        tuple(_one_row_per_sig(rows.sample(frac=1, random_state=seed))[["id", "study"]].itertuples(index=False))
        for seed in range(20)
    }

    # Sig 1 joins a two-sig group; A beats C on name.
    assert picks == {((1, "A"), (2, "A"), (3, "C"))}
