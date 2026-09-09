import streamlit as st
import pandas as pd
import plotly.express as px

# ==========================================
# 0. 페이지 설정
# ==========================================
st.set_page_config(page_title="무역 분석 대시보드", layout="wide")

# ==========================================
# 1. 데이터 로드 및 전처리
# ==========================================
@st.cache_data
def load_data():
    baci_df = pd.read_csv('baci_85_sample.csv', encoding='utf-8')
    country_df = pd.read_csv('country_codes_sample.csv', encoding='utf-8')

    # 결측치 처리
    baci_df = baci_df.dropna(subset=['t', 'j'])
    baci_df['v'] = pd.to_numeric(baci_df['v'], errors='coerce').fillna(0)

    # 데이터 병합
    baci_df['j'] = baci_df['j'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
    country_df['j'] = country_df['j'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
    df = pd.merge(baci_df, country_df, on='j', how='inner')
    df = df.rename(columns={'t': '연도', 'country_name': '국가명', 'v': '수출액'})

    # 무역액 등급 부여
    if len(df) >= 3:
        df['무역액등급'] = pd.qcut(df['수출액'].rank(method='first'), q=3, labels=['소', '중', '대'])
    else:
        df['무역액등급'] = '소'
    
    return df

try:
    df = load_data()

    # ==========================================
    # 사이드바 (조건 검색)
    # ==========================================
    st.sidebar.header("🔍 검색 필터")
    
    all_countries = sorted(df['국가명'].astype(str).unique())
    selected_countries = st.sidebar.multiselect("국가 선택", all_countries, default=all_countries[:15])
    
    selected_tiers = st.sidebar.multiselect("무역액등급 선택", ['대', '중', '소'], default=['대', '중', '소'])

    filtered_df = df[
        (df['국가명'].isin(selected_countries)) & 
        (df['무역액등급'].isin(selected_tiers))
    ]

    # ==========================================
    # 메인 화면 (오른쪽)
    # ==========================================
    
    st.title("1. 무역 분석 대시보드")
    st.markdown("---")

    st.subheader("2. baci_85_sample.csv 파일의 결측치 처리")
    st.write("✅ 기준 연도 및 목적지 누락 데이터 제거 완료")
    st.write("✅ 수출액 누락 데이터 '0'으로 대체 완료")
    st.markdown("---")

    st.subheader("3. 총거래건수 / 총수출액(달러)")
    col1, col2 = st.columns(2)
    col1.metric(label="총 거래건수", value=f"{len(filtered_df):,.0f} 건")
    col2.metric(label="총 수출액", value=f"${filtered_df['수출액'].sum():,.2f}")
    st.markdown("---")

    # ==========================================
    # 반응형 차트 (Plotly 적용 - 이전 색상 복구)
    # ==========================================
    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        st.subheader("4. 국가 * 연도 수출액 (상위 8개국)")
        if not filtered_df.empty:
            top8 = filtered_df.groupby('국가명')['수출액'].sum().nlargest(8).index
            heat_df = filtered_df[filtered_df['국가명'].isin(top8)].pivot_table(
                index='국가명', columns='연도', values='수출액', aggfunc='sum'
            ).fillna(0)

            # 반응형 히트맵 (색상을 파란색 'Blues'로 변경)
            fig1 = px.imshow(heat_df,
                             labels=dict(x="연도", y="국가명", color="총 수출액(달러)"),
                             x=heat_df.columns,
                             y=heat_df.index,
                             color_continuous_scale="Blues", # 이전 파란색 톤으로 복구
                             text_auto=",.0f",
                             aspect="auto")
            
            fig1.update_layout(title="국가별/연도별 수출액 히트맵", title_font_size=16)
            st.plotly_chart(fig1, use_container_width=True)
        else:
            st.info("조건에 맞는 데이터가 없습니다.")

    with chart_col2:
        st.subheader("4. 무역액 등급 분포")
        if not filtered_df.empty:
            tier_counts = filtered_df['무역액등급'].value_counts().reindex(['대', '중', '소']).reset_index()
            tier_counts.columns = ['무역액 등급', '거래 건수']

            # 반응형 막대 차트 (이전 파스텔 색상: 파랑, 주황, 초록 조합으로 복구)
            fig2 = px.bar(tier_counts, 
                          x='무역액 등급', 
                          y='거래 건수',
                          text='거래 건수',
                          color='무역액 등급',
                          color_discrete_sequence=['#a1c9f4', '#ffb482', '#8de5a1']) # 이전 느낌의 파스텔톤 지정
            
            fig2.update_traces(textposition='outside')
            fig2.update_layout(title="무역액 등급(대/중/소)별 거래 빈도", 
                               title_font_size=16, 
                               showlegend=False)
            
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("조건에 맞는 데이터가 없습니다.")

    st.markdown("---")

    # ==========================================
    # 교차표
    # ==========================================
    st.subheader("5. 상위 5개국 * 무역액 등급 교차표")
    if not filtered_df.empty:
        top5 = filtered_df.groupby('국가명')['수출액'].sum().nlargest(5).index
        cross_df = filtered_df[filtered_df['국가명'].isin(top5)]

        tbl_col1, tbl_col2 = st.columns(2)

        with tbl_col1:
            st.markdown("##### 📌 원본 건수 (단위: 건)")
            ct_count = pd.crosstab(cross_df['국가명'], cross_df['무역액등급'], margins=True, margins_name="총계")
            cols = [c for c in ['대', '중', '소', '총계'] if c in ct_count.columns]
            ct_count = ct_count.reindex(columns=cols)
            st.dataframe(ct_count, use_container_width=True)
            
        with tbl_col2:
            st.markdown("##### 📌 정규화 비율 (%)")
            ct_norm = pd.crosstab(cross_df['국가명'], cross_df['무역액등급'], normalize='index') * 100
            cols = [c for c in ['대', '중', '소'] if c in ct_norm.columns]
            ct_norm = ct_norm.reindex(columns=cols)
            st.dataframe(ct_norm.style.format("{:.2f}%"), use_container_width=True)
    else:
        st.info("조건에 맞는 데이터가 없습니다.")

except Exception as e:
    st.error(f"오류가 발생했습니다: {e}")