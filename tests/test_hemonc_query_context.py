from types import SimpleNamespace

import sqlalchemy as sa

from hemonc_alchemy.model.enums import (
    Sigs_PhaseEnum,
    Studies_IntentEnum,
    StudyResults_IntentEnum,
)
from scoop.protocols.hemonc.queries import get_stmt_studies_by_curative_context


def _context_models():
    base = sa.orm.declarative_base()

    class Studies(base):
        __tablename__ = "studies"
        id = sa.Column(sa.Integer, primary_key=True)
        study = sa.Column(sa.String, nullable=False)
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
                {"id": 1, "study": "primary-curative", "condition_cui": 101},
                {"id": 2, "study": "result-curative", "condition_cui": 101},
                {"id": 3, "study": "result-non-curative", "condition_cui": 101},
                {"id": 4, "study": "no-intent", "condition_cui": 101},
                {"id": 5, "study": "unspecified-primary", "condition_cui": 101},
                {"id": 6, "study": "preventive-primary", "condition_cui": 101},
                {"id": 7, "study": "unspecified-only", "condition_cui": 101},
                {"id": 8, "study": "unspecified-result", "condition_cui": 101},
                {"id": 9, "study": "phase-match", "condition_cui": 101},
                {"id": 10, "study": "phase-mismatch", "condition_cui": 101},
                {"id": 11, "study": "other-condition", "condition_cui": 202},
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
        assert _studies(connection, models) == {
            "primary-curative",
            "result-curative",
            "unspecified-primary",
        }
        assert _studies(connection, models, non_curative=True) == {
            "result-non-curative"
        }
    engine.dispose()


def test_unknown_policy_requires_absent_or_unspecified_intent():
    engine, models = _context_models()
    with engine.connect() as connection:
        assert _studies(connection, models, unknown_context_policy="include") == {
            "primary-curative",
            "result-curative",
            "unspecified-primary",
            "no-intent",
            "unspecified-only",
            "unspecified-result",
            "phase-match",
            "phase-mismatch",
        }
        assert _studies(
            connection,
            models,
            unknown_context_policy="phase_type",
            phase_type="adjuvant",
        ) == {
            "primary-curative",
            "result-curative",
            "unspecified-primary",
            "phase-match",
        }
    engine.dispose()
