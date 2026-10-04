"""홈택스 매입 전자세금계산서 대사 (증빙 메뉴 → 🧾 홈택스 매입 대사).

자재관리는 매입(받는) 쪽이라 세금계산서를 '발행'하지 않는다. 대신 국세청에 올라온 매입 전자세금계산서 목록과
이 시스템의 증빙·입고를 맞춰 본다 — 빠진 증빙, 금액이 다른 증빙, 홈택스에 없는(취소·위조 의심) 증빙을 찾는다.

- 파일: 홈택스 → 조회/발급 → 전자세금계산서 → 목록조회 → 매입 → 엑셀 내려받기 (.xlsx 로 저장해 올리거나 CSV).
  머리글 줄 위치는 '승인번호' 칸을 찾아 맞춘다. 같은 승인번호가 품목마다 여러 줄이면 한 건으로 합친다.
- 결과
    일치            승인번호가 증빙에 있고 공급가액·세액이 같음
    금액 다름       승인번호는 있는데 공급가액·세액이 다름 (수정세금계산서 확인)
    증빙 없음       홈택스에는 있는데 증빙으로 등록 안 됨 → 같은 공급자의 가까운 날짜(±10일) 입고를 후보로 보여 줌
    홈택스에 없음   이 기간 증빙(전자세금계산서)인데 홈택스 목록에 없음 → 취소된 계산서·잘못 적은 승인번호 확인
- 국세청·ASP 에 직접 접속하지 않는다(인증서·계약 필요). 업로드한 파일은 저장하지 않고 감사로그에 건수만 남긴다.
"""
from __future__ import annotations

import io
import re
from datetime import date, timedelta

import pandas as pd

from core import db

COLS = {  # 표준 이름: 홈택스·회사 양식에서 쓰는 머리글들
    "approval_no": ["승인번호"],
    "write_date": ["작성일자", "작성일"],
    "supplier_biz_no": ["공급자사업자등록번호", "공급자 사업자등록번호", "공급자등록번호", "공급자사업자번호"],
    "supplier_name": ["상호", "공급자상호", "공급자 상호", "공급자명"],
    "supply": ["공급가액", "공급가액합계"],
    "tax": ["세액", "세액합계"],
    "total": ["합계금액", "합계"],
    "item": ["품목명"],
    "kind": ["전자세금계산서종류", "발급유형", "종류"],
}
RESULT = {"MATCH": "일치", "AMOUNT": "금액 다름", "MISSING": "증빙 없음", "EXTRA": "홈택스에 없음"}
NEAR_DAYS = 10


def _digits(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


def _num(v) -> float:
    try:
        return float(str(v).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def read(data: bytes, filename: str) -> tuple[pd.DataFrame, str]:
    """홈택스 매입 목록 → [approval_no, write_date, supplier_biz_no, supplier_name, supply, tax, total, item, kind]."""
    name = filename.lower()
    try:
        if name.endswith(".csv"):
            raw = None
            for enc in ("utf-8-sig", "cp949"):
                try:
                    raw = pd.read_csv(io.BytesIO(data), header=None, dtype=str, encoding=enc)
                    break
                except UnicodeDecodeError:
                    continue
            if raw is None:
                raise ValueError
        elif name.endswith(".xlsx"):
            raw = pd.read_excel(io.BytesIO(data), header=None, dtype=str)
        else:
            return pd.DataFrame(), "엑셀(.xlsx) 또는 CSV 파일을 올리세요 (홈택스 .xls 는 엑셀에서 .xlsx 로 다시 저장)."
    except Exception:
        return pd.DataFrame(), "파일을 읽을 수 없습니다. 홈택스에서 받은 목록을 .xlsx 로 저장해 올려 주세요."
    head = next((i for i in range(min(len(raw), 30)) if any(str(v).replace(" ", "") == "승인번호" for v in raw.iloc[i])), None)
    if head is None:
        return pd.DataFrame(), "'승인번호' 머리글을 찾지 못했습니다. 홈택스 매입 전자세금계산서 목록 파일인지 확인하세요."
    period = None                                         # 머리글 위 '조회기간 2026-09-01 ~ 2026-09-30' (있으면 그 기간으로 대사)
    for i in range(head):
        found = re.findall(r"(\d{4})[-./](\d{2})[-./](\d{2})", " ".join(str(v) for v in raw.iloc[i] if str(v) != "nan"))
        if len(found) >= 2:
            period = tuple("-".join(x) for x in found[:2])
    header = [str(v).replace(" ", "").strip() for v in raw.iloc[head]]
    body = raw.iloc[head + 1:].reset_index(drop=True)
    body.columns = header
    out = pd.DataFrame()
    for std, names in COLS.items():
        col = next((n.replace(" ", "") for n in names if n.replace(" ", "") in header), None)
        out[std] = body[col].fillna("").astype(str).str.strip() if col else ""
    missing = [COLS[k][0] for k in ("approval_no", "write_date", "supply", "tax") if not (out[k] != "").any()]
    if missing:
        return pd.DataFrame(), f"필요한 칸이 없습니다: {', '.join(missing)}"
    out["approval_no"] = out["approval_no"].map(_digits)
    out = out[out["approval_no"].str.len() >= 20]                 # 합계 줄·빈 줄 제외 (승인번호 24자리)
    for c in ("supply", "tax", "total"):
        out[c] = out[c].map(_num)
    from core.bulk import _excel_date
    out["write_date"] = out["write_date"].map(lambda v: (lambda d: d.date().isoformat() if pd.notna(d) else str(v)[:10])(
        _excel_date(str(v).replace(".", "-") if re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", str(v)) else v)))
    out["supplier_biz_no"] = out["supplier_biz_no"].map(_digits)
    # 품목마다 여러 줄 → 승인번호로 한 건. 줄마다 같은 금액(문서 합계를 되풀이)이면 한 번만, 다르면(품목 금액) 더한다
    def _amount(s):
        s = s[s != 0]
        return float(s.iloc[0]) if s.nunique() == 1 else float(s.sum())
    agg = out.groupby("approval_no", as_index=False).agg(
        write_date=("write_date", "first"), supplier_biz_no=("supplier_biz_no", "first"), supplier_name=("supplier_name", "first"),
        supply=("supply", _amount), tax=("tax", _amount), total=("total", _amount), item=("item", "first"), kind=("kind", "first"))
    agg.attrs["period"] = period
    return agg, ""


def reconcile(inv: pd.DataFrame, wh_ids=None, user_id: int | None = None) -> dict:
    """대사 결과 {rows: [...], counts: {...}, start, end}."""
    period = inv.attrs.get("period")
    start, end = period if period else (str(inv["write_date"].min())[:10], str(inv["write_date"].max())[:10])
    docs = db.query_df("""
        SELECT d.id, d.doc_type, d.issue_date, d.approval_no, d.supplier_biz_no, d.supplier_name, d.supply_amount, d.tax_amount,
               d.tx_id, t.warehouse_id, d.created_by_id
        FROM documents d LEFT JOIN transactions t ON t.id = d.tx_id""")
    if wh_ids is not None and len(docs):                 # 증빙 목록과 같은 규칙: 미연결 증빙은 본인이 올린 것만
        from core import repository as repo
        docs = docs[[repo.doc_visible(r, wh_ids, user_id) for r in docs.to_dict("records")]]
    docs["key"] = docs["approval_no"].map(_digits) if len(docs) else []
    by_no = {k: r for k, r in zip(docs["key"], docs.to_dict("records")) if k} if len(docs) else {}
    rows, used = [], set()
    for r in inv.to_dict("records"):
        d = by_no.get(r["approval_no"])
        base = {"approval_no": r["approval_no"], "write_date": r["write_date"], "supplier": r["supplier_name"],
                "biz_no": r["supplier_biz_no"], "supply": r["supply"], "tax": r["tax"], "item": r["item"],
                "doc_id": None, "doc_supply": None, "doc_tax": None, "candidate": ""}
        if d is not None:
            used.add(d["id"])
            same = abs(float(d["supply_amount"] or 0) - r["supply"]) < 1 and abs(float(d["tax_amount"] or 0) - r["tax"]) < 1
            rows.append({**base, "result": "MATCH" if same else "AMOUNT", "doc_id": int(d["id"]),
                         "doc_supply": float(d["supply_amount"] or 0), "doc_tax": float(d["tax_amount"] or 0)})
        else:
            rows.append({**base, "result": "MISSING", "candidate": _candidate(r, wh_ids)})
    # 이 기간의 전자세금계산서 증빙인데 홈택스 목록에 없는 것
    if len(docs):
        extra = docs[(docs["doc_type"] == "E_TAX_INVOICE") & ~docs["id"].isin(used)
                     & (docs["issue_date"] >= start) & (docs["issue_date"] <= end)]
        for d in extra.to_dict("records"):
            rows.append({"approval_no": d["key"] or "(없음)", "write_date": d["issue_date"], "supplier": d["supplier_name"],
                         "biz_no": d["supplier_biz_no"], "supply": None, "tax": None, "item": "", "result": "EXTRA",
                         "doc_id": int(d["id"]), "doc_supply": float(d["supply_amount"] or 0),
                         "doc_tax": float(d["tax_amount"] or 0), "candidate": ""})
    order = {"AMOUNT": 0, "MISSING": 1, "EXTRA": 2, "MATCH": 3}
    rows.sort(key=lambda x: (order[x["result"]], str(x["write_date"])))
    counts = {k: sum(1 for x in rows if x["result"] == k) for k in RESULT}
    return {"rows": rows, "counts": counts, "start": start, "end": end}


def _candidate(inv: dict, wh_ids) -> str:
    """증빙 없는 계산서의 후보 입고: 같은 공급자(사업자번호·이름) ±10일 입고 중 공급가액과 가장 가까운 날짜·문서."""
    try:
        d = date.fromisoformat(str(inv["write_date"])[:10])
    except ValueError:
        return ""
    from core import partners
    pid = None
    if inv["supplier_biz_no"]:
        pid = db.scalar("SELECT id FROM partners WHERE biz_no = ? AND active = 1", (inv["supplier_biz_no"],))
    names = {partners.key(inv["supplier_name"])} if inv["supplier_name"] else set()
    frag, wp = db.in_clause(None if wh_ids is None else (list(wh_ids) or [-1]))
    df = db.query_df(f"""
        SELECT t.tx_date, t.ref_no, t.partner, t.partner_id, SUM(t.qty * t.unit_price) AS amount, COUNT(*) AS lines
        FROM transactions t
        WHERE t.tx_type = 'IN' AND t.transfer_no = '' AND t.reversal_of IS NULL AND t.tx_date BETWEEN ? AND ?
              {'AND t.warehouse_id' + frag if frag else ''}
        GROUP BY t.tx_date, t.ref_no, t.partner, t.partner_id""",
                     ((d - timedelta(days=NEAR_DAYS)).isoformat(), (d + timedelta(days=NEAR_DAYS)).isoformat(), *wp))
    if df.empty:
        return ""
    hit = df[(df["partner_id"] == pid) if pid else df["partner"].map(partners.key).isin(names)]
    if hit.empty:
        return ""
    hit = hit.assign(gap=(hit["amount"] - float(inv["supply"])).abs()).sort_values("gap")
    b = hit.iloc[0]
    exact = " (금액 같음)" if b["gap"] < 1 else f" (차이 ₩{b['gap']:,.0f})"
    return f"{b['tx_date']} 입고 {b['ref_no'] or ''} {b['partner']} ₩{b['amount']:,.0f}{exact}".replace("  ", " ")


def export_df(result: dict) -> pd.DataFrame:
    return pd.DataFrame([{"결과": RESULT[r["result"]], "승인번호": r["approval_no"], "작성일자": r["write_date"],
                          "공급자": r["supplier"], "사업자번호": r["biz_no"], "공급가액(홈택스)": r["supply"],
                          "세액(홈택스)": r["tax"], "공급가액(증빙)": r["doc_supply"], "세액(증빙)": r["doc_tax"],
                          "증빙번호": r["doc_id"], "후보 입고": r["candidate"], "품목": r["item"]} for r in result["rows"]])
