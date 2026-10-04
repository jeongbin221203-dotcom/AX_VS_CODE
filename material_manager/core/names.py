"""이름 설정: 회사마다 부르는 말이 다르다 (예: '입고' → '입하', '진행(재공)' → '생산 중', '발주' → '구매오더').

- 거래 구분 · 작업지시 상태 · 구매요청·발주 상태 · 결재 상태: DB 에는 코드(IN, RELEASED 등)로 저장되므로
  **화면·엑셀·알림에 보이는 이름만** 바꾼다 → 지난 데이터를 고칠 필요가 없고, 기본값으로 언제든 되돌릴 수 있다.
  값은 app_settings 'names' 에 두고 요청마다(15초 캐시) 이름표(dict)를 제자리에서 바꾼다(서버 여러 대도 같게).
- 자재 분류: 자재마다 글자로 저장되므로 이름을 바꾸면 그 분류의 자재를 **같은 트랜잭션에서** 모두 바꾼다
  (이미 있는 분류 이름으로 바꾸면 합쳐진다). 감사로그에 바뀐 자재 수가 남는다.
"""
from __future__ import annotations

import json
import time

import config
from core import audit, db
from core.utils import now_str

KEY = "names"
CACHE_SECONDS = 15
MAX_LEN = 20


def _groups() -> dict:
    """그룹: (화면 이름, 이름표 dict). dict 는 다른 모듈이 쓰는 바로 그 객체 (제자리에서 바꾼다)."""
    from core import approvals, production, purchasing
    return {"tx": ("거래 구분", config.TX_LABEL), "wo": ("작업지시 상태", production.STATUS),
            "pr": ("구매요청 상태", purchasing.PR_STATUS), "po": ("발주 상태", purchasing.PO_STATUS),
            "approval": ("결재 상태", approvals.STATUS)}


_defaults: dict[str, dict] = {}
_cache = {"at": 0.0, "values": {}}


def defaults() -> dict[str, dict]:
    if not _defaults:
        _defaults.update({g: dict(labels) for g, (_, labels) in _groups().items()})
    return {g: dict(v) for g, v in _defaults.items()}


def stored() -> dict[str, dict]:
    raw = db.scalar("SELECT value FROM app_settings WHERE key = ?", (KEY,))
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


def _apply(st: dict) -> None:
    base = defaults()
    for g, (_, labels) in _groups().items():
        labels.update(base[g])                            # 먼저 기본값으로 (지운 항목은 돌아온다)
        labels.update({k: v for k, v in (st.get(g) or {}).items() if k in base[g] and v})


def refresh(force: bool = False) -> None:
    if not force and time.time() - _cache["at"] < CACHE_SECONDS:
        return
    defaults()
    try:
        st = stored()
    except Exception:                                    # DB가 아직 없거나 잠깐 끊겨도 화면은 열린다
        return
    if st or _cache["values"]:
        _apply(st)
    _cache.update(at=time.time(), values=st)


def rows() -> list[dict]:
    """화면: 그룹별 [코드, 기본 이름, 지금 이름]."""
    base, st = defaults(), stored()
    return [{"group": g, "title": title, "items": [{"code": c, "default": base[g][c], "value": (st.get(g) or {}).get(c, "")}
                                                   for c in base[g]]}
            for g, (title, _) in _groups().items()]


def save(form: dict, actor: dict) -> tuple[bool, str]:
    """form: {'tx.IN': '입하', ...}. 빈 칸 = 기본 이름. 같은 그룹 안에서 이름이 겹치면 거부."""
    base = defaults()
    new: dict[str, dict] = {}
    for g in base:
        for code, default in base[g].items():
            v = str(form.get(f"{g}.{code}", "")).strip()[:MAX_LEN]
            if v and v != default:
                new.setdefault(g, {})[code] = v
        shown = [(new.get(g) or {}).get(c) or base[g][c] for c in base[g]]
        if len(set(shown)) != len(shown):
            return False, f"{_groups()[g][0]}: 같은 이름을 두 번 쓸 수 없습니다."
    before = stored()
    if new == before:
        return True, "바뀐 내용이 없습니다."
    diff = {f"{g}.{c}": [(before.get(g) or {}).get(c) or base[g][c], (new.get(g) or {}).get(c) or base[g][c]]
            for g in base for c in base[g]
            if ((before.get(g) or {}).get(c) or base[g][c]) != ((new.get(g) or {}).get(c) or base[g][c])}
    with db.transaction() as conn:
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                     (KEY, json.dumps(new, ensure_ascii=False)))
        audit.record(conn, actor, "NAMES_SAVE", "settings", KEY, {"changed": diff, "at": now_str()})
    refresh(force=True)
    return True, f"이름 {len(diff)}개를 바꿨습니다 (화면·엑셀·알림에 바로 적용, 지난 데이터는 그대로)."


def relabel(text: str) -> str:
    """표 머리글 등에 들어간 기본 거래 구분 이름('입고금액', '이동출고')을 회사가 바꾼 이름으로."""
    base = defaults().get("tx", {})
    for code, default in base.items():
        now = config.TX_LABEL.get(code, default)
        if now != default and default in text:
            text = text.replace(default, now)
    return text


# ── 자재 분류 ────────────────────────────────────────────────
def categories() -> list[dict]:
    df = db.query_df("SELECT category, COUNT(*) AS n, SUM(active) AS active FROM materials GROUP BY category ORDER BY category")
    return df.to_dict("records")


def rename_category(old: str, new: str, actor: dict) -> tuple[bool, str]:
    old, new = (old or "").strip(), (new or "").strip()[:40]
    if not old or not new:
        return False, "바꿀 분류와 새 이름을 입력하세요."
    if old == new:
        return True, "바뀐 내용이 없습니다."
    with db.transaction() as conn:
        n = conn.execute("SELECT COUNT(*) FROM materials WHERE category = ?", (old,)).fetchone()[0]
        if not n:
            return False, f"'{old}' 분류의 자재가 없습니다."
        merged = conn.execute("SELECT COUNT(*) FROM materials WHERE category = ?", (new,)).fetchone()[0]
        conn.execute("UPDATE materials SET category = ?, updated_at = ? WHERE category = ?", (new, now_str(), old))
        audit.record(conn, actor, "CATEGORY_RENAME", "material", "", {"from": old, "to": new, "materials": n, "merged_into": merged})
    return True, (f"분류 '{old}' → '{new}' — 자재 {n}종을 바꿨습니다" + (f" (이미 있던 '{new}' {merged}종과 합쳐짐)." if merged else "."))
