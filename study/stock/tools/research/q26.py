"""순환 매매 시뮬레이션(2026-10-18): 한 번에 N종목만 들고, 고점(매도) 신호가 나온 종목을 하나씩 정리하고 비는 자리에 저점 매수 신호 종목을 필터로 골라 사는 일을 반복.
신호는 5100 앱의 대형주 규칙(storyrules.js)이 종목마다 낸 매수·매도 그대로(현재 파일). 후보가 여러 개이면 필터 점수가 높은 종목부터.
비용 0.6%(왕복), 신호 다음 날 시가 체결, 자리마다 동일 비중, 빈 자리는 현금. python q26.py"""
import os, sys, json, subprocess, numpy as np, pandas as pd

sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
import q7

T = os.environ["TEMP"] + "/stk/"
COST = 0.006
big = {c for c, v in q7.C.items() if v["g"] == "대형주"}
sig = json.load(open(T + "q5/out14.json", encoding="utf-8"))["앱 기본"]
A = pd.read_pickle(T + "AO.pkl"); A["date"] = pd.to_datetime(A["date"]); A = A[A.code.isin(big)]
cal = pd.DatetimeIndex(q7.cal); cid = pd.Series(np.arange(len(cal)), index=cal)


def rsi14(c):
    d = pd.Series(c).diff(); up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean(); dn = (-d).clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    return (100 - 100 / (1 + up / dn.replace(0, np.nan))).to_numpy()


EV = []
for code in big:
    a = np.array(q7.C[code]["c"]); dates = pd.DatetimeIndex(pd.to_datetime(a[:, 0] + 9 * 3600, unit="s").normalize()); o, c = a[:, 1], a[:, 4]; n = len(c)
    rs = rsi14(c); dd = c / pd.Series(c).rolling(60).max().to_numpy() - 1
    g = A[A.code == code].set_index("date")["r_ens"].sort_index()
    k = None
    for s in sig[code] + [None]:
        if s is not None and s["side"] == "buy": k = s["i"] + 1; t = s["i"]; continue
        if k is None: continue
        e = s["i"] if s else n - 1
        if k >= n or k > e:
            k = None; continue
        sell = s is not None and e + 1 < n
        rr = np.empty(e - k + 1 + (1 if sell else 0)); rr[0] = c[k] / o[k] - 1
        if e > k: rr[1:e - k + 1] = c[k + 1:e + 1] / c[k:e] - 1
        if sell: rr[-1] = o[e + 1] / c[e] - 1
        rr[-1] -= COST if s is not None else 0
        last = e + 1 if sell else e
        ids = cid.loc[dates[k:last + 1]].to_numpy()
        sd = dates[t]; ai = g[g.index <= sd]; ai = float(ai.iloc[-1]) if len(ai) and (sd - ai.index[-1]).days <= 7 else np.nan
        EV.append(dict(code=code, start=ids[0], end=ids[-1], ids=ids, rr=rr, rsi=rs[t], dd=dd[t], ai=ai, closed=s is not None, sd=sd))
        k = None
EV.sort(key=lambda e: e["start"])
print("거래(신호) 수", len(EV), "닫힌", sum(e["closed"] for e in EV))


def simulate(N, score, lo, hi, seed=0):
    rng = np.random.RandomState(seed)
    free_at = [-1] * N; port = np.zeros(len(cal)); cnt = np.zeros(len(cal)); trades = []
    by_start = {}
    for e in EV:
        if lo <= cal[e["start"]] <= hi: by_start.setdefault(e["start"], []).append(e)
    for d in sorted(by_start):
        cands = by_start[d]
        free = [i for i in range(N) if free_at[i] <= d]
        if not free: continue
        if score == "random": cands = [cands[i] for i in rng.permutation(len(cands))]
        elif score == "rsi_low": cands = sorted(cands, key=lambda e: (e["rsi"] if e["rsi"] == e["rsi"] else 99))
        elif score == "dd_deep": cands = sorted(cands, key=lambda e: e["dd"])
        elif score == "ai_high": cands = sorted(cands, key=lambda e: -(e["ai"] if e["ai"] == e["ai"] else -1))
        if score == "ai_high": cands = [e for e in cands if e["ai"] == e["ai"] and e["ai"] >= 0.5]
        for e, slot in zip(cands, free):
            free_at[slot] = e["end"]
            port[e["ids"]] += e["rr"] / N; trades.append(e)
    return port, trades


def stats(port, lo, hi):
    m = (cal >= lo) & (cal <= hi); r = port[m]; eq = np.cumprod(1 + r); yrs = m.sum() / 245
    return (eq[-1] ** (1 / yrs) - 1) * 100, r.mean() / r.std() * np.sqrt(245) if r.std() > 0 else np.nan, (eq / np.maximum.accumulate(eq) - 1).min() * 100


for lab, lo, hi, scores in (("2017~2026 (지표 신호만)", pd.Timestamp("2017-01-01"), pd.Timestamp("2026-12-31"), ("random", "rsi_low", "dd_deep")),
                            ("2022~2026 (AI 필터 가능 구간)", pd.Timestamp("2022-01-01"), pd.Timestamp("2026-12-31"), ("random", "rsi_low", "dd_deep", "ai_high"))):
    print(f"\n=== {lab}")
    for N in (1, 2, 3, 5, 10):
        for sc in scores:
            res = [simulate(N, sc, lo, hi, s) for s in (range(5) if sc == "random" else [0])]
            st = np.array([stats(p, lo, hi) for p, _ in res]); tr = res[0][1]
            yrs = ((hi if hi < cal[-1] else cal[-1]) - lo).days / 365
            win = np.mean([(np.prod(1 + e["rr"]) - 1) > 0 for e in tr]) * 100 if tr else np.nan
            print(f"  N={N:2d} {sc:8s}: 연수익 {st[:,0].mean():+6.1f}% 샤프 {st[:,1].mean():.2f} 낙폭 {st[:,2].mean():.0f}% | 거래 {len(tr)/yrs:.0f}건/년 승률 {win:.0f}%")
