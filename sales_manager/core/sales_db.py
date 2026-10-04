"""영업관리 시스템 - 데이터 계층 (SQLite 개발 / PostgreSQL 운영)

웹 화면(Flask views)과 완전히 분리된 순수 파이썬 모듈이다.
설계 원칙
  * 모든 SQL은 파라미터 바인딩으로만 수행한다 (SQL Injection 차단)
  * 커넥션은 core.database 가 준다 (SQLite 파일 또는 PostgreSQL 커넥션 풀)
  * 스키마 변경은 Alembic 마이그레이션(migrations/)으로만 한다
  * 목록 조회는 pandas.DataFrame, 단건 조회는 dict 를 반환한다
  * 금액 단위는 원(KRW) 정수로 저장한다 (실수 오차 방지)
  * 담당자는 사용자 id(owner_id)로 연결한다. owner(이름)는 표시용 사본이다
  * 매출·이력이 딸린 데이터는 지우지 않는다(매출은 '취소', 거래처는 '종료')
  * 감사로그는 해시 체인으로 묶고 DB 트리거로 수정·삭제를 막는다
"""
from __future__ import annotations

import calendar
import hashlib
import json
from decimal import Decimal
import os
import random
import re
import shutil
import subprocess
from contextvars import ContextVar
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional

import pandas as pd

from . import database
from .database import days_between, days_since, get_conn, lock, transaction  # noqa: F401 - 다른 모듈이 db.get_conn 으로 쓴다

# ----------------------------------------------------------------------------
# 설정 / 공통 상수
# ----------------------------------------------------------------------------
BASE_DIR = database.BASE_DIR   # sales/

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
ROLES = {"REP": 1, "SUPPORT": 1, "MANAGER": 2, "EXEC": 3, "ADMIN": 4}
ROLE_LABEL = {"REP": "영업사원", "SUPPORT": "영업지원", "MANAGER": "팀장", "EXEC": "임원", "ADMIN": "시스템관리자"}
# 영업지원(SUPPORT): 결재 서열은 영업사원과 같고(결재 못 함), 전사 데이터를 보며 데이터 관리 업무(데이터 점검·
# 거래처 병합·품목 등록)만 시스템관리자와 함께 한다. 사용자·ERP·API·회사 설정은 시스템관리자만 (enterprise.has_role)
DATA_ROLES = ("SUPPORT", "ADMIN")

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

DEFAULT_OPEN_STAGES = list(OPEN_STAGES)


def apply_stage_names(names: list[str]) -> None:
    """진행 단계 이름 바꾸기 (회사 설정 stage_names) — 같은 리스트·딕셔너리 객체를 제자리에서 바꾼다.

    단계 조건(STAGE_REQUIREMENTS)·확률은 '몇 번째 단계인가'를 따라간다. 수주·실주는 매출·채권 로직이 쓰므로 고정.
    DB 의 deals.stage 등은 company.save 가 같은 트랜잭션에서 바꾼다."""
    names = list(names)
    if names == OPEN_STAGES or len(names) != len(OPEN_STAGES):
        return
    old = list(OPEN_STAGES)
    probs = {new: STAGE_PROB.get(o, 0) for o, new in zip(old, names)}
    reqs = {new: STAGE_REQUIREMENTS[o] for o, new in zip(old, names) if o in STAGE_REQUIREMENTS}
    OPEN_STAGES[:] = names
    STAGES[:] = [*names, STAGE_WON, STAGE_LOST]
    STAGE_PROB.clear()
    STAGE_PROB.update({**probs, STAGE_WON: 100, STAGE_LOST: 0})
    fixed = {k: v for k, v in STAGE_REQUIREMENTS.items() if k in (STAGE_WON, STAGE_LOST)}
    STAGE_REQUIREMENTS.clear()
    STAGE_REQUIREMENTS.update({**reqs, **fixed})


# 할인율 구간별 필요 결재 권한 (Deal Desk 정책)
DISCOUNT_POLICY = [(0.0, None), (10.0, "MANAGER"), (20.0, "EXEC"), (100.0, "ADMIN")]

LOST_REASONS = ["가격", "기능/스펙 부족", "경쟁사 선정", "예산 취소/보류",
                "일정 지연", "의사결정 중단", "내부 개발 전환", "기타"]
APPROVAL_STATUS = ["미요청", "대기", "승인", "반려", "취소"]
SALE_CANCELLED = "취소"                 # 수금상태와 별개인 매출 취소 상태 (삭제 대신 사용)
TAX_TYPES = ["과세", "영세", "면세"]       # 과세 10%, 영세율 0%(수출 등), 면세(계산서 발행)
VAT_RATE = {"과세": 0.10, "영세": 0.0, "면세": 0.0}
ACTIVE_SALE = "status <> '취소'"         # 실적·채권 집계에서 취소 매출 제외
AR_BUCKETS = ["정상", "1~30일", "31~60일", "61~90일", "90일 초과"]   # 결제기일 경과일 (enterprise.ar_aging)
PAYMENT_TERMS = [0, 15, 30, 45, 60, 90]


# ----------------------------------------------------------------------------
# 스키마 (Alembic 마이그레이션) / 담당자 연결
# ----------------------------------------------------------------------------
OWNER_TABLES = ("customers", "deals", "activities", "sales", "targets", "pipeline_snapshots")


def link_owner_ids(conn, name: str | None = None) -> None:
    """owner_id 가 비어 있는 기록을 같은 이름의 사용자에게 연결한다.

    이름이 정확히 한 명에게만 해당할 때만 연결한다(동명이인은 관리자가 '담당자 이관'으로 지정).
    """
    for table in OWNER_TABLES:
        extra, params = ("AND t.owner = ?", [name]) if name else ("", [])
        conn.execute(
            f"UPDATE {table} AS t SET owner_id = (SELECT u.id FROM users u WHERE u.name = t.owner) "
            f"WHERE t.owner_id IS NULL AND t.owner IS NOT NULL {extra} "
            f"AND (SELECT COUNT(*) FROM users u WHERE u.name = t.owner) = 1", params)
    # owner_key 를 사용자 id 로 바꾸되, 같은 키가 이미 있으면(유일성 충돌) 그대로 둔다
    for table, keys in (("targets", ("yyyymm",)), ("pipeline_snapshots", ("snap_date", "yyyymm", "category"))):
        same = " AND ".join(f"x.{k} = {table}.{k}" for k in keys)
        conn.execute(f"UPDATE {table} SET owner_key = CAST(owner_id AS TEXT) "
                     f"WHERE owner_id IS NOT NULL AND owner_key <> CAST(owner_id AS TEXT) "
                     f"AND NOT EXISTS (SELECT 1 FROM {table} x WHERE {same} "
                     f"AND x.owner_key = CAST({table}.owner_id AS TEXT))")
    for col, id_col in (("requested_by", "requested_by_id"), ("approver", "approver_id")):
        conn.execute(
            f"UPDATE approvals AS a SET {id_col} = (SELECT u.id FROM users u WHERE u.name = a.{col}) "
            f"WHERE a.{id_col} IS NULL AND a.{col} IS NOT NULL "
            f"AND (SELECT COUNT(*) FROM users u WHERE u.name = a.{col}) = 1")


def init_db(db_path: str | None = None) -> None:
    """스키마를 최신 버전으로 올린다 (Alembic upgrade head).

    전환 전 DB 도 데이터를 지우지 않고 올라간다. 운영에서는 배포 단계에서
    `python manage.py db upgrade` 로 미리 올리고 SALES_AUTO_MIGRATE=0 으로 둔다.
    """
    database.migrate(db_path)
    with get_conn(db_path) as conn:
        link_owner_ids(conn)


# ── 실행 컨텍스트: 로그인 사용자와 데이터 접근 범위 ──────────────────────
# ContextVar 를 쓰면 요청(스레드)마다 값이 독립적으로 유지되어
# 다른 사용자의 범위가 섞이지 않는다.
_ACTOR: ContextVar[str] = ContextVar("actor", default="system")
_ACTOR_ID: ContextVar[Optional[int]] = ContextVar("actor_id", default=None)
_SCOPE: ContextVar[Optional[tuple]] = ContextVar("owner_scope", default=None)   # 볼 수 있는 사용자 id


def set_context(actor: str = "system", owner_scope: Optional[Iterable[int]] = None,
                actor_id: int | None = None) -> None:
    """로그인 직후/매 요청마다 호출. owner_scope=None 이면 전사 조회."""
    _ACTOR.set(actor or "system")
    _ACTOR_ID.set(actor_id)
    _SCOPE.set(None if owner_scope is None else tuple(int(i) for i in owner_scope))


_IP: ContextVar[Optional[str]] = ContextVar("sales_ip", default=None)


def set_ip(ip: Optional[str]) -> None:
    """요청을 보낸 IP — 감사로그 내용에 함께 남긴다(자재관리 감사로그와 같은 항목)."""
    _IP.set(ip)


def current_actor() -> str:
    return _ACTOR.get()


def current_actor_id() -> Optional[int]:
    return _ACTOR_ID.get()


def current_scope() -> Optional[tuple]:
    return _SCOPE.get()


def _scope_clause(alias: str = "") -> tuple[str, list]:
    """로그인 사용자의 접근 범위를 SQL 조건으로 변환한다 (담당자 id 기준)."""
    scope = _SCOPE.get()
    if scope is None:
        return "", []
    if not scope:                      # 볼 수 있는 담당자가 없음 → 결과 없음
        return " AND 1=0", []
    prefix = f"{alias}." if alias else ""
    return f" AND {prefix}owner_id IN ({','.join('?' * len(scope))})", list(scope)


def in_scope(owner_id: Optional[int]) -> bool:
    scope = _SCOPE.get()
    return scope is None or (owner_id is not None and int(owner_id) in scope)


def check_record_scope(row: Optional[dict], label: str = "데이터") -> None:
    """수정·삭제 대상이 로그인 사용자의 범위 안인지 확인한다 (화면을 거치지 않는 호출도 막는다)."""
    if row is None:
        raise ValueError(f"존재하지 않는 {label}입니다.")
    if not in_scope(row.get("owner_id")):
        raise PermissionError(f"조회 권한이 없는 {label}입니다.")


def resolve_owner(value: Any, db_path: str | None = None) -> tuple[int, str]:
    """담당자 입력을 등록된 활성 사용자로 확정한다.

    int 는 사용자 id, 문자열은 사번 또는 이름으로 찾는다. 이름이 여러 명이면 사번을 요구한다.
    권한 범위 밖 사용자에게 데이터를 넘기는 것도 여기서 막는다.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError("담당자는 필수입니다.")
    row = None
    if isinstance(value, int):
        row = _one("SELECT id, name FROM users WHERE id=? AND active=1", [value], db_path)
    else:
        text = str(value).strip()
        row = _one("SELECT id, name FROM users WHERE emp_no=? AND active=1", [text], db_path)
        if not row:
            rows = _df("SELECT id, name, emp_no FROM users WHERE name=? AND active=1", [text], db_path)
            if len(rows) > 1:
                raise ValueError(f"담당자 '{text}' 는 동명이인이 있습니다 → 사번으로 지정하세요 "
                                 f"({', '.join(rows['emp_no'].astype(str))})")
            row = rows.iloc[0].to_dict() if len(rows) == 1 else None
    if not row:
        raise ValueError(f"담당자 '{value}' 는 등록된 활성 사용자가 아닙니다 "
                         f"(👥 조직·사용자 메뉴에서 먼저 등록하세요).")
    owner_id = int(row["id"])
    if not in_scope(owner_id):
        raise ValueError(f"담당자 '{row['name']}' 는 본인 권한 범위 밖입니다.")
    return owner_id, str(row["name"])


def _audit_hash(prev: str, ts: str, actor: str, actor_id: Any, action: str, entity: str,
                entity_id: Any, detail: Any) -> str:
    payload = json.dumps([prev, ts, actor, actor_id, action, entity, entity_id, detail],
                         ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _audit_fallback(record: dict, error: Exception) -> None:
    """DB 기록에 실패한 감사 이벤트를 파일에라도 남긴다 (조용히 버리지 않는다)."""
    folder = os.path.join(BASE_DIR, "data", "logs")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "audit_fallback.log"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({**record, "error": str(error)}, ensure_ascii=False) + "\n")


def audit(action: str, entity: str, entity_id: int | None = None,
          detail: Any = None, db_path: str | None = None) -> None:
    """감사로그 기록. 직전 기록의 해시를 이어 받아 위변조를 탐지할 수 있게 한다.

    DB 기록에 실패해도 업무 트랜잭션은 막지 않되, data/logs/audit_fallback.log 에 남긴다.
    """
    text = (json.dumps(detail, ensure_ascii=False, default=str)
            if not isinstance(detail, (str, type(None))) else detail)
    ip = _IP.get()
    try:                                       # 요청 ID — 사용자 문의(오류 화면의 문의 번호)와 감사로그를 잇는다
        from .observability import request_id
        rid = request_id()
    except Exception:   # noqa: BLE001
        rid = "-"
    if (ip or rid not in ("-", "", None)) and isinstance(detail, (dict, type(None))):
        extra = {k: v for k, v in (("접속IP", ip), ("요청ID", rid if rid not in ("-", "") else None)) if v}
        text = json.dumps({**(detail or {}), **extra}, ensure_ascii=False, default=str)
    record = {"ts": _now(), "actor": _ACTOR.get(), "actor_id": _ACTOR_ID.get(), "action": action,
              "entity": entity, "entity_id": entity_id, "detail": text}
    try:
        with get_conn(db_path) as conn:
            lock(conn, "audit_chain")               # 해시 체인이 엇갈리지 않도록 쓰기 잠금 (서버 여러 대 공통)
            last = conn.execute("SELECT hash FROM audit_log WHERE hash IS NOT NULL "
                                "ORDER BY id DESC LIMIT 1").fetchone()
            if not last:                            # 모두 파일로 이관된 뒤 → 마지막 이관 지점에서 체인을 잇는다
                try:
                    last = conn.execute("SELECT last_hash FROM audit_archives ORDER BY last_id DESC LIMIT 1").fetchone()
                except Exception:   # noqa: BLE001 - 이관 테이블이 없던 옛 DB
                    last = None
            prev = (last[0] if last else "") or ""
            digest = _audit_hash(prev, *record.values())
            conn.execute(
                "INSERT INTO audit_log (ts, actor, actor_id, action, entity, entity_id, detail, "
                "prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?)",
                (*record.values(), prev, digest))
    except Exception as exc:   # noqa: BLE001 - 감사 기록 실패가 업무를 막지 않되 파일에 남긴다
        _audit_fallback(record, exc)
        return
    if os.environ.get("SALES_AUDIT_STDOUT") == "1" and not database.SQLITE_FAST:   # 샘플 대량 생성 중에는 찍지 않음
        # 시연 서버처럼 DB 가 재시작마다 초기화되는 곳: 서버 로그(Render 로그)에도 한 줄 JSON 으로 남겨 방문자 행동을 확인
        print("AUDIT " + json.dumps(record, ensure_ascii=False, default=str), flush=True)


def verify_audit_chain(db_path: str | None = None) -> dict:
    """감사로그 해시 체인을 처음부터 다시 계산해 중간 삭제·수정·삽입을 찾아낸다."""
    rows = _df("SELECT id, ts, actor, actor_id, action, entity, entity_id, detail, prev_hash, hash "
               "FROM audit_log ORDER BY id", (), db_path)
    result = {"total": len(rows), "unsigned": 0, "verified": 0, "broken_id": None, "reason": ""}
    try:                               # 보관기간이 지나 파일로 옮긴 구간이 있으면 그 마지막 해시에서 이어서 검증
        anchor = _one("SELECT last_id, last_hash FROM audit_archives ORDER BY last_id DESC LIMIT 1", (), db_path)
    except Exception:   # noqa: BLE001 - 이관 테이블이 없던 옛 DB
        anchor = None
    prev, started = ((anchor or {}).get("last_hash") or ""), bool(anchor and anchor.get("last_hash"))
    result["archived_until"] = int(anchor["last_id"]) if anchor else None
    def blank(v: Any) -> bool:                # pandas 는 빈 칸(NULL)을 NaN 으로 읽는다
        return v is None or (isinstance(v, float) and pd.isna(v)) or v == ""

    for r in rows.to_dict("records"):
        if blank(r["hash"]):
            if started:               # 체인 시작 이후에 해시 없는 기록 → 직접 삽입된 것
                result.update(broken_id=int(r["id"]), reason="해시 없는 기록이 끼어 있음")
                return result
            result["unsigned"] += 1
            continue
        started = True
        actor_id = None if pd.isna(r["actor_id"]) else int(r["actor_id"])
        entity_id = None if pd.isna(r["entity_id"]) else int(r["entity_id"])
        detail = None if (r["detail"] is None or (isinstance(r["detail"], float) and pd.isna(r["detail"]))) \
            else r["detail"]
        expect = _audit_hash(prev, r["ts"], r["actor"], actor_id, r["action"], r["entity"],
                             entity_id, detail)
        if ("" if blank(r["prev_hash"]) else r["prev_hash"]) != prev:
            result.update(broken_id=int(r["id"]), reason="앞 기록과 연결이 끊김(중간 기록 삭제 의심)")
            return result
        if expect != r["hash"]:
            result.update(broken_id=int(r["id"]), reason="내용이 기록 당시와 다름(수정 의심)")
            return result
        prev = r["hash"]
        result["verified"] += 1
    return result


def diff(prev: Optional[dict], new: dict, fields: Iterable[str]) -> dict:
    """감사로그용 변경 전·후 값 {필드: [전, 후]}."""
    def norm(v):
        if v is None or (isinstance(v, float) and pd.isna(v)) or v == "":
            return None
        if isinstance(v, (int, float)):
            return float(v)
        try:
            return float(str(v).replace(",", ""))
        except ValueError:
            return str(v)[:500]
    out = {}
    for f in fields:
        a, b = (prev or {}).get(f), new.get(f)
        if norm(a) != norm(b):
            out[f] = [a, b]
    return out


def list_audit(limit: int = 300, actor: str = "", entity: str = "",
               db_path: str | None = None, date_from: str = "", date_to: str = "", entity_id: int | None = None,
               action: str = "", keyword: str = "") -> pd.DataFrame:
    """감사로그 검색 — 사용자·대상·기간·대상 번호·작업·상세 글자(접속 IP·요청 ID·거래처명 등)."""
    sql = ("SELECT id AS 번호, ts AS 시각, actor AS 사용자, action AS 작업, entity AS 대상, "
           'entity_id AS "대상ID", detail AS 상세 FROM audit_log WHERE 1=1')
    params: list[Any] = []
    if actor:
        sql += " AND actor LIKE ?"
        params.append(f"%{actor}%")
    if entity:
        sql += " AND entity = ?"
        params.append(entity)
    if _d(date_from):
        sql += " AND ts >= ?"
        params.append(_d(date_from))
    if _d(date_to):
        sql += " AND ts <= ?"
        params.append(_d(date_to) + " 23:59:59")
    if entity_id:
        sql += " AND entity_id = ?"
        params.append(int(entity_id))
    if action:
        sql += " AND action LIKE ?"
        params.append(f"%{action}%")
    if keyword:
        sql += " AND detail LIKE ?"
        params.append(f"%{keyword}%")
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
    return database.df(sql, params, db_path)


def _one(sql: str, params: Iterable[Any] = (), db_path: str | None = None) -> Optional[dict]:
    return database.one(sql, params, db_path)


def _scalar(sql: str, params: Iterable[Any] = (), db_path: str | None = None) -> float:
    return database.scalar(sql, params, db_path)


# ----------------------------------------------------------------------------
# 거래처(customers)
# ----------------------------------------------------------------------------
def list_customers(keyword: str = "", owner_id: int | None = None, grade: str = "",
                   status: str = "", db_path: str | None = None) -> pd.DataFrame:
    sql = f"""
        SELECT c.id, c.name AS 거래처명, c.grade AS 등급, c.industry AS 업종,
               c.owner AS 담당자, c.manager AS 고객담당자, c.phone AS 연락처,
               c.email AS 이메일, c.status AS 상태,
               {{open_cnt}} AS 진행딜,
               {{sales_total}} AS 누적매출,
               {{last_act}} AS 최근접촉일,
               c.credit_limit AS 여신한도, c.payment_terms AS 결제조건일,
               c.erp_code AS "ERP코드",
               c.address AS 주소, c.memo AS 메모, c.biz_no AS 사업자번호, c.owner_id
          FROM customers c {{joins}}
         WHERE 1=1
    """
    # 집계 방식: 검색으로 좁혀지면 거래처마다 인덱스로(빠름), 전체 목록이면 표를 한 번씩 읽어 묶는다(흩어진 조회보다 빠름).
    # 거래처 1만 · 매출 10만 건 측정 — 전체: 묶기 0.5초 vs 거래처마다 1.0초 / 검색: 거래처마다 수 ms
    if keyword:
        sql = sql.replace("{open_cnt}", "(SELECT COUNT(*) FROM deals d WHERE d.customer_id = c.id AND d.stage NOT IN ('수주','실주'))")                  .replace("{sales_total}", f"COALESCE((SELECT SUM(s.amount) FROM sales s WHERE s.customer_id = c.id AND s.{ACTIVE_SALE}), 0)")                  .replace("{last_act}", "(SELECT MAX(a.act_date) FROM activities a WHERE a.customer_id = c.id)")                  .replace("{joins}", "")
    else:
        sql = sql.replace("{open_cnt}", "COALESCE(d.open_cnt, 0)").replace("{sales_total}", "COALESCE(s.total, 0)")                  .replace("{last_act}", "a.last_date").replace("{joins}", f"""
          LEFT JOIN (SELECT customer_id, COUNT(*) open_cnt FROM deals
                      WHERE stage NOT IN ('수주','실주') GROUP BY customer_id) d ON d.customer_id = c.id
          LEFT JOIN (SELECT customer_id, SUM(amount) total FROM sales
                      WHERE {ACTIVE_SALE} GROUP BY customer_id) s ON s.customer_id = c.id
          LEFT JOIN (SELECT customer_id, MAX(act_date) last_date FROM activities
                      GROUP BY customer_id) a ON a.customer_id = c.id""")
    params: list[Any] = []
    if keyword:
        sql += " AND (c.name LIKE ? OR c.manager LIKE ? OR c.memo LIKE ?)"
        params += [f"%{keyword}%"] * 3
    if owner_id:
        sql += " AND c.owner_id = ?"
        params.append(int(owner_id))
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


class ConflictError(ValueError):
    """다른 사용자가 먼저 수정한 레코드를 덮어쓰려 할 때."""


CUSTOMER_FIELDS = ["name", "biz_no", "industry", "grade", "owner", "manager",
                   "phone", "email", "address", "status", "memo",
                   "credit_limit", "payment_terms", "erp_code"]


class DuplicateCustomer(ValueError):
    """같은 사업자번호의 거래처가 이미 있음 — 저장하지 않는다."""


class SimilarCustomer(ValueError):
    """이름이 거의 같은 거래처가 있음 — 확인하면 등록할 수 있다."""

    def __init__(self, message: str, names: list[str]):
        super().__init__(message)
        self.names = names


_CORP_WORDS = re.compile(r"주식회사|유한회사|유한책임회사|합자회사|\(주\)|\(유\)|㈜|co\.?,?\s*ltd\.?|inc\.?|corp\.?",
                         re.I)


def name_key(name: Any) -> str:
    """'(주)대한산업', '대한산업 주식회사', '대한 산업' 을 같은 이름으로 본다."""
    return re.sub(r"[\s\-_.,·()\[\]]", "", _CORP_WORDS.sub("", str(name or ""))).lower()


def valid_biz_no(value: Any) -> bool:
    """사업자등록번호 10자리 검증번호 (국세청 가중치 1,3,7,1,3,7,1,3,5). documents.valid_biz_no 와 같은 규칙."""
    d = [int(c) for c in biz_digits(value)]
    if len(d) != 10:
        return False
    total = sum(a * w for a, w in zip(d[:9], [1, 3, 7, 1, 3, 7, 1, 3, 5])) + (d[8] * 5) // 10
    return (10 - total % 10) % 10 == d[9]


def _with_check_digit(raw: str) -> str:
    """샘플용: 앞 9자리는 그대로 두고 마지막 자리를 검증번호로 맞춘다 (xxx-xx-xxxxx)."""
    d = [int(c) for c in biz_digits(raw)][:9]
    total = sum(a * w for a, w in zip(d, [1, 3, 7, 1, 3, 7, 1, 3, 5])) + (d[8] * 5) // 10
    full = "".join(map(str, d)) + str((10 - total % 10) % 10)
    return f"{full[:3]}-{full[3:5]}-{full[5:]}"


def biz_digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def find_duplicates(name: str, biz_no: str, exclude_id: int | None = None,
                    db_path: str | None = None) -> tuple[Optional[dict], list[dict]]:
    """(같은 사업자번호 거래처, 이름이 거의 같은 거래처들). 병합된 거래처는 제외."""
    digits_ = biz_digits(biz_no)
    same_biz = None
    if digits_:
        same_biz = _one("SELECT id, name, owner, owner_id FROM customers WHERE biz_no_norm=? AND id<>? "
                        "AND merged_into IS NULL", [digits_, int(exclude_id or 0)], db_path)
    key = name_key(name)
    similar = []
    if key:
        rows = _df("SELECT id, name, owner, owner_id, biz_no FROM customers WHERE id<>? AND merged_into IS NULL",
                   [int(exclude_id or 0)], db_path).to_dict("records")
        similar = [r for r in rows if name_key(r["name"]) == key]
    return same_biz, similar


ERP_LOCKED_FIELDS = ("name", "biz_no", "erp_code", "payment_terms", "credit_limit")


def upsert_customer(data: dict, db_path: str | None = None, confirm_similar: bool = True, source: str = "화면") -> int:
    """거래처 신규 등록 또는 수정. data['id'] 가 있으면 수정.

    data['row_version'] 을 넘기면 그 사이 다른 사람이 수정했는지 확인한다(동시 수정 충돌 방지).
    같은 사업자번호는 등록할 수 없다(매출·채권·여신이 두 거래처로 나뉘는 것을 막음).
    confirm_similar=False 이면 이름이 거의 같은 거래처가 있을 때 SimilarCustomer 로 확인을 받는다.
    """
    if not str(data.get("name", "")).strip():
        raise ValueError("거래처명은 필수입니다.")
    new_biz = str(data.get("biz_no") or "").strip()
    if new_biz and not valid_biz_no(new_biz):
        # 이미 저장된 번호를 그대로 두고 다른 항목만 고치는 것은 막지 않는다 (예전 데이터 수정 가능하게)
        old = _one("SELECT biz_no FROM customers WHERE id=?", [int(data["id"])], db_path) if data.get("id") else None
        if not old or biz_digits(old.get("biz_no")) != biz_digits(new_biz):
            raise ValueError(f"사업자번호 {new_biz} 가 올바르지 않습니다 (10자리·검증번호를 확인하세요).")
    same_biz, similar = find_duplicates(data["name"], data.get("biz_no"), data.get("id"), db_path)
    if same_biz:
        who = (f"'{same_biz['name']}' (담당 {same_biz['owner']})" if in_scope(same_biz.get("owner_id"))
               else "다른 팀이 담당하는 거래처")
        raise DuplicateCustomer(f"사업자번호 {data.get('biz_no')} 는 이미 {who}로 등록되어 있습니다. "
                                f"같은 회사라면 그 거래처를 쓰고, 중복이면 관리자에게 병합을 요청하세요.")
    if similar and not confirm_similar and not data.get("id"):
        names = [f"{r['name']} (담당 {r['owner']})" if in_scope(r.get("owner_id")) else "다른 팀 거래처"
                 for r in similar[:5]]
        raise SimilarCustomer("이름이 거의 같은 거래처가 이미 있습니다: " + ", ".join(names) +
                              ". 다른 회사가 맞으면 '그래도 등록'에 체크하고 다시 저장하세요.", names)
    owner_id, owner_name = resolve_owner(data.get("owner_id") or data.get("owner"), db_path)
    record = {f: data.get(f) for f in CUSTOMER_FIELDS}
    record.update(owner=owner_name, name=str(data["name"]).strip(),
                  credit_limit=int(data.get("credit_limit") or 0),
                  payment_terms=int(data.get("payment_terms") if data.get("payment_terms") not in (None, "")
                                    else 30),
                  erp_code=(str(data.get("erp_code") or "").strip() or None))
    if record["credit_limit"] < 0 or record["payment_terms"] < 0:
        raise ValueError("여신한도와 결제조건은 0 이상이어야 합니다.")
    values = [record[f] for f in CUSTOMER_FIELDS]

    if data.get("id"):
        cid = int(data["id"])
        prev = get_customer(cid, db_path)
        check_record_scope(prev, "거래처")
        if prev.get("erp_synced_at") and source != "ERP":
            changed = [f for f in ERP_LOCKED_FIELDS if str(prev.get(f) or "") != str(record.get(f) or "")]
            if changed:
                raise ValueError("ERP 에서 받아 온 거래처라 이 항목은 ERP 에서 고쳐야 합니다: " + ", ".join(changed))
        with get_conn(db_path) as conn:
            sql = (f"UPDATE customers SET {', '.join(f'{f}=?' for f in CUSTOMER_FIELDS)}, owner_id=?, "
                   f"updated_at=?, row_version=COALESCE(row_version,0)+1 WHERE id=?")
            params: list[Any] = [*values, owner_id, _now(), cid]
            if data.get("row_version") not in (None, ""):
                sql += " AND COALESCE(row_version,0)=?"
                params.append(int(data["row_version"]))
            if conn.execute(sql, params).rowcount == 0:
                raise ConflictError("다른 사용자가 먼저 이 거래처를 수정했습니다. "
                                    "화면을 새로고침해 최신 내용을 확인한 뒤 다시 저장하세요.")
        _set_biz_norm(cid, record.get("biz_no"), db_path)
        if str(prev.get("name") or "").strip() != record["name"]:       # 예전 이름은 다른 이름으로 남겨 예전 엑셀도 맞춰지게
            from . import customer_names
            customer_names.add_alias(cid, prev["name"], db_path=db_path, quiet=True)
        if db_path is None and (prev.get("manager"), prev.get("phone"), prev.get("email")) != \
                (record.get("manager"), record.get("phone"), record.get("email")):
            from . import contacts
            contacts.sync_from_customer(cid, record.get("manager"), record.get("phone"), record.get("email"))
        audit("수정", "거래처", cid, {"거래처명": record["name"],
                                      "변경": diff(prev, {**record, "owner_id": owner_id},
                                                   CUSTOMER_FIELDS + ["owner_id"])}, db_path)
        return cid
    with get_conn(db_path) as conn:
        cur = conn.execute(
            f"INSERT INTO customers ({', '.join(CUSTOMER_FIELDS)}, owner_id, created_at, updated_at) "
            f"VALUES ({', '.join('?' * len(CUSTOMER_FIELDS))}, ?, ?, ?)",
            (*values, owner_id, _now(), _now()),
        )
        new_id = int(cur.lastrowid)
    _set_biz_norm(new_id, record.get("biz_no"), db_path)
    if db_path is None and record.get("manager"):
        from . import contacts
        contacts.sync_from_customer(new_id, record.get("manager"), record.get("phone"), record.get("email"))
    audit("등록", "거래처", new_id, {"거래처명": record["name"], "담당자": owner_name}, db_path)
    return new_id


def _set_biz_norm(cid: int, biz_no: Any, db_path: str | None = None) -> None:
    with get_conn(db_path) as conn:
        conn.execute("UPDATE customers SET biz_no_norm=? WHERE id=?", (biz_digits(biz_no) or None, cid))


MERGE_TABLES = ("deals", "activities", "sales", "quotes", "customer_prices")


def duplicate_groups(db_path: str | None = None) -> list[dict]:
    """병합 후보: 사업자번호가 같거나 이름이 거의 같은 거래처 묶음."""
    rows = _df("SELECT id, name, biz_no, biz_no_norm, owner, status FROM customers WHERE merged_into IS NULL "
               "ORDER BY id", (), db_path).to_dict("records")
    groups: dict[tuple, list] = {}
    for r in rows:
        if r.get("biz_no_norm"):
            groups.setdefault(("사업자번호", r["biz_no_norm"]), []).append(r)
        groups.setdefault(("이름", name_key(r["name"])), []).append(r)
    out, seen = [], set()
    for (kind, key), items in groups.items():
        ids = tuple(sorted(i["id"] for i in items))
        if len(items) > 1 and key and ids not in seen:
            seen.add(ids)
            out.append({"기준": kind, "값": key, "거래처": items})
    return out


def merge_customers(source_id: int, target_id: int, reason: str, db_path: str | None = None) -> dict:
    """source 의 영업기회·활동·매출·견적·특가를 target 으로 옮기고 source 는 '종료'(병합됨)로 남긴다."""
    if int(source_id) == int(target_id):
        raise ValueError("같은 거래처끼리는 병합할 수 없습니다.")
    if not str(reason or "").strip():
        raise ValueError("병합 사유를 입력하세요.")
    src, dst = get_customer(int(source_id), db_path), get_customer(int(target_id), db_path)
    if not src or not dst:
        raise ValueError("거래처를 찾을 수 없습니다.")
    if src.get("merged_into") or dst.get("merged_into"):
        raise ValueError("이미 병합된 거래처입니다.")
    warnings = []
    if src.get("erp_code") and dst.get("erp_code") and src["erp_code"] != dst["erp_code"]:
        warnings.append(f"ERP 코드가 다릅니다({src['erp_code']} → {dst['erp_code']}). "
                        f"ERP 에 이미 보낸 매출은 ERP 쪽에서도 거래처를 맞춰야 합니다.")
    moved = {}
    with get_conn(db_path) as conn:
        for table in MERGE_TABLES:
            moved[table] = conn.execute(f"UPDATE {table} SET customer_id=? WHERE customer_id=?",
                                        (int(target_id), int(source_id))).rowcount
        fill = {f: src[f] for f in ("biz_no", "erp_code", "manager", "phone", "email", "address", "industry")
                if not dst.get(f) and src.get(f)}
        if fill.get("biz_no"):
            fill["biz_no_norm"] = biz_digits(fill["biz_no"])
        if fill:
            conn.execute(f"UPDATE customers SET {', '.join(f'{k}=?' for k in fill)}, updated_at=?, "
                         f"row_version=COALESCE(row_version,0)+1 WHERE id=?", (*fill.values(), _now(), int(target_id)))
        conn.execute("UPDATE customers SET status='종료', merged_into=?, biz_no_norm=NULL, updated_at=?, "
                     "memo=COALESCE(memo,'') || ?, row_version=COALESCE(row_version,0)+1 WHERE id=?",
                     (int(target_id), _now(), f"\n[병합 → {dst['name']} #{target_id}] {reason.strip()}",
                      int(source_id)))
    from . import customer_names                    # 합쳐진 쪽 이름·다른 이름 → 남는 쪽 다른 이름
    with get_conn(db_path) as conn:
        conn.execute("UPDATE customer_aliases SET customer_id=? WHERE customer_id=?", (int(target_id), int(source_id)))
    customer_names.add_alias(int(target_id), src["name"], db_path=db_path, quiet=True)
    result = {"이동": moved, "보완": list(fill), "경고": warnings}
    audit("거래처병합", "거래처", int(target_id),
          {"원거래처": f"{src['name']} #{source_id}", "대상": f"{dst['name']} #{target_id}", "사유": reason.strip(),
           **result}, db_path)
    return result


def delete_customer(customer_id: int, db_path: str | None = None) -> None:
    """이력이 없는 거래처만 삭제한다. 거래가 있었던 거래처는 상태를 '종료'로 바꿔 보존한다."""
    prev = get_customer(customer_id, db_path)
    check_record_scope(prev, "거래처")
    with get_conn(db_path) as conn:
        used = {t: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE customer_id=?", (customer_id,)).fetchone()[0]
                for t in ("deals", "activities", "sales")}
        if any(used.values()):
            raise ValueError("영업기회·활동·매출 이력이 있는 거래처는 삭제할 수 없습니다 "
                             f"(기회 {used['deals']} · 활동 {used['activities']} · 매출 {used['sales']}건). "
                             "상태를 '종료'로 바꾸세요.")
        conn.execute("DELETE FROM customer_aliases WHERE customer_id = ?", (customer_id,))   # 남으면 같은 이름을 못 씀
        conn.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
    audit("삭제", "거래처", customer_id, {"삭제 전": prev}, db_path)


def customer_options(db_path: str | None = None, include_closed: bool = True) -> dict[int, str]:
    scope_sql, scope_params = _scope_clause()
    closed = "" if include_closed else " AND status <> '종료'"
    with get_conn(db_path) as conn:                 # 거래처가 많아도 가볍게 (DataFrame 을 거치지 않음)
        rows = conn.execute(f"SELECT id, name FROM customers WHERE 1=1{closed}{scope_sql} ORDER BY name",
                            scope_params).fetchall()
    return {int(r[0]): r[1] for r in rows}


# ----------------------------------------------------------------------------
# 영업기회(deals)
# ----------------------------------------------------------------------------
def list_deals(keyword: str = "", owner_id: int | None = None, stage: str = "",
               only_open: bool = False, customer_id: int | None = None,
               db_path: str | None = None) -> pd.DataFrame:
    sql = f"""
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
               {days_since("COALESCE(d.stage_since, d.created_at)")} AS 단계체류일,
               d.source AS 유입경로, d.competitor AS 경쟁사, d.memo AS 메모,
               d.customer_id, d.closed_at AS 종료일, d.owner_id
          FROM deals d JOIN customers c ON c.id = d.customer_id
         WHERE 1=1
    """
    params: list[Any] = []
    if keyword:
        sql += " AND (d.title LIKE ? OR c.name LIKE ?)"
        params += [f"%{keyword}%"] * 2
    if owner_id:
        sql += " AND d.owner_id = ?"
        params.append(int(owner_id))
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


def approval_covers(deal: dict, db_path: str | None = None) -> bool:
    """현재 할인율·제안금액이 실제로 승인받은 조건 안에 있는지 결재 기록으로 확인한다."""
    if not deal.get("id"):
        return False
    row = _one("SELECT id FROM approvals WHERE deal_id=? AND status='승인' "
               "AND discount_rate >= ? - 0.0001 AND final_amount <= ? ORDER BY id DESC LIMIT 1",
               [int(deal["id"]), float(deal.get("discount_rate") or 0), int(deal.get("amount") or 0)],
               db_path)
    return row is not None


def validate_stage(data: dict, stage: str, db_path: str | None = None) -> list[str]:
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
            # 상태값만 믿지 않고 결재 기록의 승인 조건(할인율·금액)과 대조한다
            if role and not (data.get("approval_status") == "승인" and approval_covers(data, db_path)):
                missing.append(
                    f"할인 {float(data.get('discount_rate') or 0):.1f}% · "
                    f"제안금액 {int(data.get('amount') or 0):,}원에 대한 {ROLE_LABEL[role]} 승인")
        elif req in MEDDIC_FIELDS and not int(data.get(req) or 0):
            missing.append(MEDDIC_FIELDS[req].split(" - ")[0] + " 확인")
    return missing


DEAL_FIELDS = ["customer_id", "title", "owner", "stage", "amount", "probability",
               "expected_close", "source", "competitor", "memo", "closed_at",
               "forecast_category", "lost_reason", "list_amount", "discount_rate",
               "approval_status", "stage_since", *MEDDIC_FIELDS]


COMMERCIAL_FIELDS = ("list_amount", "amount", "discount_rate")


def _fresh_stage_names(db_path: str | None = None) -> None:
    """단계 이름은 회사 설정에서 바뀔 수 있다 — 서버가 여러 대면 다른 서버의 15초 캐시 사이에 옛 이름이 저장될 수 있어,
    단계를 쓰기 직전에는 설정을 다시 읽는다 (옛 이름이면 STAGES 검사에서 거부된다)."""
    if db_path is None and not database.SQLITE_FAST:      # 샘플 대량 생성 중에는 설정이 바뀌지 않으므로 생략
        from . import company
        company.refresh(max_age=2)                  # 2초 안에 읽은 설정이면 그대로 (저장마다 DB 를 읽지 않게)


def upsert_deal(data: dict, db_path: str | None = None, force: bool = False,
                force_reason: str = "") -> int:
    """영업기회 등록/수정.

    force=False 이면 Stage Gate 를 검증해 필수 조건 미충족 시 예외를 던진다.
    (관리자 우회나 데이터 이관 시에만 force=True 사용 — 사유가 감사로그에 남는다)
    승인 상태는 호출자가 넘긴 값을 믿지 않는다. 정가·금액·할인율이 바뀌면 기존 승인은 무효가 된다.
    """
    if not data.get("customer_id"):
        raise ValueError("거래처를 선택하세요.")
    if not str(data.get("title", "")).strip():
        raise ValueError("기회명은 필수입니다.")
    _fresh_stage_names(db_path)
    stage = data.get("stage") or OPEN_STAGES[0]
    if stage not in STAGES:
        raise ValueError(f"단계 값이 올바르지 않습니다: {stage}")
    cust = get_customer(int(data["customer_id"]), db_path)
    check_record_scope(cust, "거래처")
    owner_id, owner_name = resolve_owner(data.get("owner_id") or data.get("owner"), db_path)

    prev = get_deal(int(data["id"]), db_path) if data.get("id") else None
    if data.get("id"):
        check_record_scope(prev, "영업기회")

    list_amount = int(data.get("list_amount") or data.get("amount") or 0)
    amount = int(data.get("amount") or 0)
    discount = float(data.get("discount_rate") if data.get("discount_rate") is not None
                     else ((list_amount - amount) / list_amount * 100 if list_amount else 0))
    discount = round(discount, 2)

    # 승인 상태: 이관(force)일 때만 입력값을 쓰고, 평소에는 기존 값을 이어받는다
    if force:
        approval_status = data.get("approval_status") or (prev or {}).get("approval_status") or "미요청"
    else:
        approval_status = (prev or {}).get("approval_status") or "미요청"
    invalidated = False
    new_commercial = {"list_amount": list_amount, "amount": amount, "discount_rate": discount}
    if prev and approval_status in ("승인", "대기") and diff(prev, new_commercial, COMMERCIAL_FIELDS):
        approval_status, invalidated = "미요청", True

    merged = dict(prev or {})
    merged.update({k: v for k, v in data.items() if v is not None})
    merged.update(new_commercial, stage=stage, approval_status=approval_status)
    if not force and (prev is None or prev.get("stage") != stage):
        missing = validate_stage(merged, stage, db_path)
        if missing:
            raise ValueError(f"'{stage}' 단계로 진행하려면 다음이 필요합니다 → " + ", ".join(missing))

    closed_at = _d(data.get("closed_at")) or _d((prev or {}).get("closed_at"))
    if stage in (STAGE_WON, STAGE_LOST) and not closed_at:
        closed_at = _d(TODAY())
    if stage in OPEN_STAGES:
        closed_at = None

    stage_changed = prev is None or prev.get("stage") != stage
    stage_since = _d(TODAY()) if stage_changed else (prev or {}).get("stage_since") or _d(TODAY())

    values = [
        int(data["customer_id"]), str(data["title"]).strip(), owner_name, stage,
        amount, int(data.get("probability") or STAGE_PROB[stage]),
        _d(data.get("expected_close")), data.get("source"), data.get("competitor"),
        data.get("memo"), closed_at,
        data.get("forecast_category") or "Pipeline", data.get("lost_reason"),
        list_amount, discount, approval_status, stage_since,
        *[int(data.get(f) or 0) for f in MEDDIC_FIELDS],
    ]
    with get_conn(db_path) as conn:
        if data.get("id"):
            deal_id = int(data["id"])
            sql = (f"UPDATE deals SET {', '.join(f'{f}=?' for f in DEAL_FIELDS)}, owner_id=?, "
                   f"updated_at=?, row_version=COALESCE(row_version,0)+1 WHERE id=?")
            params: list[Any] = [*values, owner_id, _now(), deal_id]
            if data.get("row_version") not in (None, ""):
                sql += " AND COALESCE(row_version,0)=?"
                params.append(int(data["row_version"]))
            if conn.execute(sql, params).rowcount == 0:
                raise ConflictError("다른 사용자가 먼저 이 영업기회를 수정했습니다. "
                                    "화면을 새로고침해 최신 내용을 확인한 뒤 다시 저장하세요.")
            if invalidated:
                conn.execute("UPDATE approval_steps SET status='취소' WHERE status IN ('대기','예정') "
                             "AND approval_id IN (SELECT id FROM approvals WHERE deal_id=? AND status='대기')",
                             (deal_id,))
                conn.execute("UPDATE approvals SET status='취소', comment=COALESCE(comment,'') || "
                             "'[조건 변경으로 자동 취소]' WHERE deal_id=? AND status='대기'", (deal_id,))
        else:
            cur = conn.execute(
                f"INSERT INTO deals ({', '.join(DEAL_FIELDS)}, owner_id, created_at, updated_at) "
                f"VALUES ({', '.join('?' * len(DEAL_FIELDS))}, ?, ?, ?)",
                (*values, owner_id, _now(), _now()),
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
    new_row = dict(zip(DEAL_FIELDS, values), owner_id=owner_id)
    detail = {"기회명": new_row["title"], "단계": stage, "금액": amount,
              "변경": diff(prev, new_row, DEAL_FIELDS + ["owner_id"]) if prev else None}
    if force and _ACTOR.get() != "system":
        detail["단계검증우회"] = force_reason or "(사유 없음)"
    audit("수정" if prev else "등록", "영업기회", deal_id, detail, db_path)
    if invalidated:
        audit("승인무효화", "영업기회", deal_id,
              {"사유": "정가·금액·할인율 변경", "변경": diff(prev, new_commercial, COMMERCIAL_FIELDS)},
              db_path)
    return deal_id


def change_stage(deal_id: int, stage: str, db_path: str | None = None,
                 force: bool = False, lost_reason: str | None = None) -> None:
    """단계 변경. 확률/종료일/단계진입일/이력/감사로그를 함께 정리한다."""
    _fresh_stage_names(db_path)
    if stage not in STAGES:
        raise ValueError(f"단계 값이 올바르지 않습니다: {stage}")
    deal = get_deal(deal_id, db_path)
    check_record_scope(deal, "영업기회")
    merged = dict(deal)
    if lost_reason:
        merged["lost_reason"] = lost_reason
    if not force:
        missing = validate_stage(merged, stage, db_path)
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
            "lost_reason=COALESCE(?, lost_reason), updated_at=?, "
            "row_version=COALESCE(row_version,0)+1 WHERE id=?",
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
    """이력이 없는 영업기회만 삭제한다. 수주 건·매출·결재 이력이 있으면 '실주' 처리로 보존한다."""
    prev = get_deal(deal_id, db_path)
    check_record_scope(prev, "영업기회")
    with get_conn(db_path) as conn:
        sales_cnt = conn.execute("SELECT COUNT(*) FROM sales WHERE deal_id=?", (deal_id,)).fetchone()[0]
        appr_cnt = conn.execute("SELECT COUNT(*) FROM approvals WHERE deal_id=?", (deal_id,)).fetchone()[0]
        if prev["stage"] == STAGE_WON or sales_cnt or appr_cnt:
            raise ValueError("수주 건이거나 매출·결재 이력이 있는 영업기회는 삭제할 수 없습니다 "
                             f"(매출 {sales_cnt} · 결재 {appr_cnt}건). 진행을 멈추려면 '실주'로 변경하세요.")
        conn.execute("DELETE FROM deals WHERE id = ?", (deal_id,))
    audit("삭제", "영업기회", deal_id, {"삭제 전": prev}, db_path)


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
    with get_conn(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return {int(r[0]): r[1] for r in rows}


# ----------------------------------------------------------------------------
# 영업활동(activities)
# ----------------------------------------------------------------------------
def list_activities(days: int = 90, owner_id: int | None = None, act_type: str = "",
                    customer_id: int | None = None, db_path: str | None = None) -> pd.DataFrame:
    since = _d(TODAY() - timedelta(days=days))
    sql = """
        SELECT a.id, a.act_date AS 활동일, a.act_type AS 유형, c.name AS 거래처,
               d.title AS 관련기회, a.owner AS 담당자, a.summary AS 활동내용,
               a.next_action AS 다음액션, a.next_date AS 다음일정, a.customer_id, a.owner_id
          FROM activities a
          JOIN customers c ON c.id = a.customer_id
          LEFT JOIN deals d ON d.id = a.deal_id
         WHERE a.act_date >= ?
    """
    params: list[Any] = [since]
    if owner_id:
        sql += " AND a.owner_id = ?"
        params.append(int(owner_id))
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
    check_record_scope(get_customer(int(data["customer_id"]), db_path), "거래처")
    owner_id, owner_name = resolve_owner(data.get("owner_id") or data.get("owner"), db_path)
    with get_conn(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO activities (customer_id, deal_id, act_date, act_type, owner, owner_id, "
            "summary, next_action, next_date, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (int(data["customer_id"]), data.get("deal_id"), _d(data.get("act_date")) or _d(TODAY()),
             data.get("act_type") or "기타", owner_name, owner_id, data["summary"],
             data.get("next_action"), _d(data.get("next_date")), _now()),
        )
        new_id = int(cur.lastrowid)
    audit("등록", "영업활동", new_id, {"유형": data.get("act_type"), "담당자": owner_name}, db_path)
    return new_id


def delete_activity(activity_id: int, db_path: str | None = None) -> None:
    prev = _one("SELECT * FROM activities WHERE id = ?", [activity_id], db_path)
    check_record_scope(prev, "영업활동")
    with get_conn(db_path) as conn:
        conn.execute("DELETE FROM activities WHERE id = ?", (activity_id,))
    audit("삭제", "영업활동", activity_id, {"삭제 전": prev}, db_path)


def upcoming_actions(days: int = 7, owner_id: int | None = None, db_path: str | None = None) -> pd.DataFrame:
    sql = """
        SELECT a.next_date AS 예정일, c.name AS 거래처, a.owner AS 담당자,
               a.next_action AS 할일, a.summary AS 직전활동
          FROM activities a JOIN customers c ON c.id = a.customer_id
         WHERE a.next_date IS NOT NULL AND a.next_date <= ?
           AND a.next_action IS NOT NULL AND a.next_action <> ''
    """
    params: list[Any] = [_d(TODAY() + timedelta(days=days))]
    if owner_id:
        sql += " AND a.owner_id = ?"
        params.append(int(owner_id))
    scope_sql, scope_params = _scope_clause("a")
    sql += scope_sql
    params += scope_params
    sql += " ORDER BY a.next_date"
    return _df(sql, params, db_path)


# ----------------------------------------------------------------------------
# 매출(sales)
# ----------------------------------------------------------------------------
def list_sales(ym_from: str = "", ym_to: str = "", owner_id: int | None = None, status: str = "",
               customer_id: int | None = None, db_path: str | None = None) -> pd.DataFrame:
    sql = """
        SELECT s.id, s.sale_date AS 매출일, c.name AS 거래처, s.item AS 품목,
               s.qty AS 수량, s.unit_price AS 단가, s.amount AS 공급가액,
               COALESCE(s.vat_amount, 0) AS 부가세, COALESCE(s.total_amount, s.amount) AS 합계,
               COALESCE(s.paid_amount, 0) AS 입금액, COALESCE(s.tax_type, '과세') AS 과세구분,
               s.owner AS 담당자, s.status AS 수금상태, s.memo AS 메모,
               (SELECT COUNT(*) FROM sale_documents sd
                 WHERE sd.sale_id = s.id AND sd.voided_at IS NULL) AS 증빙,
               COALESCE(s.erp_status, '') AS "ERP",
               s.customer_id, d.title AS 관련기회, s.owner_id,
               COALESCE(e.code, '') AS 법인, COALESCE(s.currency, 'KRW') AS 통화
          FROM sales s
          JOIN customers c ON c.id = s.customer_id
          LEFT JOIN deals d ON d.id = s.deal_id
          LEFT JOIN entities e ON e.id = s.entity_id
         WHERE 1=1
    """
    params: list[Any] = []
    if ym_from:
        sql += " AND substr(s.sale_date, 1, 7) >= ?"
        params.append(ym_from)
    if ym_to:
        sql += " AND substr(s.sale_date, 1, 7) <= ?"
        params.append(ym_to)
    if owner_id:
        sql += " AND s.owner_id = ?"
        params.append(int(owner_id))
    if status:
        sql += " AND s.status = ?"
        params.append(status)
    if customer_id:
        sql += " AND s.customer_id = ?"
        params.append(int(customer_id))
    scope_sql, scope_params = _scope_clause("s")
    sql += scope_sql + " ORDER BY s.sale_date DESC, s.id DESC"
    return _df(sql, params + scope_params, db_path)


def get_sale(sale_id: int, db_path: str | None = None) -> Optional[dict]:
    return _one("SELECT * FROM sales WHERE id = ?", [sale_id], db_path)


SALE_FIELDS = ["customer_id", "deal_id", "sale_date", "item", "item_code", "qty", "unit_price",
               "amount", "owner", "owner_id", "status", "memo", "due_date", "paid_amount",
               "product_id", "quote_id", "tax_type", "vat_amount", "total_amount",
               "entity_id", "currency", "fx_rate", "foreign_amount"]


def vat_for(supply: int, tax_type: str) -> int:
    """부가세: 과세 10% (원 미만 절사), 영세·면세 0."""
    if tax_type not in VAT_RATE:
        raise ValueError(f"과세구분 값이 올바르지 않습니다: {tax_type}")
    return int(int(supply) * VAT_RATE[tax_type])


def status_for(paid: int, total: int) -> str:
    """수금상태는 입금액과 합계로 정한다 (손으로 고른 상태가 입금액과 어긋나지 않게)."""
    return "입금완료" if total > 0 and paid >= total else ("부분입금" if paid > 0 else "입금대기")


def _etax_active(sale_id: int, db_path: str | None = None) -> bool:
    """세금계산서가 나간 매출인지 — 금액·과세구분·일자 수정과 취소를 막는다.
    전자 발행(진행 중·완료), 진행 중인 수정세금계산서, 손으로 등록한 종이·전자 세금계산서 증빙(무효 처리 안 된 것)."""
    try:
        if _scalar("SELECT COUNT(*) FROM etax_invoices WHERE sale_id=? AND ("
                   "(modify_code IS NULL AND status IN ('발행요청','전송중','발행완료')) OR "
                   "(modify_code IS NOT NULL AND status IN ('발행요청','전송중')))", [int(sale_id)], db_path):
            return True
        return bool(_scalar("SELECT COUNT(*) FROM sale_documents WHERE sale_id=? AND voided_at IS NULL "
                            "AND doc_type IN ('전자세금계산서','세금계산서')", [int(sale_id)], db_path))
    except Exception:                                # noqa: BLE001 - 마이그레이션 전
        return False


def upsert_sale(data: dict, db_path: str | None = None) -> int:
    """매출 등록/수정. 신규 매출은 ERP 전송 대기열에 자동으로 올라간다."""
    if not data.get("customer_id"):
        raise ValueError("거래처를 선택하세요.")
    if not str(data.get("item", "")).strip():
        raise ValueError("품목은 필수입니다.")
    qty = int(data.get("qty") or 0)
    from . import entities as ent_mod
    currency = str(data.get("currency") or "KRW").upper()
    fx_rate, foreign_amount = 1.0, None
    if currency != "KRW":                 # 외화: 외화 단가 × 환율 = 원화 단가 (집계·채권·부가세는 원화)
        if data.get("foreign_unit_price") not in (None, ""):
            foreign_unit = Decimal(str(data["foreign_unit_price"]).replace(",", ""))
            foreign_total = foreign_unit * qty
            # 외화 합계 × 환율을 한 번만 반올림한다 (원화 단가를 먼저 반올림해 수량을 곱하면 수량만큼 오차가 커짐)
            amount_krw, fx_rate = ent_mod.to_krw(currency, foreign_total, data.get("fx_rate"), data.get("sale_date"))
            data = {**data, "unit_price": round(amount_krw / qty) if qty else amount_krw, "amount": amount_krw}
            foreign_amount = float(round(foreign_total, 2))
        else:
            _x, fx_rate = ent_mod.to_krw(currency, 1, data.get("fx_rate"), data.get("sale_date"))
    unit_price = int(data.get("unit_price") or 0)
    amount = int(data.get("amount") or qty * unit_price)
    if amount <= 0:
        raise ValueError("금액은 0보다 커야 합니다.")
    if currency != "KRW" and foreign_amount is None:
        foreign_amount = (float(data["foreign_amount"]) if data.get("foreign_amount") not in (None, "")
                          else round(amount / fx_rate, 2))
    entity_id = ent_mod.resolve(data.get("entity_id")) if "entity_id" in data or not data.get("id") else None
    status = data.get("status") or "입금대기"
    if status not in SALE_STATUS:
        raise ValueError(f"수금상태 값이 올바르지 않습니다: {status}")
    tax_type = data.get("tax_type") or "과세"
    vat = vat_for(amount, tax_type)
    total = amount + vat
    cust = get_customer(int(data["customer_id"]), db_path)
    check_record_scope(cust, "거래처")
    if cust.get("trade_blocked") and not data.get("id"):
        raise ValueError(f"'{cust['name']}' 은(는) 거래정지 거래처라 새 매출을 등록할 수 없습니다 (ERP·관리자 설정).")
    owner_id, owner_name = resolve_owner(data.get("owner_id") or data.get("owner"), db_path)

    sale_date = _d(data.get("sale_date")) or _d(TODAY())
    from . import periods
    periods.check(sale_date)
    due_date = _d(data.get("due_date"))
    if not due_date:                      # 거래처 결제조건(일)을 반영해 자동 계산
        terms = int(cust.get("payment_terms") or 30)
        due_date = _d(datetime.strptime(sale_date, "%Y-%m-%d").date() + timedelta(days=terms))

    record = {"customer_id": int(data["customer_id"]), "deal_id": data.get("deal_id"),
              "sale_date": sale_date, "item": str(data["item"]).strip(),
              "item_code": (str(data.get("item_code") or "").strip() or None),
              "qty": qty, "unit_price": unit_price, "amount": amount,
              "owner": owner_name, "owner_id": owner_id, "status": status,
              "memo": data.get("memo"), "due_date": due_date,
              "paid_amount": min(int(data.get("paid_amount") or (total if status == "입금완료" else 0)), total),
              "product_id": data.get("product_id"), "quote_id": data.get("quote_id"),
              "tax_type": tax_type, "vat_amount": vat, "total_amount": total,
              "entity_id": entity_id, "currency": currency, "fx_rate": fx_rate, "foreign_amount": foreign_amount}
    if not data.get("id"):
        record["status"] = status_for(record["paid_amount"], total)   # '부분입금'인데 입금 0 같은 어긋남 방지
    values = [record[f] for f in SALE_FIELDS]

    if data.get("id"):
        sid = int(data["id"])
        prev = get_sale(sid, db_path)
        check_record_scope(prev, "매출")
        if prev["status"] == SALE_CANCELLED:
            raise ValueError("취소된 매출은 수정할 수 없습니다.")
        if (prev.get("sale_kind") or "매출") != "매출":
            raise ValueError("반품·정정 매출은 고칠 수 없습니다. 원매출에서 반대 방향 정정으로 바로잡으세요.")
        periods.check(prev["sale_date"])
        if "entity_id" not in data:              # 수정 화면이 다루지 않는 값은 기존 값 유지
            record["entity_id"] = prev.get("entity_id")
        if "currency" not in data:
            record.update(currency=prev.get("currency") or "KRW", fx_rate=float(prev.get("fx_rate") or 1))
            record["foreign_amount"] = (round(amount / record["fx_rate"], 2) if record["currency"] != "KRW" else None)
        values = [record[f] for f in SALE_FIELDS]
        paid_before = int(prev.get("paid_amount") or 0)
        # 수정으로는 입금액을 바꾸지 않는다 (입금액 = 입금 내역 합계 — 넘어온 paid_amount 는 무시, 입금·반제 화면에서만)
        if paid_before > total:
            raise ValueError(f"이미 받은 금액({paid_before:,}원)보다 합계를 줄일 수 없습니다 — 반품·정정을 쓰세요.")
        mark_paid = status == "입금완료" and prev["status"] != "입금완료"   # 일부러 '입금완료'로 바꾼 경우만 남은 금액 입금
        record["paid_amount"] = total if mark_paid else paid_before
        record["status"] = status_for(int(record["paid_amount"]), total)
        values = [record[f] for f in SALE_FIELDS]
        money_changed = diff(prev, record, ["customer_id", "qty", "unit_price", "amount", "sale_date", "tax_type"])
        if money_changed and _etax_active(sid, db_path):
            raise ValueError("세금계산서가 발행(등록)된 매출은 금액·과세구분·일자를 바꿀 수 없습니다 — 반품·정정, 또는 매출 화면의 '수정세금계산서'(01 착오·04 계약 해제·05 내국신용장·06 이중발급)를 쓰세요. 손으로 올린 증빙이 잘못이면 증빙을 무효 처리하세요.")
        if money_changed and prev.get("erp_status") == "전송완료":
            raise ValueError("ERP로 전송된 매출은 금액·거래처·일자를 바꿀 수 없습니다. "
                             "매출을 취소한 뒤 다시 등록하세요(ERP에는 취소 전표가 전송됩니다).")
        sql = (f"UPDATE sales SET {', '.join(f'{f}=?' for f in SALE_FIELDS)}, "
               f"row_version=COALESCE(row_version,0)+1 WHERE id=? AND status <> ?")
        params: list = [*values, sid, SALE_CANCELLED]
        if money_changed:                     # 읽은 뒤 ERP 로 나갔다면 금액을 바꾸지 않는다
            sql += " AND COALESCE(erp_status,'') NOT IN ('전송완료','취소대기','취소완료')"
        if data.get("row_version") not in (None, ""):
            sql += " AND COALESCE(row_version,0)=?"
            params.append(int(data["row_version"]))
        with get_conn(db_path) as conn:
            if conn.execute(sql, params).rowcount == 0:
                raise ConflictError("그 사이 다른 사용자(또는 입금·ERP 처리)가 이 매출을 바꿨습니다. "
                                    "새로고침해서 최신 내용을 확인한 뒤 다시 저장하세요.")
        _sync_payment_rows(sid, int(prev.get("paid_amount") or 0), int(record["paid_amount"]), "수금상태변경", db_path)
        audit("수정", "매출", sid, {"변경": diff(prev, record, SALE_FIELDS)}, db_path)
        return sid
    with get_conn(db_path) as conn:
        cur = conn.execute(
            f"INSERT INTO sales ({', '.join(SALE_FIELDS)}, erp_status, created_by_id, created_at) "
            f"VALUES ({', '.join('?' * len(SALE_FIELDS))}, '대기', ?, ?)",
            (*values, current_actor_id(), _now()),
        )
        new_id = int(cur.lastrowid)
        conn.execute("INSERT INTO erp_outbox (doc_type, ref_id, status, created_at) "
                     "VALUES ('매출', ?, '대기', ?)", (new_id, _now()))
    _sync_payment_rows(new_id, 0, int(record["paid_amount"]), "등록시입금완료", db_path, sale_date)
    audit("등록", "매출", new_id, {"금액": amount, "품목": record["item"], "담당자": owner_name}, db_path)
    return new_id


def _sync_payment_rows(sale_id: int, before: int, after: int, source: str, db_path: str | None = None,
                       pay_date: str | None = None) -> None:
    """수금상태를 '입금완료'로 바꿔 입금액이 바뀐 경우에도 입금 내역 합계 = 입금액이 되도록 한 줄 남긴다."""
    if after == before:
        return
    with get_conn(db_path) as conn:
        conn.execute("INSERT INTO payments (sale_id, pay_date, amount, method, source, memo, created_by, created_by_id, "
                     "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                     (sale_id, pay_date or _d(TODAY()), after - before, "기타", source,
                      "수금상태 변경으로 맞춘 입금액", current_actor(), current_actor_id(), _now()))


def cancel_sale(sale_id: int, reason: str, db_path: str | None = None, actor: dict | None = None) -> None:
    """매출 취소. 기록은 지우지 않고 '취소' 상태로 남긴다(회계·감사 추적).

    ERP 로 이미 전송된 매출이면 취소 전표를 전송 대기열에 올린다.
    """
    if not str(reason or "").strip():
        raise ValueError("취소 사유를 입력하세요.")
    prev = get_sale(sale_id, db_path)
    check_record_scope(prev, "매출")
    if prev["status"] == SALE_CANCELLED:
        raise ValueError("이미 취소된 매출입니다.")
    from . import periods
    periods.check(prev["sale_date"])
    if actor is not None and prev.get("erp_status") in ("전송완료", "취소대기"):
        # 직무 분리: ERP 에 전기된 매출은 등록자 본인이 아닌 팀장 이상이 취소한다
        if ROLES.get(actor.get("role"), 0) < ROLES["MANAGER"]:
            raise PermissionError("ERP 로 전송된 매출은 팀장 이상만 취소할 수 있습니다.")
        if prev.get("created_by_id") and int(prev["created_by_id"]) == int(actor.get("id") or 0):
            raise PermissionError("본인이 등록한 매출은 본인이 취소할 수 없습니다(직무 분리). 다른 팀장에게 요청하세요.")
    if int(prev.get("paid_amount") or 0) > 0:
        raise ValueError("입금 내역이 있는 매출은 취소할 수 없습니다. 입금 반제를 먼저 처리하세요.")
    if _etax_active(sale_id, db_path):
        raise ValueError("세금계산서가 발행(등록)된 매출은 바로 취소할 수 없습니다 — 매출 화면에서 수정세금계산서 04(계약 해제)를 "
                         "발행한 뒤 취소하거나, 반품으로 처리하세요.")
    if (prev.get("sale_kind") or "매출") != "매출":
        raise ValueError("반품·정정 행은 취소하지 않습니다. 반대 방향 정정으로 바로잡으세요.")
    if _one("SELECT id FROM sales WHERE original_sale_id=? AND status <> ?", [sale_id, SALE_CANCELLED], db_path):
        raise ValueError("반품·정정이 있는 매출은 취소할 수 없습니다. 남은 수량을 반품으로 처리하세요.")
    with get_conn(db_path) as conn:
        if conn.execute("SELECT COUNT(*) FROM erp_outbox WHERE ref_id=? AND status='전송중'", (sale_id,)).fetchone()[0]:
            raise ValueError("지금 ERP 로 전송하는 중인 매출입니다. 잠시 뒤 다시 취소하세요.")
        erp_status = prev.get("erp_status")
        if erp_status == "전송완료":
            conn.execute("INSERT INTO erp_outbox (doc_type, ref_id, status, created_at) "
                         "VALUES ('매출취소', ?, '대기', ?)", (sale_id, _now()))
            erp_status = "취소대기"
        elif erp_status in ("대기", "실패"):   # 아직 ERP 에 없으므로 보낼 필요가 없다
            conn.execute("UPDATE erp_outbox SET status='취소' WHERE ref_id=? AND doc_type='매출' "
                         "AND status IN ('대기','실패')", (sale_id,))
            erp_status = None
        done = conn.execute("UPDATE sales SET status=?, cancelled_at=?, cancel_reason=?, erp_status=?, "
                            "row_version=COALESCE(row_version,0)+1 WHERE id=? AND status <> ? "
                            "AND COALESCE(paid_amount,0)=0 AND COALESCE(erp_status,'') = COALESCE(?,'')",
                            (SALE_CANCELLED, _now(), reason.strip(), erp_status, sale_id, SALE_CANCELLED,
                             prev.get("erp_status"))).rowcount
        if not done:
            raise ConflictError("그 사이 입금·ERP 전송 등으로 매출이 바뀌었습니다. 새로고침 후 다시 취소하세요.")
    if prev.get("order_item_id") and db_path is None:      # 수주에서 납품한 매출 → 그 수량은 다시 잔량
        from . import orders
        orders.release_cancelled_sale(prev)
    audit("취소", "매출", sale_id, {"사유": reason.strip(), "금액": prev["amount"],
                                    "ERP": prev.get("erp_status")}, db_path)


# ----------------------------------------------------------------------------
# 목표(targets)
# ----------------------------------------------------------------------------
def list_targets(yyyymm: str = "", db_path: str | None = None) -> pd.DataFrame:
    sql = ("SELECT id, yyyymm AS 월, owner AS 담당자, target_amount AS 목표금액, owner_id "
           "FROM targets WHERE 1=1")
    params: list[Any] = []
    if yyyymm:
        sql += " AND yyyymm = ?"
        params.append(yyyymm)
    scope_sql, scope_params = _scope_clause()
    sql += scope_sql + " ORDER BY yyyymm DESC, owner"
    return _df(sql, params + scope_params, db_path)


def upsert_target(yyyymm: str, owner: Any, amount: int, db_path: str | None = None) -> None:
    """월 목표 저장. owner 는 사용자 id(int)·사번·이름 중 하나."""
    if not yyyymm:
        raise ValueError("월은 필수입니다.")
    owner_id, owner_name = resolve_owner(owner, db_path)
    amount = int(amount or 0)
    if amount < 0:
        raise ValueError("목표금액은 0 이상이어야 합니다.")
    prev = _one("SELECT target_amount FROM targets WHERE yyyymm=? AND owner_key=?",
                [yyyymm, str(owner_id)], db_path)
    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO targets (yyyymm, owner, owner_id, owner_key, target_amount) VALUES (?,?,?,?,?) "
            "ON CONFLICT(yyyymm, owner_key) DO UPDATE SET target_amount=excluded.target_amount, "
            "owner=excluded.owner",
            (yyyymm, owner_name, owner_id, str(owner_id), amount),
        )
    audit("목표설정", "목표", None, {"월": yyyymm, "담당자": owner_name,
                                     "변경": [prev["target_amount"] if prev else None, amount]}, db_path)


def delete_target(target_id: int, db_path: str | None = None) -> None:
    prev = _one("SELECT * FROM targets WHERE id = ?", [target_id], db_path)
    check_record_scope(prev, "목표")
    with get_conn(db_path) as conn:
        conn.execute("DELETE FROM targets WHERE id = ?", (target_id,))
    audit("삭제", "목표", target_id, {"삭제 전": prev}, db_path)


# ----------------------------------------------------------------------------
# 분석 / KPI
# ----------------------------------------------------------------------------
def owner_choices(assignable: bool = False, db_path: str | None = None) -> list[dict]:
    """담당자 선택 목록 (로그인 사용자의 범위 안).

    assignable=True  → 새로 배정할 수 있는 활성 사용자만
    assignable=False → 조회 필터용. 퇴사(비활성) 사용자라도 기록을 가진 사람은 포함
    동명이인은 '이름 (사번)' 으로 구분해 표시한다.
    """
    sql = "SELECT id, name, emp_no, active FROM users"
    if assignable:
        sql += " WHERE active = 1"
    else:
        sql += (" WHERE active = 1 OR id IN (SELECT owner_id FROM customers UNION "
                "SELECT owner_id FROM deals UNION SELECT owner_id FROM sales)")
    df = _df(sql + " ORDER BY name, emp_no", (), db_path)
    scope = _SCOPE.get()
    rows = [r for r in df.to_dict("records") if scope is None or int(r["id"]) in scope]
    dup = {n for n in (r["name"] for r in rows) if sum(1 for x in rows if x["name"] == n) > 1}
    out = []
    for r in rows:
        label = f"{r['name']} ({r['emp_no']})" if r["name"] in dup else r["name"]
        if not r["active"]:
            label += " · 퇴사"
        out.append({"id": int(r["id"]), "name": r["name"], "emp_no": r["emp_no"], "label": label})
    return out


def owner_labels(db_path: str | None = None) -> dict[str, str]:
    """집계 키(사용자 id 문자열 또는 'n:이름') → 표시 이름."""
    df = _df("SELECT id, name, emp_no FROM users", (), db_path)
    names = df["name"].tolist()
    return {str(int(r.id)): (f"{r.name} ({r.emp_no})" if names.count(r.name) > 1 else r.name)
            for r in df.itertuples()}


OWNER_KEY = "COALESCE(CAST(owner_id AS TEXT), 'n:' || owner)"


def _owner_clause(owner_id: int | None, alias: str = "") -> tuple[str, list]:
    """담당자 필터 + 로그인 사용자의 접근 범위를 함께 적용한다."""
    p = f"{alias}." if alias else ""
    sql, params = (f" AND {p}owner_id = ?", [int(owner_id)]) if owner_id else ("", [])
    scope_sql, scope_params = _scope_clause(alias)
    return sql + scope_sql, params + scope_params


def kpi_summary(yyyymm: str, owner_id: int | None = None, db_path: str | None = None) -> dict:
    """기준월 기준 핵심 지표 묶음."""
    oc, op = _owner_clause(owner_id)
    prev = prev_month(yyyymm)
    today = _d(TODAY())

    month_sales = _scalar(
        f"SELECT SUM(amount) FROM sales WHERE {ACTIVE_SALE} AND substr(sale_date, 1, 7)=?{oc}",
        [yyyymm, *op], db_path)
    # 이번 달이면 전월도 같은 날짜까지만 더한다 (월초에 '-95% MoM'처럼 보이는 것을 막는다)
    partial = yyyymm == TODAY().strftime("%Y-%m")
    prev_cut = f"{prev}-{min(TODAY().day, calendar.monthrange(int(prev[:4]), int(prev[5:]))[1]):02d}"
    prev_sales = _scalar(
        f"SELECT SUM(amount) FROM sales WHERE {ACTIVE_SALE} AND substr(sale_date, 1, 7)=?"
        f"{' AND sale_date <= ?' if partial else ''}{oc}",
        [prev, *([prev_cut] if partial else []), *op], db_path)
    target = _scalar(
        f"SELECT SUM(target_amount) FROM targets WHERE yyyymm=?{oc}",
        [yyyymm, *op], db_path)
    unpaid = _scalar(
        f"SELECT SUM(COALESCE(total_amount, amount) - COALESCE(paid_amount,0)) FROM sales "
        f"WHERE status IN ('입금대기','부분입금'){oc}",
        [*op], db_path)
    open_amount = _scalar(
        f"SELECT SUM(amount) FROM deals WHERE stage NOT IN ('수주','실주'){oc}",
        [*op], db_path)
    weighted = _scalar(
        f"SELECT SUM(amount * probability / 100.0) FROM deals "
        f"WHERE stage NOT IN ('수주','실주'){oc}", [*op], db_path)
    won = _scalar(
        f"SELECT COUNT(*) FROM deals WHERE stage='수주' AND substr(closed_at, 1, 7)=?{oc}",
        [yyyymm, *op], db_path)
    lost = _scalar(
        f"SELECT COUNT(*) FROM deals WHERE stage='실주' AND substr(closed_at, 1, 7)=?{oc}",
        [yyyymm, *op], db_path)
    new_cust = _scalar(
        f"SELECT COUNT(*) FROM customers WHERE substr(created_at, 1, 7)=?{oc}",
        [yyyymm, *op], db_path)
    overdue = _scalar(
        f"SELECT COUNT(*) FROM deals WHERE stage NOT IN ('수주','실주') "
        f"AND expected_close IS NOT NULL AND expected_close < ?{oc}", [today, *op], db_path)
    act_cnt = _scalar(
        f"SELECT COUNT(*) FROM activities WHERE substr(act_date, 1, 7)=?{oc}",
        [yyyymm, *op], db_path)
    # 금액 카드용 (자재관리 대시보드와 같은 형식: 금액 · 건수 · 전월 같은 기간 대비)
    sales_cnt = _scalar(
        f"SELECT COUNT(*) FROM sales WHERE {ACTIVE_SALE} AND COALESCE(sale_kind,'매출')='매출' "
        f"AND substr(sale_date, 1, 7)=?{oc}", [yyyymm, *op], db_path)
    adj = _one(
        f"SELECT COUNT(*) AS n, COALESCE(SUM(amount),0) AS amt FROM sales WHERE {ACTIVE_SALE} "
        f"AND sale_kind IN ('반품','정정') AND substr(sale_date, 1, 7)=?{oc}", [yyyymm, *op], db_path) or {}
    # 입금: 고객이 실제로 낸 돈만 (반품상계·선수금 배분·대손 같은 내부 정리는 빼고, 반제(음수)는 포함)
    def pay_where(cut: bool) -> str:
        return (f"COALESCE(source,'') NOT IN ('반품상계','선수금','대손') AND substr(pay_date, 1, 7)=?"
                f"{' AND pay_date <= ?' if cut else ''} AND sale_id IN (SELECT id FROM sales WHERE 1=1{oc})")
    pay = _one(f"SELECT COUNT(*) AS n, COALESCE(SUM(amount),0) AS amt FROM payments WHERE {pay_where(False)}",
               [yyyymm, *op], db_path) or {}
    prev_pay = _scalar(f"SELECT COALESCE(SUM(amount),0) FROM payments WHERE {pay_where(partial)}",
                       [prev, *([prev_cut] if partial else []), *op], db_path)
    late = _one(
        f"SELECT COUNT(*) AS n, COALESCE(SUM(COALESCE(total_amount, amount) - COALESCE(paid_amount,0)),0) AS amt "
        f"FROM sales WHERE status IN ('입금대기','부분입금') AND due_date IS NOT NULL AND due_date < ?{oc}",
        [today, *op], db_path) or {}

    return {
        "month_sales": int(month_sales),
        "prev_sales": int(prev_sales),
        "mom": (month_sales - prev_sales) / prev_sales * 100 if prev_sales else 0.0,
        "mom_partial": partial,
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
        "sales_cnt": int(sales_cnt or 0),
        "adj_cnt": int(adj.get("n") or 0), "adj_amount": int(adj.get("amt") or 0),
        "receipts": int(pay.get("amt") or 0), "receipts_cnt": int(pay.get("n") or 0),
        "receipts_mom": ((pay.get("amt") or 0) - prev_pay) / prev_pay * 100 if prev_pay else None,
        "late_cnt": int(late.get("n") or 0), "late_amount": int(late.get("amt") or 0),
    }


def monthly_trend(months: int = 12, owner_id: int | None = None, db_path: str | None = None) -> pd.DataFrame:
    """최근 N개월 매출/목표 추이 (데이터 없는 달도 0으로 채움)."""
    end = date.today().replace(day=1)
    index = [(end - pd.DateOffset(months=i)).strftime("%Y-%m") for i in range(months - 1, -1, -1)]
    base = pd.DataFrame({"월": index})

    oc, op = _owner_clause(owner_id)
    sales = _df(
        f"SELECT substr(sale_date, 1, 7) AS 월, SUM(amount) AS 매출 FROM sales "
        f"WHERE {ACTIVE_SALE}{oc} GROUP BY 1", op, db_path)
    tg = _df(
        f"SELECT yyyymm AS 월, SUM(target_amount) AS 목표 FROM targets WHERE 1=1{oc} GROUP BY 1",
        op, db_path)

    out = base.merge(sales, on="월", how="left").merge(tg, on="월", how="left")
    out[["매출", "목표"]] = out[["매출", "목표"]].fillna(0).astype("int64")
    out["달성률"] = (out["매출"] / out["목표"].replace(0, pd.NA) * 100).fillna(0).round(1)
    return out


def stage_funnel(owner_id: int | None = None, db_path: str | None = None) -> pd.DataFrame:
    oc, op = _owner_clause(owner_id)
    df = _df(
        f"SELECT stage AS 단계, COUNT(*) AS 건수, SUM(amount) AS 금액, "
        f"SUM(amount*probability/100.0) AS 가중금액 FROM deals "
        f"WHERE stage NOT IN ('수주','실주'){oc} GROUP BY stage", op, db_path)
    base = pd.DataFrame({"단계": OPEN_STAGES})
    out = base.merge(df, on="단계", how="left").fillna(0)
    out[["건수", "금액", "가중금액"]] = out[["건수", "금액", "가중금액"]].astype("int64")
    return out


def owner_performance(yyyymm: str, db_path: str | None = None) -> pd.DataFrame:
    """담당자별 목표/실적/달성률/파이프라인 (담당자 id 기준 집계, 동명이인 분리)."""
    sc, sp = _scope_clause()
    sales = _df(f"SELECT {OWNER_KEY} AS k, MAX(owner) AS n, SUM(amount) AS 매출 FROM sales "
                f"WHERE {ACTIVE_SALE} AND substr(sale_date, 1, 7)=?{sc} GROUP BY 1",
                [yyyymm, *sp], db_path)
    tg = _df(f"SELECT {OWNER_KEY} AS k, MAX(owner) AS n, SUM(target_amount) AS 목표 FROM targets "
             f"WHERE yyyymm=?{sc} GROUP BY 1", [yyyymm, *sp], db_path)
    pipe = _df(f"SELECT {OWNER_KEY} AS k, MAX(owner) AS n, SUM(amount) AS 파이프라인, "
               f"COUNT(*) AS 진행건수 FROM deals WHERE stage NOT IN ('수주','실주'){sc} GROUP BY 1",
               sp, db_path)
    labels = owner_labels(db_path)
    names: dict[str, str] = {}
    for part in (sales, tg, pipe):
        for r in part.itertuples():
            names.setdefault(str(r.k), labels.get(str(r.k), f"{r.n} (미연결)"))
    out = pd.DataFrame({"k": pd.Series(list(names), dtype=object), "담당자": list(names.values())})
    for part, cols in ((sales, ["매출"]), (tg, ["목표"]), (pipe, ["파이프라인", "진행건수"])):
        if part.empty:
            for col in cols:
                out[col] = 0
            continue
        part = part.assign(k=part["k"].astype(str))
        out = out.merge(part[["k", *cols]], on="k", how="left")
    out[["매출", "목표", "파이프라인", "진행건수"]] = (
        out[["매출", "목표", "파이프라인", "진행건수"]].fillna(0).astype("int64"))
    out["달성률"] = (out["매출"] / out["목표"].replace(0, pd.NA) * 100).fillna(0).round(1)
    out["owner_id"] = out["k"].map(lambda k: int(k) if str(k).isdigit() else None)
    return out.drop(columns=["k"]).sort_values("매출", ascending=False).reset_index(drop=True)


def top_customers(yyyymm: str = "", limit: int = 10, db_path: str | None = None) -> pd.DataFrame:
    sql = ("SELECT c.name AS 거래처, SUM(s.amount) AS 매출, COUNT(*) AS 건수 "
           "FROM sales s JOIN customers c ON c.id = s.customer_id WHERE s.status <> '취소'")
    params: list[Any] = []
    if yyyymm:
        sql += " AND substr(s.sale_date, 1, 7)=?"
        params.append(yyyymm)
    scope_sql, scope_params = _scope_clause("s")
    sql += scope_sql + " GROUP BY c.id ORDER BY 매출 DESC LIMIT ?"
    params += scope_params
    params.append(int(limit))
    return _df(sql, params, db_path)


def deals_closing_soon(days: int = 14, owner_id: int | None = None, db_path: str | None = None) -> pd.DataFrame:
    oc, op = _owner_clause(owner_id, "d")
    return _df(
        f"SELECT d.expected_close AS 예상마감일, c.name AS 거래처, d.title AS 기회명, "
        f"d.stage AS 단계, d.amount AS 예상금액, d.owner AS 담당자 "
        f"FROM deals d JOIN customers c ON c.id = d.customer_id "
        f"WHERE d.stage NOT IN ('수주','실주') AND d.expected_close IS NOT NULL "
        f"AND d.expected_close <= ?{oc} ORDER BY d.expected_close",
        [_d(TODAY() + timedelta(days=days)), *op], db_path)


def stale_customers(days: int = 30, owner_id: int | None = None, db_path: str | None = None) -> pd.DataFrame:
    oc, op = _owner_clause(owner_id, "c")
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
def backup_database(folder: str | os.PathLike, keep: int | None = None, db_path: str | None = None) -> str:
    """DB 백업. keep 을 주면 최근 keep 개만, 주지 않으면 회사 설정의 세대 관리(일·월말·연말)를 따른다.

    SQLite     : 온라인 백업 API — 서비스 중에도 일관된 스냅샷
    PostgreSQL : pg_dump 사용자 지정 형식(.dump) — pg_restore 로 복구 (SALES_PG_DUMP 로 실행 파일 지정)
    배치: python manage.py backup
    """
    import sqlite3
    os.makedirs(folder, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    if database.is_pg() and not db_path:
        target = os.path.join(folder, f"sales_{stamp}.dump")
        exe = os.environ.get("SALES_PG_DUMP") or shutil.which("pg_dump") or "pg_dump"
        result = subprocess.run([exe, "--format=custom", "--no-owner", f"--file={target}",
                                 f"--dbname={database.DATABASE_URL}"],
                                capture_output=True, text=True, timeout=3600)
        if result.returncode != 0:
            raise RuntimeError(f"pg_dump 실패: {result.stderr.strip()[:500]}")
        # 검증: pg_restore --list 로 읽히는 파일인지 (깨진 백업은 남기지 않는다)
        restore = os.environ.get("SALES_PG_RESTORE") or shutil.which("pg_restore")
        if restore:
            check = subprocess.run([restore, "--list", target], capture_output=True, text=True, timeout=600)
            if check.returncode != 0:
                os.remove(target)
                raise RuntimeError(f"백업 검증 실패(pg_restore --list): {check.stderr.strip()[:300]}")
        pattern = (".dump",)
    else:
        target = os.path.join(folder, f"sales_{stamp}.db")
        src = sqlite3.connect(db_path or database.DB_PATH)
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
            # 검증: 복사본을 열어 무결성 검사 — 통과하지 못한 백업은 지운다 (복원해 보기 전엔 백업이 아니다)
            ok = dst.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            dst.close()
            src.close()
        if ok != "ok":
            os.remove(target)
            raise RuntimeError(f"백업 검증 실패(quick_check): {ok}")
        pattern = (".db",)
    if keep is not None:
        olds = sorted(f for f in os.listdir(folder) if f.startswith("sales_") and f.endswith(pattern))
        for name in olds[:-keep] if keep else []:
            os.remove(os.path.join(folder, name))
    else:
        from .retention import prune_backups
        prune_backups(folder, pattern)
    same = database.same_disk(folder)
    audit("DB백업", "시스템", None, {"파일": os.path.basename(target), "검증": "통과",
                                    **({"경고": "DB 와 같은 디스크 — 디스크가 고장 나면 함께 잃습니다"} if same else {})}, db_path)
    return target


def reset_db(db_path: str | None = None) -> None:
    """업무 데이터 전체 삭제 (개발·시연용). 감사로그는 지우지 않는다."""
    with get_conn(db_path) as conn:
        for table in ("sale_documents", "erp_outbox", "activities", "sales", "deal_stage_history",
                      "approvals", "pipeline_snapshots", "deals", "targets", "customers",
                      "users", "orgs"):
            conn.execute(f"DELETE FROM {table}")
        if not conn.pg:
            conn.execute("DELETE FROM sqlite_sequence WHERE name <> 'audit_log'")
    audit("전체초기화", "시스템", None, "업무 데이터 전체 삭제 (감사로그 보존)", db_path)


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
    owner_ids = {}
    for name in sales_owners:
        try:
            owner_ids[name] = resolve_owner(name, db_path)[0]
        except ValueError as exc:
            raise ValueError("샘플 데이터의 담당자 계정이 없습니다. "
                             "'샘플 조직·계정 생성'을 먼저 실행하세요.") from exc
    first_sale_id = int(_scalar("SELECT COALESCE(MAX(id), 0) FROM sales", (), db_path))
    cust_ids: list[int] = []

    for i in range(24):
        owner = rng.choice(sales_owners)
        name = f"{rng.choice(prefix)}{rng.choice(suffix)}"
        cid = upsert_customer({
            "name": f"{name}{i+1:02d}", "biz_no": _with_check_digit(f"{rng.randint(100,999)}-{rng.randint(10,99)}-{rng.randint(10000,99999)}"),
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

    created["products"] = _seed_fixups(first_sale_id, seed, today, db_path)

    # 샘플 매출은 ERP 로 보낼 대상이 아니므로 이번에 만든 건만 전송 대기열에서 뺀다
    with get_conn(db_path) as conn:
        conn.execute("DELETE FROM erp_outbox WHERE doc_type='매출' AND ref_id > ?", (first_sale_id,))
        conn.execute("UPDATE sales SET erp_status=NULL WHERE id > ?", (first_sale_id,))

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
                        "WHERE substr(sale_date, 1, 7)=? AND owner_id=?", (ym, owner_ids[owner])
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
                            "INSERT INTO pipeline_snapshots (snap_date, yyyymm, owner, owner_id, "
                            "owner_key, category, deal_cnt, amount, weighted) VALUES (?,?,?,?,?,?,?,?,?) "
                            "ON CONFLICT(snap_date, yyyymm, owner_key, category) DO UPDATE SET "
                            "amount=excluded.amount",
                            (snap, ym, owner, owner_ids[owner], str(owner_ids[owner]), cat,
                             rng.randint(1, 6), amt, int(amt * 0.6)))
                        created["snapshots"] += 1
    return created


# 샘플 매출 품목명 → 품목 코드 (catalog.seed_products, 모두 과세라 매출의 부가세와 맞는다)
SEED_ITEM_PRODUCT = {"ERP 라이선스": "SW-ERP-01", "유지보수 계약": "SV-MNT-01", "설비 공급": "HW-SRV-01",
                     "클라우드 구독": "SW-CLD-01", "컨설팅": "SV-CON-01", "부품 납품": "HW-PRT-01",
                     "교육 서비스": "SV-TRN-01", "커스터마이징 개발": "SV-DEV-01"}


def _seed_fixups(first_sale_id: int, seed: int, today: date, db_path: str | None) -> int:
    """샘플 데이터가 실제 업무와 어긋나지 않게 보정한다 (기존 난수 순서는 건드리지 않도록 따로 돌린다).
    - '부분입금' 매출에 실제 입금액(합계의 20~80%)을 넣는다 (상태만 부분입금이고 입금액 0이던 문제)
    - 매출을 품목 마스터에 연결한다 (품목군별 매출이 '품목 미지정'만 나오던 문제) — 기본 DB 에만
    - 매출이 하나도 없는 '수주' 기회에 매출을 만든다 (수주했는데 매출이 없던 문제)
    돌려주는 값: 새로 만든 품목 수."""
    rng = random.Random(seed + 1000)
    products = 0
    with get_conn(db_path) as conn:
        partial = conn.execute("SELECT id, COALESCE(total_amount, amount) AS total FROM sales WHERE id > ? "
                               "AND status = '부분입금' AND COALESCE(paid_amount, 0) = 0", (first_sale_id,)).fetchall()
        for r in partial:
            paid = int(round(int(r["total"]) * rng.uniform(0.2, 0.8), -4))
            conn.execute("UPDATE sales SET paid_amount = ? WHERE id = ?", (paid, int(r["id"])))
            # 입금 내역 합계 = 입금액 (payments 가 원장, paid_amount 는 합계를 담아 둔 값)
            conn.execute("INSERT INTO payments (sale_id, pay_date, amount, method, source, memo, created_by, created_at) "
                         "SELECT id, COALESCE(due_date, sale_date), ?, '계좌이체', '샘플', '샘플 데이터 부분입금', 'system', ? "
                         "FROM sales WHERE id = ?", (paid, _now(), int(r["id"])))
    if db_path is None:
        from . import catalog
        products = catalog.seed_products()
        with get_conn(db_path) as conn:
            ids = {r["code"]: int(r["id"]) for r in conn.execute("SELECT id, code FROM products").fetchall()}
            for item, code in SEED_ITEM_PRODUCT.items():
                if code in ids:
                    conn.execute("UPDATE sales SET product_id = ? WHERE id > ? AND item = ? AND product_id IS NULL",
                                 (ids[code], first_sale_id, item))
    # 수주 기회 금액 = 연결된 매출 합계 (샘플 매출을 기회에 무작위로 붙여 금액이 크게 어긋나던 문제). 정가는 할인율로 역산
    with get_conn(db_path) as conn:
        won = conn.execute("SELECT d.id, d.discount_rate, SUM(s.amount) AS total FROM deals d "
                           "JOIN sales s ON s.deal_id = d.id AND s.status <> '취소' "
                           "WHERE d.stage = ? AND d.memo = '샘플 기회' GROUP BY d.id, d.discount_rate",
                           (STAGE_WON,)).fetchall()
        for r in won:
            amount = int(r["total"])
            list_amount = int(round(amount * 100 / (100 - float(r["discount_rate"] or 0))))
            conn.execute("UPDATE deals SET amount = ?, list_amount = ? WHERE id = ?", (amount, list_amount, int(r["id"])))
    orphans = _df("SELECT d.id, d.customer_id, d.owner, d.amount, d.title, d.closed_at FROM deals d "
                  f"WHERE d.stage = '{STAGE_WON}' AND NOT EXISTS (SELECT 1 FROM sales s WHERE s.deal_id = d.id)",
                  (), db_path)
    for r in orphans.itertuples():
        sale_date = _d(r.closed_at) or _d(today)
        upsert_sale({"customer_id": int(r.customer_id), "deal_id": int(r.id), "sale_date": sale_date,
                     "item": str(r.title).replace(" 건", ""), "qty": 1, "unit_price": int(r.amount),
                     "amount": int(r.amount), "owner": r.owner, "status": SALE_STATUS[0], "memo": "수주 기회 매출"},
                    db_path)
    return products


if __name__ == "__main__":  # 간단한 CLI: python -m core.sales_db --seed
    import sys

    init_db()
    if "--seed" in sys.argv:
        print("샘플 데이터 생성:", seed_demo_data())
    print("DB 준비 완료:", database.DATABASE_URL or database.DB_PATH)
