"""테스트 공통 설정 — 모든 파일·DB 는 임시 폴더에 만든다 (data/ 는 건드리지 않는다)."""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

TMP = Path(tempfile.mkdtemp(prefix="sales_test_"))


def biz(prefix9: str) -> str:
    """앞 9자리에 검증번호를 붙인 유효한 사업자등록번호."""
    d = [int(c) for c in prefix9]
    weights = [1, 3, 7, 1, 3, 7, 1, 3, 5]
    total = sum(a * w for a, w in zip(d, weights)) + (d[8] * 5) // 10
    return prefix9 + str((10 - total % 10) % 10)


COMPANY_BIZ = biz("123456789")

os.environ.update({
    "SALES_DB_PATH": str(TMP / "sales.db"),
    "SALES_AUTH_MODE": "simple",
    "SALES_BACKUP_DIR": str(TMP / "backups"),
    "SALES_STORAGE": "local",
    "SALES_STORAGE_DIR": str(TMP / "storage"),
    "SALES_ERP_OUTBOUND_DIR": str(TMP / "erp_out"),
    "SALES_ERP_ADAPTER": "file",
    "SALES_COMPANY_BIZ_NO": COMPANY_BIZ,
})
os.environ.pop("SALES_SSO_USER", None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# PostgreSQL 로도 같은 테스트를 돌린다:  SALES_TEST_PG_URL=postgresql://postgres@127.0.0.1:5433/postgres
PG_ADMIN_URL = os.environ.get("SALES_TEST_PG_URL", "")


def _pg_url(dbname: str) -> str:
    return PG_ADMIN_URL.rsplit("/", 1)[0] + "/" + dbname


def _pg_recreate(dbname: str) -> str:
    import psycopg
    with psycopg.connect(PG_ADMIN_URL, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{dbname}"')
    return _pg_url(dbname)


if PG_ADMIN_URL:
    os.environ["SALES_DATABASE_URL"] = _pg_recreate("sales_pytest")

from app import create_app  # noqa: E402
from core import database  # noqa: E402
from core import enterprise as ent  # noqa: E402
from core import sales_db as db  # noqa: E402

IS_PG = database.is_pg()
sqlite_only = pytest.mark.skipif(IS_PG, reason="SQLite 전용(전환 전 SQLite 파일 대상)")


@pytest.fixture
def isolated_db(monkeypatch, tmp_path):
    """다른 테스트와 섞이지 않는 빈 DB (SQLite 임시 파일 또는 PostgreSQL 새 데이터베이스)."""
    if IS_PG:
        database.close_pool()
        monkeypatch.setattr(database, "DATABASE_URL", _pg_recreate("sales_pytest_iso"))
        database.migrate()
        yield "pg"
        database.close_pool()
    else:
        path = tmp_path / "isolated.db"
        monkeypatch.setattr(database, "DB_PATH", str(path))
        database.migrate()
        yield str(path)
    # monkeypatch 가 원래 주소로 되돌린 뒤 풀을 다시 만들도록 닫아 둔다
    database.close_pool()

ROLE_USERS = {"REP": "김영업", "MANAGER": "한팀장", "EXEC": "정임원", "ADMIN": "시스템관리자"}


@pytest.fixture(scope="session")
def app():
    application = create_app({"TESTING": True})
    db.set_context("system", None)
    ent.seed_org_demo()
    db.seed_demo_data()
    return application


def csrf(client) -> str:
    """현재 세션의 CSRF 토큰 (로그인 전/후/최초설정 화면 중 열리는 곳에서 읽는다)."""
    for url in ("/", "/login", "/setup", "/account/password"):
        match = re.search(r'name="_csrf" value="([0-9a-f]+)"', client.get(url).get_data(as_text=True))
        if match:
            return match.group(1)
    raise AssertionError("CSRF 토큰을 찾지 못했습니다")


def login(app_or_client, name: str | None = None, emp_no: str | None = None):
    client = app_or_client.test_client() if hasattr(app_or_client, "test_client") else app_or_client
    user = ent.get_user(emp_no=emp_no) if emp_no else ent.get_user(name=name)
    res = client.post("/login", data={"user_id": user["id"], "_csrf": csrf(client)})
    assert res.status_code == 302, res.get_data(as_text=True)[:300]
    return client


def post(client, url: str, data: dict | None = None, **kw):
    payload = dict(data or {})
    payload["_csrf"] = csrf(client)
    return client.post(url, data=payload, **kw)


def user(name: str | None = None, emp_no: str | None = None) -> dict:
    return ent.get_user(emp_no=emp_no) if emp_no else ent.get_user(name=name)
