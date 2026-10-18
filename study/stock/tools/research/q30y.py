"""수익 극대화(낙폭 무시) 순환 매매(2026-10-18): 대형주만 / 대형+중형주 후보, 자리 수 1~6, 우선순위, 시장 필터를 바꿔 가며 연수익 비교.
비용: 거래 0.6%(시장 때문에 정리하면 한 번 더). 선택 = 2017~2023, 시험 = 2024~2026. python q30.py"""
import os, sys, json, itertools, numpy as np, pandas as pd

sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
T = os.environ["TEMP"] + "/stk/"
COST = 0.006
SETS = {"대형": (json.load(open(T + "q5/large.json", encoding="utf-8")), json.load(open(T + "q5/cands.json", encoding="utf-8"))),
        "중형": (json.load(open(T + "q5/cand_중형주.json", encoding="utf-8")), json.load(open(T + "q5/cands_mid.json", encoding="utf-8")))}
cal = pd.DatetimeIndex(sorted({pd.Timestamp(x[0] + 9 * 3600, unit="s").normalize() for cs, _ in SETS.values() for v in cs.values() for x in v["c"]})); cid = pd.Series(np.arange(len(cal)), index=cal)
M = pd.read_pickle("data/market_idx.pkl"); lvl = (1 + M["mkt_ret"].fillna(0)).cumprod(); lvl.index = pd.to_datetime(lvl.index)
BELOW = {n: (lvl < lvl.rolling(n).mean()).reindex(cal, method="ffill").fillna(False).to_numpy() for n in (120, 200)}
TAGRANK = {"T1": 0, "T6": 1, "T3b": 2, "T3": 3, "돌파": 4, "T4": 5}
EV = {"대형": [], "중형": []}
for key, (cs, cands) in SETS.items():
    DATA = {}
    for code, v in cs.items():
        a = np.array(v["c"]); dates = pd.DatetimeIndex(pd.to_datetime(a[:, 0] + 9 * 3600, unit="s").normalize()); c = a[:, 4]
        d = pd.Series(c).diff(); up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean(); dn = (-d).clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        DATA[code] = dict(o=a[:, 1], c=c, dates=dates, v=a[:, 5], rsi=(100 - 100 / (1 + up / dn.replace(0, np.nan))).to_numpy(), dd=c / pd.Series(c).rolling(60).max().to_numpy() - 1,
                          liq=(pd.Series(c * a[:, 5]).rolling(20).mean()).to_numpy())
    for cd in cands:
        D = DATA.get(cd["code"])
        if D is None: continue
        i = cd["i"]; k = i + 1; n = len(D["c"])
        if k >= n or not D["liq"][i] >= 1e8: continue
        e = cd["sell"] if cd["sell"] is not None else n - 1
        if e < k: continue
        closed = cd["sell"] is not None; sell = closed and e + 1 < n
        o, c = D["o"], D["c"]; rr = np.empty(e - k + 1 + (1 if sell else 0)); rr[0] = c[k] / o[k] - 1
        if e > k: rr[1:e - k + 1] = c[k + 1:e + 1] / c[k:e] - 1
        if sell: rr[-1] = o[e + 1] / c[e] - 1
        if closed: rr[-1] -= COST
        last = e + 1 if sell else e
        ids = cid.loc[D["dates"][k:last + 1]].to_numpy()
        EV[key].append(dict(code=cd["code"], tag=cd["tag"], start=ids[0], end=ids[-1], ids=ids, rr=rr, rsi=D["rsi"][i], dd=D["dd"][i], closed=closed, uni=key))
print({k: len(v) for k, v in EV.items()}, flush=True)


def prio(cands, rule, rng):
    if rule == "random": return [cands[i] for i in rng.permutation(len(cands))]
    if rule == "tag": return sorted(cands, key=lambda e: (TAGRANK.get(e["tag"], 9), e["dd"]))
    if rule == "dd": return sorted(cands, key=lambda e: e["dd"])
    if rule == "rsi": return sorted(cands, key=lambda e: e["rsi"] if e["rsi"] == e["rsi"] else 99)


def sim(N, rule, tags, unis, reg, cut, seed=0):
    byd = {}
    for u in unis:
        for e in EV[u]:
            if e["tag"] in tags: byd.setdefault(e["start"], []).append(e)
    rng = np.random.RandomState(seed); slots = [None] * N; port = np.zeros(len(cal)); ntr = 0
    for d in sorted(byd):
        free = [i for i in range(N) if slots[i] is None or slots[i][0] <= d]
        if not free: continue
        if reg and d > 0 and BELOW[reg][d - 1]: continue
        held = {s[1] for s in slots if s is not None and s[0] > d}
        cands = [e for e in byd[d] if e["code"] not in held]
        for e, i in zip(prio(cands, rule, rng), free):
            ids, rr, end = e["ids"], e["rr"], e["end"]
            if cut and reg and len(ids) > 1:
                bad = np.flatnonzero(BELOW[reg][ids[:-1]])
                if len(bad):
                    j = bad[0] + 1; rr = rr[:j + 1].copy(); ids = ids[:j + 1]; end = ids[-1]
                    if j + 1 < len(e["rr"]): rr[-1] -= COST
            slots[i] = (end, e["code"]); port[ids] += rr / N; ntr += 1
    return port, ntr


def stats(port, a, b):
    m = (cal >= pd.Timestamp(a)) & (cal <= pd.Timestamp(b)); r = port[m]; eq = np.cumprod(1 + r); yrs = m.sum() / 245
    return (eq[-1] ** (1 / yrs) - 1) * 100, (r.mean() / r.std() * np.sqrt(245) if r.std() > 0 else np.nan), (eq / np.maximum.accumulate(eq) - 1).min() * 100


PER = {"17~21": ("2017-01-01", "2021-12-31"), "22~23": ("2022-01-01", "2023-12-31"), "24~26": ("2024-01-01", "2026-12-31"), "AV": ("2017-01-01", "2023-12-31"), "전체": ("2017-01-01", "2026-12-31")}
TAG = {"T4 제외": {"T1", "T6", "T3b", "T3", "돌파"}, "전체": set(TAGRANK)}
UNI = {"대형": ["대형"], "대형+중형": ["대형", "중형"]}
rows = []
for N, rule, (tn, tags), (un, unis), reg, cut in itertools.product((1, 2, 3, 4, 5, 6), ("rsi", "dd", "tag", "random"), TAG.items(), UNI.items(), (None, 200), (False, True)):
    if cut and not reg: continue
    seeds = range(4) if rule == "random" else [0]
    res = [sim(N, rule, tags, unis, reg, cut, s) for s in seeds]
    row = dict(N=N, rule=rule, tags=tn, uni=un, reg=reg, cut=cut, trades=res[0][1])
    for k, (a, b) in PER.items():
        s = np.mean([stats(p, a, b) for p, _ in res], axis=0); row[f"{k}_cagr"], row[f"{k}_sh"], row[f"{k}_mdd"] = s
    for y in range(2017, 2027):
        row[f"y{y}"] = np.mean([stats(p, f"{y}-01-01", f"{y}-12-31")[0] for p, _ in res])
    rows.append(row)
R = pd.DataFrame(rows); R.to_pickle(T + "Q30y.pkl")
