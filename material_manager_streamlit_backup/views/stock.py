"""재고 현황 화면."""

from datetime import date

import streamlit as st

from core import repository as repo, services
from core.utils import to_excel_bytes

HIGHLIGHT = "background-color: rgba(255, 75, 75, 0.18)"


def render() -> None:
    st.title("📦 재고 현황")

    df = repo.stock_df()
    if df.empty:
        st.info("등록된 자재가 없습니다.")
        return

    c1, c2, c3 = st.columns([2, 2, 1])
    keyword = c1.text_input("검색 (코드 / 자재명 / 규격 / 위치)")
    selected_cats = c2.multiselect("분류", sorted(df["category"].dropna().unique()))
    only_shortage = c3.checkbox("미달만 보기")

    filtered = df
    if keyword:
        kw = keyword.lower()
        mask = (
            filtered["code"].str.lower().str.contains(kw, regex=False)
            | filtered["name"].str.lower().str.contains(kw, regex=False)
            | filtered["spec"].fillna("").str.lower().str.contains(kw, regex=False)
            | filtered["location"].fillna("").str.lower().str.contains(kw, regex=False)
        )
        filtered = filtered[mask]
    if selected_cats:
        filtered = filtered[filtered["category"].isin(selected_cats)]
    if only_shortage:
        filtered = filtered[filtered["shortage"]]

    m1, m2, m3 = st.columns(3)
    m1.metric("조회 품목", f"{len(filtered):,} 종")
    m2.metric("재고금액 합계", f"₩ {filtered['stock_value'].sum():,.0f}")
    m3.metric("미달 품목", f"{int(filtered['shortage'].sum()):,} 종")

    view = services.stock_display(filtered)
    styled = (view.style
              .apply(lambda r: [HIGHLIGHT if r["부족수량"] > 0 else ""] * len(r), axis=1)
              .format({"현재고": "{:,.2f}", "안전재고": "{:,.2f}", "부족수량": "{:,.2f}",
                       "단가": "₩{:,.0f}", "재고금액": "₩{:,.0f}"}))
    st.dataframe(styled, width="stretch", hide_index=True)

    st.download_button(
        "⬇️ 재고현황 엑셀 다운로드",
        data=to_excel_bytes({"재고현황": view}),
        file_name=f"재고현황_{date.today():%Y%m%d}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
