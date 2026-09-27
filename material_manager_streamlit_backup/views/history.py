"""거래 이력 화면."""

from datetime import date, timedelta

import streamlit as st

import config
from core import repository as repo, services
from core.utils import to_excel_bytes

RENAME = {"id": "ID", "tx_date": "일자", "tx_type": "구분", "code": "자재코드", "name": "자재명",
          "unit": "단위", "qty": "수량", "unit_price": "단가", "amount": "금액",
          "ref_no": "문서번호", "partner": "거래처/부서", "created_by": "담당자",
          "note": "비고", "created_at": "등록일시"}


def render() -> None:
    st.title("🧾 거래 이력")

    c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
    start = c1.date_input("시작일", value=date.today() - timedelta(days=30))
    end = c2.date_input("종료일", value=date.today())
    types = c3.multiselect("구분", list(config.TX_LABEL), default=list(config.TX_LABEL),
                           format_func=config.TX_LABEL.get)
    opts = services.material_options(active_only=False)
    mats = c4.multiselect("자재", options=list(opts), format_func=opts.get)

    if start > end:
        st.error("시작일이 종료일보다 늦습니다.")
        return
    if not types:
        st.info("구분을 하나 이상 선택하세요.")
        return

    df = repo.history_df(start.isoformat(), end.isoformat(), types, mats)
    if df.empty:
        st.info("조회된 거래가 없습니다.")
        return

    df["tx_type"] = df["tx_type"].map(config.TX_LABEL)
    df["amount"] = df["amount"].abs()

    s1, s2, s3 = st.columns(3)
    s1.metric("입고 수량 합", f"{df.loc[df['tx_type'] == '입고', 'qty'].sum():,.2f}")
    s2.metric("출고 수량 합", f"{df.loc[df['tx_type'] == '출고', 'qty'].sum():,.2f}")
    s3.metric("거래 건수", f"{len(df):,} 건")

    view = df.rename(columns=RENAME)
    st.dataframe(view, width="stretch", hide_index=True)
    st.download_button(
        "⬇️ 거래이력 엑셀 다운로드",
        data=to_excel_bytes({"거래이력": view}),
        file_name=f"거래이력_{start:%Y%m%d}_{end:%Y%m%d}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    with st.expander("거래 삭제 (잘못 등록한 건 취소)"):
        tx_id = st.selectbox("삭제할 거래 ID", options=df["id"].tolist())
        confirm = st.checkbox("삭제하면 되돌릴 수 없음을 확인했습니다.")
        if st.button("삭제", type="primary", disabled=not confirm):
            result = services.delete_transaction(int(tx_id))
            if result.ok:
                st.success(result.message)
                st.rerun()
            else:
                st.error(result.message)
