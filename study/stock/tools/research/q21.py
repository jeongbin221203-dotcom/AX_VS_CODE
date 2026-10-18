"""다음 거래일 예측 능력 검증(2026-10-18): 과거 데이터(~전 해)로 학습해 그 해의 '다음 거래일 시가→종가 수익률'을 예측하고,
방향 적중률·AUC·상위/하위 10% 차이와, 매일 상위 K개를 사고 당일 종가에 파는 거래(비용 0.6%)의 수익을 본다. 걸어가며 검증 2022~2026.
python q21.py → %TEMP%/stk/Q21.pkl"""
import os, sys, time, numpy as np, pandas as pd, joblib
from sklearn.ensemble import HistGradientBoostingClassifier as HC, HistGradientBoostingRegressor as HR
from sklearn.metrics import roc_auc_score

T = os.environ["TEMP"] + "/stk/"
D = pd.read_pickle(T + "D6g.pkl"); L = pd.read_pickle(T + "D1.pkl")
D = D.merge(L, on=["code", "date"], how="left")
cols0 = joblib.load(T + "m1_sets.joblib")["sets"]["C +차트 지표 전체"]
rk = [c for c in cols0 if D[c].nunique() > 10]
R = D.groupby("date")[rk].rank(pct=True).astype("float32"); R.columns = [c + "_r" for c in rk]
D = pd.concat([D, R], axis=1)
gmap = {"대형주": 0, "중형주": 1, "소형주": 2, "잡주": 3}
D["gid"] = D.grp.map(gmap)
cols = cols0 + list(R.columns) + ["gid"]
D = D.dropna(subset=["r1o", "r1c"])
D = D[D.yr >= 2016]
D["y_rank"] = D.groupby("date")["r1o"].rank(pct=True).astype("float32")
D["up"] = (D.r1o > 0).astype(int)
COST = 0.006
out = []; t0 = time.time()
for Y in range(2022, 2027):
    tr = D[(D.yr < Y)]
    if len(tr) > 600000: tr = tr.sample(600000, random_state=0)
    te = D[D.yr == Y].copy()
    X, Xt = tr[cols].astype("float32"), te[cols].astype("float32")
    reg = HR(max_iter=150, learning_rate=0.06, max_depth=6, min_samples_leaf=200, l2_regularization=2.0, random_state=0).fit(X, tr.y_rank)
    clf = HC(max_iter=150, learning_rate=0.06, max_depth=5, random_state=0).fit(X, tr.up)
    te["p_rank"] = reg.predict(Xt); te["p_up"] = clf.predict_proba(Xt)[:, 1]
    out.append(te[["code", "date", "yr", "grp", "r1o", "r1c", "gap", "liqv", "p_rank", "p_up"]])
    print(Y, len(tr), len(te), f"{time.time() - t0:.0f}초", flush=True)
A = pd.concat(out, ignore_index=True); A.to_pickle(T + "Q21.pkl")
A["score"] = (A.groupby("date").p_rank.rank(pct=True) + A.groupby("date").p_up.rank(pct=True)) / 2
print("\n== 예측 능력(시험: 걸어가며 검증, 그 해의 다음 거래일 시가→종가)")
for name, d in (("전체", A), *[(g, A[A.grp == g]) for g in gmap]):
    auc = roc_auc_score((d.r1o > 0).astype(int), d.score)
    dd = d.assign(dec=d.groupby("date").score.rank(pct=True))
    top, bot = dd[dd.dec > 0.9], dd[dd.dec <= 0.1]
    hit = (top.r1o > 0).mean()
    print(f"{name}: AUC {auc:.3f} | 상위10% 상승 비율 {hit*100:.1f}% (전체 {(d.r1o>0).mean()*100:.1f}%) 평균 {top.r1o.mean()*100:+.2f}% | 하위10% 평균 {bot.r1o.mean()*100:+.2f}% | 격차 {(top.r1o.mean()-bot.r1o.mean())*100:.2f}%p")
print("\n== 매일 상위 K개를 시가에 사서 종가에 파는 거래(왕복 비용 0.6%) 연환산(1년 245거래일, 복리)")
for g in [None, "대형주", "중형주", "소형주", "잡주"]:
    d = A if g is None else A[A.grp == g]
    d = d[d.liqv >= 3e8]
    for K in (5, 20):
        top = d.sort_values(["date", "score"], ascending=[True, False]).groupby("date").head(K)
        gross = top.groupby("date").r1o.mean(); net = gross - COST
        for lbl, s in (("전체 2022~26", net), ("2024~26", net[net.index >= "2024"])):
            ann = (1 + s.mean()) ** 245 - 1
            print(f"  {g or '전체'} K={K:2d} {lbl}: 일평균 총 {gross[s.index].mean()*100:+.2f}% → 순 {s.mean()*100:+.2f}% | 연환산(순) {ann*100:+.0f}%")
