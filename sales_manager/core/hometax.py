"""홈택스 매출 전자(세금)계산서 대사 — 국세청에 올라간 '내가 발행한' 목록과 이 시스템의 증빙·발행 기록을 맞춰 본다.

  파일: 홈택스 → 조회/발급 → 전자세금계산서 → 목록조회 → 매출 → 엑셀 내려받기 (.xlsx 로 저장하거나 CSV).
        머리글 줄은 '승인번호' 칸을 찾아 맞추고, 같은 승인번호가 품목마다 여러 줄이면 한 건으로 합친다.
  결과
    일치          승인번호가 증빙에 있고 공급가액·세액이 같음
    금액 다름     승인번호는 있는데 공급가액·세액이 다름 (수정세금계산서·단수 확인)
    증빙 없음     홈택스에는 있는데 이 시스템에 없음 → 다른 프로그램·홈택스에서 직접 발행한 건. 같은 거래처(사업자번호)
                  ±10일 · 합계가 같은 매출을 후보로 보여 준다 (증빙으로 붙이면 됨)
    홈택스에 없음 이 기간 전자 발행 증빙인데 홈택스 목록에 없음 → 전송 실패·취소·승인번호 오기 확인
  국세청·ASP 에 직접 접속하지 않는다. 올린 파일은 저장하지 않고 감사로그에 건수만 남긴다.
"""
from __future__ import annotations

import io
import re
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from . import sales_db as db

COLS = {
    "approval_no": ["승인번호"],
    "write_date": ["작성일자", "작성일"],
    "buyer_biz_no": ["공급받는자사업자등록번호", "공급받는자 사업자등록번호", "공급받는자등록번호", "공급받는자사업자번호",
                     "공급받는자사업자등록번호(주민번호)"],
    "buyer_name": ["공급받는자상호", "공급받는자 상호", "상호(공급받는자)", "공급받는자명"],
    "supply": ["공급가액", "공급가액합계"],
    "tax": ["세액", "세액합계"],
    "total": ["합계금액", "합계"],
    "item": ["품목명"],
    "kind": ["전자세금계산서종류", "전자계산서종류", "발급유형", "종류"],
}
NEAR_DAYS = 10
ELECTRONIC = ("전자세금계산서", "수정세금계산서", "전자계산서", "수정계산서")


def _digits(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


def _num(v) -> int:
    try:
        return int(round(float(str(v).replace(",", "").strip() or 0)))
    except ValueError:
        return 0


def read(data: bytes, filename: str) -> tuple[pd.DataFrame, Optional[tuple[str, str]]]:
    """홈택스 매출 목록 → 승인번호별 한 줄. (표, 파일 위쪽에 적힌 조회기간 또는 None). 못 읽으면 ValueError."""
    from . import dataio
    name = (filename or "").lower()
    if name.endswith(".csv"):
        raw = None
        for enc in ("utf-8-sig", "cp949"):
            try:
                raw = pd.read_csv(io.BytesIO(data), header=None, dtype=str, encoding=enc)
                break
            except UnicodeDecodeError:
                continue
        if raw is None:
            raise ValueError("CSV 글자 인코딩을 읽지 못했습니다.")
    elif name.endswith(".xlsx"):
        dataio.check_excel_safe(data)
        raw = pd.read_excel(io.BytesIO(data), header=None, dtype=str, nrows=dataio.MAX_UPLOAD_ROWS + 30)
    else:
        raise ValueError("엑셀(.xlsx) 또는 CSV 파일을 올리세요 (홈택스 .xls 는 엑셀에서 .xlsx 로 다시 저장).")
    head = next((i for i in range(min(len(raw), 30))
                 if any(str(v).replace(" ", "") == "승인번호" for v in raw.iloc[i])), None)
    if head is None:
        raise ValueError("'승인번호' 머리글을 찾지 못했습니다. 홈택스 매출 전자세금계산서 목록 파일인지 확인하세요.")
    period = None
    for i in range(head):
        found = re.findall(r"(\d{4})[-./](\d{2})[-./](\d{2})", " ".join(str(v) for v in raw.iloc[i] if str(v) != "nan"))
        if len(found) >= 2:
            period = tuple("-".join(x) for x in found[:2])
    header = [str(v).replace(" ", "").strip() for v in raw.iloc[head]]
    body = raw.iloc[head + 1:].reset_index(drop=True)
    picked = {}
    for key, names in COLS.items():
        idx = next((header.index(n.replace(" ", "")) for n in names if n.replace(" ", "") in header), None)
        if idx is not None:
            picked[key] = body.iloc[:, idx]
    for need in ("approval_no", "supply"):
        if need not in picked:
            raise ValueError(f"'{COLS[need][0]}' 열이 없습니다.")
    df = pd.DataFrame(picked)
    df["approval_no"] = df["approval_no"].map(_digits)
    df = df[df["approval_no"].str.len() == 24]
    for col in ("supply", "tax", "total"):
        df[col] = df[col].map(_num) if col in df else 0
    if "write_date" in df:
        df["write_date"] = df["write_date"].map(lambda v: f"{_digits(v)[:4]}-{_digits(v)[4:6]}-{_digits(v)[6:8]}"
                                                if len(_digits(v)) >= 8 else None)
    agg = {"supply": "sum", "tax": "sum", "total": "sum"}
    for col in ("write_date", "buyer_biz_no", "buyer_name", "item", "kind"):
        if col in df:
            agg[col] = "first"
    df = df.groupby("approval_no", as_index=False).agg(agg)
    if "buyer_biz_no" in df:
        df["buyer_biz_no"] = df["buyer_biz_no"].map(_digits)
    return df, period


def reconcile(df: pd.DataFrame, period: Optional[tuple[str, str]] = None) -> pd.DataFrame:
    """홈택스 목록과 이 시스템 증빙(권한 범위 안)을 승인번호로 맞춘다."""
    sc, sp = db._scope_clause("s")
    docs = db._df("SELECT d.id AS doc_id, d.sale_id, d.doc_type, d.approval_no, d.issue_date, d.supply_amount, "
                  "d.tax_amount, c.name AS customer, c.biz_no FROM sale_documents d JOIN sales s ON s.id = d.sale_id "
                  "JOIN customers c ON c.id = s.customer_id WHERE d.voided_at IS NULL AND d.approval_no IS NOT NULL"
                  f"{sc}", sp)
    by_no = {str(r["approval_no"]): r for r in docs.to_dict("records")}
    out = []
    seen = set()
    for r in df.to_dict("records"):
        no = r["approval_no"]
        line = {"승인번호": no, "작성일자": r.get("write_date"), "공급받는자": r.get("buyer_name"),
                "홈택스 공급가액": int(r["supply"]), "홈택스 세액": int(r.get("tax") or 0)}
        doc = by_no.get(no)
        if doc:
            seen.add(no)
            same = int(doc.get("supply_amount") or 0) == int(r["supply"]) and \
                int(doc.get("tax_amount") or 0) == int(r.get("tax") or 0)
            out.append({**line, "결과": "일치" if same else "금액 다름", "매출번호": int(doc["sale_id"]),
                        "증빙 공급가액": doc.get("supply_amount"), "증빙 세액": doc.get("tax_amount"),
                        "참고": "" if same else "수정세금계산서·단수 처리 확인"})
            continue
        out.append({**line, "결과": "증빙 없음", "매출번호": None, "참고": _candidates(r)})
    lo, hi = period or ((min(df["write_date"].dropna()) if "write_date" in df and df["write_date"].notna().any() else None),
                        (max(df["write_date"].dropna()) if "write_date" in df and df["write_date"].notna().any() else None))
    for no, doc in by_no.items():
        if no in seen or doc["doc_type"] not in ELECTRONIC:
            continue
        day = str(doc.get("issue_date") or "")[:10]
        if lo and hi and not (lo <= day <= hi):
            continue
        out.append({"승인번호": no, "작성일자": day, "공급받는자": doc["customer"], "홈택스 공급가액": None,
                    "홈택스 세액": None, "결과": "홈택스에 없음", "매출번호": int(doc["sale_id"]),
                    "증빙 공급가액": doc.get("supply_amount"), "증빙 세액": doc.get("tax_amount"),
                    "참고": "전송 실패·취소·승인번호 오기 확인"})
    order = {"증빙 없음": 0, "금액 다름": 1, "홈택스에 없음": 2, "일치": 3}
    result = pd.DataFrame(out)
    if not result.empty:
        result = result.sort_values(by="결과", key=lambda s: s.map(order), kind="stable").reset_index(drop=True)
    db.audit("홈택스대사", "매출", None, {"홈택스건수": len(df), **(result["결과"].value_counts().to_dict()
                                                               if not result.empty else {})})
    return result


def _candidates(r: dict) -> str:
    biz, day = r.get("buyer_biz_no"), r.get("write_date")
    if not biz or not day:
        return ""
    lo = (date.fromisoformat(day) - timedelta(days=NEAR_DAYS)).isoformat()
    hi = (date.fromisoformat(day) + timedelta(days=NEAR_DAYS)).isoformat()
    sc, sp = db._scope_clause("s")
    rows = db._df("SELECT s.id FROM sales s JOIN customers c ON c.id = s.customer_id WHERE c.biz_no_norm = ? "
                  "AND s.sale_date BETWEEN ? AND ? AND s.status <> ? AND COALESCE(s.total_amount, s.amount) = ?"
                  f"{sc} ORDER BY s.sale_date", [biz, lo, hi, db.SALE_CANCELLED, int(r["supply"]) + int(r.get("tax") or 0), *sp])
    return ("후보 매출 " + ", ".join(f"#{int(i)}" for i in rows["id"][:5]) + " — 증빙으로 붙이세요") if not rows.empty else \
        "같은 거래처·금액의 매출이 없음 — 매출 등록 누락 확인"
