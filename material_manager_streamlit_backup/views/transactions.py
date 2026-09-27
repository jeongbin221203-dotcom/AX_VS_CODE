"""입출고 등록 화면."""

from datetime import date

import streamlit as st

import config
from core import db, repository as repo, services


def render() -> None:
    st.title("🔄 입출고 등록")

    opts = services.material_options(active_only=True)
    if not opts:
        st.info("사용 중인 자재가 없습니다. 먼저 자재를 등록하세요.")
        return

    tx_type = st.radio("구분", options=list(config.TX_LABEL),
                       format_func=config.TX_LABEL.get, horizontal=True)
    mid = st.selectbox("자재", options=list(opts), format_func=opts.get)

    mat = repo.get_material(mid)
    with db.get_conn() as conn:
        stock_now = repo.current_stock(conn, mid)

    c1, c2, c3 = st.columns(3)
    c1.metric("현재고", f"{stock_now:,.2f} {mat['unit']}")
    c2.metric("안전재고", f"{mat['safety_stock']:,.2f} {mat['unit']}")
    c3.metric("기준단가", f"₩ {mat['unit_price']:,.0f}")

    with st.form("tx_form", clear_on_submit=True):
        f1, f2, f3 = st.columns(3)
        if tx_type == "ADJ":
            qty_input = f1.number_input("실사수량 *", min_value=0.0, step=1.0,
                                        value=float(max(stock_now, 0)),
                                        help="창고에서 직접 센 수량을 입력하세요. 차이는 자동 계산됩니다.")
        else:
            qty_input = f1.number_input("수량 *", min_value=0.0, step=1.0)
        tx_date = f2.date_input("일자", value=date.today())
        price = f3.number_input("단가(원)", min_value=0.0, step=100.0,
                                value=float(mat["unit_price"]))

        f4, f5, f6 = st.columns(3)
        ref_no = f4.text_input("문서번호 (PO/출고요청/B/L 등)")
        partner = f5.text_input("거래처 / 사용부서")
        created_by = f6.text_input("담당자")
        note = st.text_input("비고")
        submitted = st.form_submit_button("등록", type="primary")

    if not submitted:
        return

    result = services.register_transaction(
        material_id=mid, tx_type=tx_type, qty_input=qty_input,
        tx_date=tx_date.isoformat(), unit_price=price,
        ref_no=ref_no, partner=partner, note=note, created_by=created_by,
    )
    if not result.ok:
        st.error(result.message)
        return

    st.toast(f"{config.TX_LABEL[tx_type]} 등록 완료")
    st.success(result.message)
    if result.warning:
        st.warning(result.warning)
