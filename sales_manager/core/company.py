"""회사별 설정 — 같은 프로그램을 여러 회사가 각자 설치해 쓸 때 회사마다 다른 값

  회사 정보(상호·사업자번호·대표·주소·시스템 이름), 할인 결재 구간, 결재 단계 기한, 견적 유효기간,
  기본 결제조건, 영업 단계별 확률, 코드 목록(업종·유입경로·실주사유·활동유형), 개인정보 보관기간.

  값은 DB(company_settings)에 두고 관리자 화면에서 바꾼다. 서버가 여러 대여도 각 서버가
  CACHE_SECONDS 마다 다시 읽어 맞춘다. 바꾼 내용은 바꾸기 전·후 값과 함께 감사로그에 남는다.
  기존 데이터에 쓰인 코드는 지울 수 없다(지우면 그 데이터를 다시 저장할 때 값이 바뀌어 버린다).

  업무 코드(sales_db.INDUSTRIES 등)는 같은 리스트·딕셔너리 객체를 제자리에서 바꾼다 —
  화면·업로드 검증·API 가 따로 고칠 필요 없이 새 값을 본다.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime
from typing import Any

from . import sales_db as db

CACHE_SECONDS = 15

DEFAULTS: dict[str, Any] = {
    "company_name": os.environ.get("SALES_COMPANY_NAME", ""),
    "company_biz_no": os.environ.get("SALES_COMPANY_BIZ_NO", ""),
    "company_ceo": os.environ.get("SALES_COMPANY_CEO", ""),
    "company_address": os.environ.get("SALES_COMPANY_ADDRESS", ""),
    "app_title": "영업관리 시스템",
    "discount_manager_max": 10.0,          # 이 할인율까지 팀장 결재
    "discount_exec_max": 20.0,             # 이 할인율까지 팀장 → 임원, 넘으면 → 관리자
    "approval_sla_hours": int(os.environ.get("SALES_APPROVAL_SLA_HOURS", "48")),
    "quote_valid_days": 30,
    "default_payment_terms": 30,
    "stage_names": list(db.DEFAULT_OPEN_STAGES),     # 진행 단계 이름 (수주·실주는 고정)
    "stage_prob": {"리드": 10, "접촉": 25, "제안": 45, "견적": 60, "협상": 80},
    "industries": list(db.INDUSTRIES),
    "lead_sources": list(db.LEAD_SOURCES),
    "lost_reasons": list(db.LOST_REASONS),
    "act_types": list(db.ACT_TYPES),
    "pii_retention_years": 0,              # 종료 거래처의 고객 연락처 보관 연수 (0 = 자동 파기 안 함)
    "audit_retention_years": 0,            # 감사로그 DB 보관 연수 (0 = 계속 보관), 지나면 파일로 이관
    "backup_keep_daily": 30, "backup_keep_monthly": 12, "backup_keep_yearly": 7,
    "fiscal_start_month": 1,               # 회계연도 시작 월 (4 = 4월~다음 해 3월)
    "writeoff_exec_threshold": 10_000_000, # 대손 금액이 이보다 크면 임원 결재 (이하 팀장)
    "auto_block_overdue_days": 0,          # 결제기일이 N일 넘게 지난 미수가 있으면 자동 거래정지 (0 = 끔)
    "auto_block_over_credit": False,       # 미수가 여신한도를 넘으면 자동 거래정지
    "auto_block_exempt_days": 30,          # 해제 결재 뒤 다시 자동 정지하지 않는 기간
    "status_labels": {},                   # 상태 화면 이름 {원래 이름: 회사에서 부르는 이름} — DB 값은 그대로 (자재관리 names.py)
    "holidays": [],                        # 공휴일·대체공휴일 (YYYY-MM-DD) — 세금계산서 발급 기한이 이 날이면 다음 영업일로
    # 운영 상태 (화면·명령으로 켜고 끔 — set_state, 서버 여러 대가 15초 안에 함께)
    "maintenance": {"on": False},          # 점검(읽기 전용) 모드 {on, reason, by, at}
    "sso_outage_until": "",                # SSO 장애 모드 끝나는 시각 (그때까지 비밀번호 계정 로그인 허용)
}

# 코드 목록 → (sales_db 의 리스트, 쓰이는 테이블·컬럼)
CODE_LISTS = {
    "industries": ("INDUSTRIES", "customers", "industry", "업종"),
    "lead_sources": ("LEAD_SOURCES", "deals", "source", "유입경로"),
    "lost_reasons": ("LOST_REASONS", "deals", "lost_reason", "실주사유"),
    "act_types": ("ACT_TYPES", "activities", "act_type", "활동유형"),
}
LABELS = {
    "company_name": "회사명", "company_biz_no": "사업자번호", "company_ceo": "대표자", "company_address": "주소",
    "app_title": "시스템 이름", "discount_manager_max": "팀장 결재 한도(%)", "discount_exec_max": "임원 결재 한도(%)",
    "approval_sla_hours": "결재 단계 기한(시간)", "quote_valid_days": "견적 유효기간(일)",
    "default_payment_terms": "기본 결제조건(일)", "stage_prob": "단계별 확률(%)", "stage_names": "진행 단계 이름",
    "maintenance": "점검(읽기 전용) 모드", "sso_outage_until": "SSO 장애 모드",
    "pii_retention_years": "개인정보 보관기간(년)",
    "audit_retention_years": "감사로그 보관(년)", "backup_keep_daily": "일 백업 보관(개)",
    "backup_keep_monthly": "월말 백업 보관(개월)", "backup_keep_yearly": "연말 백업 보관(년)",
    "fiscal_start_month": "회계연도 시작 월", "writeoff_exec_threshold": "대손 임원결재 기준(원)",
    "auto_block_overdue_days": "자동 거래정지 연체일", "auto_block_over_credit": "여신 초과 자동 거래정지",
    "auto_block_exempt_days": "해제 후 재정지 유예(일)", "holidays": "공휴일", "status_labels": "상태 표시 이름", **{k: v[3] for k, v in CODE_LISTS.items()},
}

_lock = threading.Lock()
_state = {"values": dict(DEFAULTS), "loaded_at": 0.0}


def _read_db() -> dict:
    try:
        rows = db._df("SELECT key, value FROM company_settings").to_dict("records")
    except Exception:   # noqa: BLE001 - 마이그레이션 전(테이블 없음)에는 기본값
        return {}
    out = {}
    for r in rows:
        if r["key"] in DEFAULTS:
            try:
                out[r["key"]] = json.loads(r["value"])
            except json.JSONDecodeError:
                continue
    return out


def _apply(values: dict) -> None:
    """업무 코드·정책을 제자리에서 바꾼다 (같은 객체를 참조하는 화면·검증이 그대로 새 값을 본다)."""
    for key, (attr, *_rest) in CODE_LISTS.items():
        target = getattr(db, attr)
        if target != values[key]:
            target[:] = list(values[key])
    names = values.get("stage_names") or db.DEFAULT_OPEN_STAGES
    if len(names) == len(db.OPEN_STAGES) and list(names) != db.OPEN_STAGES:
        db.apply_stage_names(list(names))
    probs = values["stage_prob"]
    if not any(s in probs for s in db.OPEN_STAGES):          # 예전 이름으로 저장된 확률 → 위치대로
        probs = dict(zip(db.OPEN_STAGES, list(probs.values())))
    for stage, prob in probs.items():
        if stage in db.STAGE_PROB:
            db.STAGE_PROB[stage] = int(prob)
    db.DISCOUNT_POLICY[:] = [(0.0, None), (float(values["discount_manager_max"]), "MANAGER"),
                             (float(values["discount_exec_max"]), "EXEC"), (100.0, "ADMIN")]


def refresh(force: bool = False, max_age: float | None = None) -> dict:
    """CACHE_SECONDS(또는 max_age)가 지났거나 force 면 DB 에서 다시 읽는다. 요청 시작·작업 시작 때 부른다."""
    if not force and time.monotonic() - _state["loaded_at"] < (CACHE_SECONDS if max_age is None else max_age):
        return _state["values"]
    with _lock:
        values = {**DEFAULTS, **_read_db()}
        _apply(values)
        _state.update(values=values, loaded_at=time.monotonic())
    return values


# 표시 이름을 바꿀 수 있는 상태 (DB 에는 원래 이름이 그대로 남는다)
STATUS_CODES = ["입금대기", "부분입금", "입금완료", "취소", "작성중", "발송", "수락", "거절", "만료", "대체됨",
                "진행", "완료", "대기", "승인", "반려", "회수"]


def label(value: Any) -> Any:
    """화면에 보일 상태 이름."""
    labels = get("status_labels") or {}
    return labels.get(value, value) if isinstance(value, str) else value


def get(key: str) -> Any:
    return refresh()[key]


def all_values() -> dict:
    return dict(refresh())


# ---------------------------------------------------------------------------
# 저장
# ---------------------------------------------------------------------------
def _codes(raw: Any, label: str) -> list[str]:
    items = raw if isinstance(raw, list) else re.split(r"[\n,]", str(raw or ""))
    out: list[str] = []
    for item in (str(i).strip() for i in items):
        if item and item not in out:
            if len(item) > 30:
                raise ValueError(f"{label}: '{item[:30]}…' 은(는) 30자를 넘습니다.")
            out.append(item)
    if not out:
        raise ValueError(f"{label}: 한 개 이상 있어야 합니다.")
    if "기타" not in out:
        out.append("기타")                      # 업로드에서 비어 있는 값의 기본값
    return out


def _number(raw: Any, label: str, lo: float, hi: float, integer: bool = True):
    try:
        value = float(str(raw).replace(",", "").replace("%", ""))
    except ValueError as exc:
        raise ValueError(f"{label}: 숫자를 입력하세요.") from exc
    if not lo <= value <= hi:
        raise ValueError(f"{label}: {lo:g} ~ {hi:g} 사이여야 합니다.")
    return int(value) if integer else round(value, 2)


def validate(changes: dict) -> dict:
    """화면 입력 → 저장할 값. 잘못된 값이면 ValueError."""
    cur = all_values()
    out: dict[str, Any] = {}
    for key in ("company_name", "company_ceo", "company_address", "app_title"):
        if key in changes:
            out[key] = str(changes[key] or "").strip()[:200]
    if "app_title" in out and not out["app_title"]:
        raise ValueError("시스템 이름은 비울 수 없습니다.")
    if "company_biz_no" in changes:
        digits = re.sub(r"\D", "", str(changes["company_biz_no"] or ""))
        if digits:
            from .documents import valid_biz_no
            if not valid_biz_no(digits):
                raise ValueError("사업자번호가 올바르지 않습니다 (검증번호 불일치).")
        out["company_biz_no"] = digits
    if "discount_manager_max" in changes or "discount_exec_max" in changes:
        m = _number(changes.get("discount_manager_max", cur["discount_manager_max"]), "팀장 결재 한도", 0.1, 99, False)
        e = _number(changes.get("discount_exec_max", cur["discount_exec_max"]), "임원 결재 한도", 0.1, 100, False)
        if e <= m:
            raise ValueError("임원 결재 한도는 팀장 결재 한도보다 커야 합니다.")
        out.update(discount_manager_max=m, discount_exec_max=e)
    for key, lo, hi in (("approval_sla_hours", 1, 720), ("quote_valid_days", 1, 365),
                        ("default_payment_terms", 0, 365), ("pii_retention_years", 0, 20),
                        ("audit_retention_years", 0, 30), ("backup_keep_daily", 1, 365),
                        ("backup_keep_monthly", 0, 120), ("backup_keep_yearly", 0, 50),
                        ("fiscal_start_month", 1, 12), ("writeoff_exec_threshold", 0, 10**12),
                        ("auto_block_overdue_days", 0, 3650), ("auto_block_exempt_days", 0, 365)):
        if key in changes:
            out[key] = _number(changes[key], LABELS[key], lo, hi)
    if "status_labels" in changes:
        raw = changes["status_labels"]
        pairs = raw.items() if isinstance(raw, dict) else (
            line.split("=", 1) for line in str(raw or "").splitlines() if line.strip())
        labels: dict[str, str] = {}
        for item in pairs:
            if len(item) != 2:
                raise ValueError("상태 표시 이름은 한 줄에 '원래 이름=표시 이름' 으로 적으세요.")
            src, dst = str(item[0]).strip(), str(item[1]).strip()
            if src not in STATUS_CODES:
                raise ValueError(f"'{src}' 은(는) 바꿀 수 있는 상태가 아닙니다 ({', '.join(STATUS_CODES)}).")
            if not dst or len(dst) > 20 or any(c in dst for c in "<>\"'"):
                raise ValueError(f"'{src}' 의 표시 이름은 20자 이내로 적으세요.")
            if dst != src:
                labels[src] = dst
        out["status_labels"] = labels
    if "holidays" in changes:
        days = set()
        for part in re.split(r"[\s,]+", str(changes["holidays"] or "")):
            if not part:
                continue
            try:
                days.add(datetime.strptime(part.replace(".", "-").replace("/", "-"), "%Y-%m-%d").date().isoformat())
            except ValueError:
                raise ValueError(f"공휴일 '{part}' 은(는) 날짜(YYYY-MM-DD)가 아닙니다.") from None
        out["holidays"] = sorted(days)
    if "auto_block_over_credit" in changes:
        out["auto_block_over_credit"] = changes["auto_block_over_credit"] in (True, 1, "1", "on", "true")
    names = list(cur["stage_names"])
    if "stage_names" in changes:
        names = [str(n or "").strip() for n in changes["stage_names"]]
        if len(names) != len(db.OPEN_STAGES) or not all(names):
            raise ValueError(f"진행 단계 이름 {len(db.OPEN_STAGES)}개를 모두 입력하세요.")
        if len(set(names)) != len(names):
            raise ValueError("진행 단계 이름이 겹칩니다.")
        for n in names:
            if n in (db.STAGE_WON, db.STAGE_LOST) or len(n) > 20 or any(c in n for c in ",'\"<>"):
                raise ValueError(f"단계 이름 '{n}' 은(는) 쓸 수 없습니다 (수주·실주는 고정, 20자 이내, 쉼표·따옴표 불가).")
        out["stage_names"] = names
    if "stage_prob" in changes or "stage_names" in changes:
        old_names = list(cur["stage_names"])
        cur_probs = {n: cur["stage_prob"].get(o, db.STAGE_PROB.get(o, 0)) for o, n in zip(old_names, names)}
        given = changes.get("stage_prob") or {}
        probs = {n: _number(given.get(n, cur_probs[n]), f"{n} 확률", 0, 99) for n in names}
        seq = [probs[n] for n in names]
        if seq != sorted(seq):
            raise ValueError(f"단계별 확률은 {names[0]} → {names[-1]} 순서로 같거나 커져야 합니다.")
        out["stage_prob"] = probs
    for key, (_attr, table, column, label) in CODE_LISTS.items():
        if key in changes:
            new = _codes(changes[key], label)
            removed = [c for c in cur[key] if c not in new]
            for code in removed:
                used = int(db._scalar(f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", [code]))
                if used:
                    raise ValueError(f"{label} '{code}' 은(는) {used:,}건에 쓰이고 있어 지울 수 없습니다. "
                                     f"이름을 바꾸려면 새 값을 추가하고 데이터를 옮긴 뒤 지우세요.")
            out[key] = new
    return out


def save(changes: dict, actor: str | None = None) -> dict:
    """바뀐 값만 저장하고 {키: [전, 후]} 를 돌려준다."""
    values = validate(changes)
    cur = all_values()
    diff = {k: [cur[k], v] for k, v in values.items() if cur.get(k) != v}
    if not diff:
        return {}
    now = db._now()
    with db.get_conn() as conn:
        if "stage_names" in diff:                       # 데이터에 쓰인 단계 이름도 같은 트랜잭션에서 바꾼다
            renames = [(o, n) for o, n in zip(diff["stage_names"][0], diff["stage_names"][1]) if o != n]
            for i, (o, _n) in enumerate(renames):        # 이름을 서로 바꾸는 경우를 위해 임시 이름을 거친다
                for table, col in (("deals", "stage"), ("deal_stage_history", "from_stage"),
                                   ("deal_stage_history", "to_stage")):
                    conn.execute(f"UPDATE {table} SET {col} = ? WHERE {col} = ?", (f"__stage_{i}", o))
            for i, (_o, n) in enumerate(renames):
                for table, col in (("deals", "stage"), ("deal_stage_history", "from_stage"),
                                   ("deal_stage_history", "to_stage")):
                    conn.execute(f"UPDATE {table} SET {col} = ? WHERE {col} = ?", (n, f"__stage_{i}"))
        for key, (_old, new) in diff.items():
            text = json.dumps(new, ensure_ascii=False)
            if conn.execute("UPDATE company_settings SET value=?, updated_by=?, updated_at=? WHERE key=?",
                            (text, actor or db.current_actor(), now, key)).rowcount == 0:
                conn.execute("INSERT INTO company_settings (key, value, updated_by, updated_at) VALUES (?,?,?,?)",
                             (key, text, actor or db.current_actor(), now))
    db.audit("설정변경", "시스템", None, {LABELS.get(k, k): v for k, v in diff.items()})
    refresh(force=True)
    return diff


def set_state(key: str, value: Any, actor: str, detail: dict | None = None) -> None:
    """운영 상태 값(maintenance · sso_outage_until) 저장 — 화면 검증 없이, 감사로그를 남긴다."""
    if key not in ("maintenance", "sso_outage_until"):
        raise ValueError(f"상태 값이 아닙니다: {key}")
    text, now = json.dumps(value, ensure_ascii=False), db._now()
    with db.get_conn() as conn:
        if conn.execute("UPDATE company_settings SET value=?, updated_by=?, updated_at=? WHERE key=?",
                        (text, actor, now, key)).rowcount == 0:
            conn.execute("INSERT INTO company_settings (key, value, updated_by, updated_at) VALUES (?,?,?,?)",
                         (key, text, actor, now))
    db.audit("운영상태변경", "시스템", None, {"항목": LABELS.get(key, key), "값": value, **(detail or {})})
    refresh(force=True)


# ---------------------------------------------------------------------------
# 개인정보 보관기간 — 종료 거래처의 고객 연락처 파기 (개인정보 보호법: 목적 달성 후 지체 없이 파기)
# ---------------------------------------------------------------------------
PII_COLUMNS = ("manager", "phone", "email")


def purge_pii(dry_run: bool = False) -> dict:
    years = int(get("pii_retention_years") or 0)
    if years <= 0:
        return {"purged": 0, "message": "보관기간이 0(자동 파기 안 함)입니다."}
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=365 * years)).strftime("%Y-%m-%d %H:%M:%S")
    rows = db._df("SELECT id FROM customers WHERE status = '종료' AND updated_at < ? AND "
                  "(COALESCE(manager,'') <> '' OR COALESCE(phone,'') <> '' OR COALESCE(email,'') <> '')",
                  [cutoff]).to_dict("records")
    ids = [int(r["id"]) for r in rows]
    if ids and not dry_run:
        with db.get_conn() as conn:
            for cid in ids:
                conn.execute("UPDATE customers SET manager=NULL, phone=NULL, email=NULL WHERE id=?", (cid,))
                conn.execute("UPDATE customer_contacts SET name='[파기]', phone=NULL, email=NULL, memo=NULL, active=0, "
                             "is_primary=0 WHERE customer_id=?", (cid,))
        db.audit("개인정보파기", "거래처", None, {"건수": len(ids), "기준": f"종료 후 {years}년", "거래처ID": ids[:200]})
    return {"purged": len(ids), "dry_run": dry_run, "cutoff": cutoff}
