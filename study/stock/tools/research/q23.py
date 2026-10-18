"""패턴 지표를 더하면 예측이 좋아지는지(2026-10-18): 기본 지표(94개+순위) 대 기본+패턴(q22)을 같은 방식으로 걸어가며 검증(2022~2026).
(A) 다음 거래일 시가→종가 방향·순위, (B) 20거래일 수익률 순위(상위 K 보유 수익, 비용 0.6%). python q23.py"""
import os, sys, time, numpy as np, pandas as pd, joblib
from sklearn.ensemble import HistGradientBoostingClassifier as HC, HistGradientBoostingRegressor as HR
from sklearn.metrics import roc_auc_score

T = os.environ["TEMP"] + "/stk/"
D = pd.read_pickle(T + "D6g.pkl"); L = pd.read_pickle(T + "D1.pkl"); F = pd.read_pickle(T + "F22.pkl")
D = D.merge(L, on=["code", "date"], how="left").merge(F, on=["code", "date"], how="left")
cols0 = joblib.load(T + "m1_sets.joblib")["sets"]["C +차트 지표 전체"]
rk = [c for c in cols0 if D[c].nunique() > 10]
newc = [c for c in F.columns if c.startswith("p_")]
nrk = [c for c in newc if D[c].nunique() > 10 and not c.startswith("p_full") and not c.startswith("p_touch") and not c.startswith("p_bounce") and not c.startswith("p_swing_")]
R = D.groupby("date")[rk + nrk].rank(pct=True).astype("float32"); R.columns = [c + "_r" for c in rk + nrk]
D = pd.concat([D, R], axis=1)
D["gid"] = D.grp.map({"대형주": 0, "중형주": 1, "소형주": 2, "잡주": 3})
base_cols = cols0 + [c + "_r" for c in rk] + ["gid"]
new_cols = base_cols + newc + [c + "_r" for c in nrk]
D = D[D.yr >= 2016].copy()
COST = 0.006
print("행", len(D), "기본 특징", len(base_cols), "패턴 포함", len(new_cols), flush=True)


def fit(tr, cols, target):
    X = tr[cols].astype("float32")
    if target == "r1o":
        y = tr.groupby("date")["r1o"].rank(pct=True).astype("float32"); reg = HR(max_iter=150, learning_rate=0.06, max_depth=6, min_samples_leaf=200, l2_regularization=2.0, random_state=0).fit(X, y)
        clf = HC(max_iter=150, learning_rate=0.06, max_depth=5, random_state=0).fit(X, (tr.r1o > 0).astype(int)); return reg, clf
    y = tr.groupby("date")["r20"].rank(pct=True).astype("float32")
    reg = HR(max_iter=150, learning_rate=0.06, max_depth=6, min_samples_leaf=200, l2_regularization=2.0, random_state=0).fit(X, y)
    clf = HC(max_iter=150, learning_rate=0.06, max_depth=5, random_state=0).fit(X, (tr.x20 >= 0.08).astype(int)); return reg, clf


res = {}
t0 = time.time()
for target, lab in (("r1o", "다음 거래일"), ("r20", "20거래일")):
    d0 = D.dropna(subset=[target]).copy()
    for name, cols in (("기본", base_cols), ("기본+패턴", new_cols)):
        outs = []
        for Y in range(2022, 2027):
            gap = f"{Y - 1}-12-01" if target == "r20" else f"{Y}-01-01"
            tr = d0[(d0.yr < Y) & (d0.date < gap)]
            if len(tr) > 600000: tr = tr.sample(600000, random_state=0)
            te = d0[d0.yr == Y].copy()
            reg, clf = fit(tr, cols, target)
            Xt = te[cols].astype("float32"); te["p_rank"] = reg.predict(Xt); te["p_up"] = clf.predict_proba(Xt)[:, 1]
            outs.append(te[["code", "date", "yr", "grp", target, "liqv", "p_rank", "p_up"]])
            print(target, name, Y, f"{time.time() - t0:.0f}초", flush=True)
        A = pd.concat(outs, ignore_index=True)
        A["score"] = (A.groupby("date").p_rank.rank(pct=True) + A.groupby("date").p_up.rank(pct=True)) / 2
        res[(target, name)] = A
        A.to_pickle(T + f"Q23_{target}_{name}.pkl")
        y = A[target] > (0 if target == "r1o" else 0.0)
        print(f"== [{lab}] {name}: AUC(상승 방향) {roc_auc_score((A[target] > 0).astype(int), A.score):.4f}", flush=True)
        for g in (None, "대형주", "중형주", "소형주", "잡주"):
            d = A if g is None else A[A.grp == g]
            d = d[d.liqv >= 3e8]
            dd = d.assign(dec=d.groupby("date").score.rank(pct=True)); top, bot = dd[dd.dec > 0.9], dd[dd.dec <= 0.1]
            K = 5
            tk = d.sort_values(["date", "score"], ascending=[True, False]).groupby("date").head(K)
            gross = tk.groupby("date")[target].mean(); net = gross - COST
            if target == "r1o": ann = (1 + net.mean()) ** 245 - 1; extra = f"매일 상위{K} 순 연환산 {ann*100:+.0f}% (일평균 총 {gross.mean()*100:+.2f}%)"
            else:
                m = net[net.index >= "2024"]; extra = f"상위{K} 20일 순 평균 {net.mean()*100:+.2f}% (2024~ {m.mean()*100:+.2f}%), 20일 보유 연환산(단순 12.2회) {(1+m.mean())**12.2*100-100:+.0f}%"
            print(f"   {g or '전체'}: 상위10% 평균 {top[target].mean()*100:+.2f}% 하위10% {bot[target].mean()*100:+.2f}% 격차 {(top[target].mean()-bot[target].mean())*100:.2f}%p | {extra}", flush=True)
print("끝", f"{time.time() - t0:.0f}초")
