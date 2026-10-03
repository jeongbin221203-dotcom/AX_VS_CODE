"""감사로그. 누가 · 언제 · 무엇을 · 어떻게 바꿨는지 남긴다.

- 변경을 일으킨 쓰기와 **같은 DB 트랜잭션**에서 기록한다 → 변경은 됐는데 로그가 없는 일이 없다.
- audit_log 테이블은 트리거로 수정·삭제가 막혀 있다(추가만 가능).

actor(행위자)는 {"id": 사용자ID|None, "name": 이름, "role": 역할, "ip": 접속IP} 형태의 dict다.
"""

import json
import sys
from contextlib import contextmanager
from typing import Any

import pandas as pd

import config
from core import db
from core.utils import now_str

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
    "DELEGATION": "대결 지정", "COMPANY_SETTINGS": "회사 설정 변경", "PARTNER_MERGE": "거래처 병합",
    "API_KEY": "API 키", "API_CALL": "API 호출", "READ_ONLY": "점검(읽기 전용) 모드", "DOWNLOAD": "파일 내려받기",
    "QUALITY_FIX": "데이터 점검 고침",
}


def record(conn, actor: dict | None, action: str, entity: str = "",
           entity_id: Any = "", detail: dict | None = None) -> None:
    actor = actor or SYSTEM
    conn.execute(
        """
        INSERT INTO audit_log (at, user_id, user_name, action, entity, entity_id, detail, ip)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (now_str(), actor.get("id"), actor.get("name", ""), action, entity,
         "" if entity_id is None else str(entity_id),
         json.dumps(detail, ensure_ascii=False, default=str) if detail else "", actor.get("ip", "")),
    )
    if config.AUDIT_STDOUT and not _quiet["n"]:
        _emit(actor, action, entity, entity_id, detail)


_quiet = {"n": 0}


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
        sql += " AND (detail LIKE ? OR entity_id = ?)"
        params += [f"%{keyword}%", keyword]
    return sql, params


AUDIT_SELECT = "SELECT id, at, user_name, action, entity, entity_id, detail, ip FROM audit_log"


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
        return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.4g}"
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
