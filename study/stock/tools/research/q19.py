"""그룹별 이론상 최대 수익(미래를 아는 매매) — 종목-연도(2017~2026) 중앙값. 비용: 한쪽 0.3%, 종가 체결.
(A) 횟수 제한 없음 (B) 연 4번 왕복만 (C) 연 12번 왕복만."""
import os, sys, numpy as np, pandas as pd
T = os.environ["TEMP"] + "/stk/"
P = pd.read_pickle(T + "Q1.pkl")
COST = 0.003
def best_unlimited(c):
    cash, hold = 1.0, 1.0 * (1 - COST)
    for i in range(1, len(c)):
        r = c[i] / c[i - 1]
        cash, hold = max(cash, hold * (1 - COST)), max(hold * r, cash * (1 - COST) * r)
    return max(cash, hold * (1 - COST)) - 1
def best_k(c, K):
    """왕복 K번까지(로그 수익 DP, 곱셈 DP)"""
    cash = np.ones(K + 1); hold = np.full(K + 1, -np.inf)   # cash[j]: j번 왕복 끝, hold[j]: j번째 매수 보유 중(j>=1)
    hold[1:] = (1 - COST); hold[0] = -np.inf
    cash = np.ones(K + 1)
    for i in range(1, len(c)):
        r = c[i] / c[i - 1]
        ncash = cash.copy(); nhold = hold * r
        for j in range(1, K + 1):
            ncash[j] = max(cash[j], hold[j] * (1 - COST))
            nhold[j] = max(hold[j] * r, cash[j - 1] * (1 - COST) * r)
        cash, hold = ncash, nhold
    return max(cash.max(), (hold[1:] * (1 - COST)).max()) - 1
rows = []
for code, (g, x) in P.items():
    cl = x.close
    for y in range(2017, 2027):
        s = cl[cl.index.year == y]
        if len(s) < 120: continue
        c = s.to_numpy(float)
        rows.append(dict(g=g, y=y, bh=c[-1] / c[0] - 1, A=best_unlimited(c), B=best_k(c, 4), C=best_k(c, 12)))
D = pd.DataFrame(rows); D.to_pickle(T + "Q19.pkl")
print("그룹별 종목-연도 중앙값(%) / 평균(%)")
for g in ("대형주", "중형주", "소형주", "잡주"):
    d = D[D.g == g]
    print(f"{g}: n{len(d)} | 사서 보유 중앙 {d.bh.median()*100:+.0f} | 연4회 왕복 최대 중앙 {d.B.median()*100:+.0f}% (평균 {d.B.mean()*100:+.0f}) | 연12회 중앙 {d.C.median()*100:+.0f}% (평균 {d.C.mean()*100:+.0f}) | 횟수제한없음 중앙 {d.A.median()*100:+.0f}% (평균 {d.A.mean()*100:+.0f})")
