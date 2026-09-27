"""감사로그. 누가 · 언제 · 무엇을 · 어떻게 바꿨는지 남긴다.

- 변경을 일으킨 쓰기와 **같은 DB 트랜잭션**에서 기록한다 → 변경은 됐는데 로그가 없는 일이 없다.
- audit_log 테이블은 트리거로 수정·삭제가 막혀 있다(추가만 가능).

actor(행위자)는 {"id": 사용자ID|None, "name": 이름, "role": 역할, "ip": 접속IP} 형태의 dict다.
"""

import json
from typing import Any

import pandas as pd

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
    "MFA_FAIL": "2단계 인증 실패", "SSO_LOGIN": "SSO 로그인", "SSO_FAIL": "SSO 로그인 실패",
    "FORM_UPDATE": "엑셀 양식 변경",
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
