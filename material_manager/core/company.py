"""회사 설정: 회사마다 다른 업무 기준을 화면(관리자 → 🏢 회사 설정)에서 바꾼다. 코드·환경변수를 고치지 않아도 된다.

- 값은 app_settings 의 'company'(JSON)에 저장하고, 요청마다(15초 캐시) config 모듈의 같은 이름 값을 덮어쓴다
  → 서버가 여러 대여도 15초 안에 모두 같은 값을 쓴다. 저장하지 않은 항목은 환경변수·기본값 그대로.
- 바꿀 때마다 감사로그에 바뀐 항목(전·후)이 남는다. 바뀐 기준은 그 뒤 요청·결재부터 적용된다(지난 결재는 그대로).
"""
from __future__ import annotations

import json
import time

import config
from core import audit, db
from core.utils import now_str

KEY = "company"
CACHE_SECONDS = 15

# 항목: (화면 이름, 종류, 도움말, 최솟값)
FIELDS = {
    "COMPANY_NAME": ("회사 이름", "text", "화면 위·알림 제목에 함께 나옵니다 (비우면 시스템 이름만)", None),
    "PR_TIER1": ("구매요청 1단계 결재 한도 (원)", "int", "이 금액까지는 관리자 1명", 0),
    "PR_TIER2": ("구매요청 2단계 결재 한도 (원)", "int", "이 금액까지는 관리자 2명, 넘으면 + 시스템관리자 (3단계)", 0),
    "PO_OVER_PR_TOLERANCE_PCT": ("발주 금액 허용 초과율 (%)", "float", "승인된 요청 금액보다 이만큼 넘게 크면 발주도 결재", 0),
    "ADJ_APPROVAL_AMOUNT": ("실사 조정 결재 기준 (원)", "int", "조정 금액(기준단가)이 이 이상이면 관리자 결재 후 반영", 0),
    "APPROVAL_SLA_HOURS": ("결재 기한 (시간)", "int", "넘으면 결재자에게 하루 한 번 독촉 (0 = 독촉 안 함)", 0),
    "PARTNER_REQUIRED": ("거래처 마스터에 있는 이름만 허용", "bool", "켜면 마스터에 없는 거래처 이름으로는 등록할 수 없음", None),
    "COST_DAYS": ("실제 원가 기준 기간 (일)", "int", "생산 투입 단가 = 이 기간 입고의 가중평균", 7),
}
_cache = {"at": 0.0, "values": {}}
_defaults: dict = {}


def _current_from_config() -> dict:
    from core import production
    t1, t2 = config.PR_APPROVAL_TIERS[0][0], config.PR_APPROVAL_TIERS[1][0]
    return {"COMPANY_NAME": config.COMPANY_NAME, "PR_TIER1": int(t1), "PR_TIER2": int(t2),
            "PO_OVER_PR_TOLERANCE_PCT": round(config.PO_OVER_PR_TOLERANCE * 100, 4),
            "ADJ_APPROVAL_AMOUNT": int(config.ADJ_APPROVAL_AMOUNT), "APPROVAL_SLA_HOURS": int(config.APPROVAL_SLA_HOURS),
            "PARTNER_REQUIRED": bool(config.PARTNER_REQUIRED), "COST_DAYS": int(production.COST_DAYS)}


def defaults() -> dict:
    """환경변수·기본값 (처음 한 번 기억해 둔다 — 화면 값을 지우면 이 값으로 돌아간다)."""
    if not _defaults:
        _defaults.update(_current_from_config())
    return dict(_defaults)


def stored() -> dict:
    raw = db.scalar("SELECT value FROM app_settings WHERE key = ?", (KEY,))
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


def values() -> dict:
    return {**defaults(), **stored()}


def _apply(v: dict, keys) -> None:
    """keys 에 든 항목만 config 에 넣는다 (저장한 적 없는 항목은 환경변수·기본값을 그대로 둔다)."""
    from core import production
    if "PR_TIER1" in keys or "PR_TIER2" in keys:
        config.PR_APPROVAL_TIERS = [(float(v["PR_TIER1"]), 1), (float(v["PR_TIER2"]), 2), (float("inf"), 3)]
    setters = {
        "COMPANY_NAME": lambda x: setattr(config, "COMPANY_NAME", x),
        "PO_OVER_PR_TOLERANCE_PCT": lambda x: setattr(config, "PO_OVER_PR_TOLERANCE", float(x) / 100),
        "ADJ_APPROVAL_AMOUNT": lambda x: setattr(config, "ADJ_APPROVAL_AMOUNT", int(x)),
        "APPROVAL_SLA_HOURS": lambda x: setattr(config, "APPROVAL_SLA_HOURS", int(x)),
        "PARTNER_REQUIRED": lambda x: setattr(config, "PARTNER_REQUIRED", bool(x)),
        "COST_DAYS": lambda x: setattr(production, "COST_DAYS", int(x)),
    }
    for k in keys:
        if k in setters:
            setters[k](v[k])


def refresh(force: bool = False) -> None:
    """요청마다 부른다 — 15초 안에 다시 부르면 DB를 읽지 않는다.
    저장된 항목과, 지난번엔 저장돼 있었는데 지워진 항목(기본값으로 되돌림)만 config 에 넣는다."""
    if not force and time.time() - _cache["at"] < CACHE_SECONDS:
        return
    defaults()
    try:
        st = stored()
    except Exception:                                    # DB가 아직 없거나 잠깐 끊겨도 화면은 열린다
        return
    keys = set(st) | set(_cache["values"])
    _apply({**defaults(), **st}, keys)
    _cache.update(at=time.time(), values=st)


def _parse(form: dict) -> tuple[dict, str]:
    out = {}
    for k, (label, kind, _, minimum) in FIELDS.items():
        raw = form.get(k, "")
        if kind == "bool":
            out[k] = raw in ("1", "on", "true", True)
            continue
        if kind == "text":
            out[k] = str(raw).strip()[:60]
            continue
        try:
            num = float(str(raw).replace(",", "").strip())          # '1,000,000' · '1e+06' 모두
            if num != num or num in (float("inf"), float("-inf")):
                raise ValueError
            val = int(round(num)) if kind == "int" else num
        except ValueError:
            return {}, f"{label}: 숫자를 입력하세요."
        if minimum is not None and val < minimum:
            return {}, f"{label}: {minimum} 이상이어야 합니다."
        out[k] = val
    if out["PR_TIER2"] < out["PR_TIER1"]:
        return {}, "2단계 한도는 1단계 한도 이상이어야 합니다."
    return out, ""


def save(form: dict, actor: dict) -> tuple[bool, str]:
    new, problem = _parse(form)
    if problem:
        return False, problem
    before = values()
    diff = {k: [before.get(k), v] for k, v in new.items() if before.get(k) != v}
    if not diff:
        return True, "바뀐 내용이 없습니다."
    base = defaults()
    keep = {k: v for k, v in new.items() if v != base.get(k)}           # 기본값과 같은 항목은 저장하지 않는다
    with db.transaction() as conn:
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                     (KEY, json.dumps(keep, ensure_ascii=False)))
        audit.record(conn, actor, "COMPANY_SETTINGS", "settings", KEY, {"changed": diff, "at": now_str()})
    refresh(force=True)
    return True, f"회사 설정 {len(diff)}개 항목을 저장했습니다 (서버 여러 대는 15초 안에 함께 바뀝니다)."


def history(limit: int = 30):
    return db.query_df("SELECT at, user_name, detail FROM audit_log WHERE action = 'COMPANY_SETTINGS' ORDER BY id DESC LIMIT ?",
                       (limit,))
