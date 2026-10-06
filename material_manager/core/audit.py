"""감사로그. 누가 · 언제 · 무엇을 · 어떻게 바꿨는지 남긴다.

- 변경을 일으킨 쓰기와 **같은 DB 트랜잭션**에서 기록한다 → 변경은 됐는데 로그가 없는 일이 없다.
- audit_log 테이블은 트리거로 수정·삭제가 막혀 있다(추가만 가능).

actor(행위자)는 {"id": 사용자ID|None, "name": 이름, "role": 역할, "ip": 접속IP} 형태의 dict다.
"""

import hashlib
import json
import sys
from contextlib import contextmanager
from typing import Any

import pandas as pd

import config
from core import db
from core.utils import fmt_qty, now_str

SYSTEM = {"id": None, "name": "system", "role": "ADMIN", "ip": ""}

ACTIONS = {
    "LOGIN": "로그인", "LOGIN_FAIL": "로그인 실패", "LOGOUT": "로그아웃",
    "USER_CREATE": "사용자 등록", "USER_UPDATE": "사용자 변경", "PASSWORD_RESET": "비밀번호 초기화",
    "PASSWORD_CHANGE": "비밀번호 변경",
    "MATERIAL_CREATE": "자재 등록", "MATERIAL_UPDATE": "자재 수정", "MATERIAL_ACTIVE": "자재 사용/중지",
    "MATERIAL_IMPORT": "자재 일괄 반영",
    "TX_CREATE": "입출고 등록", "TX_REVERSE": "거래 취소",
    "DOC_CREATE": "증빙 등록", "DOC_LINK": "증빙 연결 변경", "DOC_DELETE": "증빙 삭제",
    "PERIOD_CLOSE": "월 마감", "PERIOD_REOPEN": "마감 해제",
    "SAP_RUN": "SAP 전송 실행", "SAP_RETRY": "SAP 재전송",
    "SEED": "샘플 데이터 생성", "BACKUP": "백업 다운로드",
    "TX_TRANSFER": "창고 간 이동", "APPROVAL_REQUEST": "결재 요청", "APPROVAL_DECIDE": "결재 처리",
    "DOC_VIEW": "증빙 열람", "DOC_DOWNLOAD": "증빙 다운로드", "EXPORT": "데이터 내보내기",
    "USER_SCOPE": "데이터 범위 변경", "ORG_CHANGE": "플랜트·창고 변경", "RECONCILE": "재고 대사",
    "JOB_RUN": "배치 수동 실행",
    "PR_CREATE": "구매요청", "PR_DECIDE": "구매요청 결재", "PR_CANCEL": "구매요청 취소",
    "PO_CREATE": "발주", "PO_APPROVE": "발주 결재", "PO_UPDATE": "발주 변경", "PO_CANCEL": "발주 취소",
    "MATERIAL_SYNC": "SAP 마스터 동기화", "MFA_ENABLE": "2단계 인증 등록", "MFA_DISABLE": "2단계 인증 해제",
    "MFA_FAIL": "2단계 인증 실패", "MFA_RECOVERY": "복구 코드 사용", "SSO_LOGIN": "SSO 로그인", "ERP_TEST": "ERP 연결 확인", "SSO_OUTAGE": "SSO 장애 모드", "DOCTOR": "운영 점검", "STATEMENT_CREATE": "거래명세서 입출고", "STATEMENT_CANCEL": "거래명세서 취소", "OFFLINE_SYNC": "오프라인 입력 반영", "SSO_FAIL": "SSO 로그인 실패",
    "FORM_UPDATE": "엑셀 양식 변경", "DEMO_RESET": "시연 데이터 초기화",
    "TX_BATCH": "여러 줄 입출고", "TX_GROUP_CANCEL": "묶음·생산 전체 취소",
    "PARTNER_CREATE": "거래처 등록", "PARTNER_UPDATE": "거래처 수정", "PARTNER_ACTIVE": "거래처 사용/중지",
    "PARTNER_ALIAS": "거래처 다른 이름 연결", "PARTNER_UNALIAS": "거래처 다른 이름 해제",
    "BOM_SAVE": "BOM 저장", "BOM_ACTIVE": "BOM 사용/중지", "PRODUCTION_CREATE": "생산 투입",
    "USER_EMAIL": "사용자 메일 변경", "UNIT_ADD": "단위 환산 추가", "UNIT_REMOVE": "단위 환산 삭제",
    "WO_CREATE": "작업지시", "WO_ISSUE": "작업지시 자재 투입", "WO_OPERATION": "공정 실적", "WO_COMPLETE": "작업지시 완료",
    "ROUTING_SAVE": "공정(라우팅) 저장", "MRP_RUN": "MRP 실행", "MRP_CONVERT": "MRP 계획 → 요청·지시",
    "MRP_DEMAND": "MRP 수요 등록", "PARTNER_IMPORT": "거래처 일괄 등록", "BOM_IMPORT": "BOM 일괄 등록",
    "DELEGATION": "대결 지정", "CHANNEL_SAVE": "알림 채널 저장", "NAMES_SAVE": "이름 설정 변경", "HOMETAX_CHECK": "홈택스 매입 대사", "PO_SHORT_CLOSE": "발주 잔량 종결", "STD_COST": "표준원가 산정", "WO_SETTLE": "오더 정산", "WO_SAP_ORDER": "SAP 생산오더 번호", "CALENDAR": "작업 달력", "PO_DELIVERY": "발주 납기일 변경", "PAYMENT_RELEASE": "지급 보류 해제", "CATEGORY_RENAME": "자재 분류 이름 변경", "CHANNEL_DELETE": "알림 채널 삭제", "COMPANY_SETTINGS": "회사 설정 변경", "PARTNER_MERGE": "거래처 병합",
    "API_KEY": "API 키", "API_CALL": "API 호출", "READ_ONLY": "점검(읽기 전용) 모드", "DOWNLOAD": "파일 내려받기",
    "QUALITY_FIX": "데이터 점검 고침", "API_AUTH_FAIL": "API 인증 실패(IP 차단)", "AUDIT_VERIFY": "감사로그 무결성 검증",
    "BULK_TASK": "대용량 작업", "AP_SNAPSHOT": "월말 미지급 스냅샷", "AUDIT_ANCHOR": "감사로그 앵커 내려받기·대조",
    "APPROVAL_WITHDRAW": "결재 요청 회수", "SCOPE_TEMP": "임시 데이터 범위",
}


def record(conn, actor: dict | None, action: str, entity: str = "",
           entity_id: Any = "", detail: dict | None = None) -> None:
    actor = actor or SYSTEM
    conn.execute(
        """
        INSERT INTO audit_log (at, user_id, user_name, action, entity, entity_id, detail, ip, request_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (now_str(), actor.get("id"), actor.get("name", ""), action, entity,
         "" if entity_id is None else str(entity_id),
         json.dumps(detail, ensure_ascii=False, default=str) if detail else "", actor.get("ip", ""), request_id()),
    )
    if config.AUDIT_STDOUT and not _quiet["n"]:
        _emit(actor, action, entity, entity_id, detail)


def request_id() -> str:
    """지금 요청의 번호(오류 화면의 '문의 번호'). 요청 밖(배치·CLI)이면 ''."""
    try:
        from flask import g, has_request_context
        return str(g.get("request_id", "") or "")[:64] if has_request_context() else ""
    except Exception:       # 요청 번호를 못 얻어도 감사 기록은 계속
        return ""


_quiet = {"n": 0}


# ── 해시 체인 (위변조 탐지) ───────────────────────────────────
# 기록 순서에 맞춰 직전 기록의 해시를 이어 붙인다. 기록할 때마다 잠그면 서버 여러 대가 서로 기다리다 교착할 수 있어
# (업무 트랜잭션 안에서 기록하므로), 기록은 그대로 두고 '봉인'(seal)이 번호(seq)·해시를 채운다 — 배치(1분)·검증·요청 중 가끔.
# 봉인된 기록을 고치거나 지우거나 사이에 끼워 넣으면 verify() 가 찾아낸다. (DB 관리자가 트리거를 끄고 전체를 다시 계산하면
# 막지 못하므로 chain_head() 의 마지막 해시를 서버 밖(운영 문서·표준출력 로그)에 따로 보관하면 더 안전하다.)
CHAIN_FIELDS = ("id", "at", "user_id", "user_name", "action", "entity", "entity_id", "detail", "ip", "request_id")
_CHAIN_SELECT = "SELECT seq, prev_hash, hash, " + ", ".join(CHAIN_FIELDS) + " FROM audit_log"
_seal_state = {"at": 0.0}


def _digest(prev: str, row) -> str:
    payload = json.dumps([prev or ""] + ["" if row[f] is None else str(row[f]) for f in CHAIN_FIELDS],
                         ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def seal(limit: int = 5000) -> int:
    """아직 봉인 안 된 기록(번호 없음)에 순서 번호와 해시를 채운다. 봉인한 건수. 서버 여러 대가 동시에 불러도 한 대씩."""
    with db.transaction() as conn:
        db.lock(conn, "audit_seal")                                 # 이 안에서는 다른 잠금을 잡지 않으므로 교착 없음
        last = conn.execute("SELECT seq, hash FROM audit_log WHERE seq IS NOT NULL ORDER BY seq DESC LIMIT 1").fetchone()
        seq, prev = (int(last["seq"]), last["hash"] or "") if last else (0, "")
        rows = conn.execute(_CHAIN_SELECT + " WHERE seq IS NULL ORDER BY id LIMIT ?", (int(limit),)).fetchall()
        for r in rows:
            seq += 1
            digest = _digest(prev, r)
            conn.execute("UPDATE audit_log SET seq = ?, prev_hash = ?, hash = ? WHERE id = ? AND seq IS NULL",
                         (seq, prev, digest, r["id"]))
            prev = digest
    return len(rows)


def seal_soon(min_seconds: int = 60) -> None:
    """요청 처리 뒤 가끔(프로세스마다 min_seconds 에 한 번) 봉인 — 배치 서버가 없는 환경에서도 체인이 이어지게."""
    import time
    now = time.time()
    if now - _seal_state["at"] < min_seconds:
        return
    _seal_state["at"] = now
    try:
        seal()
    except Exception:       # 봉인 실패가 화면을 막지 않는다 (다음 기회에 다시)
        pass


def chain_head() -> dict:
    row = db.query_df("SELECT seq, hash FROM audit_log WHERE seq IS NOT NULL ORDER BY seq DESC LIMIT 1")
    return {"seq": int(row.iloc[0]["seq"]), "hash": row.iloc[0]["hash"]} if not row.empty else {"seq": 0, "hash": ""}


ANCHOR_KEY = "audit_anchor_saved"
ANCHOR_WARN_DAYS = 35


def anchor_text() -> str:
    """서버 밖(보안 담당 문서·메일·인쇄)에 보관할 앵커. 나중에 '앵커 대조'에 이 값을 넣으면, 그 시점까지의 감사로그가
    그대로인지 알 수 있다 — DB 관리자가 트리거를 끄고 체인 전체를 다시 계산해도 이 값과는 맞지 않는다."""
    for _ in range(200):
        if seal() < 5000:
            break
    head = chain_head()
    return (f"자재관리 감사로그 앵커 — 서버 밖에 보관하세요\n기록 시각: {now_str()}\n"
            f"봉인 번호: {head['seq']}\n해시: {head['hash']}\n\n"
            f"ANCHOR seq={head['seq']} hash={head['hash']}\n")


def parse_anchor(text: str) -> tuple[int, str] | None:
    """붙여넣은 앵커(파일 전체 또는 'ANCHOR seq=.. hash=..' 한 줄)에서 번호와 해시를 읽는다."""
    import re
    seq = re.search(r"(?:seq\s*=\s*|봉인 번호:\s*)(\d+)", text or "")
    digest = re.search(r"\b([0-9a-fA-F]{64})\b", text or "")
    return (int(seq.group(1)), digest.group(1).lower()) if seq and digest else None


def mark_anchor_saved() -> None:
    """앵커를 내려받거나 대조한 시각 (운영 점검이 '월 1회 보관'을 확인한다)."""
    db.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
               (ANCHOR_KEY, now_str()))


def anchor_age_days() -> int | None:
    from datetime import datetime
    raw = db.scalar("SELECT value FROM app_settings WHERE key = ?", (ANCHOR_KEY,))
    if not raw:
        return None
    try:
        return (datetime.now() - datetime.strptime(str(raw), "%Y-%m-%d %H:%M:%S")).days
    except ValueError:
        return None


def check_anchor(seq: int, digest: str) -> tuple[bool, str]:
    """이전에 서버 밖에 적어 둔 앵커와 지금 감사로그를 대조한다. (ok, 설명)."""
    row = db.query_df("SELECT hash FROM audit_log WHERE seq = ?", (int(seq),))
    if row.empty:
        return False, f"봉인 번호 {seq}번 기록이 없습니다 — 그 뒤로 감사로그가 지워졌거나 번호를 잘못 입력했습니다."
    if (row.iloc[0]["hash"] or "") != digest:
        return False, (f"봉인 번호 {seq}번의 해시가 보관해 둔 값과 다릅니다 — 그 시점 이전의 감사로그가 바뀌었거나 "
                       "체인이 다시 계산됐습니다. 즉시 DB 관리자·보안 담당에게 알리세요.")
    result = verify()
    if not result["ok"]:
        return False, f"앵커는 맞지만 그 이후 체인이 깨졌습니다 — 봉인 번호 {result['broken_seq']}: {result['reason']}"
    return True, f"봉인 번호 {seq}번까지의 감사로그가 보관해 둔 값과 같고, 지금({result['sealed']:,}건)까지 체인도 정상입니다."


def announce_anchor() -> str:
    """앵커를 서버 로그(표준출력)에 한 줄 남긴다 — 호스팅 로그(Render 등)가 서버 밖 보관 역할을 한다. 배치 audit_anchor 가 하루에 한 번."""
    head = chain_head()
    if not head["seq"]:
        return "봉인된 기록이 아직 없습니다"
    print("AUDIT_ANCHOR " + json.dumps({"at": now_str(), "seq": head["seq"], "hash": head["hash"]}, ensure_ascii=False),
          file=sys.stdout, flush=True)
    return f"앵커 봉인 {head['seq']:,}번 {head['hash'][:16]}… 를 서버 로그에 남김"


def verify(batch: int = 5000) -> dict:
    """봉인된 기록을 처음부터 다시 계산한다. {ok, sealed, unsealed, broken_seq, reason, head}."""
    for _ in range(200):                                            # 먼저 남은 기록을 모두 봉인 (한 번에 5,000건씩)
        if seal() < 5000:
            break
    result = {"ok": True, "sealed": 0, "unsealed": 0, "broken_seq": None, "reason": "", "head": ""}
    prev, expect, after = "", 1, 0
    with db.get_conn() as conn:
        result["unsealed"] = int(conn.execute("SELECT COUNT(*) FROM audit_log WHERE seq IS NULL").fetchone()[0])
        while True:
            rows = conn.execute(_CHAIN_SELECT + " WHERE seq > ? ORDER BY seq LIMIT ?", (after, int(batch))).fetchall()
            if not rows:
                break
            for r in rows:
                seq = int(r["seq"])
                reason = ""
                if seq != expect:
                    reason = f"번호 {expect}번 기록이 없습니다 (삭제됐거나 끼어 넣은 기록)"
                elif (r["prev_hash"] or "") != prev:
                    reason = "앞 기록과의 연결이 끊어졌습니다"
                elif r["hash"] != _digest(prev, r):
                    reason = "기록 내용이 봉인한 뒤 바뀌었습니다"
                if reason:
                    result.update(ok=False, broken_seq=seq, reason=reason, head=prev)
                    return result
                prev, expect, after = r["hash"], expect + 1, seq
                result["sealed"] += 1
    result["head"] = prev
    return result

@contextmanager
def quiet():
    """이 안의 기록은 서버 로그로 내보내지 않는다 (샘플 생성처럼 수백 줄이 방문자 기록을 덮을 때). DB 기록은 그대로."""
    _quiet["n"] += 1
    try:
        yield
    finally:
        _quiet["n"] -= 1


def _emit(actor: dict, action: str, entity: str, entity_id: Any, detail: dict | None) -> None:
    """서버 로그(표준출력)에도 한 줄 JSON으로 남긴다 → DB가 초기화돼도 호스팅 로그(Render 등)에서 확인할 수 있다.
    같은 트랜잭션이 나중에 실패하면 DB에는 없고 로그에만 남을 수 있다(로그는 참고용)."""
    try:
        line = json.dumps({"at": now_str(), "user": actor.get("name", ""), "ip": actor.get("ip", ""), "action": action,
                           "label": ACTIONS.get(action, action), "entity": entity,
                           "id": "" if entity_id is None else str(entity_id), "detail": detail or {}},
                          ensure_ascii=False, default=str)
        print("AUDIT " + line, file=sys.stdout, flush=True)
    except Exception:       # 로그 출력 실패가 업무를 막지 않게
        pass


def log(actor: dict | None, action: str, entity: str = "", entity_id: Any = "",
        detail: dict | None = None) -> None:
    """다른 쓰기가 없는 행위(로그인·다운로드 등)를 단독으로 기록한다."""
    with db.transaction() as conn:
        record(conn, actor, action, entity, entity_id, detail)


def changes(before: dict, after: dict, fields) -> dict:
    """바뀐 필드만 {필드: [이전, 이후]}."""
    out = {}
    for f in fields:
        b, a = before.get(f), after.get(f)
        try:                                   # 숫자는 숫자로 비교 (18500 == 18500.0)
            if b is not None and a is not None and float(b) == float(a):
                continue
        except (TypeError, ValueError):
            pass
        if str(b if b is not None else "") != str(a if a is not None else ""):
            out[f] = [b, a]
    return out


def _audit_where(start, end, user, action, keyword) -> tuple[str, list]:
    sql = " WHERE at BETWEEN ? AND ?"
    params: list = [f"{start} 00:00:00", f"{end} 23:59:59"]
    if user:
        sql += " AND user_name = ?"
        params.append(user)
    if action:
        sql += " AND action = ?"
        params.append(action)
    if keyword:
        sql += " AND (detail LIKE ? OR entity_id = ? OR request_id = ?)"      # 요청 번호(오류 화면의 문의 번호)로도 찾는다
        params += [f"%{keyword}%", keyword, keyword]
    return sql, params


AUDIT_SELECT = "SELECT id, at, user_name, action, entity, entity_id, detail, ip, request_id FROM audit_log"


def audit_page(start: str, end: str, user: str = "", action: str = "", keyword: str = "",
               page: int = 1, size: int = 100):
    from core import repository as repo
    where, params = _audit_where(start, end, user, action, keyword)
    return repo.paged(AUDIT_SELECT + where + " ORDER BY id DESC", params, page, size)


def audit_df(start: str, end: str, user: str = "", action: str = "", keyword: str = "",
             limit: int = 100_000) -> pd.DataFrame:
    """엑셀 내보내기용 전체 조회."""
    where, params = _audit_where(start, end, user, action, keyword)
    return db.query_df(AUDIT_SELECT + where + " ORDER BY id DESC LIMIT ?", [*params, limit])


def user_names() -> list[str]:
    return db.query_df("SELECT DISTINCT user_name FROM audit_log ORDER BY user_name")["user_name"].tolist()


# 변경 이력 탭 (자재 수정 화면 · 거래처 상세) — 항목 이름
FIELD_LABELS = {
    "material": {**config.MATERIAL_COLS, "lot_managed": "로트 관리", "expiry_managed": "유효기한 관리", "active": "사용"},
    "partner": {"name": "거래처명", "biz_no": "사업자등록번호", "kind": "구분", "contact": "담당자", "phone": "전화",
                "email": "메일", "note": "메모", "active": "사용"},
}
_HISTORY_ACTIONS = {
    "material": ("MATERIAL_CREATE", "MATERIAL_UPDATE", "MATERIAL_ACTIVE", "UNIT_ADD", "UNIT_REMOVE", "ROUTING_SAVE"),
    "partner": ("PARTNER_CREATE", "PARTNER_UPDATE", "PARTNER_ACTIVE", "PARTNER_ALIAS", "PARTNER_UNALIAS", "PARTNER_MERGE"),
}


def _show(v) -> str:
    if v is True:
        return "예"
    if v is False:
        return "아니오"
    if v is None or v == "":
        return "(빈 값)"
    if isinstance(v, (int, float)):
        return f"{v:,.0f}" if float(v).is_integer() else f"{fmt_qty(v)}"
    return str(v)


def entity_history(entity: str, entity_id, limit: int = 100) -> list[dict]:
    """한 자재·거래처의 변경 이력. 수정은 바뀐 항목마다 [항목, 이전, 이후], 그 밖의 기록은 요약 한 줄."""
    labels = FIELD_LABELS.get(entity, {})
    frag, params = db.in_clause(list(_HISTORY_ACTIONS[entity]))
    df = db.query_df(f"SELECT at, user_name, action, detail, ip FROM audit_log WHERE entity = ? AND entity_id = ? "
                     f"AND action{frag} ORDER BY id DESC LIMIT ?", (entity, str(entity_id), *params, int(limit)))
    out = []
    for r in df.itertuples(index=False):
        try:
            d = json.loads(r.detail) if r.detail else {}
        except ValueError:
            d = {}
        via = d.pop("via", "")
        changes, summary = [], ""
        if r.action in ("MATERIAL_UPDATE", "PARTNER_UPDATE", "MATERIAL_ACTIVE", "PARTNER_ACTIVE"):
            for k, v in d.items():
                if isinstance(v, list) and len(v) == 2:
                    changes.append({"field": labels.get(k, k), "before": _show(v[0]), "after": _show(v[1])})
        elif r.action in ("MATERIAL_CREATE", "PARTNER_CREATE"):
            summary = " · ".join(f"{labels.get(k, k)} {_show(v)}" for k, v in d.items()
                                 if k in labels and v not in ("", None, 0, 0.0))[:300]
        elif r.action in ("UNIT_ADD", "UNIT_REMOVE"):
            summary = f"{d.get('unit', '')}" + (f" = ×{_show(d.get('factor'))}" if d.get("factor") is not None else "") + \
                      (f" · 바코드 {d['barcode']}" if d.get("barcode") else "")
        elif r.action in ("PARTNER_ALIAS", "PARTNER_UNALIAS"):
            summary = d.get("alias", "")
        else:
            summary = ", ".join(f"{k} {_show(v)}" for k, v in d.items() if not isinstance(v, (list, dict)))[:300]
        out.append({"at": r.at, "user": r.user_name, "action": ACTIONS.get(r.action, r.action), "via": via,
                    "changes": changes, "summary": summary, "ip": r.ip})
    return out
