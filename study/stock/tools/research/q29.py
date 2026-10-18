"""대형주 순환 매매 수익 끌어올리기(2026-10-18): 자리 수·우선순위·시장 필터·정리 + (선택) 비어 있는 자리를 시장 바구니(대형주 동일 비중)로 채우기.
선택 = 2017~2023, 시험 = 2024~2026. 비용: 거래 0.6% + 정리 때 0.6%(이번엔 반영) + 바구니 비중 변경 0.3%(한쪽)."""
import os, sys, itertools, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
src = open(os.path.join(os.path.dirname(__file__), "q27.py"), encoding="utf-8").read().split("P = {")[0]
exec(compile(src, "q27_head", "exec"))
M = pd.read_pickle("data/market_idx.pkl"); lvl = (1 + M["mkt_ret"].fillna(0)).cumprod(); lvl.index = pd.to_datetime(lvl.index)
BELOW = {n: (lvl < lvl.rolling(n).mean()).reindex(cal, method="ffill").fillna(False).to_numpy() for n in (60, 120, 200)}
# 시장 바구니: 표본 대형주 동일 비중 일수익
bk = pd.concat([pd.Series(DATA[c]["c"], index=DATA[c]["dates"]).pct_change() for c in DATA], axis=1).mean(axis=1).reindex(cal).fillna(0).to_numpy()
LO, HI = pd.Timestamp("2017-01-01"), pd.Timestamp("2026-12-31")


def sim(N, rule, tags, reg, cut, fill, seed=0):
    rng = np.random.RandomState(seed); slots = [None] * N; port = np.zeros(len(cal)); active = np.zeros(len(cal)); ntr = 0
    for d in DAYS:
        free = [i for i in range(N) if slots[i] is None or slots[i][0] <= d]
        if not free: continue
        if reg and d > 0 and BELOW[reg][d - 1]: continue
        held = {s[1] for s in slots if s is not None and s[0] > d}
        cands = [e for e in BYD[d] if e["tag"] in tags and e["code"] not in held]
        for e, i in zip(prio(cands, rule, rng), free):
            ids, rr, end = e["ids"], e["rr"], e["end"]
            if cut and reg and len(ids) > 1:
                bad = np.flatnonzero(BELOW[reg][ids[:-1]])
                if len(bad):
                    j = bad[0] + 1; rr = rr[:j + 1].copy(); ids = ids[:j + 1]; end = ids[-1]
                    if e["closed"] is False or True: rr[-1] -= 0.0 if (j + 1 == len(e["rr"])) else COST   # 시장 때문에 일찍 정리하면 매도 비용을 더 낸다
            slots[i] = (end, e["code"]); port[ids] += rr / N; active[ids] += 1 / N; ntr += 1
    if fill:
        idle = 1 - np.clip(active, 0, 1)
        ok = np.ones(len(cal), bool) if not reg else ~np.r_[False, BELOW[reg][:-1]]
        w = idle * ok
        port = port + w * bk - np.abs(np.diff(w, prepend=0)) * 0.003
    return port, ntr


def stats(port, a, b):
    m = (cal >= pd.Timestamp(a)) & (cal <= pd.Timestamp(b)); r = port[m]; eq = np.cumprod(1 + r); yrs = m.sum() / 245
    return (eq[-1] ** (1 / yrs) - 1) * 100, (r.mean() / r.std() * np.sqrt(245) if r.std() > 0 else np.nan), (eq / np.maximum.accumulate(eq) - 1).min() * 100


PER = {"17~21": ("2017-01-01", "2021-12-31"), "22~23": ("2022-01-01", "2023-12-31"), "24~26": ("2024-01-01", "2026-12-31"), "AV": ("2017-01-01", "2023-12-31"), "전체": ("2017-01-01", "2026-12-31")}
TAG = {"T4 제외": {"T1", "T6", "T3b", "T3", "돌파"}, "전체": set(TAGRANK)}
rows = []
for N, rule, (tn, tags), reg, cut, fill in itertools.product((2, 3, 4, 5, 6, 8), ("rsi", "dd"), TAG.items(), (None, 200), (False, True), (False, True)):
    if cut and not reg: continue
    p, ntr = sim(N, rule, tags, reg, cut, fill)
    row = dict(N=N, rule=rule, tags=tn, reg=reg, cut=cut, fill=fill, trades=ntr)
    for k, (a, b) in PER.items():
        s = stats(p, a, b); row[f"{k}_cagr"], row[f"{k}_sh"], row[f"{k}_mdd"] = s
    rows.append(row)
R = pd.DataFrame(rows); R.to_pickle(T + "Q29.pkl")
pd.set_option("display.width", 260)
cols = ["N", "rule", "tags", "reg", "cut", "fill", "17~21_cagr", "22~23_cagr", "24~26_cagr", "전체_cagr", "전체_mdd", "전체_sh"]
R = R.rename(columns={c: c for c in R.columns})
print("전체(2017~26) 연수익 상위 10:"); print(R.sort_values("전체_cagr", ascending=False).head(10)[cols].round(1).to_string(index=False))
print("\n선택(2017~2023 샤프) 상위 10 → 시험:"); print(R.sort_values("AV_sh", ascending=False).head(10)[cols + ["AV_cagr", "AV_mdd", "24~26_mdd"]].round(1).to_string(index=False))
print("\n바구니 채우기 효과(평균):"); print(R.groupby(["fill"])[["AV_cagr", "24~26_cagr", "전체_cagr", "전체_mdd"]].mean().round(1).to_string())
print("자리 수 평균:"); print(R.groupby("N")[["AV_cagr", "24~26_cagr", "전체_cagr", "전체_mdd"]].mean().round(1).to_string())
