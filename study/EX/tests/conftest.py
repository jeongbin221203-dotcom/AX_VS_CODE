import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app  # noqa: E402


@pytest.fixture
def app(tmp_path):
    return create_app({'DATA_DIR': str(tmp_path), 'DATABASE': str(tmp_path / 'ex.db'), 'SECRET_KEY': 'test',
                       'TESTING': True})


@pytest.fixture
def client(app):
    c = app.test_client()
    c.get('/')
    with c.session_transaction() as s:
        c.csrf = s['csrf']
    return c
