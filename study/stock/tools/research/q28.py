"""순환 매매에 시장 동향 필터(지수가 N일선 아래이면 새로 사지 않음, 선택으로 보유 종목도 정리)를 더했을 때(2026-10-18)."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
src = open(os.path.join(os.path.dirname(__file__), "q27.py"), encoding="utf-8").read().split("P = {")[0]
exec(compile(src, "q27_head", "exec"))
M = pd.read_pickle("data/market_idx.pkl"); lvl = (1 + M["mkt_ret"].fillna(0)).cumprod(); lvl.index = pd.to_datetime(lvl.index)
BELOW = {n: (lvl < lvl.rolling(n).mean()).reindex(cal, method="ffill").fillna(False).to_numpy() for n in (60, 120, 200)}
def simulate2(N, rule, tags, reg, lo, hi, cut, seed=0):
    rng = np.random.RandomState(seed); slots = [None] * N; port = np.zeros(len(cal)); tr = []
    for d in DAYS:
        if not (lo <= cal[d] <= hi): continue
        free = [i for i in range(N) if slots[i] is None or slots[i][0] <= d]
        if not free: continue
        if reg and BELOW[reg][d - 1 if d > 0 else 0]: continue
        held = {s[1] for s in slots if s is not None and s[0] > d}
        cands = [e for e in BYD[d] if e["tag"] in tags and e["code"] not in held]
        for e, i in zip(prio(cands, rule, rng), free):
            ids, rr, end = e["ids"], e["rr"], e["end"]
            if cut and reg:                                  # 보유 중 시장이 무너지면(지수가 선 아래) 다음 날 정리
                bad = np.flatnonzero(BELOW[reg][ids[:-1]]) if len(ids) > 1 else []
                if len(bad):
                    j = bad[0] + 1; ids, rr, end = ids[:j + 1], rr[:j + 1].copy(), ids[j]
                    rr[-1] = rr[-1] - COST if False else rr[-1]
            slots[i] = (end, e["code"]); port[ids] += rr / N; tr.append(e)
    return port, tr
def stats(port, lo, hi):
    m = (cal >= lo) & (cal <= hi); r = port[m]; eq = np.cumprod(1 + r); yrs = m.sum() / 245
    return (eq[-1] ** (1 / yrs) - 1) * 100, (r.mean() / r.std() * np.sqrt(245) if r.std() > 0 else np.nan), (eq / np.maximum.accumulate(eq) - 1).min() * 100
P = {"2017~21": ("2017-01-01", "2021-12-31"), "2022~23": ("2022-01-01", "2023-12-31"), "2024~26": ("2024-01-01", "2026-12-31"), "2017~26": ("2017-01-01", "2026-12-31")}
tags = {"T1", "T6", "T3b", "T3", "돌파"}
print("N 규칙 시장필터 | " + " | ".join(P))
for N, rule in ((10, "rsi"), (15, "rsi"), (20, "rsi"), (5, "rsi")):
    for reg, cut in ((None, False), (200, False), (120, False), (200, True)):
        port, tr = simulate2(N, rule, tags, reg, pd.Timestamp("2017-01-01"), pd.Timestamp("2026-12-31"), cut)
        row = []
        for k, (a, b) in P.items():
            s = stats(port, pd.Timestamp(a), pd.Timestamp(b)); row.append(f"{s[0]:+.1f}% ({s[2]:.0f}) 샤프{s[1]:.2f}")
        print(f"N={N:2d} {rule} {'없음' if not reg else str(reg)+('+정리' if cut else '')} | " + " | ".join(row))
