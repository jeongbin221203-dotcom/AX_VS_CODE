"""회계연도·분기 — 회사 설정의 회계연도 시작 월 기준

  예) 시작 월 4 → 2026 회계연도 = 2026-04 ~ 2027-03, 1분기 = 4~6월
  목표는 월 단위로 저장하고(기존 화면·대시보드 그대로), 분기·연간 목표는 월로 나눠 넣는다.
  분기·연간 실적 = 취소를 뺀 매출의 공급가액 합계(담당자 데이터 범위 적용).
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd

from . import sales_db as db


def start_month() -> int:
    from . import company
    return int(company.get("fiscal_start_month") or 1)


def fy_of(ym: str) -> tuple[int, int]:
    """'2026-05' → (회계연도, 분기)."""
    y, m = int(ym[:4]), int(ym[5:7])
    s = start_month()
    fy = y if m >= s else y - 1
    offset = (m - s) % 12
    return fy, offset // 3 + 1


def months(fy: int, quarter: Optional[int] = None) -> list[str]:
    s = start_month()
    out = []
    for i in range(12):
        m = (s - 1 + i) % 12 + 1
        y = fy + (s - 1 + i) // 12
        out.append(f"{y:04d}-{m:02d}")
    if quarter:
        if quarter not in (1, 2, 3, 4):
            raise ValueError("분기는 1~4 입니다.")
        return out[(quarter - 1) * 3: quarter * 3]
    return out


def label(fy: int) -> str:
    ms = months(fy)
    return f"{fy} 회계연도 ({ms[0]} ~ {ms[-1]})" if start_month() != 1 else f"{fy}년"


def current_fy() -> int:
    return fy_of(date.today().strftime("%Y-%m"))[0]


def distribute(fy: int, quarter: Optional[int], owner_id: int, amount: int) -> list[tuple[str, int]]:
    """분기·연간 목표를 월로 똑같이 나눠 저장한다(나머지는 마지막 달). 이미 있던 그 기간 월 목표는 덮어쓴다."""
    amount = int(amount)
    if amount < 0:
        raise ValueError("목표는 0 이상이어야 합니다.")
    ms = months(fy, quarter)
    base = amount // len(ms)
    parts = [(ym, base) for ym in ms]
    parts[-1] = (ms[-1], amount - base * (len(ms) - 1))
    for ym, value in parts:
        db.upsert_target(ym, int(owner_id), value)
    db.audit("목표배분", "목표", None, {"회계연도": fy, "분기": quarter, "담당자ID": owner_id, "금액": amount})
    return parts


def summary(fy: int) -> pd.DataFrame:
    """담당자 × 분기 목표·실적 (데이터 범위 적용)."""
    ms = months(fy)
    marks = ",".join("?" * 12)
    sc, sp = db._scope_clause()
    targets = db._df(f"SELECT owner_id, owner, yyyymm, target_amount FROM targets WHERE yyyymm IN ({marks}){sc}",
                     [*ms, *sp])
    sales = db._df(f"SELECT owner_id, owner, substr(sale_date, 1, 7) AS ym, SUM(amount) AS amount FROM sales "
                   f"WHERE {db.ACTIVE_SALE} AND substr(sale_date, 1, 7) IN ({marks}){sc} "
                   f"GROUP BY owner_id, owner, substr(sale_date, 1, 7)", [*ms, *sp])
    q_of = {ym: i // 3 + 1 for i, ym in enumerate(ms)}
    rows: dict = {}
    for frame, value_col, month_col, kind in ((targets, "target_amount", "yyyymm", "목표"),
                                              (sales, "amount", "ym", "매출")):
        for r in frame.itertuples():
            key = int(r.owner_id) if pd.notna(r.owner_id) else str(r.owner)
            row = rows.setdefault(key, {"담당자": r.owner, **{f"Q{q} {k}": 0 for q in range(1, 5)
                                                          for k in ("목표", "매출")}})
            row[f"Q{q_of[getattr(r, month_col)]} {kind}"] += int(getattr(r, value_col) or 0)
    out = pd.DataFrame(list(rows.values()))
    if out.empty:
        return out
    out["연간 목표"] = sum(out[f"Q{q} 목표"] for q in range(1, 5))
    out["연간 매출"] = sum(out[f"Q{q} 매출"] for q in range(1, 5))
    out["달성률(%)"] = (out["연간 매출"] / out["연간 목표"].where(out["연간 목표"] > 0) * 100).round(1).fillna(0)
    total = {c: (out[c].sum() if c not in ("담당자", "달성률(%)") else "") for c in out.columns}
    total["담당자"] = "합계"
    total["달성률(%)"] = round(total["연간 매출"] / total["연간 목표"] * 100, 1) if total["연간 목표"] else 0
    return pd.concat([out.sort_values("담당자"), pd.DataFrame([total])], ignore_index=True)
