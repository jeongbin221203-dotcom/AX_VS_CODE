"""오르는 종목 패턴 찾기(2026-10-18): 그룹별로 '이후 20거래일에 +10% 이상 오른' 날(상승)의 직전 지표 모양을 찾는다.
(1) 지표 하나씩: 학습(~2021) 기준 AUC 상위 + 검증(2022~23)·시험(2024~26)에서도 같은 방향인지, (2) 얕은 결정나무로 읽을 수 있는 규칙(잎)을 뽑고 검증·시험 적중률을 확인.
python q18.py"""
import os, sys, numpy as np, pandas as pd
from sklearn.tree import DecisionTreeClassifier, export_text
from sklearn.metrics import roc_auc_score
import joblib

T = os.environ["TEMP"] + "/stk/"
D = pd.read_pickle(T + "D6g.pkl")
cols = [c for c in joblib.load(T + "m1_sets.joblib")["sets"]["C +차트 지표 전체"] if c in D.columns and D[c].nunique() > 5]
GR = ["대형주", "중형주", "소형주", "잡주"]
NAMES = {"rsi14": "RSI", "bb_pctb": "볼린저 %B", "bb_width": "볼린저 폭", "bb_width_pct": "볼린저 폭 순위", "dist20": "20일선 이격", "dist60": "60일선 이격", "dist112": "112일선 이격", "dist224": "224일선 이격",
         "slope20": "20일선 기울기", "slope60": "60일선 기울기", "pos60": "60일 구간 위치", "nh250": "250일 신고가 근접", "ret5": "5일 수익률", "ret20": "20일 수익률", "ret60": "60일 수익률", "ret120": "120일 수익률", "ret250": "250일 수익률",
         "volratio": "거래량/평균", "vr5": "5일 거래량비", "obv20": "OBV 20일", "cmf20": "CMF20", "adx": "ADX", "stoch_k": "스토캐스틱K", "cci20": "CCI20", "mfi14": "MFI14", "atrp": "변동폭(ATR%)",
         "vol20": "20일 변동성", "dd120": "120일 고점 대비", "from_high250": "250일 고점 대비", "from_low250": "250일 저점 대비", "mom12_1": "모멘텀 12-1", "mom6_1": "모멘텀 6-1", "rs60": "시장대비 60일", "rs20": "시장대비 20일",
         "aligned": "정배열", "reversed": "역배열", "macd_n": "MACD", "macd_hist_n": "MACD 히스토그램", "above200": "200일선 위", "low_age": "저점 경과일", "high_age": "고점 경과일", "range20": "20일 등락폭", "vol_z60": "거래량 z", "ichi_cloud": "일목 구름", "donch55": "돈치안55", "keltner": "켈트너"}
nm = lambda c: NAMES.get(c, c)
for g in GR:
    G = D[(D.grp == g)].dropna(subset=["r20"]).copy()
    G["up"] = (G.r20 >= 0.10).astype(int)
    tr, va, te = G[G.yr <= 2021], G[(G.yr >= 2022) & (G.yr <= 2023)], G[G.yr >= 2024]
    print(f"\n=============== {g} (행 {len(G):,}) 20일 +10% 이상 비율: 학습 {tr.up.mean()*100:.1f}% 검증 {va.up.mean()*100:.1f}% 시험 {te.up.mean()*100:.1f}%")
    res = []
    for c in cols:
        a = []
        for df in (tr, va, te):
            d = df[[c, "up"]].dropna()
            a.append(roc_auc_score(d.up, d[c]) if d.up.nunique() == 2 and len(d) > 500 else np.nan)
        res.append((c, *a))
    R = pd.DataFrame(res, columns=["c", "tr", "va", "te"]).dropna()
    R["str"] = (R.tr - 0.5).abs()
    R["same"] = ((R.tr - 0.5) * (R.va - 0.5) > 0) & ((R.tr - 0.5) * (R.te - 0.5) > 0)
    R = R[R.same].sort_values("str", ascending=False).head(8)
    print("지표 하나씩(AUC: 0.5=무관, 학습/검증/시험 같은 방향만):")
    for r in R.itertuples():
        print(f"   {nm(r.c):14s} {'높을수록' if r.tr > 0.5 else '낮을수록'} 상승  AUC {r.tr:.3f}/{r.va:.3f}/{r.te:.3f}")
    X = tr[cols].fillna(tr[cols].median())
    tree = DecisionTreeClassifier(max_depth=4, min_samples_leaf=max(300, len(tr) // 150), random_state=0).fit(X, tr.up)
    leaf_tr = tree.apply(X); leaf_va = tree.apply(va[cols].fillna(tr[cols].median())); leaf_te = tree.apply(te[cols].fillna(tr[cols].median()))
    txt = export_text(tree, feature_names=[nm(c) for c in cols], max_depth=6)
    rows = []
    for leaf in np.unique(leaf_tr):
        m = leaf_tr == leaf; mv = leaf_va == leaf; mt = leaf_te == leaf
        if m.sum() < 300 or mv.sum() < 50 or mt.sum() < 50: continue
        rows.append((leaf, m.sum(), tr.up[m].mean(), va.up[mv].mean(), te.up[mt].mean(), mv.sum(), mt.sum(), tr.r20[m].mean(), va.r20[mv].mean(), te.r20[mt].mean()))
    L = pd.DataFrame(rows, columns=["leaf", "n", "p_tr", "p_va", "p_te", "n_va", "n_te", "r_tr", "r_va", "r_te"])
    L["lift"] = L.p_te / te.up.mean()
    best = L[(L.p_tr > tr.up.mean() * 1.3) & (L.p_va > va.up.mean()) & (L.p_te > te.up.mean())].sort_values("p_te", ascending=False).head(3)
    print("결정나무 규칙(학습으로 만든 잎, 검증·시험에서도 기준선보다 높은 것):")
    for r in best.itertuples():
        path = tree.decision_path(X.iloc[[np.flatnonzero(leaf_tr == r.leaf)[0]]]).indices
        feats = tree.tree_.feature; thr = tree.tree_.threshold; cond = []
        x0 = X.iloc[np.flatnonzero(leaf_tr == r.leaf)[0]]
        for nd in path[:-1]:
            f, t = feats[nd], thr[nd]; cond.append(f"{nm(cols[f])} {'≤' if x0.iloc[f] <= t else '>'} {t:.3g}")
        print(f"   [{' 그리고 '.join(cond)}]")
        print(f"      20일 +10%↑ 적중 학습 {r.p_tr*100:.0f}% 검증 {r.p_va*100:.0f}% 시험 {r.p_te*100:.0f}% (기준 {te.up.mean()*100:.0f}%) | 20일 평균 수익 {r.r_tr*100:+.1f}%/{r.r_va*100:+.1f}%/{r.r_te*100:+.1f}% | 표본 {r.n:,}/{r.n_va:,}/{r.n_te:,}")
