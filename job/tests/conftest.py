import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def app(tmp_path):
    from app import create_app
    app = create_app({"TESTING": True, "TESTING_NO_CSRF": True, "DB_PATH": tmp_path / "job.db"})
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def fixture_text():
    return lambda name: (FIXTURES / name).read_text(encoding="utf-8")
