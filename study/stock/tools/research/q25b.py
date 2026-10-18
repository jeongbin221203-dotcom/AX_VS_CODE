"""'고점인가 더 오를 자리인가' · '하락이 일시적인가 계속되는가'(2026-10-18).
상승 중(20일 +10% 이상)과 하락 중(20일 −10% 이하)인 날만 따로 모아, 이후 60거래일 안에 +10%를 먼저 가는지(−7% 먼저가 아닌지) 예측.
걸어가며 검증(2022~2026): 학습은 그 해 이전, 라벨이 겹치지 않게 2개월 띄움. python q25.py"""
import os, sys, time, numpy as np, pandas as pd, joblib
from sklearn.ensemble import HistGradientBoostingClassifier as HC
from sklearn.metrics import roc_auc_score

T = os.environ["TEMP"] + "/stk/"
D = pd.read_pickle(T + "D6g.pkl"); F = pd.read_pickle(T + "F22.pkl"); L = pd.read_pickle(T + "L24.pkl")
D = D[D.yr >= 2016].merge(F, on=["code", "date"], how="left").merge(L, on=["code", "date"], how="inner")
cols0 = joblib.load(T + "m1_sets.joblib")["sets"]["C +차트 지표 전체"]
newc = [c for c in F.columns if c.startswith("p_")]
rk = [c for c in cols0 if D[c].nunique() > 10]
R = D.groupby("date")[rk].rank(pct=True).astype("float32"); R.columns = [c + "_r" for c in rk]
D = pd.concat([D, R], axis=1); D["gid"] = D.grp.map({"대형주": 0, "중형주": 1, "소형주": 2, "잡주": 3})
cols = cols0 + list(R.columns) + newc + ["gid"]
COST = 0.006
STATES = {"상승 중(20일 +10%↑)": D.ret20 >= 0.10, "하락 중(20일 −10%↓)": D.ret20 <= -0.10}
print("상태별 행:", {k: int(v.sum()) for k, v in STATES.items()}, flush=True)
t0 = time.time()
for (U, Dn, ycol, tcol) in ((0.20, 0.10, "y20", "t20"),):
    print(f"\n########## 목표 +{U*100:.0f}% 먼저 vs −{Dn*100:.0f}% 먼저 (60거래일 안, 고가·저가 기준)")
    for sname, smask in STATES.items():
        if '하락' not in sname: continue
        S = D[smask & D[ycol].notna()].copy()
        S["ret_b"] = np.where(S[ycol] == 1, U, np.where(S[ycol] == 0, -Dn, S.end_ret)) - COST
        outs = []
        for Y in range(2018, 2027):
            tr = S[(S.yr < Y) & (S.date < f"{Y - 1}-10-15")]; te = S[S.yr == Y]
            if len(tr) < 5000 or len(te) < 500: continue
            if len(tr) > 400000: tr = tr.sample(400000, random_state=0)
            m = HC(max_iter=150, learning_rate=0.06, max_depth=5, random_state=0).fit(tr[cols].astype("float32"), tr[ycol].astype(int))
            te = te.copy(); te["p"] = m.predict_proba(te[cols].astype("float32"))[:, 1]; outs.append(te[["code", "date", "yr", "grp", ycol, tcol, "ret_b", "p", "liqv", "end_ret"]])
        A = pd.concat(outs, ignore_index=True)
        A["dec"] = A.groupby("date").p.rank(pct=True)
        base = A[ycol].mean(); auc = roc_auc_score(A[ycol].astype(int), A.p)
        print(f"\n[{sname}] 시험 {len(A):,}건(2022~2026) | 기준 '먼저 +{U*100:.0f}%' 비율 {base*100:.1f}% | AUC {auc:.3f} | 기준 거래 기대수익 {A.ret_b.mean()*100:+.2f}% (평균 {A[tcol].mean():.0f}일)")
        for lab, sub in (("예측 상위 10%", A[A.p >= A.p.quantile(0.9)]), ("예측 상위 30%", A[A.p >= A.p.quantile(0.7)]), ("예측 하위 30%", A[A.p <= A.p.quantile(0.3)])):
            e = sub.ret_b.mean(); days = sub[tcol].where(sub[tcol] > 0, 60).mean()
            print(f"   {lab}: 먼저 +{U*100:.0f}% 비율 {sub[ycol].mean()*100:.1f}% | 거래 기대수익 {e*100:+.2f}% | 평균 보유 {days:.0f}일 | 건수 {len(sub):,}")
        for g in ("대형주", "중형주", "소형주", "잡주"):
            a = A[A.grp == g]
            if len(a) < 800: continue
            t = a[a.p >= a.p.quantile(0.9)]
            print(f"   {g}: AUC {roc_auc_score(a[ycol].astype(int), a.p):.3f} | 상위10% 먼저+ {t[ycol].mean()*100:.1f}% (그룹 기준 {a[ycol].mean()*100:.1f}%) 기대수익 {t.ret_b.mean()*100:+.2f}% (기준 {a.ret_b.mean()*100:+.2f}%)")
        print(f"   ({time.time() - t0:.0f}초)", flush=True)
        A.to_pickle(T + f"Q25b_{ycol}_{sname[:2]}.pkl")
