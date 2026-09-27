"""영업관리 시스템 - 데이터 계층 (SQLite)

Streamlit UI(app.py)와 완전히 분리된 순수 파이썬 모듈이다.
설계 원칙
  * 모든 SQL은 파라미터 바인딩으로만 수행한다 (SQL Injection 차단)
  * 커넥션은 호출 단위로 열고 닫으며 WAL 모드로 읽기/쓰기 동시성을 확보한다
  * 목록 조회는 pandas.DataFrame, 단건 조회는 dict 를 반환한다
  * 금액 단위는 원(KRW) 정수로 저장한다 (실수 오차 방지)
"""
from __future__ import annotations

import io
import json
import os
import random
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional

import pandas as pd

# ----------------------------------------------------------------------------
# 설정 / 공통 상수
# ----------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODULE_VERSION = 2          # 모듈 버전(엔터프라이즈). app.py 가 기동 시 검증한다.

DB_PATH = os.environ.get("SALES_DB_PATH", os.path.join(BASE_DIR, "sales.db"))

STAGES = ["리드", "접촉", "제안", "견적", "협상", "수주", "실주"]
OPEN_STAGES = ["리드", "접촉", "제안", "견적", "협상"]
STAGE_WON = "수주"
STAGE_LOST = "실주"
STAGE_PROB = {"리드": 10, "접촉": 25, "제안": 45, "견적": 60, "협상": 80, "수주": 100, "실주": 0}

GRADES = ["VIP", "A", "B", "C"]
INDUSTRIES = ["제조", "유통", "건설", "IT/SW", "의료", "교육", "금융", "공공", "기타"]
ACT_TYPES = ["방문", "전화", "이메일", "미팅", "견적발송", "기타"]
SALE_STATUS = ["입금대기", "부분입금", "입금완료"]
LEAD_SOURCES = ["인바운드", "소개", "전시회", "콜드콜", "온라인광고", "기존고객", "기타"]

TODAY = date.today

# ── 엔터프라이즈 공통 상수 ────────────────────────────────────────────────
# 역할: 숫자가 클수록 상위 권한. 데이터 접근 범위와 결재 권한을 함께 결정한다.
ROLES = {"REP": 1, "MANAGER": 2, "EXEC": 3, "ADMIN": 4}
ROLE_LABEL = {"REP": "영업사원", "MANAGER": "팀장", "EXEC": "임원", "ADMIN": "시스템관리자"}

# 매출 예측 카테고리 (대기업 Forecast 운영의 표준 구분)
FORECAST_CATS = ["Commit", "Best Case", "Pipeline", "Omitted"]
FORECAST_DESC = {
    "Commit": "이번 달 반드시 마감한다고 약속한 건 (예측 정확도 책임 대상)",
    "Best Case": "잘 풀리면 마감 가능한 건",
    "Pipeline": "아직 예측에 넣기 이른 건",
    "Omitted": "이번 분기 예측에서 제외",
}

# MEDDIC 자격검증 항목 (B2B 대형 딜 검증 표준)
MEDDIC_FIELDS = {
    "m_metrics": "정량 효과(Metrics) - 고객이 얻을 수치 효과가 합의됨",
    "m_econ_buyer": "구매 결정권자(Economic Buyer) - 예산 결재자를 만남",
    "m_criteria": "평가 기준(Decision Criteria) - 고객의 선정 기준을 확보",
    "m_process": "구매 프로세스(Decision Process) - 품의/계약 절차와 일정 파악",
    "m_pain": "핵심 문제(Identified Pain) - 해결할 문제와 시급성 확인",
    "m_champion": "내부 지지자(Champion) - 우리를 대신해 움직일 사람 확보",
}

# 단계별 필수 충족 조건 (Stage Gate). 미충족 시 단계 진행을 차단한다.
STAGE_REQUIREMENTS = {
    "제안": ["amount", "expected_close", "m_pain"],
    "견적": ["amount", "expected_close", "m_pain", "m_econ_buyer", "m_criteria"],
    "협상": ["amount", "expected_close", "m_pain", "m_econ_buyer", "m_criteria",
             "m_process", "m_champion"],
    "수주": ["amount", "expected_close", "m_pain", "m_econ_buyer", "m_criteria",
             "m_process", "m_champion", "m_metrics", "discount_approved"],
    "실주": ["lost_reason"],
}

# 할인율 구간별 필요 결재 권한 (Deal Desk 정책)
DISCOUNT_POLICY = [(0.0, None), (10.0, "MANAGER"), (20.0, "EXEC"), (100.0, "ADMIN")]

LOST_REASONS = ["가격", "기능/스펙 부족", "경쟁사 선정", "예산 취소/보류",
                "일정 지연", "의사결정 중단", "내부 개발 전환", "기타"]
APPROVAL_STATUS = ["미요청", "대기", "승인", "반려"]
AR_BUCKETS = ["정상", "30일 초과", "60일 초과", "90일 초과"]
PAYMENT_TERMS = [0, 15, 30, 45, 60, 90]


# ----------------------------------------------------------------------------
# 커넥션 / 스키마
# ----------------------------------------------------------------------------
@contextmanager
def get_conn(db_path: str | None = None):
    """커밋/롤백/클로즈를 보장하는 커넥션 컨텍스트 매니저."""
    conn = sqlite3.connect(db_path or DB_PATH, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    biz_no      TEXT,
    industry    TEXT,
    grade       TEXT    DEFAULT 'B',
    owner       TEXT    NOT NULL,
    manager     TEXT,
    phone       TEXT,
    email       TEXT,
    address     TEXT,
    status      TEXT    DEFAULT '활성',
    memo        TEXT,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cust_owner ON customers(owner);
CREATE INDEX IF NOT EXISTS idx_cust_name  ON customers(name);

CREATE TABLE IF NOT EXISTS deals (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id    INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    title          TEXT    NOT NULL,
    owner          TEXT    NOT NULL,
    stage          TEXT    NOT NULL DEFAULT '리드',
    amount         INTEGER NOT NULL DEFAULT 0,
    probability    INTEGER NOT NULL DEFAULT 10,
    expected_close TEXT,
    source         TEXT,
    competitor     TEXT,
    memo           TEXT,
    closed_at      TEXT,
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deal_cust  ON deals(customer_id);
CREATE INDEX IF NOT EXISTS idx_deal_stage ON deals(stage);
CREATE INDEX IF NOT EXISTS idx_deal_owner ON deals(owner);

CREATE TABLE IF NOT EXISTS activities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    deal_id     INTEGER REFERENCES deals(id) ON DELETE SET NULL,
    act_date    TEXT    NOT NULL,
    act_type    TEXT    NOT NULL,
    owner       TEXT    NOT NULL,
    summary     TEXT    NOT NULL,
    next_action TEXT,
    next_date   TEXT,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_act_cust ON activities(customer_id);
CREATE INDEX IF NOT EXISTS idx_act_date ON activities(act_date);

CREATE TABLE IF NOT EXISTS sales (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    deal_id     INTEGER REFERENCES deals(id) ON DELETE SET NULL,
    sale_date   TEXT    NOT NULL,
    item        TEXT    NOT NULL,
    qty         INTEGER NOT NULL DEFAULT 1,
    unit_price  INTEGER NOT NULL DEFAULT 0,
    amount      INTEGER NOT NULL DEFAULT 0,
    owner       TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT '입금대기',
    memo        TEXT,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sale_date  ON sales(sale_date);
CREATE INDEX IF NOT EXISTS idx_sale_owner ON sales(owner);

CREATE TABLE IF NOT EXISTS targets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    yyyymm        TEXT    NOT NULL,
    owner         TEXT    NOT NULL,
    target_amount INTEGER NOT NULL DEFAULT 0,
    UNIQUE(yyyymm, owner)
);
"""


SCHEMA_ENTERPRISE = """
-- 조직도: 본부 → 팀 계층 구조 (parent_id 로 자기참조)
CREATE TABLE IF NOT EXISTS orgs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL UNIQUE,
    parent_id  INTEGER REFERENCES orgs(id) ON DELETE SET NULL,
    org_type   TEXT    DEFAULT '팀',
    created_at TEXT    NOT NULL
);

-- 사용자: 로그인 주체이자 데이터 접근 범위의 기준
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    emp_no     TEXT    NOT NULL UNIQUE,
    name       TEXT    NOT NULL,
    role       TEXT    NOT NULL DEFAULT 'REP',
    org_id     INTEGER REFERENCES orgs(id) ON DELETE SET NULL,
    email      TEXT,
    active     INTEGER NOT NULL DEFAULT 1,
    pw_hash    TEXT,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_name ON users(name);

-- 감사로그: 누가 언제 무엇을 바꿨는지 (내부통제/감사 대응)
CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    actor     TEXT NOT NULL,
    action    TEXT NOT NULL,
    entity    TEXT NOT NULL,
    entity_id INTEGER,
    detail    TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);

-- 단계 이력: 전환율과 단계별 체류일(Sales Velocity) 산출 근거
CREATE TABLE IF NOT EXISTS deal_stage_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id       INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    from_stage    TEXT,
    to_stage      TEXT    NOT NULL,
    changed_at    TEXT    NOT NULL,
    actor         TEXT,
    days_in_stage INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_hist_deal ON deal_stage_history(deal_id);

-- 할인 승인 결재 (Deal Desk)
CREATE TABLE IF NOT EXISTS approvals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id       INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    kind          TEXT    NOT NULL DEFAULT '할인승인',
    requested_by  TEXT    NOT NULL,
    requested_at  TEXT    NOT NULL,
    list_amount   INTEGER NOT NULL DEFAULT 0,
    final_amount  INTEGER NOT NULL DEFAULT 0,
    discount_rate REAL    NOT NULL DEFAULT 0,
    required_role TEXT,
    reason        TEXT,
    status        TEXT    NOT NULL DEFAULT '대기',
    approver      TEXT,
    decided_at    TEXT,
    comment       TEXT
);
CREATE INDEX IF NOT EXISTS idx_appr_status ON approvals(status);

-- 주간 파이프라인 스냅샷: 예측 정확도와 파이프라인 변동 추적
CREATE TABLE IF NOT EXISTS pipeline_snapshots (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    snap_date TEXT    NOT NULL,
    yyyymm    TEXT    NOT NULL,
    owner     TEXT    NOT NULL,
    category  TEXT    NOT NULL,
    deal_cnt  INTEGER NOT NULL DEFAULT 0,
    amount    INTEGER NOT NULL DEFAULT 0,
    weighted  INTEGER NOT NULL DEFAULT 0,
    UNIQUE(snap_date, yyyymm, owner, category)
);
CREATE INDEX IF NOT EXISTS idx_snap_ym ON pipeline_snapshots(yyyymm);
"""

# 기존 DB를 무손실 업그레이드하기 위한 추가 컬럼 정의 (테이블, 컬럼, DDL)
MIGRATIONS = [
    ("deals", "forecast_category", "TEXT DEFAULT 'Pipeline'"),
    ("deals", "lost_reason", "TEXT"),
    ("deals", "list_amount", "INTEGER DEFAULT 0"),
    ("deals", "discount_rate", "REAL DEFAULT 0"),
    ("deals", "approval_status", "TEXT DEFAULT '미요청'"),
    ("deals", "m_metrics", "INTEGER DEFAULT 0"),
    ("deals", "m_econ_buyer", "INTEGER DEFAULT 0"),
    ("deals", "m_criteria", "INTEGER DEFAULT 0"),
    ("deals", "m_process", "INTEGER DEFAULT 0"),
    ("deals", "m_pain", "INTEGER DEFAULT 0"),
    ("deals", "m_champion", "INTEGER DEFAULT 0"),
    ("deals", "stage_since", "TEXT"),
    ("customers", "credit_limit", "INTEGER DEFAULT 0"),
    ("customers", "payment_terms", "INTEGER DEFAULT 30"),
    ("customers", "org_id", "INTEGER"),
    ("sales", "due_date", "TEXT"),
    ("sales", "paid_amount", "INTEGER DEFAULT 0"),
]


def _table_columns(conn, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return set()


def init_db(db_path: str | None = None) -> None:
    """테이블/인덱스를 생성하고 기존 DB를 최신 스키마로 마이그레이션한다.

    운영 중인 sales.db 가 이미 있어도 데이터를 지우지 않고 컬럼만 추가한다.
    """
    with get_conn(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.executescript(SCHEMA_ENTERPRISE)
        for table, column, ddl in MIGRATIONS:
            if column not in _table_columns(conn, table):
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        # 기존 딜의 단계 진입일이 비어 있으면 생성일로 보정
        conn.execute("UPDATE deals SET stage_since = COALESCE(stage_since, created_at) "
                     "WHERE stage_since IS NULL")
        conn.execute("UPDATE deals SET list_amount = amount "
                     "WHERE COALESCE(list_amount, 0) = 0")


# ── 실행 컨텍스트: 로그인 사용자와 데이터 접근 범위 ──────────────────────
# ContextVar 를 쓰면 Streamlit 세션(스레드)마다 값이 독립적으로 유지되어
# 다른 사용자의 범위가 섞이지 않는다.
_ACTOR: ContextVar[str] = ContextVar("actor", default="system")
_SCOPE: ContextVar[Optional[tuple]] = ContextVar("owner_scope", default=None)


def set_context(actor: str = "system", owner_scope: Optional[Iterable[str]] = None) -> None:
    """로그인 직후/매 요청마다 호출. owner_scope=None 이면 전사 조회."""
    _ACTOR.set(actor or "system")
    _SCOPE.set(None if owner_scope is None else tuple(owner_scope))


def current_actor() -> str:
    return _ACTOR.get()


def current_scope() -> Optional[tuple]:
    return _SCOPE.get()


def _scope_clause(alias: str = "") -> tuple[str, list]:
    """로그인 사용자의 접근 범위를 SQL 조건으로 변환한다."""
    scope = _SCOPE.get()
    if scope is None:
        return "", []
    if not scope:                      # 볼 수 있는 담당자가 없음 → 결과 없음
        return " AND 1=0", []
    prefix = f"{alias}." if alias else ""
    return f" AND {prefix}owner IN ({','.join('?' * len(scope))})", list(scope)


def audit(action: str, entity: str, entity_id: int | None = None,
          detail: Any = None, db_path: str | None = None) -> None:
    """감사로그 기록. 실패해도 업무 트랜잭션을 막지 않는다."""
    try:
        with get_conn(db_path) as conn:
            conn.execute(
                "INSERT INTO audit_log (ts, actor, action, entity, entity_id, detail) "
                "VALUES (?,?,?,?,?,?)",
                (_now(), _ACTOR.get(), action, entity, entity_id,
                 json.dumps(detail, ensure_ascii=False) if not isinstance(detail, (str, type(None)))
                 else detail),
            )
    except sqlite3.Error:
        pass


def list_audit(limit: int = 300, actor: str = "", entity: str = "",
               db_path: str | None = None) -> pd.DataFrame:
    sql = ("SELECT ts AS 시각, actor AS 사용자, action AS 작업, entity AS 대상, "
           "entity_id AS 대상ID, detail AS 상세 FROM audit_log WHERE 1=1")
    params: list[Any] = []
    if actor:
        sql += " AND actor = ?"
        params.append(actor)
    if entity:
        sql += " AND entity = ?"
        params.append(entity)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    return _df(sql, params, db_path)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _d(value: Any) -> Optional[str]:
    """date/datetime/str → 'YYYY-MM-DD' 문자열로 정규화."""
    if value in (None, ""):
        return None
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


def _df(sql: str, params: Iterable[Any] = (), db_path: str | None = None) -> pd.DataFrame:
    with get_conn(db_path) as conn:
        return pd.read_sql_query(sql, conn, params=tuple(params))


def _one(sql: str, params: Iterable[Any] = (), db_path: str | None = None) -> Optional[dict]:
    with get_conn(db_path) as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None


def _scalar(sql: str, params: Iterable[Any] = (), db_path: str | None = None) -> float:
    with get_conn(db_path) as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0


# ----------------------------------------------------------------------------
# 거래처(customers)
# ----------------------------------------------------------------------------
def list_customers(keyword: str = "", owner: str = "", grade: str = "",
                   status: str = "", db_path: str | None = None) -> pd.DataFrame:
    sql = """
        SELECT c.id, c.name AS 거래처명, c.grade AS 등급, c.industry AS 업종,
               c.owner AS 담당자, c.manager AS 고객담당자, c.phone AS 연락처,
               c.email AS 이메일, c.status AS 상태,
               COALESCE(d.open_cnt, 0)  AS 진행딜,
               COALESCE(s.total, 0)     AS 누적매출,
               a.last_date              AS 최근접촉일,
               c.credit_limit AS 여신한도, c.payment_terms AS 결제조건일,
               c.address AS 주소, c.memo AS 메모, c.biz_no AS 사업자번호
          FROM customers c
          LEFT JOIN (SELECT customer_id, COUNT(*) open_cnt FROM deals
                      WHERE stage NOT IN ('수주','실주') GROUP BY customer_id) d
                 ON d.customer_id = c.id
          LEFT JOIN (SELECT customer_id, SUM(amount) total FROM sales
                      GROUP BY customer_id) s ON s.customer_id = c.id
          LEFT JOIN (SELECT customer_id, MAX(act_date) last_date FROM activities
                      GROUP BY customer_id) a ON a.customer_id = c.id
         WHERE 1=1
    """
    params: list[Any] = []
    if keyword:
        sql += " AND (c.name LIKE ? OR c.manager LIKE ? OR c.memo LIKE ?)"
        params += [f"%{keyword}%"] * 3
    if owner:
        sql += " AND c.owner = ?"
        params.append(owner)
    if grade:
        sql += " AND c.grade = ?"
        params.append(grade)
    if status:
        sql += " AND c.status = ?"
        params.append(status)
    scope_sql, scope_params = _scope_clause("c")
    sql += scope_sql + " ORDER BY c.name"
    return _df(sql, params + scope_params, db_path)


def get_customer(customer_id: int, db_path: str | None = None) -> Optional[dict]:
    return _one("SELECT * FROM customers WHERE id = ?", [customer_id], db_path)


def upsert_customer(data: dict, db_path: str | None = None) -> int:
    """거래처 신규 등록 또는 수정. data['id'] 가 있으면 수정."""
    if not str(data.get("name", "")).strip():
        raise ValueError("거래처명은 필수입니다.")
    if not str(data.get("owner", "")).strip():
        raise ValueError("담당자는 필수입니다.")

    fields = ["name", "biz_no", "industry", "grade", "owner", "manager",
              "phone", "email", "address", "status", "memo",
              "credit_limit", "payment_terms"]
    values = [data.get(f) for f in fields[:-2]]
    values += [int(data.get("credit_limit") or 0), int(data.get("payment_terms") or 30)]
    with get_conn(db_path) as conn:
        if data.get("id"):
            conn.execute(
                f"UPDATE customers SET {', '.join(f'{f}=?' for f in fields)}, updated_at=? WHERE id=?",
                (*values, _now(), int(data["id"])),
            )
            audit("수정", "거래처", int(data["id"]), {"거래처명": data.get("name")}, db_path)
            return int(data["id"])
        cur = conn.execute(
            f"INSERT INTO customers ({', '.join(fields)}, created_at, updated_at) "
            f"VALUES ({', '.join('?' * len(fields))}, ?, ?)",
            (*values, _now(), _now()),
        )
        new_id = int(cur.lastrowid)
    audit("등록", "거래처", new_id, {"거래처명": data.get("name")}, db_path)
    return new_id


def delete_customer(customer_id: int, db_path: str | None = None) -> None:
    with get_conn(db_path) as conn:
        conn.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
    audit("삭제", "거래처", customer_id, None, db_path)


def customer_options(db_path: str | None = None) -> dict[int, str]:
    scope_sql, scope_params = _scope_clause()
    df = _df(f"SELECT id, name FROM customers WHERE 1=1{scope_sql} ORDER BY name",
             scope_params, db_path)
    return {int(r.id): r.name for r in df.itertuples()}


# ----------------------------------------------------------------------------
# 영업기회(deals)
# ----------------------------------------------------------------------------
def list_deals(keyword: str = "", owner: str = "", stage: str = "",
               only_open: bool = False, customer_id: int | None = None,
               db_path: str | None = None) -> pd.DataFrame:
    sql = """
        SELECT d.id, c.name AS 거래처, d.title AS 기회명, d.stage AS 단계,
               d.amount AS 예상금액, d.probability AS 확률,
               CAST(d.amount * d.probability / 100.0 AS INTEGER) AS 가중금액,
               d.expected_close AS 예상마감일, d.owner AS 담당자,
               COALESCE(d.forecast_category,'Pipeline') AS 예측구분,
               CAST((COALESCE(d.m_metrics,0)+COALESCE(d.m_econ_buyer,0)+COALESCE(d.m_criteria,0)
                    +COALESCE(d.m_process,0)+COALESCE(d.m_pain,0)+COALESCE(d.m_champion,0))
                    * 100.0 / 6 AS INTEGER) AS 검증점수,
               COALESCE(d.discount_rate,0) AS 할인율,
               COALESCE(d.approval_status,'미요청') AS 승인상태,
               d.lost_reason AS 실주사유,
               CAST(julianday('now') - julianday(COALESCE(d.stage_since, d.created_at))
                    AS INTEGER) AS 단계체류일,
               d.source AS 유입경로, d.competitor AS 경쟁사, d.memo AS 메모,
               d.customer_id, d.closed_at AS 종료일
          FROM deals d JOIN customers c ON c.id = d.customer_id
         WHERE 1=1
    """
    params: list[Any] = []
    if keyword:
        sql += " AND (d.title LIKE ? OR c.name LIKE ?)"
        params += [f"%{keyword}%"] * 2
    if owner:
        sql += " AND d.owner = ?"
        params.append(owner)
    if stage:
        sql += " AND d.stage = ?"
        params.append(stage)
    if only_open:
        sql += " AND d.stage NOT IN ('수주','실주')"
    if customer_id:
        sql += " AND d.customer_id = ?"
        params.append(int(customer_id))
    scope_sql, scope_params = _scope_clause("d")
    sql += scope_sql + " ORDER BY COALESCE(d.expected_close,'9999-12-31'), d.amount DESC"
    return _df(sql, params + scope_params, db_path)


def get_deal(deal_id: int, db_path: str | None = None) -> Optional[dict]:
    return _one("SELECT * FROM deals WHERE id = ?", [deal_id], db_path)


def qual_score(data: dict) -> int:
    """MEDDIC 6개 항목 충족도를 0~100 점으로 환산한다."""
    done = sum(1 for f in MEDDIC_FIELDS if int(data.get(f) or 0))
    return int(done * 100 / len(MEDDIC_FIELDS))


def required_approval_role(discount_rate: float | None) -> Optional[str]:
    """할인율에 따라 필요한 결재 권한을 돌려준다. 할인이 없으면 None."""
    rate = float(discount_rate or 0)
    if rate <= 0:
        return None
    for threshold, role in DISCOUNT_POLICY:
        if role and rate <= threshold:
            return role
    return "ADMIN"


def validate_stage(data: dict, stage: str) -> list[str]:
    """Stage Gate 검증: 해당 단계로 가기 위해 빠진 조건을 한글 메시지로 반환."""
    missing: list[str] = []
    for req in STAGE_REQUIREMENTS.get(stage, []):
        if req == "amount" and int(data.get("amount") or 0) <= 0:
            missing.append("예상금액 입력")
        elif req == "expected_close" and not data.get("expected_close"):
            missing.append("예상 마감일 입력")
        elif req == "lost_reason" and not str(data.get("lost_reason") or "").strip():
            missing.append("실주사유 선택")
        elif req == "discount_approved":
            role = required_approval_role(data.get("discount_rate"))
            if role and data.get("approval_status") != "승인":
                missing.append(
                    f"할인 {float(data.get('discount_rate') or 0):.1f}% 에 대한 "
                    f"{ROLE_LABEL[role]} 승인")
        elif req in MEDDIC_FIELDS and not int(data.get(req) or 0):
            missing.append(MEDDIC_FIELDS[req].split(" - ")[0] + " 확인")
    return missing


DEAL_FIELDS = ["customer_id", "title", "owner", "stage", "amount", "probability",
               "expected_close", "source", "competitor", "memo", "closed_at",
               "forecast_category", "lost_reason", "list_amount", "discount_rate",
               "approval_status", "stage_since", *MEDDIC_FIELDS]


def upsert_deal(data: dict, db_path: str | None = None, force: bool = False) -> int:
    """영업기회 등록/수정.

    force=False 이면 Stage Gate 를 검증해 필수 조건 미충족 시 예외를 던진다.
    (관리자 우회나 데이터 이관 시에만 force=True 사용)
    """
    if not data.get("customer_id"):
        raise ValueError("거래처를 선택하세요.")
    if not str(data.get("title", "")).strip():
        raise ValueError("기회명은 필수입니다.")
    stage = data.get("stage") or "리드"
    if stage not in STAGES:
        raise ValueError(f"단계 값이 올바르지 않습니다: {stage}")

    prev = get_deal(int(data["id"]), db_path) if data.get("id") else None
    merged = dict(prev or {})
    merged.update({k: v for k, v in data.items() if v is not None})
    merged["stage"] = stage

    if not force and (prev is None or prev.get("stage") != stage):
        missing = validate_stage(merged, stage)
        if missing:
            raise ValueError(f"'{stage}' 단계로 진행하려면 다음이 필요합니다 → " + ", ".join(missing))

    closed_at = _d(data.get("closed_at"))
    if stage in (STAGE_WON, STAGE_LOST) and not closed_at:
        closed_at = _d(TODAY())
    if stage in OPEN_STAGES:
        closed_at = None

    stage_changed = prev is None or prev.get("stage") != stage
    stage_since = _d(TODAY()) if stage_changed else (prev or {}).get("stage_since") or _d(TODAY())
    list_amount = int(data.get("list_amount") or data.get("amount") or 0)
    amount = int(data.get("amount") or 0)
    discount = float(data.get("discount_rate") if data.get("discount_rate") is not None
                     else ((list_amount - amount) / list_amount * 100 if list_amount else 0))

    values = [
        int(data["customer_id"]), data["title"], data.get("owner"), stage,
        amount, int(data.get("probability") or STAGE_PROB[stage]),
        _d(data.get("expected_close")), data.get("source"), data.get("competitor"),
        data.get("memo"), closed_at,
        data.get("forecast_category") or "Pipeline", data.get("lost_reason"),
        list_amount, round(discount, 2),
        data.get("approval_status") or (prev or {}).get("approval_status") or "미요청",
        stage_since,
        *[int(data.get(f) or 0) for f in MEDDIC_FIELDS],
    ]
    with get_conn(db_path) as conn:
        if data.get("id"):
            deal_id = int(data["id"])
            conn.execute(
                f"UPDATE deals SET {', '.join(f'{f}=?' for f in DEAL_FIELDS)}, updated_at=? WHERE id=?",
                (*values, _now(), deal_id),
            )
        else:
            cur = conn.execute(
                f"INSERT INTO deals ({', '.join(DEAL_FIELDS)}, created_at, updated_at) "
                f"VALUES ({', '.join('?' * len(DEAL_FIELDS))}, ?, ?)",
                (*values, _now(), _now()),
            )
            deal_id = int(cur.lastrowid)
        if stage_changed:
            prev_since = (prev or {}).get("stage_since")
            days = 0
            if prev_since:
                days = max((TODAY() - datetime.strptime(prev_since[:10], "%Y-%m-%d").date()).days, 0)
            conn.execute(
                "INSERT INTO deal_stage_history (deal_id, from_stage, to_stage, changed_at, "
                "actor, days_in_stage) VALUES (?,?,?,?,?,?)",
                (deal_id, (prev or {}).get("stage"), stage, _now(), _ACTOR.get(), days),
            )
    audit("수정" if data.get("id") else "등록", "영업기회", deal_id,
          {"기회명": data["title"], "단계": stage, "금액": amount}, db_path)
    return deal_id


def change_stage(deal_id: int, stage: str, db_path: str | None = None,
                 force: bool = False, lost_reason: str | None = None) -> None:
    """단계 변경. 확률/종료일/단계진입일/이력/감사로그를 함께 정리한다."""
    if stage not in STAGES:
        raise ValueError(f"단계 값이 올바르지 않습니다: {stage}")
    deal = get_deal(deal_id, db_path)
    if not deal:
        raise ValueError("존재하지 않는 영업기회입니다.")
    merged = dict(deal)
    if lost_reason:
        merged["lost_reason"] = lost_reason
    if not force:
        missing = validate_stage(merged, stage)
        if missing:
            raise ValueError(f"'{stage}' 단계로 진행하려면 다음이 필요합니다 → " + ", ".join(missing))

    closed_at = _d(TODAY()) if stage in (STAGE_WON, STAGE_LOST) else None
    prev_since = deal.get("stage_since") or deal.get("created_at")
    days = 0
    if prev_since:
        days = max((TODAY() - datetime.strptime(str(prev_since)[:10], "%Y-%m-%d").date()).days, 0)
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE deals SET stage=?, probability=?, closed_at=?, stage_since=?, "
            "lost_reason=COALESCE(?, lost_reason), updated_at=? WHERE id=?",
            (stage, STAGE_PROB[stage], closed_at, _d(TODAY()), lost_reason, _now(), deal_id),
        )
        conn.execute(
            "INSERT INTO deal_stage_history (deal_id, from_stage, to_stage, changed_at, "
            "actor, days_in_stage) VALUES (?,?,?,?,?,?)",
            (deal_id, deal.get("stage"), stage, _now(), _ACTOR.get(), days),
        )
    audit("단계변경", "영업기회", deal_id,
          {"이전": deal.get("stage"), "이후": stage, "체류일": days}, db_path)


def delete_deal(deal_id: int, db_path: str | None = None) -> None:
    with get_conn(db_path) as conn:
        conn.execute("DELETE FROM deals WHERE id = ?", (deal_id,))
    audit("삭제", "영업기회", deal_id, None, db_path)


def deal_options(customer_id: int | None = None, db_path: str | None = None) -> dict[int, str]:
    sql = ("SELECT d.id, c.name || ' - ' || d.title AS label FROM deals d "
           "JOIN customers c ON c.id = d.customer_id WHERE 1=1")
    params: list[Any] = []
    if customer_id:
        sql += " AND d.customer_id = ?"
        params.append(int(customer_id))
    scope_sql, scope_params = _scope_clause("d")
    sql += scope_sql
    params += scope_params
    sql += " ORDER BY d.id DESC"
    df = _df(sql, params, db_path)
    return {int(r.id): r.label for r in df.itertuples()}


# ----------------------------------------------------------------------------
# 영업활동(activities)
# ----------------------------------------------------------------------------
def list_activities(days: int = 90, owner: str = "", act_type: str = "",
                    customer_id: int | None = None, db_path: str | None = None) -> pd.DataFrame:
    since = _d(TODAY() - timedelta(days=days))
    sql = """
        SELECT a.id, a.act_date AS 활동일, a.act_type AS 유형, c.name AS 거래처,
               d.title AS 관련기회, a.owner AS 담당자, a.summary AS 활동내용,
               a.next_action AS 다음액션, a.next_date AS 다음일정, a.customer_id
          FROM activities a
          JOIN customers c ON c.id = a.customer_id
          LEFT JOIN deals d ON d.id = a.deal_id
         WHERE a.act_date >= ?
    """
    params: list[Any] = [since]
    if owner:
        sql += " AND a.owner = ?"
        params.append(owner)
    if act_type:
        sql += " AND a.act_type = ?"
        params.append(act_type)
    if customer_id:
        sql += " AND a.customer_id = ?"
        params.append(int(customer_id))
    scope_sql, scope_params = _scope_clause("a")
    sql += scope_sql + " ORDER BY a.act_date DESC, a.id DESC"
    return _df(sql, params + scope_params, db_path)


def add_activity(data: dict, db_path: str | None = None) -> int:
    if not data.get("customer_id"):
        raise ValueError("거래처를 선택하세요.")
    if not str(data.get("summary", "")).strip():
        raise ValueError("활동내용은 필수입니다.")
    with get_conn(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO activities (customer_id, deal_id, act_date, act_type, owner, "
            "summary, next_action, next_date, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (int(data["customer_id"]), data.get("deal_id"), _d(data.get("act_date")) or _d(TODAY()),
             data.get("act_type") or "기타", data.get("owner"), data["summary"],
             data.get("next_action"), _d(data.get("next_date")), _now()),
        )
        new_id = int(cur.lastrowid)
    audit("등록", "영업활동", new_id, {"유형": data.get("act_type")}, db_path)
    return new_id


def delete_activity(activity_id: int, db_path: str | None = None) -> None:
    with get_conn(db_path) as conn:
        conn.execute("DELETE FROM activities WHERE id = ?", (activity_id,))


def upcoming_actions(days: int = 7, owner: str = "", db_path: str | None = None) -> pd.DataFrame:
    sql = """
        SELECT a.next_date AS 예정일, c.name AS 거래처, a.owner AS 담당자,
               a.next_action AS 할일, a.summary AS 직전활동
          FROM activities a JOIN customers c ON c.id = a.customer_id
         WHERE a.next_date IS NOT NULL AND a.next_date <= ?
           AND a.next_action IS NOT NULL AND a.next_action <> ''
    """
    params: list[Any] = [_d(TODAY() + timedelta(days=days))]
    if owner:
        sql += " AND a.owner = ?"
        params.append(owner)
    scope_sql, scope_params = _scope_clause("a")
    sql += scope_sql
    params += scope_params
    sql += " ORDER BY a.next_date"
    return _df(sql, params, db_path)


# ----------------------------------------------------------------------------
# 매출(sales)
# ----------------------------------------------------------------------------
def list_sales(ym_from: str = "", ym_to: str = "", owner: str = "", status: str = "",
               customer_id: int | None = None, db_path: str | None = None) -> pd.DataFrame:
    sql = """
        SELECT s.id, s.sale_date AS 매출일, c.name AS 거래처, s.item AS 품목,
               s.qty AS 수량, s.unit_price AS 단가, s.amount AS 금액,
               s.owner AS 담당자, s.status AS 수금상태, s.memo AS 메모,
               s.customer_id, d.title AS 관련기회
          FROM sales s
          JOIN customers c ON c.id = s.customer_id
          LEFT JOIN deals d ON d.id = s.deal_id
         WHERE 1=1
    """
    params: list[Any] = []
    if ym_from:
        sql += " AND strftime('%Y-%m', s.sale_date) >= ?"
        params.append(ym_from)
    if ym_to:
        sql += " AND strftime('%Y-%m', s.sale_date) <= ?"
        params.append(ym_to)
    if owner:
        sql += " AND s.owner = ?"
        params.append(owner)
    if status:
        sql += " AND s.status = ?"
        params.append(status)
    if customer_id:
        sql += " AND s.customer_id = ?"
        params.append(int(customer_id))
    scope_sql, scope_params = _scope_clause("s")
    sql += scope_sql + " ORDER BY s.sale_date DESC, s.id DESC"
    return _df(sql, params + scope_params, db_path)


def upsert_sale(data: dict, db_path: str | None = None) -> int:
    if not data.get("customer_id"):
        raise ValueError("거래처를 선택하세요.")
    if not str(data.get("item", "")).strip():
        raise ValueError("품목은 필수입니다.")
    qty = int(data.get("qty") or 0)
    unit_price = int(data.get("unit_price") or 0)
    amount = int(data.get("amount") or qty * unit_price)
    if amount <= 0:
        raise ValueError("금액은 0보다 커야 합니다.")

    sale_date = _d(data.get("sale_date")) or _d(TODAY())
    due_date = _d(data.get("due_date"))
    if not due_date:                      # 거래처 결제조건(일)을 반영해 자동 계산
        cust = get_customer(int(data["customer_id"]), db_path) or {}
        terms = int(cust.get("payment_terms") or 30)
        due_date = _d(datetime.strptime(sale_date, "%Y-%m-%d").date() + timedelta(days=terms))

    fields = ["customer_id", "deal_id", "sale_date", "item", "qty", "unit_price",
              "amount", "owner", "status", "memo", "due_date", "paid_amount"]
    values = [int(data["customer_id"]), data.get("deal_id"), sale_date,
              data["item"], qty, unit_price, amount, data.get("owner"),
              data.get("status") or "입금대기", data.get("memo"), due_date,
              int(data.get("paid_amount") or (amount if data.get("status") == "입금완료" else 0))]
    with get_conn(db_path) as conn:
        if data.get("id"):
            conn.execute(
                f"UPDATE sales SET {', '.join(f'{f}=?' for f in fields)} WHERE id=?",
                (*values, int(data["id"])),
            )
            audit("수정", "매출", int(data["id"]), {"금액": amount}, db_path)
            return int(data["id"])
        cur = conn.execute(
            f"INSERT INTO sales ({', '.join(fields)}, created_at) "
            f"VALUES ({', '.join('?' * len(fields))}, ?)",
            (*values, _now()),
        )
        new_id = int(cur.lastrowid)
    audit("등록", "매출", new_id, {"금액": amount, "품목": data.get("item")}, db_path)
    return new_id


def update_sale_status(sale_id: int, status: str, db_path: str | None = None) -> None:
    if status not in SALE_STATUS:
        raise ValueError(f"수금상태 값이 올바르지 않습니다: {status}")
    with get_conn(db_path) as conn:
        conn.execute("UPDATE sales SET status=? WHERE id=?", (status, sale_id))
    audit("수금상태변경", "매출", sale_id, status, db_path)


def delete_sale(sale_id: int, db_path: str | None = None) -> None:
    with get_conn(db_path) as conn:
        conn.execute("DELETE FROM sales WHERE id = ?", (sale_id,))


# ----------------------------------------------------------------------------
# 목표(targets)
# ----------------------------------------------------------------------------
def list_targets(yyyymm: str = "", db_path: str | None = None) -> pd.DataFrame:
    sql = "SELECT id, yyyymm AS 월, owner AS 담당자, target_amount AS 목표금액 FROM targets WHERE 1=1"
    params: list[Any] = []
    if yyyymm:
        sql += " AND yyyymm = ?"
        params.append(yyyymm)
    scope_sql, scope_params = _scope_clause()
    sql += scope_sql + " ORDER BY yyyymm DESC, owner"
    return _df(sql, params + scope_params, db_path)


def upsert_target(yyyymm: str, owner: str, amount: int, db_path: str | None = None) -> None:
    if not yyyymm or not owner:
        raise ValueError("월과 담당자는 필수입니다.")
    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO targets (yyyymm, owner, target_amount) VALUES (?,?,?) "
            "ON CONFLICT(yyyymm, owner) DO UPDATE SET target_amount=excluded.target_amount",
            (yyyymm, owner, int(amount or 0)),
        )
    audit("목표설정", "목표", None, {"월": yyyymm, "담당자": owner, "금액": int(amount or 0)}, db_path)


def delete_target(target_id: int, db_path: str | None = None) -> None:
    with get_conn(db_path) as conn:
        conn.execute("DELETE FROM targets WHERE id = ?", (target_id,))


# ----------------------------------------------------------------------------
# 분석 / KPI
# ----------------------------------------------------------------------------
def owners(db_path: str | None = None) -> list[str]:
    df = _df(
        "SELECT DISTINCT owner FROM ("
        " SELECT owner FROM customers UNION SELECT owner FROM deals"
        " UNION SELECT owner FROM sales UNION SELECT owner FROM targets) "
        "WHERE owner IS NOT NULL AND owner <> '' ORDER BY owner", (), db_path)
    names = df["owner"].tolist() if not df.empty else []
    scope = _SCOPE.get()
    if scope is not None:
        names = [n for n in names if n in scope]
    return names


def _owner_clause(owner: str, alias: str = "") -> tuple[str, list]:
    """담당자 필터 + 로그인 사용자의 접근 범위를 함께 적용한다."""
    p = f"{alias}." if alias else ""
    sql, params = (f" AND {p}owner = ?", [owner]) if owner else ("", [])
    scope_sql, scope_params = _scope_clause(alias)
    return sql + scope_sql, params + scope_params


def kpi_summary(yyyymm: str, owner: str = "", db_path: str | None = None) -> dict:
    """기준월 기준 핵심 지표 묶음."""
    oc, op = _owner_clause(owner)
    prev = prev_month(yyyymm)
    today = _d(TODAY())

    month_sales = _scalar(
        f"SELECT SUM(amount) FROM sales WHERE strftime('%Y-%m', sale_date)=?{oc}",
        [yyyymm, *op], db_path)
    prev_sales = _scalar(
        f"SELECT SUM(amount) FROM sales WHERE strftime('%Y-%m', sale_date)=?{oc}",
        [prev, *op], db_path)
    target = _scalar(
        f"SELECT SUM(target_amount) FROM targets WHERE yyyymm=?{oc}",
        [yyyymm, *op], db_path)
    unpaid = _scalar(
        f"SELECT SUM(amount) FROM sales WHERE status IN ('입금대기','부분입금'){oc}",
        [*op], db_path)
    open_amount = _scalar(
        f"SELECT SUM(amount) FROM deals WHERE stage NOT IN ('수주','실주'){oc}",
        [*op], db_path)
    weighted = _scalar(
        f"SELECT SUM(amount * probability / 100.0) FROM deals "
        f"WHERE stage NOT IN ('수주','실주'){oc}", [*op], db_path)
    won = _scalar(
        f"SELECT COUNT(*) FROM deals WHERE stage='수주' AND strftime('%Y-%m', closed_at)=?{oc}",
        [yyyymm, *op], db_path)
    lost = _scalar(
        f"SELECT COUNT(*) FROM deals WHERE stage='실주' AND strftime('%Y-%m', closed_at)=?{oc}",
        [yyyymm, *op], db_path)
    new_cust = _scalar(
        f"SELECT COUNT(*) FROM customers WHERE strftime('%Y-%m', created_at)=?{oc}",
        [yyyymm, *op], db_path)
    overdue = _scalar(
        f"SELECT COUNT(*) FROM deals WHERE stage NOT IN ('수주','실주') "
        f"AND expected_close IS NOT NULL AND expected_close < ?{oc}", [today, *op], db_path)
    act_cnt = _scalar(
        f"SELECT COUNT(*) FROM activities WHERE strftime('%Y-%m', act_date)=?{oc}",
        [yyyymm, *op], db_path)

    return {
        "month_sales": int(month_sales),
        "prev_sales": int(prev_sales),
        "mom": (month_sales - prev_sales) / prev_sales * 100 if prev_sales else 0.0,
        "target": int(target),
        "achievement": month_sales / target * 100 if target else 0.0,
        "gap": int(target - month_sales),
        "open_amount": int(open_amount),
        "weighted_pipeline": int(weighted),
        "won_cnt": int(won),
        "lost_cnt": int(lost),
        "win_rate": won / (won + lost) * 100 if (won + lost) else 0.0,
        "new_customers": int(new_cust),
        "overdue_deals": int(overdue),
        "unpaid": int(unpaid),
        "activity_cnt": int(act_cnt),
    }


def monthly_trend(months: int = 12, owner: str = "", db_path: str | None = None) -> pd.DataFrame:
    """최근 N개월 매출/목표 추이 (데이터 없는 달도 0으로 채움)."""
    end = date.today().replace(day=1)
    index = [(end - pd.DateOffset(months=i)).strftime("%Y-%m") for i in range(months - 1, -1, -1)]
    base = pd.DataFrame({"월": index})

    oc, op = _owner_clause(owner)
    sales = _df(
        f"SELECT strftime('%Y-%m', sale_date) AS 월, SUM(amount) AS 매출 FROM sales "
        f"WHERE 1=1{oc} GROUP BY 1", op, db_path)
    tg = _df(
        f"SELECT yyyymm AS 월, SUM(target_amount) AS 목표 FROM targets WHERE 1=1{oc} GROUP BY 1",
        op, db_path)

    out = base.merge(sales, on="월", how="left").merge(tg, on="월", how="left")
    out[["매출", "목표"]] = out[["매출", "목표"]].fillna(0).astype("int64")
    out["달성률"] = (out["매출"] / out["목표"].replace(0, pd.NA) * 100).fillna(0).round(1)
    return out


def stage_funnel(owner: str = "", db_path: str | None = None) -> pd.DataFrame:
    oc, op = _owner_clause(owner)
    df = _df(
        f"SELECT stage AS 단계, COUNT(*) AS 건수, SUM(amount) AS 금액, "
        f"SUM(amount*probability/100.0) AS 가중금액 FROM deals "
        f"WHERE stage NOT IN ('수주','실주'){oc} GROUP BY stage", op, db_path)
    base = pd.DataFrame({"단계": OPEN_STAGES})
    out = base.merge(df, on="단계", how="left").fillna(0)
    out[["건수", "금액", "가중금액"]] = out[["건수", "금액", "가중금액"]].astype("int64")
    return out


def owner_performance(yyyymm: str, db_path: str | None = None) -> pd.DataFrame:
    """담당자별 목표/실적/달성률/파이프라인."""
    sc, sp = _scope_clause()
    sales = _df(f"SELECT owner AS 담당자, SUM(amount) AS 매출 FROM sales "
                f"WHERE strftime('%Y-%m', sale_date)=?{sc} GROUP BY 1", [yyyymm, *sp], db_path)
    tg = _df(f"SELECT owner AS 담당자, SUM(target_amount) AS 목표 FROM targets "
             f"WHERE yyyymm=?{sc} GROUP BY 1", [yyyymm, *sp], db_path)
    pipe = _df(f"SELECT owner AS 담당자, SUM(amount) AS 파이프라인, COUNT(*) AS 진행건수 "
               f"FROM deals WHERE stage NOT IN ('수주','실주'){sc} GROUP BY 1", sp, db_path)
    names = sorted(set(sales.get("담당자", pd.Series(dtype=str)))
                   | set(tg.get("담당자", pd.Series(dtype=str)))
                   | set(pipe.get("담당자", pd.Series(dtype=str))))
    out = pd.DataFrame({"담당자": names})
    for part in (sales, tg, pipe):
        if not part.empty:
            out = out.merge(part, on="담당자", how="left")
    for col in ("매출", "목표", "파이프라인", "진행건수"):
        if col not in out.columns:
            out[col] = 0
    out[["매출", "목표", "파이프라인", "진행건수"]] = out[["매출", "목표", "파이프라인", "진행건수"]].fillna(0).astype("int64")
    out["달성률"] = (out["매출"] / out["목표"].replace(0, pd.NA) * 100).fillna(0).round(1)
    return out.sort_values("매출", ascending=False).reset_index(drop=True)


def top_customers(yyyymm: str = "", limit: int = 10, db_path: str | None = None) -> pd.DataFrame:
    sql = ("SELECT c.name AS 거래처, SUM(s.amount) AS 매출, COUNT(*) AS 건수 "
           "FROM sales s JOIN customers c ON c.id = s.customer_id WHERE 1=1")
    params: list[Any] = []
    if yyyymm:
        sql += " AND strftime('%Y-%m', s.sale_date)=?"
        params.append(yyyymm)
    scope_sql, scope_params = _scope_clause("s")
    sql += scope_sql + " GROUP BY c.id ORDER BY 매출 DESC LIMIT ?"
    params += scope_params
    params.append(int(limit))
    return _df(sql, params, db_path)


def deals_closing_soon(days: int = 14, owner: str = "", db_path: str | None = None) -> pd.DataFrame:
    oc, op = _owner_clause(owner, "d")
    return _df(
        f"SELECT d.expected_close AS 예상마감일, c.name AS 거래처, d.title AS 기회명, "
        f"d.stage AS 단계, d.amount AS 예상금액, d.owner AS 담당자 "
        f"FROM deals d JOIN customers c ON c.id = d.customer_id "
        f"WHERE d.stage NOT IN ('수주','실주') AND d.expected_close IS NOT NULL "
        f"AND d.expected_close <= ?{oc} ORDER BY d.expected_close",
        [_d(TODAY() + timedelta(days=days)), *op], db_path)


def stale_customers(days: int = 30, owner: str = "", db_path: str | None = None) -> pd.DataFrame:
    oc, op = _owner_clause(owner, "c")
    return _df(
        f"SELECT c.name AS 거래처, c.grade AS 등급, c.owner AS 담당자, "
        f"COALESCE(a.last_date,'접촉이력 없음') AS 최근접촉일 "
        f"FROM customers c LEFT JOIN (SELECT customer_id, MAX(act_date) last_date "
        f"FROM activities GROUP BY customer_id) a ON a.customer_id = c.id "
        f"WHERE c.status='활성' AND (a.last_date IS NULL OR a.last_date < ?){oc} "
        f"ORDER BY c.grade, a.last_date",
        [_d(TODAY() - timedelta(days=days)), *op], db_path)


def prev_month(yyyymm: str) -> str:
    y, m = int(yyyymm[:4]), int(yyyymm[5:7])
    return f"{y-1}-12" if m == 1 else f"{y}-{m-1:02d}"


def month_options(back: int = 24, forward: int = 6) -> list[str]:
    start = date.today().replace(day=1)
    months = [(start - pd.DateOffset(months=i)).strftime("%Y-%m") for i in range(back, 0, -1)]
    months += [(start + pd.DateOffset(months=i)).strftime("%Y-%m") for i in range(0, forward)]
    return months


# ----------------------------------------------------------------------------
# 내보내기 / 샘플데이터 / 초기화
# ----------------------------------------------------------------------------
def export_excel(db_path: str | None = None) -> bytes:
    """전체 데이터를 시트별 엑셀로 내보낸다."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        sheets = {
            "거래처": list_customers(db_path=db_path),
            "영업기회": list_deals(db_path=db_path),
            "영업활동": list_activities(days=3650, db_path=db_path),
            "매출": list_sales(db_path=db_path),
            "목표": list_targets(db_path=db_path),
        }
        for name, df in sheets.items():
            (df if not df.empty else pd.DataFrame({"데이터": ["없음"]})).to_excel(
                writer, sheet_name=name, index=False)
    return buf.getvalue()


def import_customers_csv(df: pd.DataFrame, db_path: str | None = None) -> tuple[int, list[str]]:
    """CSV 일괄 등록. 반환: (성공건수, 오류메시지 목록)"""
    required = {"거래처명", "담당자"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"필수 컬럼 누락: {', '.join(sorted(missing))}")
    ok, errors = 0, []
    colmap = {"거래처명": "name", "담당자": "owner", "등급": "grade", "업종": "industry",
              "연락처": "phone", "이메일": "email", "주소": "address",
              "고객담당자": "manager", "사업자번호": "biz_no", "메모": "memo"}
    for idx, row in df.iterrows():
        try:
            data = {v: (None if pd.isna(row.get(k)) else str(row.get(k)).strip())
                    for k, v in colmap.items() if k in df.columns}
            data.setdefault("grade", "B")
            data["status"] = "활성"
            upsert_customer(data, db_path)
            ok += 1
        except Exception as exc:  # noqa: BLE001 - 행 단위로 계속 진행
            errors.append(f"{int(idx) + 2}행: {exc}")
    return ok, errors


def reset_db(db_path: str | None = None) -> None:
    with get_conn(db_path) as conn:
        for table in ("activities", "sales", "deal_stage_history", "approvals",
                      "pipeline_snapshots", "deals", "targets", "customers",
                      "audit_log", "users", "orgs"):
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM sqlite_sequence")


def seed_demo_data(db_path: str | None = None, seed: int = 42) -> dict:
    """데모용 샘플 데이터 생성 (기존 데이터는 지우지 않음)."""
    rng = random.Random(seed)
    sales_owners = ["김영업", "이수주", "박고객", "최성과"]
    prefix = ["대한", "한빛", "삼정", "우리", "동아", "신성", "오성", "글로벌", "미래", "정우",
              "태광", "세아", "청림", "유진", "센트럴", "한솔", "제일", "코어", "브이", "엔텍"]
    suffix = ["산업", "테크", "시스템", "물산", "엔지니어링", "솔루션", "상사", "정밀"]
    items = ["ERP 라이선스", "유지보수 계약", "설비 공급", "컨설팅", "클라우드 구독",
             "부품 납품", "교육 서비스", "커스터마이징 개발"]

    created = {"customers": 0, "deals": 0, "activities": 0, "sales": 0, "targets": 0}
    today = TODAY()
    cust_ids: list[int] = []

    for i in range(24):
        owner = rng.choice(sales_owners)
        name = f"{rng.choice(prefix)}{rng.choice(suffix)}"
        cid = upsert_customer({
            "name": f"{name}{i+1:02d}", "biz_no": f"{rng.randint(100,999)}-{rng.randint(10,99)}-{rng.randint(10000,99999)}",
            "industry": rng.choice(INDUSTRIES), "grade": rng.choices(GRADES, [1, 3, 4, 2])[0],
            "owner": owner, "manager": f"{rng.choice('김이박최정강조윤장임')}{rng.choice(['과장','대리','부장','팀장','차장'])}",
            "phone": f"010-{rng.randint(1000,9999)}-{rng.randint(1000,9999)}",
            "email": f"contact{i+1}@example.co.kr", "address": rng.choice(["서울","경기","인천","부산","대전","광주"]) + " 소재",
            "status": "활성", "memo": "샘플 데이터",
            "credit_limit": rng.randrange(1, 12) * 100_000_000,
            "payment_terms": rng.choice(PAYMENT_TERMS[1:]),
        }, db_path)
        cust_ids.append(cid)
        created["customers"] += 1

    deal_ids: list[tuple[int, int, str]] = []
    for i in range(55):
        cid = rng.choice(cust_ids)
        owner = get_customer(cid, db_path)["owner"]
        stage = rng.choices(STAGES, [12, 14, 16, 14, 10, 22, 12])[0]
        list_amount = rng.randrange(3, 120) * 1_000_000
        discount = round(rng.choice([0, 0, 0, 3, 5, 8, 12, 15, 22]) * 1.0, 1)
        amount = int(list_amount * (100 - discount) / 100)
        exp = today + timedelta(days=rng.randint(-40, 90))
        # 단계가 높을수록 MEDDIC 충족 항목이 많아지도록 생성
        depth = STAGES.index(stage) if stage in OPEN_STAGES else 6
        meddic = {f: (1 if i < depth else 0) for i, f in enumerate(MEDDIC_FIELDS)}
        need_role = required_approval_role(discount)
        did = upsert_deal({
            "customer_id": cid, "title": f"{rng.choice(items)} 건", "owner": owner,
            "stage": stage, "amount": amount, "list_amount": list_amount,
            "discount_rate": discount, "probability": STAGE_PROB[stage],
            "expected_close": exp, "source": rng.choice(LEAD_SOURCES),
            "competitor": rng.choice(["", "A社", "B社", "C社"]), "memo": "샘플 기회",
            "forecast_category": rng.choices(FORECAST_CATS, [25, 30, 40, 5])[0],
            "lost_reason": rng.choice(LOST_REASONS) if stage == STAGE_LOST else None,
            "approval_status": ("승인" if need_role and stage == STAGE_WON else
                                rng.choice(["대기", "미요청"]) if need_role else "미요청"),
            **meddic,
            "closed_at": today - timedelta(days=rng.randint(0, 120)) if stage in (STAGE_WON, STAGE_LOST) else None,
        }, db_path, force=True)
        deal_ids.append((did, cid, stage))
        created["deals"] += 1

    for _ in range(220):
        did, cid, _stage = rng.choice(deal_ids)
        owner = get_customer(cid, db_path)["owner"]
        act_date = today - timedelta(days=rng.randint(0, 120))
        has_next = rng.random() < 0.45
        add_activity({
            "customer_id": cid, "deal_id": did, "act_date": act_date,
            "act_type": rng.choice(ACT_TYPES), "owner": owner,
            "summary": rng.choice(["담당자 미팅 진행", "견적서 발송", "제품 데모 시연",
                                    "예산/일정 확인", "경쟁사 동향 파악", "계약 조건 협의"]),
            "next_action": rng.choice(["재방문 일정 조율", "수정 견적 발송", "기술 검토 회신"]) if has_next else None,
            "next_date": today + timedelta(days=rng.randint(-3, 14)) if has_next else None,
        }, db_path)
        created["activities"] += 1

    # 딜 생성일/단계 이력 백데이트: 전환율·체류일·영업주기를 실제처럼 만든다
    with get_conn(db_path) as conn:
        for did, cid, stage in deal_ids:
            age = rng.randint(35, 220)
            created_date = today - timedelta(days=age)   # 집계용 dict(created)와 이름 분리
            depth = STAGES.index(stage) if stage in OPEN_STAGES else 5
            cursor_date, prev_stage = created_date, None
            for idx in range(depth + 1):
                to_stage = STAGES[idx] if idx < 5 else stage
                dwell = rng.randint(4, max(5, age // (depth + 2)))
                conn.execute(
                    "INSERT INTO deal_stage_history (deal_id, from_stage, to_stage, changed_at, "
                    "actor, days_in_stage) VALUES (?,?,?,?,?,?)",
                    (did, prev_stage, to_stage, cursor_date.strftime("%Y-%m-%d %H:%M:%S"),
                     "system", dwell if prev_stage else 0))
                prev_stage = to_stage
                cursor_date = min(cursor_date + timedelta(days=dwell), today)
            closed = cursor_date if stage in (STAGE_WON, STAGE_LOST) else None
            conn.execute(
                "UPDATE deals SET created_at=?, stage_since=?, closed_at=COALESCE(?, closed_at) "
                "WHERE id=?",
                (created_date.strftime("%Y-%m-%d %H:%M:%S"), cursor_date.strftime("%Y-%m-%d"),
                 closed.strftime("%Y-%m-%d") if closed else None, did))

    for _ in range(160):
        did, cid, stage = rng.choice(deal_ids)
        owner = get_customer(cid, db_path)["owner"]
        qty = rng.randint(1, 14)
        unit = rng.randrange(20, 600) * 10_000
        age_days = rng.randint(0, 330)
        # 오래된 매출일수록 수금이 끝나 있는 것이 정상적인 채권 구조다
        if age_days > 120:
            status = rng.choices(SALE_STATUS, [1, 1, 20])[0]
        elif age_days > 45:
            status = rng.choices(SALE_STATUS, [3, 2, 10])[0]
        else:
            status = rng.choices(SALE_STATUS, [6, 2, 4])[0]
        upsert_sale({
            "customer_id": cid, "deal_id": did if stage == STAGE_WON else None,
            "sale_date": today - timedelta(days=age_days),
            "item": rng.choice(items), "qty": qty, "unit_price": unit, "amount": qty * unit,
            "owner": owner, "status": status, "memo": "",
        }, db_path)
        created["sales"] += 1

    start = today.replace(day=1)
    for i in range(12):
        ym = (start - pd.DateOffset(months=i)).strftime("%Y-%m")
        for owner in sales_owners:
            upsert_target(ym, owner, rng.randrange(50, 110) * 1_000_000, db_path)
            created["targets"] += 1

    # 과거 6개월 주간 스냅샷: 그 달 실제 매출에 예측 오차를 섞어 생성한다
    created["snapshots"] = 0
    with get_conn(db_path) as conn:
        for i in range(1, 7):
            month_start = (start - pd.DateOffset(months=i)).date()
            ym = month_start.strftime("%Y-%m")
            for week in range(0, 4):
                snap = (month_start + timedelta(days=week * 7)).strftime("%Y-%m-%d")
                for owner in sales_owners:
                    actual = conn.execute(
                        "SELECT COALESCE(SUM(amount),0) FROM sales "
                        "WHERE strftime('%Y-%m', sale_date)=? AND owner=?", (ym, owner)
                    ).fetchone()[0]
                    if not actual:
                        continue
                    # 월초에는 오차가 크고 월말로 갈수록 실적에 수렴하도록 생성
                    error = rng.uniform(0.70, 1.25) - week * 0.04
                    commit = int(actual * max(error, 0.5))
                    for cat, amt in (("Commit", commit),
                                     ("Best Case", int(commit * rng.uniform(0.2, 0.5))),
                                     ("Pipeline", int(commit * rng.uniform(0.8, 2.0)))):
                        conn.execute(
                            "INSERT INTO pipeline_snapshots (snap_date, yyyymm, owner, category, "
                            "deal_cnt, amount, weighted) VALUES (?,?,?,?,?,?,?) "
                            "ON CONFLICT(snap_date, yyyymm, owner, category) DO UPDATE SET "
                            "amount=excluded.amount",
                            (snap, ym, owner, cat, rng.randint(1, 6), amt, int(amt * 0.6)))
                        created["snapshots"] += 1
    return created


if __name__ == "__main__":  # 간단한 CLI: python sales_db.py --seed
    import sys

    init_db()
    if "--seed" in sys.argv:
        print("샘플 데이터 생성:", seed_demo_data())
    print("DB 준비 완료:", DB_PATH)
