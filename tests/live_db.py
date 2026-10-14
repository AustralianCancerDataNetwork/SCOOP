import unittest

from sqlalchemy.orm import Session

from scoop.config import create_hemonc_engine, get_hemonc_context
from scoop.protocols.hemonc.gateway import SqlHemOncGateway
from scoop.vocabulary.database import get_omop_engine


class LiveDatabaseTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            with get_omop_engine().connect():
                pass
            _, resolved = get_hemonc_context()
            cls.engine = create_hemonc_engine(resolved)
            with cls.engine.connect():
                pass
            cls.session = Session(cls.engine)
            cls.gateway = SqlHemOncGateway(cls.session)
        except Exception as exc:  # noqa: BLE001 - report the environment in the skip
            raise unittest.SkipTest(
                f"Live HemOnc/OMOP database unavailable: {type(exc).__name__}: {exc}"
            )

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "session"):
            cls.session.close()
        if hasattr(cls, "engine"):
            cls.engine.dispose()
        super().tearDownClass()
