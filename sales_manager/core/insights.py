"""대시보드 추가 지표 (자재관리 대시보드와 맞춘 항목). 모두 로그인 사용자의 접근 범위와 담당자 필터를 따른다.

- 최근 30일 일별 매출: 월 단위 추이만으로는 보이지 않는 이번 달 흐름 (취소 매출 제외)
- 품목군별 매출: 매출에 연결된 품목의 분류(products.category). 품목 없이 입력한 매출은 '품목 미지정'
- 견적 만료 임박: 발송했지만 아직 답이 없는 견적 중 유효기한이 N일 안에 끝나는 것
"""
from __future__ import annotations

from datetime import timedelta

import pandas as pd

from core import sales_db as db


def daily_sales(days: int = 30, owner_id: int | None = None) -> pd.DataFrame:
    since = db.TODAY() - timedelta(days=days - 1)
    index = [(since + timedelta(days=i)).isoformat() for i in range(days)]
    oc, op = db._owner_clause(owner_id)
    df = db._df(f"SELECT sale_date AS 일자, SUM(amount) AS 매출, COUNT(*) AS 건수 FROM sales "
                f"WHERE {db.ACTIVE_SALE} AND sale_date >= ?{oc} GROUP BY sale_date",
                [index[0], *op])
    out = pd.DataFrame({"일자": index}).merge(df, on="일자", how="left").fillna(0)
    out[["매출", "건수"]] = out[["매출", "건수"]].astype("int64")
    out["일자"] = out["일자"].str[5:]
    return out


def category_sales(yyyymm: str, owner_id: int | None = None) -> pd.DataFrame:
    oc, op = db._owner_clause(owner_id, "s")
    df = db._df(f"SELECT COALESCE(NULLIF(p.category, ''), '품목 미지정') AS 품목군, SUM(s.amount) AS 매출, "
                f"COUNT(*) AS 건수 FROM sales s LEFT JOIN products p ON p.id = s.product_id "
                f"WHERE s.status <> '취소' AND substr(s.sale_date, 1, 7) = ?{oc} "
                f"GROUP BY 1 ORDER BY 매출 DESC", [yyyymm, *op])
    if not df.empty:
        df[["매출", "건수"]] = df[["매출", "건수"]].astype("int64")
    return df


def quotes_expiring(days: int = 7, owner_id: int | None = None) -> pd.DataFrame:
    today = db.TODAY().isoformat()
    until = (db.TODAY() + timedelta(days=days)).isoformat()
    oc, op = db._owner_clause(owner_id, "q")
    df = db._df(f"SELECT q.valid_until AS 유효기한, q.quote_no AS 견적번호, q.revision AS 판, c.name AS 거래처, "
                f"q.title AS 건명, q.total_amount AS 합계, q.owner AS 담당자 "
                f"FROM quotes q JOIN customers c ON c.id = q.customer_id "
                f"WHERE q.status = '발송' AND q.valid_until IS NOT NULL AND q.valid_until >= ? AND q.valid_until <= ?{oc} "
                f"ORDER BY q.valid_until", [today, until, *op])
    if not df.empty:
        df.insert(1, "남은 일수", [(pd.Timestamp(v) - pd.Timestamp(today)).days for v in df["유효기한"]])
    return df
