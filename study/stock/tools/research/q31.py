"""나쁜 해에도 20% 이상을 노리는 '하락 반등' 엔진 시뮬레이션(근사): 하락 중(20일 -10%↓)인 종목 중 반등 확률 예측이 가장 높은 종목을 N자리에 채우고,
+20% 도달·-10% 도달·60거래일 중 먼저 오는 때 정리(Q25b의 거래 결과 ret_b: 비용 0.6% 반영, 고가·저가 도달가 체결 가정 → 낙관적).
자리마다 거래를 이어서 복리(연수익 = 그 해에 닫힌 거래의 복리 평균). 예측은 걸어가며 학습(그 해 이전 데이터)한 결과. python q31.py"""
import os, numpy as np, pandas as pd
T = os.environ["TEMP"] + "/stk/"
A = pd.read_pickle(T + "Q25b_y20_하락.pkl"); A["date"] = pd.to_datetime(A["date"])
A = A[A.liqv >= 3e8].copy()
def run(df, N, top):
    out = {}
    slots = [pd.Timestamp("2000-01-01")] * N; eq = [1.0] * N; log = []
    for d, g in df.sort_values(["date", "p"], ascending=[True, False]).groupby("date"):
        thr = g.p.quantile(top) if top else -1
        for r in g[g.p >= thr].itertuples():
            free = [i for i in range(N) if slots[i] <= d]
            if not free: break
            i = free[0]; days = max(int(r.t20), 1) if r.t20 and r.t20 > 0 else 60
            end = d + pd.tseries.offsets.BDay(days)
            slots[i] = end; eq[i] *= 1 + r.ret_b; log.append((end.year, i, r.ret_b))
    L = pd.DataFrame(log, columns=["yr", "slot", "r"])
    yr = {}
    for y, g in L.groupby("yr"):
        yr[y] = ((g.groupby("slot").r.apply(lambda s: np.prod(1 + s) - 1)).reindex(range(N)).fillna(0)).mean() * 100
    return yr, len(L)
print("연수익(그 해에 닫힌 거래의 복리, 자리 평균) %, 거래 수")
for uni, df in (("전체", A), ("대형주", A[A.grp == "대형주"]), ("대형+중형", A[A.grp.isin(["대형주", "중형주"])])):
    for N in (3, 5, 10):
        yr, n = run(df, N, 0.7)
        print(f"{uni:8s} N={N:2d} 상위30% | " + " ".join(f"{y}:{v:+.0f}" for y, v in sorted(yr.items())) + f" | 거래 {n}")
