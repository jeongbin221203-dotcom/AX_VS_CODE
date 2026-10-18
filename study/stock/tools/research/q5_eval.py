"""5100 앱 규칙(storyrules.js) 신호를 같은 틀(다음 날 시가 체결·비용 0.6%·보유 종목 균등 배분)로 계산."""
import os, sys, json, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
import q3
T = os.environ["TEMP"] + "/stk/"; COST = 0.006
C = json.load(open(T + "q5/candles.json", encoding="utf-8")); S = json.load(open(T + "q5/" + (sys.argv[2] if len(sys.argv) > 2 else "signals.json"), encoding="utf-8"))
cal = pd.DatetimeIndex(sorted({pd.Timestamp(x[0] + 9 * 3600, unit="s").normalize() for v in C.values() for x in v["c"]})); cid = pd.Series(np.arange(len(cal)), index=cal)
import sys
GR = sys.argv[1]
C = {k: v for k, v in C.items() if v["g"] == GR}; S = {k: v for k, v in S.items() if k in C}
cal = pd.DatetimeIndex(sorted({pd.Timestamp(x[0] + 9 * 3600, unit="s").normalize() for v in C.values() for x in v["c"]})); cid = pd.Series(np.arange(len(cal)), index=cal)
gsum = np.zeros(len(cal)); gcnt = np.zeros(len(cal)); res = []
for code, sig in S.items():
    a = np.array(C[code]["c"]); dates = pd.DatetimeIndex(pd.to_datetime(a[:, 0] + 9 * 3600, unit="s").normalize()); o, c = a[:, 1], a[:, 4]; n = len(c)
    k = None
    for s in sig + [None]:
        if s is not None and s["side"] == "buy": k = s["i"] + 1; continue
        if k is None: continue
        e = (s["i"] if s else n - 1)
        if k >= n or k > e: k = None; continue
        sell = s is not None and e + 1 < n
        rr = np.empty(e - k + 1 + (1 if sell else 0)); rr[0] = c[k] / o[k] - 1
        if e > k: rr[1:e - k + 1] = c[k + 1:e + 1] / c[k:e] - 1
        if sell: rr[-1] = o[e + 1] / c[e] - 1
        rr[-1] -= COST if s is not None else 0
        last = e + 1 if sell else e
        ids = cid.loc[dates[k:last + 1]].to_numpy(); gsum[ids] += rr; gcnt[ids] += 1
        res.append((ids[0], np.prod(1 + rr) - 1, last - k + 1, code)); k = None
pers = dict(q3.PER, A=("2017-01-01", "2021-12-31"))
for k, per in pers.items():
    st = q3.stats(gsum, gcnt, res, cal, per)
    print(GR, k, per, {a: round(float(b), 2) for a, b in st.items()})
