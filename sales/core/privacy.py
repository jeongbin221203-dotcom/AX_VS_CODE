"""개인정보 열람·삭제 요청 처리 (개인정보 보호법 정보주체의 권리)

  고객(정보주체)이 "내 정보를 보여 달라 / 지워 달라"고 하면 이름·전화번호·이메일로 찾아
  거래처의 고객 담당자 정보와 영업활동 기록에 남은 내용을 모아 내보내거나 파기한다.
  * 거래처명·매출·세금계산서 같은 회사 거래 기록은 법정 보존 대상이라 지우지 않는다
  * 영업활동 내용은 기록을 지우지 않고 해당 이름·번호·이메일 부분만 '[파기]'로 바꾼다
  * 요청마다 pii_requests 에 남기고(찾은 건수·처리자), 감사로그에는 검색어 원문 대신 가린 값을 남긴다
"""
from __future__ import annotations

import re
from typing import Any

import pandas as pd

from . import sales_db as db

MASK = "[파기]"


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _masked(term: str) -> str:
    term = str(term or "")
    return term[:1] + "*" * max(len(term) - 2, 1) + term[-1:] if len(term) > 2 else "*" * len(term)


def search(term: str) -> dict:
    term = str(term or "").strip()
    if len(term) < 2:
        raise ValueError("이름·전화번호·이메일을 두 글자 이상 입력하세요.")
    digits = _digits(term)
    like = f"%{term}%"
    cust_sql = ("SELECT id, name AS 거래처, manager AS 고객담당자, phone AS 연락처, email AS 이메일, owner AS 영업담당 "
                "FROM customers WHERE manager LIKE ? OR email = ?")
    params: list[Any] = [like, term]
    if len(digits) >= 7:
        cust_sql += (" OR REPLACE(REPLACE(REPLACE(COALESCE(phone,''), '-', ''), ' ', ''), '.', '') LIKE ?")
        params.append(f"%{digits}%")
    customers = db._df(cust_sql + " ORDER BY id", params)
    acts = db._df("SELECT a.id, a.act_date AS 활동일, c.name AS 거래처, a.summary AS 활동내용, a.next_action AS 다음액션, "
                  "a.owner AS 영업담당 FROM activities a JOIN customers c ON c.id = a.customer_id "
                  "WHERE a.summary LIKE ? OR a.next_action LIKE ? ORDER BY a.id", [like, like])
    return {"term": term, "customers": customers, "activities": acts}


def _log(kind: str, term: str, matched: int, requester: str, result: str, actor: str) -> int:
    with db.get_conn() as conn:
        cur = conn.execute("INSERT INTO pii_requests (kind, requester, search_term, matched, result, handled_by, handled_at) "
                           "VALUES (?,?,?,?,?,?,?)", (kind, _masked(requester) if requester else None, _masked(term),
                                                      matched, result, actor,
                                                      db._now()))
        rid = int(cur.lastrowid)
    db.audit(f"개인정보{kind}", "거래처", None, {"요청번호": rid, "검색어": _masked(term), "건수": matched,
                                                "요청자": _masked(requester) if requester else None})
    return rid


def export(term: str, requester: str, actor: str) -> tuple[bytes, int]:
    """열람 요청: 찾은 내용을 엑셀로 (요청한 본인에게 전달)."""
    from . import dataio
    found = search(term)
    n = len(found["customers"]) + len(found["activities"])
    data = dataio.to_excel({"거래처 고객담당자": found["customers"].drop(columns=["id"], errors="ignore"),
                            "영업활동 기록": found["activities"].drop(columns=["id"], errors="ignore")},
                           {"요청": "개인정보 열람", "요청자": requester or "-", "처리자": actor, "처리일시": db._now()})
    _log("열람", term, n, requester, "엑셀 제공", actor)
    return data, n


def erase(term: str, requester: str, actor: str, customer_ids: list[int], activity_ids: list[int]) -> dict:
    """삭제 요청: 고른 거래처의 고객 담당자·연락처·이메일을 비우고, 고른 활동 내용에서 해당 부분만 [파기] 로 바꾼다."""
    found = search(term)
    allowed_c = set(int(i) for i in found["customers"]["id"]) if not found["customers"].empty else set()
    allowed_a = set(int(i) for i in found["activities"]["id"]) if not found["activities"].empty else set()
    cids = [int(i) for i in customer_ids if int(i) in allowed_c]
    aids = [int(i) for i in activity_ids if int(i) in allowed_a]
    if not cids and not aids:
        raise ValueError("파기할 항목을 고르세요 (검색 결과에서 체크).")
    patterns = {term}
    for cid in cids:
        row = db.get_customer(cid) or {}
        patterns.update(v for v in (row.get("manager"), row.get("phone"), row.get("email")) if v)
    with db.get_conn() as conn:
        for cid in cids:
            conn.execute("UPDATE customers SET manager=NULL, phone=NULL, email=NULL, updated_at=?, "
                         "row_version=COALESCE(row_version,0)+1 WHERE id=?", (db._now(), cid))
        for aid in aids:
            row = conn.execute("SELECT summary, next_action FROM activities WHERE id=?", (aid,)).fetchone()
            new = []
            for text in (row["summary"], row["next_action"]):
                for pat in sorted(patterns, key=len, reverse=True):
                    text = text.replace(pat, MASK) if text else text
                new.append(text)
            conn.execute("UPDATE activities SET summary=?, next_action=? WHERE id=?", (*new, aid))
    rid = _log("삭제", term, len(cids) + len(aids), requester, f"거래처 {len(cids)}건 · 활동 {len(aids)}건 파기", actor)
    return {"request_id": rid, "customers": len(cids), "activities": len(aids)}


def history() -> pd.DataFrame:
    return db._df("SELECT id AS 요청번호, kind AS 구분, requester AS 요청자, search_term AS 검색어, matched AS 건수, "
                  "result AS 처리결과, handled_by AS 처리자, handled_at AS 처리일시 FROM pii_requests ORDER BY id DESC LIMIT 100")
