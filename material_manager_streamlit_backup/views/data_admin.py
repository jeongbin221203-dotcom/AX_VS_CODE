"""데이터 관리 화면 (업로드 / 백업 / 샘플)."""

from datetime import datetime

import pandas as pd
import streamlit as st

import config
from core import repository as repo, seed, services
from core.utils import to_excel_bytes

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def render() -> None:
    st.title("🛠️ 데이터 관리")
    _render_upload()
    st.divider()
    _render_backup()
    st.divider()
    _render_seed()


def _render_upload() -> None:
    st.subheader("1. 자재 일괄 업로드")
    template = pd.DataFrame(
        [["PKG-100", "샘플 자재", "규격", "EA", "포장재", 10, 1000, "A-10", "공급처명"]],
        columns=list(config.MATERIAL_COLS.values()),
    )
    st.download_button("⬇️ 업로드 양식 다운로드",
                       data=to_excel_bytes({"자재마스터": template}),
                       file_name="자재마스터_업로드양식.xlsx", mime=XLSX_MIME)
    st.caption("자재코드가 이미 있으면 나머지 항목을 갱신하고, 없으면 신규 등록합니다. "
               "(재고는 입출고로만 변경됩니다)")

    uploaded = st.file_uploader("엑셀(.xlsx) 또는 CSV 업로드", type=["xlsx", "csv"])
    if uploaded is None:
        return

    try:
        raw = (pd.read_csv(uploaded) if uploaded.name.lower().endswith(".csv")
               else pd.read_excel(uploaded))
    except Exception as exc:
        st.error(f"파일을 읽을 수 없습니다: {exc}")
        return

    result = services.normalize_upload(raw)
    for err in result.errors:
        st.error(err)
    if result.errors:
        return
    if result.dropped:
        st.warning(f"자재코드 또는 자재명이 비어 있는 {result.dropped}행을 제외했습니다.")
    if result.duplicated:
        st.info(f"중복된 자재코드 {result.duplicated}행은 마지막 값만 반영합니다.")
    if result.df.empty:
        st.error("반영할 유효한 행이 없습니다.")
        return

    st.dataframe(result.df.rename(columns=config.MATERIAL_COLS),
                 width="stretch", hide_index=True)
    if st.button(f"{len(result.df)}건 반영", type="primary"):
        repo.upsert_materials(list(result.df.itertuples(index=False, name=None)))
        st.success(f"{len(result.df)}건 반영 완료")


def _render_backup() -> None:
    st.subheader("2. 백업")
    col1, col2 = st.columns(2)
    with col1:
        if config.DB_PATH.exists():
            st.download_button("⬇️ DB 파일 백업 (.db)", data=config.DB_PATH.read_bytes(),
                               file_name=f"materials_backup_{datetime.now():%Y%m%d_%H%M}.db",
                               mime="application/octet-stream")
    with col2:
        st.download_button("⬇️ 전체 데이터 엑셀 백업",
                           data=to_excel_bytes(repo.dump_all()),
                           file_name=f"자재관리_전체백업_{datetime.now():%Y%m%d_%H%M}.xlsx",
                           mime=XLSX_MIME)


def _render_seed() -> None:
    st.subheader("3. 샘플 데이터")
    count = repo.count_materials()
    if count:
        st.caption(f"현재 자재 {count}종이 등록되어 있어 샘플 생성은 빈 DB에서만 가능합니다.")
    elif st.button("샘플 데이터 생성 (포워딩 창고 포장·고박 자재)"):
        seed.seed()
        st.success("샘플 데이터가 생성되었습니다.")
        st.rerun()
