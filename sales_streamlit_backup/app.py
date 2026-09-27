"""영업관리 시스템 (Sales CRM) - Streamlit UI

실행:  streamlit run app.py
구성:  app.py(화면) + sales_db.py(데이터 계층) + sales.db(SQLite, 자동 생성)
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

import auth
import dataio
import enterprise as ent
import sales_db as db

# ----------------------------------------------------------------------------
# 기본 설정
# ----------------------------------------------------------------------------
st.set_page_config(page_title="영업관리 시스템", page_icon="📈", layout="wide")

def _st_version() -> tuple[int, int]:
    """설치된 Streamlit 버전을 (major, minor) 로 반환한다. 파싱 실패 시 (1, 32)."""
    try:
        parts = str(st.__version__).split(".")
        return int(parts[0]), int(parts[1])
    except (AttributeError, IndexError, ValueError):
        return (1, 32)


ST_VER = _st_version()
# 1.49+ 에서 use_container_width 가 width="stretch" 로 대체됨
WIDTH_KW: dict = {"width": "stretch"} if ST_VER >= (1, 49) else {"use_container_width": True}


def table_kwargs(height: int | None = None) -> dict:
    """st.dataframe / st.data_editor 공통 인자.

    1.5x 부터 height=None 이 거부되므로 값이 있을 때만 전달한다.
    """
    kwargs = dict(WIDTH_KW)
    kwargs["hide_index"] = True
    if height is not None:
        kwargs["height"] = int(height)
    return kwargs


CUSTOM_CSS = """
<style>
    .block-container {padding-top: 2rem; padding-bottom: 3rem;}
    [data-testid="stMetricValue"] {font-size: 1.6rem;}
    [data-testid="stMetricLabel"] {opacity: .75;}
    div[data-testid="stDataFrame"] {border-radius: 8px;}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

M_DASH, M_FORECAST, M_ANALYTICS = "📊 대시보드", "🔮 매출예측", "📈 파이프라인 분석"
M_CUST, M_DEALS, M_ACT = "🏢 거래처", "💼 영업기회", "📞 영업활동"
M_SALES, M_TARGET, M_APPROVAL = "💰 매출·채권", "🎯 목표", "✅ 결재함"
M_IO = "📥 데이터 등록·추출"
M_ORG, M_AUDIT, M_ADMIN = "👥 조직·사용자", "🗂️ 감사로그", "⚙️ 데이터 관리"

MENUS = [M_DASH, M_FORECAST, M_ANALYTICS, M_CUST, M_DEALS, M_ACT,
         M_SALES, M_TARGET, M_APPROVAL, M_IO, M_ORG, M_AUDIT, M_ADMIN]

# 메뉴별 최소 권한 (명시하지 않으면 전원 사용 가능)
MENU_MIN_ROLE = {M_APPROVAL: "MANAGER", M_ORG: "ADMIN", M_AUDIT: "ADMIN", M_ADMIN: "ADMIN"}


def menus_for(user: dict) -> list[str]:
    return [m for m in MENUS if ent.has_role(user, MENU_MIN_ROLE.get(m, "REP"))]


# ----------------------------------------------------------------------------
# 표시 유틸
# ----------------------------------------------------------------------------
def won(value) -> str:
    """정수 금액 → '12,345,678원'"""
    try:
        return f"{int(value):,}원"
    except (TypeError, ValueError):
        return "-"


def mil(value) -> str:
    """정수 금액 → '123.4백만'"""
    try:
        return f"{int(value) / 1_000_000:,.1f}백만"
    except (TypeError, ValueError):
        return "-"


def money_view(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """금액 컬럼을 천단위 콤마 문자열로 바꾼 표시용 사본을 만든다."""
    out = df.copy()
    for col in cols:
        if col in out.columns:
            out[col] = out[col].map(lambda v: f"{int(v):,}" if pd.notna(v) else "")
    return out


def show_table(df: pd.DataFrame, money_cols: list[str] | None = None,
               drop: list[str] | None = None, height: int | None = None) -> None:
    if df.empty:
        st.info("표시할 데이터가 없습니다.")
        return
    view = df.drop(columns=[c for c in (drop or []) if c in df.columns])
    view = money_view(view, money_cols or [])
    st.dataframe(view, **table_kwargs(height))


def csv_download(df: pd.DataFrame, filename: str, label: str = "⬇️ CSV 다운로드") -> None:
    if df.empty:
        return
    st.download_button(label, df.to_csv(index=False).encode("utf-8-sig"),
                       file_name=filename, mime="text/csv", key=f"dl_{filename}")


def flash(message: str, kind: str = "success") -> None:
    """리런 이후에도 살아남는 알림 메시지를 세션에 담는다."""
    st.session_state["_flash"] = (kind, message)


def render_flash() -> None:
    kind, message = st.session_state.pop("_flash", (None, None))
    if not message:
        return
    {"success": st.success, "error": st.error, "warning": st.warning}.get(kind, st.info)(message)


class _OwnerList:
    """담당자 목록 제공자.

    로그인 사용자마다 접근범위가 다르므로 st.cache_data 로 전역 캐시하면
    다른 사용자의 목록이 새어나간다. 쿼리 비용이 작아 매번 조회하고,
    기존 호출부 호환을 위해 clear() 는 비워 둔다.
    """

    def __call__(self) -> list[str]:
        return db.owners()

    def clear(self) -> None:
        return None


cached_owners = _OwnerList()


def owner_input(label: str = "담당자", default: str = "", key: str | None = None) -> str:
    """기존 담당자 중 선택하거나 새 이름을 직접 입력한다.

    st.form 안에서는 위젯 변경이 즉시 리런되지 않으므로,
    선택 박스와 직접입력 칸을 항상 함께 렌더링하고 입력값을 우선한다.
    """
    opts = cached_owners()
    if default and default not in opts:
        opts = [default] + opts
    options = opts + ["➕ 신규 담당자"]
    idx = options.index(default) if default in options else 0
    choice = st.selectbox(label, options, index=idx, key=key)
    manual = st.text_input("신규 담당자명 (목록에 없을 때만 입력)", key=f"{key}_new",
                           placeholder="예) 홍길동").strip()
    if manual:
        return manual
    return "" if choice == "➕ 신규 담당자" else choice


def pick_customer(label: str = "거래처", key: str = "cust", allow_blank: bool = False,
                  default_id: int | None = None) -> int | None:
    opts = db.customer_options()
    if not opts:
        st.warning("등록된 거래처가 없습니다. 먼저 거래처를 등록하세요.")
        return None
    ids = ([0] if allow_blank else []) + list(opts.keys())
    index = ids.index(default_id) if default_id in ids else 0
    return st.selectbox(label, ids, index=index, key=key,
                        format_func=lambda i: "(전체)" if i == 0 else opts.get(i, "-")) or None


# ----------------------------------------------------------------------------
# 1. 대시보드
# ----------------------------------------------------------------------------
def page_dashboard(ym: str, owner: str) -> None:
    st.subheader(f"📊 영업 대시보드 · {ym}" + (f" · {owner}" if owner else " · 전사"))
    k = db.kpi_summary(ym, owner)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("당월 매출", mil(k["month_sales"]), f"{k['mom']:+.1f}% MoM")
    c2.metric("목표 달성률", f"{k['achievement']:.1f}%",
              f"부족 {mil(k['gap'])}" if k["gap"] > 0 else f"초과 {mil(-k['gap'])}")
    c3.metric("파이프라인(가중)", mil(k["weighted_pipeline"]), f"총 {mil(k['open_amount'])}")
    c4.metric("수주/실주", f"{k['won_cnt']} / {k['lost_cnt']}", f"승률 {k['win_rate']:.0f}%")
    c5.metric("미수금", mil(k["unpaid"]), f"활동 {k['activity_cnt']}건", delta_color="inverse")

    st.divider()
    left, right = st.columns([3, 2])

    with left:
        st.markdown("**📈 월별 매출 vs 목표 (최근 12개월)**")
        trend = db.monthly_trend(12, owner)
        st.bar_chart(trend.set_index("월")[["매출", "목표"]], height=300)
        with st.expander("월별 수치 보기"):
            show_table(trend, money_cols=["매출", "목표"])

    with right:
        st.markdown("**🔻 영업 단계별 파이프라인**")
        funnel = db.stage_funnel(owner)
        st.bar_chart(funnel.set_index("단계")[["금액", "가중금액"]], height=300)
        show_table(funnel, money_cols=["금액", "가중금액"])

    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**👥 담당자별 실적 ({ym})**")
        perf = db.owner_performance(ym)
        if not perf.empty:
            st.bar_chart(perf.set_index("담당자")[["목표", "매출"]], height=260)
        show_table(perf, money_cols=["매출", "목표", "파이프라인"])
    with c2:
        st.markdown(f"**🏆 매출 상위 거래처 ({ym})**")
        top = db.top_customers(ym, 10)
        if top.empty:
            top = db.top_customers("", 10)
            st.caption("당월 매출이 없어 전체 기간 기준으로 표시합니다.")
        if not top.empty:
            st.bar_chart(top.set_index("거래처")["매출"], height=260)
        show_table(top, money_cols=["매출"])

    st.divider()
    fc = ent.forecast_summary(ym, owner)
    st.markdown("**🔮 이번 달 예측 요약**")
    f1, f2, f3, f4 = st.columns(4)
    f1.metric("확정 매출", mil(fc["closed"]), f"목표 대비 {fc['attainment']:.0f}%")
    f2.metric("Commit 포함 전망", mil(fc["commit_total"]), f"{fc['commit_attainment']:.0f}%")
    f3.metric("목표 갭", mil(fc["gap"]),
              "초과 달성 전망" if fc["gap"] <= 0 else "추가 확보 필요", delta_color="inverse")
    f4.metric("파이프라인 커버리지", f"{fc['coverage']:.1f}배",
              "건전(3배 이상)" if fc["coverage"] >= 3 else "부족")

    st.divider()
    st.markdown("**🚨 즉시 확인이 필요한 항목**")
    t1, t2, t3 = st.tabs(["⏰ 마감 임박 기회 (14일)", "💤 장기 미접촉 거래처 (30일)", "📌 예정된 후속 액션 (7일)"])
    with t1:
        soon = db.deals_closing_soon(14, owner)
        if not soon.empty:
            st.warning(f"{len(soon)}건의 기회가 마감 임박이거나 이미 마감일을 넘겼습니다.")
        show_table(soon, money_cols=["예상금액"])
    with t2:
        stale = db.stale_customers(30, owner)
        if not stale.empty:
            st.warning(f"{len(stale)}개 거래처가 30일 이상 접촉 이력이 없습니다.")
        show_table(stale)
    with t3:
        show_table(db.upcoming_actions(7, owner))


# ----------------------------------------------------------------------------
# 2. 거래처
# ----------------------------------------------------------------------------
def page_customers(owner_filter: str) -> None:
    st.subheader("🏢 거래처 관리")
    f1, f2, f3, f4 = st.columns([3, 2, 2, 2])
    keyword = f1.text_input("검색 (거래처명/담당자/메모)", key="cust_kw")
    grade = f2.selectbox("등급", ["전체"] + db.GRADES, key="cust_grade")
    status = f3.selectbox("상태", ["전체", "활성", "휴면", "종료"], key="cust_status")
    f4.write("")

    df = db.list_customers(keyword=keyword, owner=owner_filter,
                           grade="" if grade == "전체" else grade,
                           status="" if status == "전체" else status)
    st.caption(f"총 {len(df)}개 거래처")
    show_table(df, money_cols=["누적매출"], drop=["id"])
    csv_download(df.drop(columns=["id"], errors="ignore"), "거래처목록.csv")

    st.divider()
    tab_new, tab_edit = st.tabs(["➕ 신규 등록", "✏️ 수정 / 삭제"])

    with tab_new:
        with st.form("customer_new", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            name = c1.text_input("거래처명 *")
            biz_no = c2.text_input("사업자번호")
            industry = c3.selectbox("업종", db.INDUSTRIES)
            c4, c5, c6 = st.columns(3)
            with c4:
                new_owner = owner_input("영업담당자 *", key="cust_new_owner")
            grade_in = c5.selectbox("등급", db.GRADES, index=2)
            manager = c6.text_input("고객측 담당자")
            c7, c8, c9 = st.columns(3)
            phone = c7.text_input("연락처")
            email = c8.text_input("이메일")
            address = c9.text_input("주소")
            memo = st.text_area("메모", height=80)
            if st.form_submit_button("등록", type="primary"):
                try:
                    db.upsert_customer({"name": name, "biz_no": biz_no, "industry": industry,
                                        "grade": grade_in, "owner": new_owner, "manager": manager,
                                        "phone": phone, "email": email, "address": address,
                                        "status": "활성", "memo": memo})
                    cached_owners.clear()
                    flash(f"'{name}' 거래처를 등록했습니다.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

    with tab_edit:
        opts = db.customer_options()
        if not opts:
            st.info("등록된 거래처가 없습니다.")
            return
        cid = st.selectbox("수정할 거래처", list(opts.keys()),
                           format_func=lambda i: opts[i], key="cust_edit_sel")
        row = db.get_customer(cid) or {}
        with st.form("customer_edit"):
            c1, c2, c3 = st.columns(3)
            name = c1.text_input("거래처명 *", value=row.get("name", ""))
            biz_no = c2.text_input("사업자번호", value=row.get("biz_no") or "")
            ind_list = db.INDUSTRIES
            industry = c3.selectbox("업종", ind_list,
                                    index=ind_list.index(row["industry"]) if row.get("industry") in ind_list else 0)
            c4, c5, c6 = st.columns(3)
            with c4:
                e_owner = owner_input("영업담당자 *", default=row.get("owner", ""), key="cust_edit_owner")
            grade_in = c5.selectbox("등급", db.GRADES,
                                    index=db.GRADES.index(row["grade"]) if row.get("grade") in db.GRADES else 2)
            st_list = ["활성", "휴면", "종료"]
            status_in = c6.selectbox("상태", st_list,
                                     index=st_list.index(row["status"]) if row.get("status") in st_list else 0)
            c7, c8, c9 = st.columns(3)
            manager = c7.text_input("고객측 담당자", value=row.get("manager") or "")
            phone = c8.text_input("연락처", value=row.get("phone") or "")
            email = c9.text_input("이메일", value=row.get("email") or "")
            address = st.text_input("주소", value=row.get("address") or "")
            memo = st.text_area("메모", value=row.get("memo") or "", height=80)

            b1, b2 = st.columns([1, 1])
            saved = b1.form_submit_button("💾 저장", type="primary")
            confirm = b2.checkbox("삭제 확인 (관련 기회/활동/매출 함께 삭제)")
            deleted = b2.form_submit_button("🗑️ 삭제")

            if saved:
                try:
                    db.upsert_customer({"id": cid, "name": name, "biz_no": biz_no, "industry": industry,
                                        "grade": grade_in, "owner": e_owner, "manager": manager,
                                        "phone": phone, "email": email, "address": address,
                                        "status": status_in, "memo": memo})
                    cached_owners.clear()
                    flash("저장했습니다.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
            if deleted:
                if confirm:
                    db.delete_customer(cid)
                    flash("삭제했습니다.", "warning")
                    st.rerun()
                else:
                    st.error("삭제하려면 '삭제 확인'을 체크하세요.")

        st.markdown("**📇 이 거래처 요약**")
        s1, s2 = st.columns(2)
        with s1:
            st.caption("진행 중 영업기회")
            show_table(db.list_deals(customer_id=cid, only_open=True),
                       money_cols=["예상금액", "가중금액"], drop=["id", "customer_id", "종료일"])
        with s2:
            st.caption("최근 영업활동")
            show_table(db.list_activities(days=365, customer_id=cid).head(10), drop=["id", "customer_id"])


# ----------------------------------------------------------------------------
# 3. 영업기회 (파이프라인)
# ----------------------------------------------------------------------------
def _meddic_inputs(row: dict, key_prefix: str) -> dict:
    """MEDDIC 6개 항목 체크박스. 대형 딜의 진위를 가리는 표준 검증 항목이다."""
    st.markdown("**🔍 MEDDIC 자격검증** — 체크한 항목만 인정되며, 단계 진행 조건과 연결됩니다.")
    values, cols = {}, st.columns(3)
    for i, (field, label) in enumerate(db.MEDDIC_FIELDS.items()):
        title, desc = label.split(" - ", 1)
        with cols[i % 3]:
            values[field] = int(st.checkbox(title, value=bool(row.get(field)),
                                            key=f"{key_prefix}_{field}", help=desc))
    done = sum(values.values())
    st.caption(f"검증 점수: {int(done / len(db.MEDDIC_FIELDS) * 100)}점 ({done}/{len(db.MEDDIC_FIELDS)}개 충족)")
    return values


def page_deals(user: dict, owner_filter: str) -> None:
    st.subheader("💼 영업기회 (파이프라인)")
    f1, f2, f3, f4 = st.columns([3, 2, 2, 2])
    keyword = f1.text_input("검색 (기회명/거래처)", key="deal_kw")
    stage = f2.selectbox("단계", ["전체"] + db.STAGES, key="deal_stage")
    fcat = f3.selectbox("예측구분", ["전체"] + db.FORECAST_CATS, key="deal_fcat")
    only_open = f4.checkbox("진행 중만 보기", value=True, key="deal_open")

    df = db.list_deals(keyword=keyword, owner=owner_filter,
                       stage="" if stage == "전체" else stage, only_open=only_open)
    if fcat != "전체" and not df.empty:
        df = df[df["예측구분"] == fcat]

    total = int(df["예상금액"].sum()) if not df.empty else 0
    weighted = int(df["가중금액"].sum()) if not df.empty else 0
    avg_score = int(df["검증점수"].mean()) if not df.empty else 0
    stuck = int((df["단계체류일"] > 30).sum()) if not df.empty else 0
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("건수", f"{len(df):,}건")
    m2.metric("예상금액 합계", mil(total))
    m3.metric("가중 예상금액", mil(weighted))
    m4.metric("평균 검증점수", f"{avg_score}점", f"30일 이상 정체 {stuck}건", delta_color="inverse")

    show_table(df, money_cols=["예상금액", "가중금액"], drop=["customer_id"])
    csv_download(df.drop(columns=["customer_id"], errors="ignore"), "영업기회.csv")

    st.divider()
    tab_new, tab_edit, tab_move, tab_appr = st.tabs(
        ["➕ 기회 등록", "✏️ 수정 / 삭제", "🔀 단계 변경", "💳 할인 결재 요청"])

    with tab_new:
        with st.form("deal_new", clear_on_submit=False):
            c1, c2 = st.columns(2)
            with c1:
                cid = pick_customer("거래처 *", key="deal_new_cust")
            title = c2.text_input("기회명 *", placeholder="예) ERP 라이선스 갱신")
            c3, c4, c5 = st.columns(3)
            stage_in = c3.selectbox("단계", db.STAGES, index=0)
            list_amount = c4.number_input("정가(원)", min_value=0, step=1_000_000, value=10_000_000)
            discount = c5.number_input("할인율(%)", min_value=0.0, max_value=100.0,
                                       step=0.5, value=0.0)
            c6, c7, c8 = st.columns(3)
            exp = c6.date_input("예상 마감일", value=date.today() + timedelta(days=30))
            with c7:
                d_owner = owner_input("담당자 *", default=user["name"], key="deal_new_owner")
            fcat_in = c8.selectbox("예측구분", db.FORECAST_CATS, index=2,
                                   help="\n".join(f"{k}: {v}" for k, v in db.FORECAST_DESC.items()))
            c9, c10 = st.columns(2)
            source = c9.selectbox("유입경로", db.LEAD_SOURCES)
            competitor = c10.text_input("경쟁사")
            amount = int(list_amount * (100 - discount) / 100)
            need_role = db.required_approval_role(discount)
            st.caption(f"제안금액 {won(amount)}" +
                       (f" · 할인 {discount}% → {db.ROLE_LABEL[need_role]} 승인 필요" if need_role else ""))
            meddic = _meddic_inputs({}, "deal_new")
            memo = st.text_area("메모", height=70)
            if st.form_submit_button("등록", type="primary"):
                try:
                    db.upsert_deal({"customer_id": cid, "title": title, "owner": d_owner,
                                    "stage": stage_in, "amount": amount, "list_amount": list_amount,
                                    "discount_rate": discount, "probability": db.STAGE_PROB[stage_in],
                                    "expected_close": exp, "source": source, "forecast_category": fcat_in,
                                    "competitor": competitor, "memo": memo, **meddic})
                    flash(f"'{title}' 기회를 등록했습니다.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

    with tab_edit:
        opts = db.deal_options()
        if not opts:
            st.info("등록된 영업기회가 없습니다.")
        else:
            did = st.selectbox("수정할 기회", list(opts.keys()),
                               format_func=lambda i: opts[i], key="deal_edit_sel")
            row = db.get_deal(did) or {}
            st.caption(f"현재 단계 **{row.get('stage')}** · 승인상태 **{row.get('approval_status')}** · "
                       f"검증점수 **{db.qual_score(row)}점**")
            with st.form("deal_edit"):
                c1, c2 = st.columns(2)
                with c1:
                    cid = pick_customer("거래처 *", key="deal_edit_cust",
                                        default_id=row.get("customer_id"))
                title = c2.text_input("기회명 *", value=row.get("title", ""))
                c3, c4, c5 = st.columns(3)
                stage_in = c3.selectbox("단계", db.STAGES,
                                        index=db.STAGES.index(row["stage"]) if row.get("stage") in db.STAGES else 0)
                list_amount = c4.number_input("정가(원)", min_value=0, step=1_000_000,
                                              value=int(row.get("list_amount") or row.get("amount") or 0))
                discount = c5.number_input("할인율(%)", min_value=0.0, max_value=100.0, step=0.5,
                                           value=float(row.get("discount_rate") or 0))
                c6, c7, c8 = st.columns(3)
                exp_val = pd.to_datetime(row["expected_close"]).date() if row.get("expected_close") else date.today()
                exp = c6.date_input("예상 마감일", value=exp_val)
                with c7:
                    d_owner = owner_input("담당자 *", default=row.get("owner", ""), key="deal_edit_owner")
                fcats = db.FORECAST_CATS
                fcat_in = c8.selectbox("예측구분", fcats,
                                       index=fcats.index(row["forecast_category"])
                                       if row.get("forecast_category") in fcats else 2)
                c9, c10 = st.columns(2)
                src_list = db.LEAD_SOURCES
                source = c9.selectbox("유입경로", src_list,
                                      index=src_list.index(row["source"]) if row.get("source") in src_list else 0)
                competitor = c10.text_input("경쟁사", value=row.get("competitor") or "")
                lost = st.selectbox("실주사유 (실주 단계일 때 필수)", [""] + db.LOST_REASONS,
                                    index=(db.LOST_REASONS.index(row["lost_reason"]) + 1)
                                    if row.get("lost_reason") in db.LOST_REASONS else 0)
                amount = int(list_amount * (100 - discount) / 100)
                st.caption(f"제안금액 {won(amount)}")
                meddic = _meddic_inputs(row, "deal_edit")
                memo = st.text_area("메모", value=row.get("memo") or "", height=70)
                force = st.checkbox("⚠️ 단계 검증 우회 (관리자 전용)",
                                    disabled=not ent.has_role(user, "ADMIN"),
                                    help="데이터 이관 등 예외 상황에서만 사용하며 감사로그에 기록됩니다.")
                b1, b2 = st.columns(2)
                saved = b1.form_submit_button("💾 저장", type="primary")
                confirm = b2.checkbox("삭제 확인")
                deleted = b2.form_submit_button("🗑️ 삭제")
                if saved:
                    try:
                        db.upsert_deal({"id": did, "customer_id": cid, "title": title, "owner": d_owner,
                                        "stage": stage_in, "amount": amount, "list_amount": list_amount,
                                        "discount_rate": discount, "probability": db.STAGE_PROB[stage_in],
                                        "expected_close": exp, "source": source, "competitor": competitor,
                                        "memo": memo, "forecast_category": fcat_in,
                                        "lost_reason": lost or None,
                                        "approval_status": row.get("approval_status"),
                                        "closed_at": row.get("closed_at"), **meddic},
                                       force=bool(force) and ent.has_role(user, "ADMIN"))
                        flash("저장했습니다.")
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
                if deleted:
                    if confirm:
                        db.delete_deal(did)
                        flash("삭제했습니다.", "warning")
                        st.rerun()
                    else:
                        st.error("삭제하려면 '삭제 확인'을 체크하세요.")

            hist = db._df("SELECT changed_at AS 변경일시, COALESCE(from_stage,'(신규)') AS 이전단계, "
                          "to_stage AS 변경단계, days_in_stage AS 이전단계체류일, actor AS 변경자 "
                          "FROM deal_stage_history WHERE deal_id=? ORDER BY id", [did])
            with st.expander(f"📜 단계 변경 이력 ({len(hist)}건)"):
                show_table(hist)

    with tab_move:
        st.caption("단계를 바꾸면 확률·종료일·이력이 자동 정리되고, 필수 조건을 못 갖추면 차단됩니다.")
        open_df = db.list_deals(owner=owner_filter, only_open=True)
        if open_df.empty:
            st.info("진행 중인 기회가 없습니다.")
        else:
            labels = {int(r.id): f"{r.거래처} · {r.기회명} ({r.단계}, {int(r.예상금액):,}원, 검증 {int(r.검증점수)}점)"
                      for r in open_df.itertuples()}
            picked = st.multiselect("대상 기회", list(labels.keys()),
                                    format_func=lambda i: labels[i], key="deal_move_sel")
            c1, c2, c3 = st.columns([2, 2, 1])
            new_stage = c1.selectbox("변경할 단계", db.STAGES, key="deal_move_stage")
            lost_reason = c2.selectbox("실주사유", [""] + db.LOST_REASONS, key="deal_move_lost",
                                       disabled=new_stage != db.STAGE_LOST)
            c3.write("")
            if c3.button("변경 적용", type="primary", disabled=not picked):
                ok, errors = 0, []
                for deal_id in picked:
                    try:
                        db.change_stage(deal_id, new_stage, lost_reason=lost_reason or None)
                        ok += 1
                    except ValueError as exc:
                        errors.append(f"{labels[deal_id].split(' · ')[1].split(' (')[0]}: {exc}")
                if ok:
                    flash(f"{ok}건을 '{new_stage}' 단계로 변경했습니다.")
                if errors:
                    st.error("다음 건은 조건 미충족으로 변경되지 않았습니다.\n\n" + "\n\n".join(errors[:5]))
                else:
                    st.rerun()

    with tab_appr:
        st.caption("할인율 구간별 결재선 — 10% 이하: 팀장 / 20% 이하: 임원 / 20% 초과: 관리자")
        need = db.list_deals(owner=owner_filter, only_open=True)
        if not need.empty:
            need = need[(need["할인율"] > 0) & (need["승인상태"].isin(["미요청", "반려"]))]
        if need.empty:
            st.info("결재를 요청할 건이 없습니다. (할인율이 있고 아직 승인되지 않은 기회가 대상입니다)")
        else:
            labels = {int(r.id): f"{r.거래처} · {r.기회명} · 할인 {r.할인율}% · {int(r.예상금액):,}원"
                      for r in need.itertuples()}
            target = st.selectbox("결재 요청할 기회", list(labels.keys()),
                                  format_func=lambda i: labels[i], key="appr_req_sel")
            reason = st.text_area("요청 사유 *", height=80,
                                  placeholder="예) 경쟁사 대비 가격 열위, 연내 계약 조건으로 할인 요청")
            if st.button("결재 요청", type="primary", key="appr_req_btn"):
                if not reason.strip():
                    st.error("요청 사유를 입력하세요.")
                else:
                    try:
                        ent.request_approval(target, user["name"], reason.strip())
                        flash("결재를 요청했습니다. 결재자가 승인하면 수주 단계로 진행할 수 있습니다.")
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))


# ----------------------------------------------------------------------------
# 4. 영업활동
# ----------------------------------------------------------------------------
def page_activities(owner_filter: str) -> None:
    st.subheader("📞 영업활동 기록")
    f1, f2, f3 = st.columns(3)
    days = f1.selectbox("조회 기간", [7, 30, 90, 180, 365],
                        index=2, format_func=lambda d: f"최근 {d}일", key="act_days")
    act_type = f2.selectbox("활동 유형", ["전체"] + db.ACT_TYPES, key="act_type")
    with f3:
        cid_filter = pick_customer("거래처", key="act_cust_filter", allow_blank=True)

    df = db.list_activities(days=days, owner=owner_filter,
                            act_type="" if act_type == "전체" else act_type,
                            customer_id=cid_filter)
    c1, c2, c3 = st.columns(3)
    c1.metric("활동 건수", f"{len(df):,}건")
    c2.metric("접촉 거래처 수", f"{df['거래처'].nunique() if not df.empty else 0:,}개")
    c3.metric("후속 액션 예정", f"{len(db.upcoming_actions(7, owner_filter)):,}건")

    if not df.empty:
        st.bar_chart(df.groupby("유형").size().rename("건수"), height=220)
    show_table(df, drop=["customer_id"], height=380)
    csv_download(df.drop(columns=["customer_id"], errors="ignore"), "영업활동.csv")

    st.divider()
    tab_new, tab_del = st.tabs(["➕ 활동 등록", "🗑️ 삭제"])

    with tab_new:
        cid = pick_customer("거래처 *", key="act_new_cust")
        deal_opts = db.deal_options(cid) if cid else {}
        with st.form("activity_new", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            act_date = c1.date_input("활동일", value=date.today())
            a_type = c2.selectbox("활동 유형", db.ACT_TYPES)
            with c3:
                a_owner = owner_input("담당자 *", key="act_new_owner")
            deal_id = st.selectbox("관련 영업기회 (선택)", [0] + list(deal_opts.keys()),
                                   format_func=lambda i: "(없음)" if i == 0 else deal_opts[i],
                                   key="act_new_deal")
            summary = st.text_area("활동내용 *", height=90, placeholder="예) 구매팀 미팅, 도입 일정 협의")
            c4, c5 = st.columns(2)
            next_action = c4.text_input("다음 액션")
            next_date = c5.date_input("다음 일정", value=date.today() + timedelta(days=7))
            if st.form_submit_button("등록", type="primary"):
                try:
                    db.add_activity({"customer_id": cid, "deal_id": deal_id or None,
                                     "act_date": act_date, "act_type": a_type, "owner": a_owner,
                                     "summary": summary, "next_action": next_action,
                                     "next_date": next_date if next_action else None})
                    cached_owners.clear()
                    flash("활동을 등록했습니다.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

    with tab_del:
        if df.empty:
            st.info("삭제할 활동이 없습니다.")
        else:
            labels = {int(r.id): f"{r.활동일} · {r.거래처} · {r.유형} · {str(r.활동내용)[:20]}"
                      for r in df.itertuples()}
            aid = st.selectbox("삭제할 활동", list(labels.keys()),
                               format_func=lambda i: labels[i], key="act_del_sel")
            if st.button("🗑️ 삭제", key="act_del_btn"):
                db.delete_activity(aid)
                flash("삭제했습니다.", "warning")
                st.rerun()


# ----------------------------------------------------------------------------
# 5. 매출
# ----------------------------------------------------------------------------
def page_sales_records(ym: str, owner_filter: str) -> None:
    """매출 등록/조회 (매출·채권 화면의 첫 번째 탭)."""
    months = db.month_options()
    f1, f2, f3 = st.columns(3)
    default_from = months.index(ym) - 5 if ym in months and months.index(ym) >= 5 else 0
    ym_from = f1.selectbox("시작월", months, index=default_from, key="sale_from")
    ym_to = f2.selectbox("종료월", months, index=months.index(ym) if ym in months else len(months) - 1,
                         key="sale_to")
    status = f3.selectbox("수금상태", ["전체"] + db.SALE_STATUS, key="sale_status")

    df = db.list_sales(ym_from=ym_from, ym_to=ym_to, owner=owner_filter,
                       status="" if status == "전체" else status)
    total = int(df["금액"].sum()) if not df.empty else 0
    unpaid = int(df[df["수금상태"] != "입금완료"]["금액"].sum()) if not df.empty else 0
    c1, c2, c3 = st.columns(3)
    c1.metric("기간 매출", mil(total))
    c2.metric("미수금", mil(unpaid))
    c3.metric("건수", f"{len(df):,}건")

    if not df.empty:
        monthly = (df.assign(월=df["매출일"].str[:7]).groupby("월")["금액"].sum())
        st.bar_chart(monthly, height=240)
    show_table(df, money_cols=["단가", "금액"], drop=["customer_id"], height=360)
    csv_download(df.drop(columns=["customer_id"], errors="ignore"), "매출목록.csv")

    st.divider()
    tab_new, tab_edit = st.tabs(["➕ 매출 등록", "✏️ 수정 / 상태변경 / 삭제"])

    with tab_new:
        cid = pick_customer("거래처 *", key="sale_new_cust")
        deal_opts = db.deal_options(cid) if cid else {}
        with st.form("sale_new", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            sale_date = c1.date_input("매출일", value=date.today())
            item = c2.text_input("품목 *")
            with c3:
                s_owner = owner_input("담당자 *", key="sale_new_owner")
            c4, c5, c6 = st.columns(3)
            qty = c4.number_input("수량", min_value=1, step=1, value=1)
            unit_price = c5.number_input("단가(원)", min_value=0, step=10_000, value=1_000_000)
            s_status = c6.selectbox("수금상태", db.SALE_STATUS)
            deal_id = st.selectbox("관련 영업기회 (선택)", [0] + list(deal_opts.keys()),
                                   format_func=lambda i: "(없음)" if i == 0 else deal_opts[i],
                                   key="sale_new_deal")
            st.caption(f"자동 계산 금액: {won(qty * unit_price)}")
            memo = st.text_input("메모")
            if st.form_submit_button("등록", type="primary"):
                try:
                    db.upsert_sale({"customer_id": cid, "deal_id": deal_id or None,
                                    "sale_date": sale_date, "item": item, "qty": qty,
                                    "unit_price": unit_price, "amount": qty * unit_price,
                                    "owner": s_owner, "status": s_status, "memo": memo})
                    cached_owners.clear()
                    flash(f"매출 {won(qty * unit_price)}을 등록했습니다.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

    with tab_edit:
        if df.empty:
            st.info("기간 내 매출이 없습니다.")
        else:
            labels = {int(r.id): f"{r.매출일} · {r.거래처} · {r.품목} · {int(r.금액):,}원"
                      for r in df.itertuples()}
            sid = st.selectbox("대상 매출", list(labels.keys()),
                               format_func=lambda i: labels[i], key="sale_edit_sel")
            row = df[df["id"] == sid].iloc[0]
            c1, c2, c3 = st.columns([2, 2, 1])
            new_status = c1.selectbox("수금상태 변경", db.SALE_STATUS,
                                      index=db.SALE_STATUS.index(row["수금상태"]), key="sale_edit_status")
            new_amount = c2.number_input("금액 수정(원)", min_value=0, step=10_000,
                                         value=int(row["금액"]), key="sale_edit_amount")
            c3.write("")
            b1, b2, b3 = st.columns([1, 1, 2])
            if b1.button("💾 저장", type="primary", key="sale_save"):
                db.upsert_sale({"id": sid, "customer_id": int(row["customer_id"]),
                                "sale_date": row["매출일"], "item": row["품목"],
                                "qty": int(row["수량"]), "unit_price": int(row["단가"]),
                                "amount": int(new_amount), "owner": row["담당자"],
                                "status": new_status, "memo": row["메모"]})
                flash("저장했습니다.")
                st.rerun()
            confirm = b3.checkbox("삭제 확인", key="sale_del_confirm")
            if b2.button("🗑️ 삭제", key="sale_del"):
                if confirm:
                    db.delete_sale(sid)
                    flash("삭제했습니다.", "warning")
                    st.rerun()
                else:
                    st.error("삭제하려면 '삭제 확인'을 체크하세요.")


def page_ar(user: dict) -> None:
    """채권(AR) 관리 - 결제기일 경과 기준 aging 과 입금 처리."""
    summary = ent.ar_summary()
    aging = ent.ar_aging()
    total = int(summary["미수금"].sum()) if not summary.empty else 0
    overdue = int(summary[summary["연체구간"] != "정상"]["미수금"].sum()) if not summary.empty else 0

    c1, c2, c3 = st.columns(3)
    c1.metric("총 미수금", mil(total))
    c2.metric("연체 채권", mil(overdue),
              f"연체비중 {overdue / total * 100:.0f}%" if total else "-", delta_color="inverse")
    c3.metric("연체 건수", f"{int(summary[summary['연체구간'] != '정상']['건수'].sum()) if not summary.empty else 0:,}건")

    if not summary.empty and summary["미수금"].sum() > 0:
        st.bar_chart(summary.set_index("연체구간")["미수금"], height=240)
    show_table(summary, money_cols=["미수금"])

    st.markdown("**📋 미수 채권 상세**")
    bucket = st.selectbox("연체구간", ["전체"] + db.AR_BUCKETS, key="ar_bucket")
    view = aging if bucket == "전체" or aging.empty else aging[aging["연체구간"] == bucket]
    show_table(view, money_cols=["매출액", "입금액", "미수금"], drop=["id"], height=320)
    csv_download(view.drop(columns=["id"], errors="ignore"), "미수채권.csv")

    st.divider()
    st.markdown("**💵 입금 등록**")
    if aging.empty:
        st.info("미수 채권이 없습니다.")
        return
    labels = {int(r.id): f"{r.거래처} · {r.품목} · 미수 {int(r.미수금):,}원 (기일 {r.결제기일}, {int(r.경과일)}일 경과)"
              for r in aging.itertuples()}
    sid = st.selectbox("대상 매출", list(labels.keys()), format_func=lambda i: labels[i], key="ar_sel")
    row = aging[aging["id"] == sid].iloc[0]
    c1, c2 = st.columns([2, 1])
    amount = c1.number_input("입금액(원)", min_value=0, step=100_000,
                             value=int(row["미수금"]), key="ar_amount")
    c2.write("")
    if c2.button("입금 등록", type="primary", key="ar_btn"):
        try:
            ent.record_payment(sid, int(amount))
            flash(f"{won(amount)} 입금 처리했습니다.")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))


def page_credit(user: dict) -> None:
    """여신 관리 - 거래처별 한도 대비 미수 잔액."""
    df = ent.credit_exposure()
    if df.empty:
        st.info("거래처 데이터가 없습니다.")
        return
    over = df[df["한도초과"] == "초과"]
    c1, c2, c3 = st.columns(3)
    c1.metric("여신한도 합계", mil(int(df["여신한도"].sum())))
    c2.metric("미수 잔액 합계", mil(int(df["미수잔액"].sum())))
    c3.metric("한도 초과 거래처", f"{len(over)}개", delta_color="inverse")
    if not over.empty:
        st.error("여신한도를 초과한 거래처가 있습니다. 추가 출고 전 검토가 필요합니다.")
        show_table(over, money_cols=["여신한도", "미수잔액"])
    st.markdown("**거래처별 여신 소진율 (상위 15개)**")
    top = df.head(15)
    st.bar_chart(top.set_index("거래처")["소진율"], height=260)
    show_table(df, money_cols=["여신한도", "미수잔액"], height=360)
    csv_download(df, "여신현황.csv")


def page_sales(user: dict, ym: str, owner_filter: str) -> None:
    st.subheader("💰 매출 · 채권 관리")
    t1, t2, t3 = st.tabs(["📒 매출 등록/조회", "⏳ 채권(AR) 관리", "🏦 여신 현황"])
    with t1:
        page_sales_records(ym, owner_filter)
    with t2:
        page_ar(user)
    with t3:
        page_credit(user)


# ----------------------------------------------------------------------------
# 6. 목표
# ----------------------------------------------------------------------------
def page_targets(ym: str) -> None:
    st.subheader("🎯 목표 관리")
    st.caption("담당자별 월 목표를 입력하면 대시보드 달성률에 즉시 반영됩니다. (단위: 원)")

    perf = db.owner_performance(ym)
    names = perf["담당자"].tolist() if not perf.empty else db.owners()
    if not names:
        st.info("담당자 정보가 없습니다. 거래처나 매출을 먼저 등록하세요.")
        return

    current = db.list_targets(ym)
    target_map = dict(zip(current["담당자"], current["목표금액"])) if not current.empty else {}
    editor_df = pd.DataFrame({"담당자": names,
                              "목표금액": [int(target_map.get(n, 0)) for n in names]})

    edited = st.data_editor(
        editor_df, num_rows="dynamic", key=f"target_editor_{ym}", **table_kwargs(),
        column_config={
            "담당자": st.column_config.TextColumn("담당자", required=True),
            "목표금액": st.column_config.NumberColumn("목표금액(원)", min_value=0, step=1_000_000, format="%d"),
        },
    )
    c1, c2 = st.columns([1, 4])
    if c1.button(f"💾 {ym} 목표 저장", type="primary"):
        saved = 0
        for r in edited.itertuples():
            name = str(getattr(r, "담당자", "") or "").strip()
            if not name:
                continue
            db.upsert_target(ym, name, int(getattr(r, "목표금액", 0) or 0))
            saved += 1
        cached_owners.clear()
        flash(f"{saved}명의 {ym} 목표를 저장했습니다.")
        st.rerun()
    c2.caption("행을 추가해 새 담당자의 목표도 등록할 수 있습니다.")

    st.divider()
    st.markdown(f"**📌 {ym} 목표 대비 실적**")
    if not perf.empty:
        chart = perf.set_index("담당자")[["목표", "매출"]]
        st.bar_chart(chart, height=280)
        show_table(perf, money_cols=["매출", "목표", "파이프라인"])
        total_t, total_s = int(perf["목표"].sum()), int(perf["매출"].sum())
        st.progress(min(total_s / total_t, 1.0) if total_t else 0.0,
                    text=f"전사 달성률 {(total_s / total_t * 100) if total_t else 0:.1f}% "
                         f"({won(total_s)} / {won(total_t)})")

    st.markdown("**📅 최근 12개월 목표 vs 매출**")
    trend = db.monthly_trend(12)
    st.line_chart(trend.set_index("월")[["매출", "목표"]], height=260)
    show_table(trend, money_cols=["매출", "목표"])


# ----------------------------------------------------------------------------
# 8. 매출예측 (Forecast)
# ----------------------------------------------------------------------------
def page_forecast(user: dict, ym: str, owner_filter: str) -> None:
    st.subheader(f"🔮 매출예측 · {ym}")
    st.caption("Commit(반드시 마감) / Best Case(잘 되면 마감) / Pipeline(예측 이전) 으로 나누어 "
               "주간 단위로 예측하고, 실제 실적과 비교해 예측 신뢰도를 관리합니다.")
    fc = ent.forecast_summary(ym, owner_filter)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("목표", mil(fc["target"]))
    c2.metric("확정 매출", mil(fc["closed"]), f"{fc['attainment']:.0f}%")
    c3.metric("Commit 전망", mil(fc["commit_total"]), f"{fc['commit_attainment']:.0f}%")
    c4.metric("Best Case 전망", mil(fc["best_total"]))
    c5.metric("커버리지", f"{fc['coverage']:.1f}배",
              "건전" if fc["coverage"] >= 3 else "부족", delta_color="normal")

    if fc["gap"] > 0:
        st.warning(f"Commit 기준으로 목표까지 {won(fc['gap'])} 부족합니다. "
                   f"Best Case {mil(fc['best_case'])} 중 일부를 Commit 으로 전환하거나 "
                   f"신규 파이프라인 확보가 필요합니다.")
    else:
        st.success(f"Commit 기준으로 목표를 {won(-fc['gap'])} 초과 달성할 전망입니다.")

    st.markdown("**📊 예측 구성**")
    comp = pd.DataFrame({
        "구분": ["확정 매출", "Commit", "Best Case", "Pipeline"],
        "금액": [fc["closed"], fc["commit"], fc["best_case"], fc["pipeline"]],
    })
    st.bar_chart(comp.set_index("구분")["금액"], height=250)

    st.divider()
    st.markdown("**👥 담당자별 예측 (주간 예측회의용)**")
    by_owner = ent.forecast_by_owner(ym)
    show_table(by_owner, money_cols=["목표", "확정매출", "Commit", "Best Case", "Pipeline",
                                     "Commit포함전망", "목표갭"])
    csv_download(by_owner, f"예측_{ym}.csv")

    st.divider()
    t1, t2, t3 = st.tabs(["🏷️ 예측구분 지정", "📉 주간 스냅샷 추이", "🎯 예측 정확도"])

    with t1:
        st.caption("이번 달 마감 예정 기회의 예측구분을 조정합니다. 예측 책임은 Commit 에 한정됩니다.")
        target_deals = db.list_deals(owner=owner_filter, only_open=True)
        if not target_deals.empty:
            target_deals = target_deals[
                target_deals["예상마감일"].fillna("").str[:7] == ym]
        if target_deals.empty:
            st.info("이번 달 마감 예정인 진행 기회가 없습니다.")
        else:
            labels = {int(r.id): f"[{r.예측구분}] {r.거래처} · {r.기회명} · {int(r.예상금액):,}원 "
                                 f"({r.단계}, 검증 {int(r.검증점수)}점)"
                      for r in target_deals.itertuples()}
            picked = st.multiselect("대상 기회", list(labels.keys()),
                                    format_func=lambda i: labels[i], key="fc_pick")
            c1, c2 = st.columns([2, 1])
            new_cat = c1.selectbox("변경할 예측구분", db.FORECAST_CATS, key="fc_cat",
                                   help="\n".join(f"{k}: {v}" for k, v in db.FORECAST_DESC.items()))
            c2.write("")
            if c2.button("적용", type="primary", disabled=not picked, key="fc_apply"):
                with db.get_conn() as conn:
                    for deal_id in picked:
                        conn.execute("UPDATE deals SET forecast_category=?, updated_at=? WHERE id=?",
                                     (new_cat, db._now(), deal_id))
                db.audit("예측구분변경", "영업기회", None, {"건수": len(picked), "구분": new_cat})
                flash(f"{len(picked)}건을 '{new_cat}' 으로 변경했습니다.")
                st.rerun()

    with t2:
        c1, c2 = st.columns([3, 1])
        c1.caption("주 1회 스냅샷을 남기면 파이프라인이 어느 시점에 흔들렸는지 추적할 수 있습니다.")
        if ent.has_role(user, "MANAGER"):
            if c2.button("📸 오늘자 스냅샷 생성", key="snap_btn"):
                cnt = ent.take_snapshot(ym)
                flash(f"스냅샷 {cnt}건을 저장했습니다." if cnt else "스냅샷 대상 데이터가 없습니다.",
                      "success" if cnt else "warning")
                st.rerun()
        trend = ent.snapshot_trend(ym)
        if trend.empty:
            st.info("아직 스냅샷이 없습니다. 스냅샷을 생성하면 주차별 추이가 표시됩니다.")
        else:
            chart_cols = [c for c in trend.columns if c != "기준일"]
            st.line_chart(trend.set_index("기준일")[chart_cols], height=280)
            show_table(trend, money_cols=chart_cols)

    with t3:
        acc = ent.forecast_accuracy(6)
        if acc.empty:
            st.info("과거 스냅샷이 없어 정확도를 계산할 수 없습니다.")
        else:
            st.caption("월초 Commit 대비 실제 매출 비율. 80~120% 를 정상 범위로 보며, "
                       "지속적으로 120% 를 넘으면 과소예측, 80% 미만이면 과대예측입니다.")
            st.bar_chart(acc.set_index("월")["예측정확도"], height=240)
            show_table(acc, money_cols=["월초 Commit", "실제 매출"])
            avg = acc["예측정확도"].mean()
            st.metric("평균 예측 정확도", f"{avg:.1f}%",
                      "정상 범위" if 80 <= avg <= 120 else "편차 큼")


# ----------------------------------------------------------------------------
# 9. 파이프라인 분석
# ----------------------------------------------------------------------------
def page_analytics(user: dict, owner_filter: str) -> None:
    st.subheader("📈 파이프라인 분석")
    period = st.selectbox("분석 기간", [90, 180, 365, 730],
                          index=2, format_func=lambda d: f"최근 {d}일", key="an_days")

    v = ent.sales_velocity(period)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("진행 기회", f"{v['open_deals']:,}건")
    c2.metric("평균 수주금액", mil(v["avg_deal"]))
    c3.metric("승률", f"{v['win_rate']}%", f"수주 {v['won_cnt']} / 실주 {v['lost_cnt']}")
    c4.metric("평균 영업주기", f"{v['avg_cycle_days']:.0f}일")
    c5.metric("월 환산 영업속도", mil(v["velocity_per_month"]))
    st.caption("영업속도 = (진행 기회수 × 평균 수주금액 × 승률) ÷ 평균 영업주기. "
               "네 변수 중 무엇을 개선해야 매출이 가장 크게 움직이는지 판단하는 지표입니다.")

    st.divider()
    conv = ent.stage_conversion(period)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**🔻 단계별 진입 건수와 전환율**")
        st.bar_chart(conv.set_index("단계")["진입건수"], height=260)
    with c2:
        st.markdown("**⏱️ 단계별 평균 체류일**")
        st.bar_chart(conv.set_index("단계")["평균체류일"], height=260)
    show_table(conv)
    worst = conv[conv["전환율"].notna()].sort_values("전환율").head(1)
    if not worst.empty:
        row = worst.iloc[0]
        st.info(f"가장 많이 이탈하는 구간은 **{row['단계']} {row['전환대상']}** 로 전환율 "
                f"{row['전환율']}% 입니다. 이 단계의 활동 품질과 자격검증을 먼저 점검하십시오.")

    st.divider()
    st.markdown("**🏁 Win / Loss 분석**")
    wl = ent.win_loss_analysis(period)
    t1, t2, t3 = st.tabs(["실주 사유", "경쟁사별 승률", "유입경로별 성과"])
    with t1:
        if wl["reasons"].empty:
            st.info("해당 기간 실주 건이 없습니다.")
        else:
            st.bar_chart(wl["reasons"].set_index("실주사유")["건수"], height=240)
            show_table(wl["reasons"], money_cols=["금액"])
    with t2:
        if wl["competitor"].empty:
            st.info("해당 기간 마감된 건이 없습니다.")
        else:
            st.bar_chart(wl["competitor"].set_index("경쟁사")["승률"], height=240)
            show_table(wl["competitor"])
    with t3:
        show_table(wl["source"], money_cols=["수주금액"])


# ----------------------------------------------------------------------------
# 10. 결재함 (Deal Desk)
# ----------------------------------------------------------------------------
def page_approvals(user: dict) -> None:
    st.subheader("✅ 결재함")
    st.caption("할인율 구간별 결재선 — 10% 이하: 팀장 / 20% 이하: 임원 / 20% 초과: 관리자. "
               "본인이 요청한 건은 본인이 결재할 수 없습니다.")
    mine = ent.pending_for(user)
    requested = ent.list_approvals(requested_by=user["name"])

    c1, c2, c3 = st.columns(3)
    c1.metric("내 결재 대기", f"{len(mine)}건")
    c2.metric("내가 요청한 건", f"{len(requested)}건")
    c3.metric("대기 중 총액",
              mil(int(mine["제안가"].sum()) if not mine.empty else 0))

    t1, t2, t3 = st.tabs(["📥 내 결재 대기", "📤 내 요청 현황", "🗃️ 전체 결재 이력"])

    with t1:
        if mine.empty:
            st.success("결재할 건이 없습니다.")
        else:
            show_table(mine, money_cols=["정가", "제안가"], drop=["id", "deal_id"])
            labels = {int(r.id): f"{r.거래처} · {r.기회명} · 할인 {r.할인율}% "
                                 f"(정가 {int(r.정가):,} → {int(r.제안가):,}원)"
                      for r in mine.itertuples()}
            aid = st.selectbox("결재 대상", list(labels.keys()),
                               format_func=lambda i: labels[i], key="appr_sel")
            row = mine[mine["id"] == aid].iloc[0]
            st.info(f"요청자 **{row['요청자']}** · 요청사유: {row['사유'] or '(미기재)'}")
            comment = st.text_area("결재 의견", height=80, key="appr_comment")
            c1, c2, _ = st.columns([1, 1, 3])
            if c1.button("✅ 승인", type="primary", key="appr_ok"):
                try:
                    ent.decide_approval(aid, user, True, comment)
                    flash("승인 처리했습니다.")
                    st.rerun()
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
            if c2.button("❌ 반려", key="appr_no"):
                if not comment.strip():
                    st.error("반려 시에는 사유를 입력해야 합니다.")
                else:
                    try:
                        ent.decide_approval(aid, user, False, comment)
                        flash("반려 처리했습니다.", "warning")
                        st.rerun()
                    except (ValueError, PermissionError) as exc:
                        st.error(str(exc))

    with t2:
        show_table(requested, money_cols=["정가", "제안가"], drop=["id", "deal_id"])

    with t3:
        status = st.selectbox("상태", ["전체"] + db.APPROVAL_STATUS[1:], key="appr_hist_status")
        hist = ent.list_approvals("" if status == "전체" else status)
        show_table(hist, money_cols=["정가", "제안가"], drop=["id", "deal_id"], height=360)
        csv_download(hist.drop(columns=["id", "deal_id"], errors="ignore"), "결재이력.csv")


# ----------------------------------------------------------------------------
# 10-2. 데이터 등록 · 추출
# ----------------------------------------------------------------------------
def _import_tab(user: dict) -> None:
    st.caption("엑셀/CSV 파일로 대량 등록합니다. 업로드하면 **먼저 검증 결과**를 보여 주고, "
               "확인 후에 실제 등록이 진행됩니다. 본인 권한 범위 밖의 담당자로는 등록할 수 없습니다.")
    entity = st.selectbox("등록할 항목", list(dataio.IMPORT_SPECS.keys()), key="io_entity")
    spec = dataio.IMPORT_SPECS[entity]

    c1, c2 = st.columns([2, 3])
    with c1:
        template = dataio.template_df(entity)
        st.download_button(f"⬇️ {entity} 업로드 양식 (CSV)", dataio.to_csv(template),
                           file_name=f"{entity}_업로드양식.csv", mime="text/csv",
                           key=f"io_tpl_{entity}")
        st.download_button(f"⬇️ {entity} 업로드 양식 (Excel)",
                           dataio.to_excel({entity: template}),
                           file_name=f"{entity}_업로드양식.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           key=f"io_tplx_{entity}")
    with c2:
        st.markdown(f"**필수 컬럼** — {', '.join(spec['required'])}")
        st.markdown(f"**선택 컬럼** — {', '.join(spec['optional']) or '없음'}")
        st.caption("날짜는 2026-01-05 / 2026.1.5 / 20260105 형식을 모두 인식하고, "
                   "금액은 '3억', '5천만', '1,000,000' 표기를 지원합니다.")

    uploaded = st.file_uploader("파일 업로드 (CSV 또는 Excel)", type=["csv", "xlsx", "xls"],
                                key=f"io_file_{entity}")
    if uploaded is None:
        return

    try:
        raw = dataio.read_upload(uploaded, uploaded.name)
    except Exception as exc:  # noqa: BLE001 - 사용자 파일 문제는 화면에 그대로 알린다
        st.error(f"파일을 읽지 못했습니다: {exc}")
        return

    st.markdown(f"**업로드 내용 미리보기** — 총 {len(raw):,}행")
    st.dataframe(raw.head(10), **table_kwargs())
    if len(raw) > 5000:
        st.warning("5,000행이 넘습니다. 파일을 나누어 올리면 오류 확인과 재시도가 쉬워집니다.")

    try:
        check = dataio.import_rows(entity, raw, user, dry_run=True)
    except ValueError as exc:
        st.error(str(exc))
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("전체", f"{check['total']:,}행")
    c2.metric("등록 가능", f"{check['ok']:,}행")
    c3.metric("오류", f"{len(check['errors']):,}행", delta_color="inverse")

    if check["errors"]:
        st.error("아래 행은 등록되지 않습니다. 엑셀에서 해당 행을 수정한 뒤 다시 올리십시오.")
        err_df = dataio.errors_to_df(check["errors"])
        show_table(err_df, height=240)
        st.download_button("⬇️ 오류 목록 내려받기", dataio.to_csv(err_df),
                           file_name=f"{entity}_오류목록.csv", mime="text/csv", key="io_err_dl")

    if not check["ok"]:
        return

    st.divider()
    on_dup = "건너뛰기"
    if entity == "거래처":
        on_dup = st.radio("같은 거래처명이 이미 있을 때", ["건너뛰기", "덮어쓰기"],
                          horizontal=True, key="io_dup")
    if entity == "영업기회":
        st.info("일괄 등록은 이관 데이터로 간주해 단계 검증(Stage Gate)을 적용하지 않습니다. "
                "등록 건수는 감사로그에 남습니다.")
    confirm = st.checkbox(f"{check['ok']}건을 등록합니다", key="io_confirm")
    if st.button("📥 등록 실행", type="primary", disabled=not confirm, key="io_run"):
        result = dataio.import_rows(entity, raw, user, dry_run=False, on_duplicate=on_dup)
        message = f"{entity} {result['ok']}건 등록 완료"
        if result["skipped"]:
            message += f" · 중복 {result['skipped']}건 건너뜀"
        if result["errors"]:
            message += f" · 오류 {len(result['errors'])}건"
        flash(message, "success" if result["ok"] else "warning")
        st.rerun()


def _export_tab(user: dict) -> None:
    st.caption("조건을 지정해 필요한 항목만 추출합니다. 추출 범위는 로그인 사용자의 권한을 따릅니다.")
    preset = st.selectbox("추출 프리셋", ["직접 선택"] + list(dataio.EXPORT_PRESETS.keys()),
                          key="ex_preset")
    available = [src for src in dataio.EXPORT_SOURCES
                 if src != "감사로그" or ent.has_role(user, "ADMIN")]
    default = dataio.EXPORT_PRESETS.get(preset, ["매출"])
    sources = st.multiselect("추출 항목", available,
                             default=[d for d in default if d in available], key="ex_sources")

    c1, c2, c3, c4 = st.columns(4)
    date_from = c1.date_input("시작일", value=date.today().replace(day=1) - timedelta(days=180),
                              key="ex_from")
    date_to = c2.date_input("종료일", value=date.today(), key="ex_to")
    owner_opts = ["전체"] + cached_owners()
    owner = c3.selectbox("담당자", owner_opts, key="ex_owner")
    months = db.month_options()
    ym = c4.selectbox("기준월 (목표·예측용)", months,
                      index=months.index(date.today().strftime("%Y-%m"))
                      if date.today().strftime("%Y-%m") in months else len(months) - 1,
                      key="ex_ym")
    stage = st.selectbox("영업기회 단계", ["전체"] + db.STAGES, key="ex_stage")

    if not sources:
        st.info("추출할 항목을 선택하세요.")
        return

    sheets = dataio.collect(sources,
                            date_from=date_from.strftime("%Y-%m-%d"),
                            date_to=date_to.strftime("%Y-%m-%d"),
                            owner="" if owner == "전체" else owner,
                            stage="" if stage == "전체" else stage, ym=ym)
    st.markdown("**추출 결과 요약**")
    summary = pd.DataFrame([{"항목": name, "행수": len(frame), "열수": len(frame.columns)}
                            for name, frame in sheets.items()])
    show_table(summary)

    meta = {"추출일시": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "추출자": f"{user['name']} ({db.ROLE_LABEL.get(user['role'], user['role'])})",
            "조회범위": "전사" if db.current_scope() is None else f"{len(db.current_scope())}명",
            "기간": f"{date_from} ~ {date_to}", "담당자": owner, "기준월": ym,
            "영업기회 단계": stage}
    st.download_button("⬇️ 엑셀로 추출 (다중 시트)", dataio.to_excel(sheets, meta),
                       file_name=f"영업데이터_{date.today():%Y%m%d}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       type="primary", key="ex_xlsx")

    st.divider()
    st.markdown("**항목별 미리보기 / 개별 CSV**")
    tabs = st.tabs(list(sheets.keys()))
    for tab, (name, frame) in zip(tabs, sheets.items()):
        with tab:
            show_table(frame.head(200), height=320)
            if not frame.empty:
                st.caption(f"미리보기는 200행까지 표시합니다. 전체 {len(frame):,}행은 다운로드에 포함됩니다.")
                st.download_button(f"⬇️ {name} CSV", dataio.to_csv(frame),
                                   file_name=f"{name}.csv", mime="text/csv", key=f"ex_csv_{name}")


def page_dataio(user: dict) -> None:
    st.subheader("📥 데이터 등록 · 추출")
    t1, t2 = st.tabs(["📤 일괄 등록 (업로드)", "📊 데이터 추출 (다운로드)"])
    with t1:
        _import_tab(user)
    with t2:
        _export_tab(user)


# ----------------------------------------------------------------------------
# 11. 조직 · 사용자
# ----------------------------------------------------------------------------
def page_org(user: dict) -> None:
    st.subheader("👥 조직 · 사용자 관리")
    if not auth.require(user, "ADMIN"):
        return
    orgs = ent.list_orgs()
    users = ent.list_users(active_only=False)
    c1, c2, c3 = st.columns(3)
    c1.metric("조직", f"{len(orgs)}개")
    c2.metric("사용자", f"{int(users['사용여부'].sum()) if not users.empty else 0}명")
    c3.metric("비활성 계정", f"{len(users) - int(users['사용여부'].sum()) if not users.empty else 0}명")

    t1, t2 = st.tabs(["🏛️ 조직도", "🙍 사용자"])

    with t1:
        show_table(orgs, drop=["id", "parent_id"])
        st.markdown("**조직 등록 / 수정**")
        org_map = {0: "(최상위)"} | {int(r.id): r.조직명 for r in orgs.itertuples()}
        with st.form("org_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            name = c1.text_input("조직명 *")
            org_type = c2.selectbox("구분", ["본부", "팀", "파트"])
            parent = c3.selectbox("상위 조직", list(org_map.keys()),
                                  format_func=lambda i: org_map[i])
            if st.form_submit_button("등록", type="primary"):
                try:
                    ent.upsert_org(name, parent or None, org_type)
                    flash(f"조직 '{name}' 을 등록했습니다.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        if not orgs.empty:
            c1, c2 = st.columns([3, 1])
            del_id = c1.selectbox("삭제할 조직", [int(r.id) for r in orgs.itertuples()],
                                  format_func=lambda i: org_map.get(i, "-"), key="org_del")
            c2.write("")
            if c2.button("🗑️ 조직 삭제", key="org_del_btn"):
                ent.delete_org(del_id)
                flash("조직을 삭제했습니다(소속 사용자는 미배정으로 변경).", "warning")
                st.rerun()

    with t2:
        show_table(users, drop=["id", "org_id", "역할코드"])
        org_map = {0: "(미배정)"} | {int(r.id): r.조직명 for r in orgs.itertuples()}
        st.markdown("**사용자 등록 / 수정**")
        mode = st.radio("작업", ["신규 등록", "기존 사용자 수정"], horizontal=True, key="user_mode")
        target = None
        if mode == "기존 사용자 수정" and not users.empty:
            labels = {int(r.id): f"{r.이름} ({r.사번})" for r in users.itertuples()}
            uid = st.selectbox("대상 사용자", list(labels.keys()),
                               format_func=lambda i: labels[i], key="user_sel")
            target = ent.get_user(user_id=uid) or {}
        target = target or {}
        with st.form("user_form"):
            c1, c2, c3 = st.columns(3)
            emp_no = c1.text_input("사번 *", value=target.get("emp_no", ""))
            name = c2.text_input("이름 *", value=target.get("name", ""),
                                 help="영업 데이터의 '담당자' 이름과 일치해야 본인 데이터가 연결됩니다.")
            role_keys = list(db.ROLES.keys())
            role = c3.selectbox("역할 *", role_keys,
                                index=role_keys.index(target["role"]) if target.get("role") in role_keys else 0,
                                format_func=lambda r: f"{db.ROLE_LABEL[r]} ({r})")
            c4, c5, c6 = st.columns(3)
            org_id = c4.selectbox("소속", list(org_map.keys()), format_func=lambda i: org_map[i],
                                  index=list(org_map.keys()).index(target["org_id"])
                                  if target.get("org_id") in org_map else 0)
            email = c5.text_input("이메일", value=target.get("email") or "")
            active = c6.checkbox("사용", value=bool(target.get("active", 1)))
            if st.form_submit_button("저장", type="primary"):
                try:
                    ent.upsert_user({"id": target.get("id"), "emp_no": emp_no, "name": name,
                                     "role": role, "org_id": org_id or None, "email": email,
                                     "active": int(active)})
                    flash(f"사용자 '{name}' 정보를 저장했습니다.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        st.caption("퇴사자는 삭제 대신 '사용' 체크를 해제하십시오. 실적·활동 이력은 보존됩니다.")


# ----------------------------------------------------------------------------
# 12. 감사로그
# ----------------------------------------------------------------------------
def page_audit(user: dict) -> None:
    st.subheader("🗂️ 감사로그")
    if not auth.require(user, "ADMIN"):
        return
    st.caption("데이터 변경 이력 전체를 시간순으로 보관합니다. 내부통제·감사 대응 자료로 사용하십시오.")
    users = ent.list_users(active_only=False)
    c1, c2, c3 = st.columns(3)
    actor = c1.selectbox("사용자", ["전체"] + (users["이름"].tolist() if not users.empty else []),
                         key="audit_actor")
    entity = c2.selectbox("대상", ["전체", "거래처", "영업기회", "영업활동", "매출", "목표",
                                   "승인", "사용자", "조직", "예측"], key="audit_entity")
    limit = c3.selectbox("표시 건수", [100, 300, 1000], index=1, key="audit_limit")
    df = db.list_audit(limit, "" if actor == "전체" else actor,
                       "" if entity == "전체" else entity)
    st.metric("조회된 로그", f"{len(df):,}건")
    show_table(df, height=460)
    csv_download(df, "감사로그.csv")


# ----------------------------------------------------------------------------
# 7. 데이터 관리
# ----------------------------------------------------------------------------
def page_admin(user: dict) -> None:
    st.subheader("⚙️ 데이터 관리")
    if not auth.require(user, "ADMIN"):
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("거래처", f"{len(db.list_customers()):,}")
    c2.metric("영업기회", f"{len(db.list_deals()):,}")
    c3.metric("매출 레코드", f"{len(db.list_sales()):,}")
    st.caption(f"DB 파일: `{db.DB_PATH}`")

    st.divider()
    st.markdown("**📤 전체 백업 (엑셀)**")
    st.download_button("⬇️ 엑셀로 전체 내보내기", db.export_excel(),
                       file_name=f"영업관리_백업_{date.today():%Y%m%d}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    st.divider()
    st.markdown("**📥 거래처 CSV 일괄 등록**")
    st.caption("필수 컬럼: 거래처명, 담당자 / 선택 컬럼: 등급, 업종, 연락처, 이메일, 주소, 고객담당자, 사업자번호, 메모")
    sample = pd.DataFrame({"거래처명": ["샘플상사"], "담당자": ["김영업"], "등급": ["A"],
                           "업종": ["제조"], "연락처": ["010-0000-0000"], "이메일": ["a@b.com"]})
    csv_download(sample, "거래처_업로드_양식.csv", "⬇️ 업로드 양식 받기")
    uploaded = st.file_uploader("CSV 파일 선택", type=["csv"], key="cust_csv")
    if uploaded is not None:
        try:
            up_df = pd.read_csv(uploaded, encoding="utf-8-sig", dtype=str)
            st.dataframe(up_df.head(10), **table_kwargs())
            if st.button("이 내용으로 등록", type="primary", key="csv_import_btn"):
                ok, errors = db.import_customers_csv(up_df)
                cached_owners.clear()
                st.success(f"{ok}건 등록 완료")
                if errors:
                    st.warning("오류 " + str(len(errors)) + "건\n\n" + "\n".join(errors[:20]))
        except Exception as exc:  # noqa: BLE001
            st.error(f"CSV 처리 오류: {exc}")

    st.divider()
    st.markdown("**🏛️ 조직 · 계정 초기 구성**")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("샘플 조직·계정 생성", key="org_seed_btn"):
            created = ent.seed_org_demo()
            flash(f"조직 {created['orgs']}개, 사용자 {created['users']}명을 생성했습니다.")
            st.rerun()
    with c2:
        if st.button("이번 달 예측 스냅샷 생성", key="snap_admin_btn"):
            cnt = ent.take_snapshot(date.today().strftime("%Y-%m"))
            flash(f"스냅샷 {cnt}건 저장" if cnt else "스냅샷 대상 데이터가 없습니다.",
                  "success" if cnt else "warning")
            st.rerun()
    st.caption("운영 환경에서는 스냅샷을 주 1회(예: 매주 월요일 오전) OS 스케줄러로 자동 실행하십시오. "
               "명령: python -c \"import sales_db,enterprise,datetime;"
               "sales_db.init_db();enterprise.take_snapshot(datetime.date.today().strftime('%Y-%m'))\"")

    st.divider()
    st.markdown("**🧪 샘플 데이터 / 초기화**")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("샘플 데이터 생성", key="seed_btn"):
            created = db.seed_demo_data()
            cached_owners.clear()
            flash("샘플 데이터 생성 완료: " + ", ".join(f"{k} {v}건" for k, v in created.items()))
            st.rerun()
    with c2:
        confirm = st.checkbox("전체 삭제에 동의합니다 (복구 불가)", key="reset_confirm")
        if st.button("⚠️ 전체 데이터 초기화", key="reset_btn"):
            if confirm:
                db.reset_db()
                cached_owners.clear()
                flash("모든 데이터를 삭제했습니다.", "warning")
                st.rerun()
            else:
                st.error("동의 체크 후 실행하세요.")


# ----------------------------------------------------------------------------
# 메인
# ----------------------------------------------------------------------------
REQUIRED_MODULE_VERSION = 2


def check_modules() -> bool:
    """실행 중인 모듈이 최신인지 검사한다.

    Streamlit 은 메인 스크립트만 다시 실행하고 import 된 모듈은 메모리에 그대로 두기 때문에,
    서버를 켜 둔 채 .py 파일을 교체하면 새 app.py 와 옛 sales_db.py 가 섞여 돌아간다.
    이 경우 'no such table' 같은 엉뚱한 오류가 나므로 기동 시점에 먼저 걸러낸다.
    """
    stale = [name for name, mod in (("sales_db.py", db), ("enterprise.py", ent),
                                    ("auth.py", auth), ("dataio.py", dataio))
             if getattr(mod, "MODULE_VERSION", 1) < REQUIRED_MODULE_VERSION]
    if not stale:
        return True
    st.error("코드가 업데이트되었지만 서버가 예전 모듈을 들고 있습니다: " + ", ".join(stale))
    st.markdown("""
**해결 방법 — 서버를 재시작하세요.**

1. 앱을 실행한 터미널에서 `Ctrl + C`
2. 같은 폴더에서 다시 실행: `python -m streamlit run app.py`

Streamlit 은 화면 Rerun 시 `app.py` 만 다시 읽고, `import` 한 모듈(`sales_db.py` 등)은
메모리에 남겨 두기 때문에 파일을 교체했어도 반영되지 않습니다.
그래도 같은 오류가 나면 폴더의 `__pycache__` 를 삭제한 뒤 다시 실행하십시오.
""")
    st.stop()
    return False


def main() -> None:
    if not check_modules():
        return
    db.init_db()

    # 1) 로그인 게이트 — 통과 전에는 어떤 데이터도 조회하지 않는다
    user = auth.login_gate()
    if not user:
        return

    # 2) 매 요청마다 감사로그 주체와 데이터 접근범위를 다시 설정한다
    ent.apply_context(user)
    scope = db.current_scope()

    with st.sidebar:
        st.title("📈 영업관리 시스템")
        org_row = db._one("SELECT name FROM orgs WHERE id=?", [user.get("org_id")]) \
            if user.get("org_id") else None
        st.markdown(f"**{user['name']}** · {db.ROLE_LABEL.get(user['role'], user['role'])}")
        st.caption(f"{(org_row or {}).get('name', '미배정')} · "
                   + ("전사 데이터 조회" if scope is None else f"조회 범위 {len(scope)}명"))
        if st.button("로그아웃", key="logout_btn"):
            auth.logout()
            st.rerun()

        st.divider()
        available = menus_for(user)
        if ent.has_role(user, "MANAGER"):
            waiting = len(ent.pending_for(user))
            if waiting:
                st.warning(f"결재 대기 {waiting}건")
        menu = st.radio("메뉴", available, label_visibility="collapsed", key="menu")

        st.divider()
        months = db.month_options()
        default_ym = date.today().strftime("%Y-%m")
        ym = st.selectbox("기준월", months,
                          index=months.index(default_ym) if default_ym in months else len(months) - 1,
                          key="base_ym")
        owner_list = cached_owners()
        if len(owner_list) > 1:
            owner = st.selectbox("담당자 필터", ["전체"] + owner_list, key="base_owner")
            owner_filter = "" if owner == "전체" else owner
        else:                       # 영업사원은 본인 데이터만 보이므로 필터가 무의미
            owner_filter = ""
        st.divider()
        st.caption(f"오늘: {date.today():%Y-%m-%d}")
        st.caption("데이터는 로컬 SQLite(sales.db)에 저장됩니다.")

    st.title(menu)
    render_flash()

    if menu == M_DASH:
        page_dashboard(ym, owner_filter)
    elif menu == M_FORECAST:
        page_forecast(user, ym, owner_filter)
    elif menu == M_ANALYTICS:
        page_analytics(user, owner_filter)
    elif menu == M_CUST:
        page_customers(owner_filter)
    elif menu == M_DEALS:
        page_deals(user, owner_filter)
    elif menu == M_ACT:
        page_activities(owner_filter)
    elif menu == M_SALES:
        page_sales(user, ym, owner_filter)
    elif menu == M_TARGET:
        page_targets(ym)
    elif menu == M_APPROVAL:
        page_approvals(user)
    elif menu == M_IO:
        page_dataio(user)
    elif menu == M_ORG:
        page_org(user)
    elif menu == M_AUDIT:
        page_audit(user)
    elif menu == M_ADMIN:
        page_admin(user)


if __name__ == "__main__":
    main()