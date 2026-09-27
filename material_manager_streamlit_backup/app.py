"""자재관리 시스템 진입점.

실행:
    pip install -r requirements.txt
    streamlit run app.py
"""

import streamlit as st

import config
from core import db, repository as repo
from views import dashboard, data_admin, history, materials, stock, transactions

st.set_page_config(page_title=config.APP_TITLE, page_icon=config.APP_ICON, layout="wide")

PAGES = {
    "📊 대시보드": dashboard.render,
    "🗂️ 자재 마스터": materials.render,
    "🔄 입출고 등록": transactions.render,
    "📦 재고 현황": stock.render,
    "🧾 거래 이력": history.render,
    "🛠️ 데이터 관리": data_admin.render,
}


def main() -> None:
    db.init_db()

    with st.sidebar:
        st.header(f"{config.APP_ICON} 자재관리")
        choice = st.radio("메뉴", list(PAGES), label_visibility="collapsed")

        shortage = repo.stock_df()
        shortage_cnt = int(shortage["shortage"].sum()) if not shortage.empty else 0
        if shortage_cnt:
            st.error(f"안전재고 미달 {shortage_cnt}종")
        st.caption(f"DB: {config.DB_PATH.name}")

    PAGES[choice]()


if __name__ == "__main__":
    main()
