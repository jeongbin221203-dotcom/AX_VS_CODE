"""대시보드 지표. 모두 창고 범위(wh_ids)를 따른다.

- 금액은 거래 단가 × 수량(취소 거래는 수량 부호가 반대라 그대로 상쇄). 창고 간 이동은 입출고 실적에서 뺀다.
- 소진 예상: 최근 30일 출고(이동 제외)의 하루 평균으로 지금 재고가 며칠 버티는지.
- 장기 미사용: 재고는 있는데 90일 동안 출고(이동 제외)가 없는 자재.
- 입고 예정 발주: 잔량이 남은 발주 품목의 납기일(발주 납기일, 없으면 구매요청 필요일). 지나면 '지연'.
"""

from datetime import date, timedelta

import pandas as pd

from core import db, repository as repo


def _wh(col: str, wh_ids) -> tuple[str, list]:
    frag, wp = db.in_clause(wh_ids)
    return (f" AND {col}{frag}", wp) if frag else ("", [])


def month_list(months: int = 12, end_ym: str | None = None) -> list[str]:
    end = pd.Period(end_ym or date.today().strftime("%Y-%m"), "M")
    return [str(end - i) for i in range(months - 1, -1, -1)]


def prev_ym(ym: str) -> str:
    return str(pd.Period(ym, "M") - 1)


LABELS = (("IN", "입고금액"), ("OUT", "출고금액"), ("ADJ", "조정금액"))


def period_amounts(start: str, end: str, wh_ids=None) -> dict:
    """start~end(포함) 입고·출고·조정 금액과 건수. 조정은 부호 그대로(감모는 음수). 이동 제외, 취소는 상계."""
    wsql, wp = _wh("t.warehouse_id", wh_ids)
    df = db.query_df(f"""
        SELECT t.tx_type, SUM(t.qty * t.unit_price) AS amount,
               SUM(CASE WHEN t.reversal_of IS NULL AND NOT EXISTS
                        (SELECT 1 FROM transactions r WHERE r.reversal_of = t.id) THEN 1 ELSE 0 END) AS cnt
        FROM transactions t
        WHERE t.transfer_no = '' AND t.tx_date >= ? AND t.tx_date <= ?{wsql}
        GROUP BY t.tx_type
        """, (start, end, *wp))
    out = {label: 0.0 for _, label in LABELS} | {code: 0 for code, _ in LABELS}
    for r in df.itertuples():
        label = dict(LABELS).get(r.tx_type)
        if label:
            out[label] = float(r.amount or 0)
            out[r.tx_type] = int(r.cnt or 0)
    return out


def month_to_date(ym: str, wh_ids=None) -> tuple[dict, dict, bool]:
    """(기준월, 비교할 전월, 같은 기간 비교 여부). 이번 달이면 전월도 같은 날짜까지만 비교한다
    (월초에 '전월 대비 -100%'처럼 보이는 것을 막는다)."""
    p = pd.Period(ym, "M")
    today = date.today()
    current = ym == today.strftime("%Y-%m")
    end_day = today.day if current else p.days_in_month
    prev = p - 1
    cur = period_amounts(f"{ym}-01", f"{ym}-{end_day:02d}", wh_ids)
    before = period_amounts(f"{prev}-01", f"{prev}-{min(end_day, prev.days_in_month):02d}", wh_ids)
    return cur, before, current


def daily_amounts(days: int = 30, wh_ids=None) -> pd.DataFrame:
    """최근 days일 일별 입고·출고·조정 금액 (단위가 다른 자재를 수량으로 더하지 않도록 금액으로)."""
    since = date.today() - timedelta(days=days - 1)
    index = [(since + timedelta(days=i)).isoformat() for i in range(days)]
    wsql, wp = _wh("t.warehouse_id", wh_ids)
    df = db.query_df(f"""
        SELECT t.tx_date, t.tx_type, SUM(t.qty * t.unit_price) AS amount FROM transactions t
        WHERE t.transfer_no = '' AND t.tx_date >= ?{wsql}
        GROUP BY t.tx_date, t.tx_type
        """, (index[0], *wp))
    out = pd.DataFrame({"일자": index})
    for code, label in LABELS:
        part = df[df["tx_type"] == code].set_index("tx_date")["amount"] if not df.empty else pd.Series(dtype=float)
        out[label] = out["일자"].map(part).fillna(0.0).astype(float)
    return out


def monthly_amounts(months: int = 12, wh_ids=None, end_ym: str | None = None) -> pd.DataFrame:
    """월별 입고·출고·조정 금액 (데이터 없는 달은 0)."""
    yms = month_list(months, end_ym)
    wsql, wp = _wh("t.warehouse_id", wh_ids)
    df = db.query_df(f"""
        SELECT substr(t.tx_date, 1, 7) AS ym, t.tx_type, SUM(t.qty * t.unit_price) AS amount, COUNT(*) AS cnt
        FROM transactions t
        WHERE t.transfer_no = '' AND t.tx_date >= ? AND t.tx_date < ?{wsql}
        GROUP BY substr(t.tx_date, 1, 7), t.tx_type
        """, (f"{yms[0]}-01", f"{pd.Period(yms[-1], 'M') + 1}-01", *wp))
    out = pd.DataFrame({"월": yms})
    for code, label in LABELS:
        part = df[df["tx_type"] == code].set_index("ym")["amount"] if not df.empty else pd.Series(dtype=float)
        out[label] = out["월"].map(part).fillna(0.0).astype(float)
    return out


def warehouse_values(wh_ids=None) -> pd.DataFrame:
    """창고별 재고금액·자재 종수."""
    df = repo.stock_by_wh(wh_ids=wh_ids)
    df = df[df["stock"].abs() > 1e-9]
    if df.empty:
        return pd.DataFrame(columns=["창고", "창고명", "자재 종수", "재고금액"])
    out = (df.groupby(["wh_code", "wh_name"], as_index=False)
             .agg(**{"자재 종수": ("material_id", "nunique"), "재고금액": ("stock_value", "sum")})
             .rename(columns={"wh_code": "창고", "wh_name": "창고명"}))
    return out.sort_values("재고금액", ascending=False).reset_index(drop=True)


def top_issues(ym: str = "", wh_ids=None, limit: int = 10) -> pd.DataFrame:
    """출고 금액 상위 자재 (ym이 비면 전체 기간)."""
    wsql, wp = _wh("t.warehouse_id", wh_ids)
    params: list = []
    msql = ""
    if ym:
        msql = " AND substr(t.tx_date, 1, 7) = ?"
        params.append(ym)
    df = db.query_df(f"""
        SELECT m.code AS 자재코드, m.name AS 자재명, SUM(t.qty) AS 출고수량, m.unit AS 단위,
               SUM(t.qty * t.unit_price) AS 출고금액
        FROM transactions t JOIN materials m ON m.id = t.material_id
        WHERE t.tx_type = 'OUT' AND t.transfer_no = ''{msql}{wsql}
        GROUP BY m.id, m.code, m.name, m.unit
        HAVING SUM(t.qty) > 0
        ORDER BY 출고금액 DESC, 출고수량 DESC
        LIMIT ?
        """, (*params, *wp, int(limit)))
    return df


def _daily_out(days: int, wh_ids) -> pd.Series:
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    wsql, wp = _wh("t.warehouse_id", wh_ids)
    df = db.query_df(f"""
        SELECT t.material_id, SUM(t.qty) AS q FROM transactions t
        WHERE t.tx_type = 'OUT' AND t.transfer_no = '' AND t.tx_date >= ?{wsql}
        GROUP BY t.material_id
        """, (since, *wp))
    return (df.set_index("material_id")["q"] / days) if not df.empty else pd.Series(dtype=float)


def stock_cover(stock: pd.DataFrame, wh_ids=None, horizon: int = 14, window: int = 30) -> pd.DataFrame:
    """지금 재고로 버틸 수 있는 날 수가 horizon 이하인 자재 (최근 window일 하루 평균 출고 기준)."""
    daily = _daily_out(window, wh_ids)
    if daily.empty or stock.empty:
        return pd.DataFrame(columns=["자재코드", "자재명", "현재고", "단위", "하루 평균 출고", "남은 일수", "소진 예상일"])
    df = stock.assign(daily=stock["id"].map(daily).fillna(0.0))
    df = df[df["daily"] > 1e-9].copy()
    df["days_left"] = (df["stock"].clip(lower=0) / df["daily"]).round(1)
    df = df[df["days_left"] <= horizon].sort_values("days_left")
    df["until"] = [(date.today() + timedelta(days=int(d))).isoformat() for d in df["days_left"]]
    return (df[["code", "name", "stock", "unit", "daily", "days_left", "until"]]
            .rename(columns={"code": "자재코드", "name": "자재명", "stock": "현재고", "unit": "단위",
                             "daily": "하루 평균 출고", "days_left": "남은 일수", "until": "소진 예상일"})
            .reset_index(drop=True))


def dead_stock(stock: pd.DataFrame, wh_ids=None, days: int = 90) -> pd.DataFrame:
    """재고는 있는데 days일 동안 출고가 없는 자재 (재고금액 큰 순)."""
    wsql, wp = _wh("t.warehouse_id", wh_ids)
    last = db.query_df(f"""
        SELECT t.material_id, MAX(t.tx_date) AS last_out FROM transactions t
        WHERE t.tx_type = 'OUT' AND t.transfer_no = '' AND t.reversal_of IS NULL{wsql}
        GROUP BY t.material_id
        """, wp)
    last_map = last.set_index("material_id")["last_out"] if not last.empty else pd.Series(dtype=object)
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    # 출고가 한 건도 없으면 map 결과가 숫자형(NaN)이 되어 날짜 글자와 비교할 수 없다 → 글자로 맞춘다 ("" = 출고 없음)
    df = stock[stock["stock"] > 1e-9].assign(last_out=lambda d: d["id"].map(last_map).astype(object))
    df = df[df["last_out"].fillna("").astype(str) < cutoff].sort_values("stock_value", ascending=False)
    df["last_out"] = df["last_out"].fillna("출고 이력 없음")
    return (df[["code", "name", "stock", "unit", "stock_value", "last_out"]]
            .rename(columns={"code": "자재코드", "name": "자재명", "stock": "현재고", "unit": "단위",
                             "stock_value": "재고금액", "last_out": "마지막 출고일"})
            .reset_index(drop=True))


def incoming_po(wh_ids=None, days: int = 14) -> pd.DataFrame:
    """잔량이 남은 발주 품목 중 납기일이 days일 안이거나 지난 것."""
    wsql, wp = _wh("o.warehouse_id", wh_ids)
    limit = (date.today() + timedelta(days=days)).isoformat()
    df = db.query_df(f"""
        SELECT COALESCE(NULLIF(o.delivery_date, ''), r.need_date) AS need_date, o.po_no, i.line_no, m.code, m.name, w.code AS wh_code, o.supplier, i.qty,
               COALESCE((SELECT SUM(t.qty) FROM transactions t WHERE t.tx_type = 'IN' AND t.po_no = o.po_no
                         AND t.po_item = CAST(i.line_no AS TEXT) AND t.transfer_no = ''), 0) AS received
        FROM purchase_orders o JOIN po_items i ON i.po_id = o.id
        JOIN purchase_requests r ON r.id = o.pr_id
        JOIN materials m ON m.id = i.material_id JOIN warehouses w ON w.id = o.warehouse_id
        WHERE o.status IN ('PENDING_APPROVAL', 'OPEN', 'PARTIAL') AND COALESCE(NULLIF(o.delivery_date, ''), r.need_date) <> '' AND COALESCE(NULLIF(o.delivery_date, ''), r.need_date) <= ?{wsql}
        ORDER BY 1, o.po_no, i.line_no
        """, (limit, *wp))
    if df.empty:
        return pd.DataFrame(columns=["납기일", "상태", "발주번호", "품목", "자재코드", "자재명", "창고", "공급처", "잔량"])
    df["remaining"] = df["qty"] - df["received"]
    df = df[df["remaining"] > 1e-9]
    today = date.today().isoformat()
    df["state"] = ["지연" if d < today else "예정" for d in df["need_date"]]
    return (df[["need_date", "state", "po_no", "line_no", "code", "name", "wh_code", "supplier", "remaining"]]
            .rename(columns={"need_date": "납기일", "state": "상태", "po_no": "발주번호", "line_no": "품목",
                             "code": "자재코드", "name": "자재명", "wh_code": "창고", "supplier": "공급처",
                             "remaining": "잔량"})
            .reset_index(drop=True))
