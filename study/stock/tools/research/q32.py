"""2022년(하락장)에 수익을 낼 수 있었던 방법 연구(2026-10-18): 같은 해 20일 뒤 수익률(r20)을 기준으로
(1) 어떤 지표가 2022년에 잘 맞았는지(순위 상관), (2) 전략별 2022년 20일 평균 수익(비용 0.6% 후)과 연환산(근사), (3) 시장 지수 하락에 베팅(인버스 근사)·롱숏.
python q32.py"""
import os, sys, numpy as np, pandas as pd, joblib
from scipy.stats import spearmanr

T = os.environ["TEMP"] + "/stk/"
D = pd.read_pickle(T + "D6g.pkl"); F = pd.read_pickle(T + "F22.pkl"); A = pd.read_pickle(T + "AO.pkl")[["date", "code", "r_ens", "score", "grp"]].rename(columns={"grp": "g2"})
D = D[D.yr >= 2021].merge(F, on=["code", "date"], how="left").merge(A, on=["date", "code"], how="left").dropna(subset=["r20"])
COST = 0.006
cols0 = [c for c in joblib.load(T + "m1_sets.joblib")["sets"]["C +차트 지표 전체"] if c in D.columns] + [c for c in F.columns if c.startswith("p_")]
Y = D[D.yr == 2022]; O = D[D.yr >= 2023]
print(f"2022: 표본 {len(Y):,}행, 그 해 20일 수익 평균 {Y.r20.mean()*100:+.2f}%, 중앙 {Y.r20.median()*100:+.2f}%, 상승 비율 {(Y.r20>0).mean()*100:.0f}%")
# (1) 지표별 순위 상관(날짜별) 평균 — 2022 vs 2023~
def ic(df, c):
    v = []
    for d, g in df[[c, "r20", "date"]].dropna().groupby("date"):
        if len(g) > 200 and g[c].nunique() > 5: v.append(spearmanr(g[c], g.r20)[0])
    return np.nanmean(v) if v else np.nan
res = []
for c in cols0:
    if D[c].nunique() < 6: continue
    res.append((c, ic(Y, c), ic(O, c)))
R = pd.DataFrame(res, columns=["지표", "IC_2022", "IC_2023~"]).dropna()
print("\n[2022년에 20일 수익과 가장 같이 움직인 지표] (양수=높을수록 그 뒤 수익↑, 음수=낮을수록 수익↑)")
for r in R.reindex(R.IC_2022.abs().sort_values(ascending=False).index).head(12).itertuples():
    print(f"   {r.지표:14s} 2022 {r.IC_2022:+.3f} | 2023~ {r._3:+.3f}")
# (2) 전략별 2022 성과(날짜마다 상위 K 평균 20일 수익, 비용 후)
def strat(df, key, K=5, asc=False, liq=3e8):
    d = df[df.liqv >= liq].dropna(subset=[key])
    top = d.sort_values(["date", key], ascending=[True, asc]).groupby("date").head(K)
    return top.groupby("date").r20.mean() - COST
names = {"AI 점수(그룹 안 순위)": ("score", False), "6개월 모멘텀 상위": ("mom6_1", False), "60일 시장대비 강세": ("rs60", False), "낮은 변동성": ("vol60", True), "베타 낮음": ("beta60", True), "250일 고점 근접": ("nh250", False),
         "과매도(RSI 낮음)": ("rsi14", True), "깊은 낙폭(120일 고점 대비)": ("dd120", True), "거래량 급증 후 횡보(burst_flat)": ("p_burst_flat30", False)}
print("\n[전략별 20일 평균 수익(비용 후): 2022 | 2023~ | 2022 연환산(복리 12.2회, 낙관)]")
for nm, (key, asc) in names.items():
    if key not in D.columns: continue
    for g in (None, "대형주"):
        d1 = Y if g is None else Y[Y.grp == g]; d2 = O if g is None else O[O.grp == g]
        s1, s2 = strat(d1, key, 5, asc), strat(d2, key, 5, asc)
        if len(s1) == 0: continue
        print(f"   {nm:24s} {g or '전체':4s}: 2022 {s1.mean()*100:+.2f}% | 2023~ {s2.mean()*100:+.2f}% | 연환산 {((1+s1.mean())**12.2-1)*100:+.0f}% (날짜 {len(s1)}개)")
# 롱숏: AI 상위5 롱 - 하위5 숏(숏 비용 포함 0.3% 추가)
for g in (None, "대형주"):
    d1 = Y if g is None else Y[Y.grp == g]; d1 = d1[d1.liqv >= 3e8].dropna(subset=["score"])
    lo = d1.sort_values(["date", "score"], ascending=[True, False]).groupby("date").head(5).groupby("date").r20.mean()
    sh = d1.sort_values(["date", "score"], ascending=[True, True]).groupby("date").head(5).groupby("date").r20.mean()
    ls = (lo - sh) / 2 - 0.006 - 0.003
    print(f"   AI 롱 상위5 / 숏 하위5(절반씩, 숏 비용 +0.3%) {g or '전체'}: 2022 20일 평균 {ls.mean()*100:+.2f}% → 연환산 {((1+ls.mean())**12.2-1)*100:+.0f}% (롱만 {lo.mean()*100:+.2f}%, 숏 레그 {(-sh).mean()*100:+.2f}%)")
# (3) 시장 지수 인버스 근사
M = pd.read_pickle("data/market_idx.pkl"); lvl = (1 + M["mkt_ret"].fillna(0)).cumprod(); lvl.index = pd.to_datetime(lvl.index)
ma200 = lvl.rolling(200).mean(); below = (lvl < ma200).shift(1).fillna(False)
y22 = lvl.index.year == 2022
print(f"\n[시장 지수(전 종목 동일 비중 근사)] 2022 수익 {(lvl[y22].iloc[-1]/lvl[y22].iloc[0]-1)*100:+.1f}% | 200일선 아래였던 날 {below[y22].mean()*100:.0f}%")
inv = np.where(below, -M["mkt_ret"].fillna(0).values, 0.0); inv = pd.Series(inv, index=lvl.index)
for lab, w in (("200일선 아래일 때 지수 1배 인버스", 1.0), ("2배 인버스", 2.0)):
    r = inv * w - (below.astype(float).diff().abs().fillna(0)) * 0.003
    print(f"   {lab}: 2022 {((1+r[y22]).prod()-1)*100:+.1f}% | 2018 {((1+r[r.index.year==2018]).prod()-1)*100:+.1f}% | 2020 {((1+r[r.index.year==2020]).prod()-1)*100:+.1f}% | 2017~2026 연환산 {((1+r['2017':]).prod()**(245/len(r['2017':]))-1)*100:+.1f}%")
