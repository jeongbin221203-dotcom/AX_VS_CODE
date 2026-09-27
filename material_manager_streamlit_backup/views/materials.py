"""자재 마스터 화면."""

import sqlite3

import streamlit as st

import config
from core import db, repository as repo, services


def _form_fields(defaults: dict | None = None, key: str = "") -> dict:
    d = defaults or {}
    c1, c2, c3 = st.columns(3)
    code = c1.text_input("자재코드 *", value=d.get("code", ""), key=f"code{key}",
                         disabled=bool(defaults))
    name = c2.text_input("자재명 *", value=d.get("name", ""), key=f"name{key}")
    spec = c3.text_input("규격", value=d.get("spec") or "", key=f"spec{key}")

    c4, c5, c6 = st.columns(3)
    unit = c4.text_input("단위", value=d.get("unit") or config.DEFAULT_UNIT, key=f"unit{key}")
    category = c5.text_input("분류", value=d.get("category") or config.DEFAULT_CATEGORY, key=f"cat{key}")
    location = c6.text_input("보관위치", value=d.get("location") or "", key=f"loc{key}")

    c7, c8, c9 = st.columns(3)
    safety_stock = c7.number_input("안전재고", min_value=0.0, step=1.0,
                                   value=float(d.get("safety_stock") or 0), key=f"ss{key}")
    unit_price = c8.number_input("단가(원)", min_value=0.0, step=100.0,
                                 value=float(d.get("unit_price") or 0), key=f"up{key}")
    supplier = c9.text_input("공급처", value=d.get("supplier") or "", key=f"sup{key}")

    return {
        "code": code.strip().upper(), "name": name.strip(), "spec": spec.strip(),
        "unit": unit.strip() or config.DEFAULT_UNIT,
        "category": category.strip() or config.DEFAULT_CATEGORY,
        "safety_stock": safety_stock, "unit_price": unit_price,
        "location": location.strip(), "supplier": supplier.strip(),
    }


def render() -> None:
    st.title("🗂️ 자재 마스터")
    tab_list, tab_new, tab_edit = st.tabs(["목록", "신규 등록", "수정 / 사용중지"])

    with tab_list:
        _render_list()
    with tab_new:
        _render_new()
    with tab_edit:
        _render_edit()


def _render_list() -> None:
    show_inactive = st.toggle("사용중지 자재 포함", value=False)
    df = repo.stock_df(include_inactive=show_inactive)
    if df.empty:
        st.info("등록된 자재가 없습니다.")
        return
    view = df[["code", "name", "spec", "unit", "category", "safety_stock",
               "unit_price", "location", "supplier", "stock", "active"]].copy()
    view["active"] = view["active"].map({1: "사용", 0: "중지"})
    view.columns = ["자재코드", "자재명", "규격", "단위", "분류", "안전재고",
                    "단가", "보관위치", "공급처", "현재고", "상태"]
    st.dataframe(view, width="stretch", hide_index=True)


def _render_new() -> None:
    with st.form("new_material", clear_on_submit=True):
        data = _form_fields(key="_new")
        submitted = st.form_submit_button("등록", type="primary")
    if not submitted:
        return
    if not data["code"] or not data["name"]:
        st.error("자재코드와 자재명은 필수입니다.")
        return
    try:
        repo.insert_material(data)
        st.success(f"자재 [{data['code']}] {data['name']} 등록 완료")
    except sqlite3.IntegrityError:
        st.error(f"자재코드 '{data['code']}'는 이미 존재합니다.")


def _render_edit() -> None:
    opts = services.material_options(active_only=False)
    if not opts:
        st.info("등록된 자재가 없습니다.")
        return

    mid = st.selectbox("수정할 자재", options=list(opts), format_func=opts.get, key="edit_sel")
    row = repo.get_material(mid)
    if row is None:
        st.error("자재를 찾을 수 없습니다.")
        return

    with st.form(f"edit_material_{mid}"):
        data = _form_fields(defaults=row, key=f"_edit_{mid}")
        save = st.form_submit_button("저장", type="primary")
    if save:
        if not data["name"]:
            st.error("자재명은 필수입니다.")
        else:
            repo.update_material(mid, data)
            st.success("수정되었습니다.")
            st.rerun()

    st.divider()
    with db.get_conn() as conn:
        stock_now = repo.current_stock(conn, mid)
    is_active = int(row["active"]) == 1
    st.write(f"상태: **{'사용' if is_active else '사용중지'}** · "
             f"현재고: **{stock_now:,.2f} {row['unit']}**")

    if is_active:
        if stock_now != 0:
            st.warning("재고가 남아 있는 자재입니다. 사용중지해도 이력은 유지되지만 "
                       "입출고 등록 목록에서 제외됩니다.")
        if st.button("사용중지", key=f"deact_{mid}"):
            repo.set_material_active(mid, False)
            st.rerun()
    elif st.button("다시 사용", key=f"react_{mid}", type="primary"):
        repo.set_material_active(mid, True)
        st.rerun()
