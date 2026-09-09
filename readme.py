import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import matplotlib.font_manager as fm
import os

# ==========================================
# 0. 한글 폰트 및 페이지 설정
# ==========================================
st.set_page_config(page_title="무역 분석 대시보드", layout="wide")

font_path = "../AX2_JBL/0907/NanumGothic-Bold.ttf"
try:
    font_name = fm.FontProperties(fname=font_path).get_name()
    plt.rc('font', family=font_name)
except Exception:
    if os.name == 'nt':
        plt.rc('font', family='Malgun Gothic')
    else:
        plt.rc('font', family='AppleGothic')
plt.rcParams['axes.unicode_minus'] = False


# ==========================================
# 1. 데이터 로드 및 전처리 (자동 컬럼 감지)
# ==========================================
@st.cache_data
def load_data():
    baci_df = pd.read_csv('baci_85_sample.csv', encoding='utf-8')
    country_df = pd.read_csv('country_codes_sample.csv', encoding='utf-8')

    baci_df = baci_df.dropna(subset=['t', 'i'])
    baci_df['v'] = baci_df['v'].fillna(0)

    # 💡 CSV 파일의 컬럼명을 자동으로 찾아서 알아서 연결해주는 마법의 코드!
    c_cols = country_df.columns.tolist()
    
    # 1. 코드 컬럼 자동 감지 (i, code, id 등)
    code_col = c_cols[0]
    for col in c_cols:
        if col.lower() in ['country_code', 'code', 'i', 'id', '국가코드']:
            code_col = col
            break
            
    # 2. 국가명 컬럼 자동 감지 (name, kr, 국가명 등) 
    name_col = c_cols[1] if len(c_cols) > 1 else c_cols[0]
    for col in c_cols:
        if any(x in col.lower() for x in ['name', 'kr', '국가명', '이름', 'country']):
            name_col = col
            break

    # 자동 감지된 컬럼으로 병합
    df = pd.merge(baci_df, country_df, left_on='i', right_on=code_col, how='inner')
    
    # 컬럼명 통일
    df = df.rename(columns={'t': '연도', name_col: '국가명', 'v': '수출액'})

    # 무역액 기준 3등분하여 등급 부여
    # 수출액이 같거나 0인 데이터가 많아 그룹이 안 나뉘는 현상 방지 (rank 사용)
    df['무역액등급'] = pd.qcut(df['수출액'].rank(method='first'), q=3, labels=['소', '중', '대'])
    
    return df

try:
    df = load_data()

    # ==========================================
    # 2. 사이드바 (조건 검색)
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
    # 3. 메인 화면 (오른쪽)
    # ==========================================
    st.title("📊 무역 분석 대시보드")
    st.markdown("---")

    col1, col2 = st.columns(2)
    col1.metric(label="총 거래건수", value=f"{len(filtered_df):,.0f} 건")
    col2.metric(label="총 수출액", value=f"${filtered_df['수출액'].sum():,.2f}")
    
    st.markdown("---")

    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        st.subheader("국가별 연도별 수출액 (상위 8개국)")
        if not filtered_df.empty:
            top8 = filtered_df.groupby('국가명')['수출액'].sum().nlargest(8).index
            heat_df = filtered_df[filtered_df['국가명'].isin(top8)].pivot_table(
                index='국가명', columns='연도', values='수출액', aggfunc='sum'
            ).fillna(0)

            fig1, ax1 = plt.subplots(figsize=(8, 6))
            sns.heatmap(heat_df, cmap="Blues", ax=ax1, annot=False)
            ax1.set_ylabel("")
            st.pyplot(fig1)
        else:
            st.info("조건에 맞는 데이터가 없습니다.")

    with chart_col2:
        st.subheader("무역액 등급 분포")
        if not filtered_df.empty:
            fig2, ax2 = plt.subplots(figsize=(8, 6))
            sns.countplot(
                data=filtered_df, 
                x='무역액등급', 
                order=['대', '중', '소'], 
                palette='pastel', 
                ax=ax2
            )
            ax2.set_xlabel("")
            ax2.set_ylabel("거래 건수")
            st.pyplot(fig2)
        else:
            st.info("조건에 맞는 데이터가 없습니다.")

    st.markdown("---")

    st.subheader("상위 5개국 무역액 등급 교차표")
    if not filtered_df.empty:
        top5 = filtered_df.groupby('국가명')['수출액'].sum().nlargest(5).index
        cross_df = filtered_df[filtered_df['국가명'].isin(top5)]

        tab1, tab2 = st.tabs(["원본 건수", "정규화 비율 (%)"])
        
        with tab1:
            ct_count = pd.crosstab(cross_df['국가명'], cross_df['무역액등급'], margins=True, margins_name="총계")
            ct_count = ct_count.reindex(columns=['대', '중', '소', '총계'])
            st.dataframe(ct_count, use_container_width=True)
            
        with tab2:
            ct_norm = pd.crosstab(cross_df['국가명'], cross_df['무역액등급'], normalize='index') * 100
            ct_norm = ct_norm.reindex(columns=['대', '중', '소'])
            st.dataframe(ct_norm.style.format("{:.2f}%"), use_container_width=True)

except Exception as e:
    st.error(f"오류가 발생했습니다: {e}")