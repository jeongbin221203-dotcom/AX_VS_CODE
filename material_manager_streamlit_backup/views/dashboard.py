"""대시보드 화면."""

from datetime import date, timedelta

import pandas as pd
import streamlit as st

import config
from core import repository as repo, services


def render() -> None:
    st.title("📊 대시보드")

    stock = repo.stock_df()
    if stock.empty:
        st.info("등록된 자재가 없습니다. '데이터 관리'에서 샘플 데이터를 생성하거나 자재를 등록하세요.")
        return

    shortage = stock[stock["shortage"]]
    month_tx = repo.monthly_tx_count(date.today().replace(day=1).isoformat())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("사용 중 자재", f"{len(stock):,} 종")
    c2.metric("총 재고금액", f"₩ {stock['stock_value'].sum():,.0f}")
    c3.metric("안전재고 미달", f"{len(shortage):,} 종",
              delta=f"-{len(shortage)}" if len(shortage) else None, delta_color="inverse")
    c4.metric("이번 달 입고 / 출고", f"{month_tx.get('IN', 0)} / {month_tx.get('OUT', 0)} 건")

    left, right = st.columns([3, 2])

    with left:
        st.subheader("최근 30일 입출고 수량")
        since = (date.today() - timedelta(days=29)).isoformat()
        trend = repo.trend_df(since)
        if trend.empty:
            st.caption("최근 30일 입출고 내역이 없습니다.")
        else:
            days = pd.date_range(since, date.today()).strftime("%Y-%m-%d")
            pivot = (trend.pivot(index="tx_date", columns="tx_type", values="qty")
                     .reindex(days).fillna(0).rename(columns=config.TX_LABEL))
            st.bar_chart(pivot)

    with right:
        st.subheader("분류별 재고금액")
        st.bar_chart(stock.groupby("category")["stock_value"].sum().sort_values(ascending=False))

    st.subheader("🚨 안전재고 미달 자재")
    if shortage.empty:
        st.success("안전재고 미달 자재가 없습니다.")
    else:
        st.dataframe(
            services.stock_display(shortage.sort_values("shortage_qty", ascending=False)),
            width="stretch", hide_index=True,
        )
