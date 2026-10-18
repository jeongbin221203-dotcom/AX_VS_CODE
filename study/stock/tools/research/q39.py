"""내 규칙(storyrules.js의 매도)과 전문가 규칙(오닐 이익 실현·단테 과열 폭발·와인스타인·샹들리에·손절)을 합친 최적 매도 조합 찾기(2026-10-18).
같은 거래 2,669건(대형주 표본 90종목 매수 신호)에 각 조합을 적용: 거래당 평균·중앙·승률·+50%↑·2017~21/2022~26, 그리고 3·5자리 순환 매매(T1·T6 우선, 낙폭 깊은 순)의 연수익·낙폭·해별 최저.
'분할'은 포지션 일부씩 다른 규칙으로 판다(남은 비중이 있는 동안 자리는 계속 차지). 체결 다음 날 시가, 비용 0.6%(다리마다). python q39.py"""
import os, sys, json, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
src = open(os.path.join(os.path.dirname(__file__), "q35.py"), encoding="utf-8").read().split("# ---------- (1)")[0]
exec(compile(src, "q35_head", "exec"))
import q7
cal = pd.DatetimeIndex(q7.cal) if os.environ.get('UNI') != 'mid' else pd.DatetimeIndex(sorted({d for D_ in S.values() for d in D_['dates']})); cid = pd.Series(np.arange(len(cal)), index=cal)
for code, D in S.items():
    cs = pd.Series(D["c"]); h = pd.Series(D["h"])
    D["m150"] = cs.rolling(150).mean().to_numpy(); D["hh22"] = h.rolling(22).max().to_numpy(); D["dd"] = D["c"] / cs.rolling(60).max().to_numpy() - 1
    D["ids"] = cid.loc[D["dates"]].to_numpy()
TAGRANK = {"T1": 0, "T6": 1, "T3b": 2, "T3": 3, "돌파": 4}
TR2 = []
for cd in CANDS:
    D = S.get(cd["code"])
    if D is None or cd["tag"] not in TAGS or cd["i"] + 1 >= len(D["c"]) or not D["liq"][cd["i"]] >= 1e8: continue
    k = cd["i"] + 1
    if not D["o"][k] > 0: continue
    TR2.append(dict(code=cd["code"], k=k, tag=cd["tag"], user=cd["sell"], dd=D["dd"][cd["i"]]))
print("거래", len(TR2), flush=True)


def leg_exit(D, k, rule, p, user):
    c, o, h = D["c"], D["o"], D["h"]; n = len(c); end = min(k + CAPS, n - 1); ent = o[k]
    if rule == "user": return (user if user is not None and user < end else None)
    for j in range(k, end):
        g = c[j] / ent - 1; held = j - k
        if user is not None and j >= user: return user
        if rule == "oneil":
            if g >= p["x"]: return j
        elif rule == "dclimax":
            if g >= p["g"] and D["volx"][j] >= p["v"] and h[j] >= D["upper"][j] * 1.03 and c[j] < o[j]: return j
        elif rule == "chand":
            a = D["atr"][j] if D["atr"][j] == D["atr"][j] else 0.03
            if g >= p["g"] and c[j] <= D["hh22"][j] * (1 - p["k"] * a): return j
        elif rule == "wein":
            if g >= p["g"] and D["m150"][j] == D["m150"][j] and c[j] < D["m150"][j] and D["m150"][j] <= D["m150"][j - 10] * 1.005: return j
        elif rule == "stop":
            if g <= -p["x"]: return j
    return None


def leg_path(D, k, j, n):
    c, o = D["c"], D["o"]; end = min(k + CAPS, n - 1)
    closed = j is not None and j + 1 < n
    if j is None: j = end - 1
    rr = [c[k] / o[k] - 1] + [c[t] / c[t - 1] - 1 for t in range(k + 1, j + 1)]
    if closed: rr.append(o[j + 1] / c[j] - 1)
    if closed: rr[-1] -= COST
    return np.array(rr), closed


def build(variant):
    """variant: list of (weight, [(rule, params), ...]) — 다리마다 '먼저 걸리는' 규칙들의 묶음"""
    EV = []
    for t in TR2:
        D = S[t["code"]]; n = len(D["c"]); k = t["k"]; legs = []
        for w, rules in variant:
            js = [leg_exit(D, k, r, p, t["user"]) for r, p in rules]
            js = [x for x in js if x is not None]; j = min(js) if js else None
            rr, closed = leg_path(D, k, j, n); legs.append((w, rr, closed))
        L = max(len(rr) for _, rr, _ in legs); path = np.zeros(L); frac = np.zeros(L)
        tot = 1.0
        for w, rr, _ in legs:                       # 다리마다 누적 수익으로 합산(일별 가중 평균 근사)
            eq = np.cumprod(1 + rr); eqf = np.r_[eq, np.full(L - len(rr), eq[-1])]; path += w * eqf
        eqp = path; dr = np.r_[eqp[0] - 1, eqp[1:] / eqp[:-1] - 1]
        ids = D["ids"][k:k + L]
        EV.append(dict(code=t["code"], tag=t["tag"], dd=t["dd"], start=ids[0], end=ids[-1], ids=ids, rr=dr, ret=eqp[-1] - 1, days=L, year=D["dates"][k].year, closed=all(c for _, _, c in legs)))
    return EV


def sim(EV, N):
    byd = {}
    for e in EV: byd.setdefault(e["start"], []).append(e)
    slots = [None] * N; port = np.zeros(len(cal))
    for d in sorted(byd):
        if cal[d] < pd.Timestamp("2017-01-01"): continue
        free = [i for i in range(N) if slots[i] is None or slots[i]["end"] <= d]
        if not free: continue
        held = {s["code"] for s in slots if s is not None and s["end"] > d}
        cands = sorted([e for e in byd[d] if e["code"] not in held], key=lambda e: (TAGRANK[e["tag"]], e["dd"]))
        for e, i in zip(cands, free): slots[i] = e; port[e["ids"]] += e["rr"] / N
    return port


def pstats(port, a, b):
    m = (cal >= pd.Timestamp(a)) & (cal <= pd.Timestamp(b)); r = port[m]; eq = np.cumprod(1 + r)
    return (eq[-1] ** (245 / m.sum()) - 1) * 100, (eq / np.maximum.accumulate(eq) - 1).min() * 100


O25, O20, O30, O50 = ("oneil", dict(x=0.25)), ("oneil", dict(x=0.20)), ("oneil", dict(x=0.30)), ("oneil", dict(x=0.5))
DC, DC2 = ("dclimax", dict(g=0.4, v=3)), ("dclimax", dict(g=0.2, v=3))
V = {
    "내 규칙(storyrules 매도)": [(1, [("user", {})])],
    "내 규칙 + 오닐 +25% 이익 실현(먼저 걸리는 쪽)": [(1, [("user", {}), O25])],
    "내 규칙 + 단테 과열 폭발": [(1, [("user", {}), DC])],
    "내 규칙 + 샹들리에(수익 50%↑, 3×ATR)": [(1, [("user", {}), ("chand", dict(g=0.5, k=3))])],
    "내 규칙 + 와인스타인 30주선(수익 50%↑)": [(1, [("user", {}), ("wein", dict(g=0.5))])],
    "내 규칙 + 손절 -25%": [(1, [("user", {}), ("stop", dict(x=0.25))])],
    "내 규칙 + 손절 -15%": [(1, [("user", {}), ("stop", dict(x=0.15))])],
    "분할: 절반 +25% / 절반 내 규칙": [(0.5, [("user", {}), O25]), (0.5, [("user", {})])],
    "분할: 절반 +20% / 절반 내 규칙": [(0.5, [("user", {}), O20]), (0.5, [("user", {})])],
    "분할: 절반 +25% / 절반 단테 과열 폭발": [(0.5, [("user", {}), O25]), (0.5, [DC])],
    "분할: 절반 +30% / 절반 내 규칙+단테 폭발": [(0.5, [("user", {}), O30]), (0.5, [("user", {}), DC])],
    "3분할: +25% / +50% / 내 규칙": [(1 / 3, [("user", {}), O25]), (1 / 3, [("user", {}), O50]), (1 / 3, [("user", {})])],
    "3분할: +25% / 내 규칙 / 단테 폭발": [(1 / 3, [("user", {}), O25]), (1 / 3, [("user", {})]), (1 / 3, [DC])],
    "분할 + 손절 -25%": [(0.5, [("user", {}), O25, ("stop", dict(x=0.25))]), (0.5, [("user", {}), ("stop", dict(x=0.25))])],
    "3분할 + 손절 -25%": [(1 / 3, [("user", {}), O25, ("stop", dict(x=0.25))]), (1 / 3, [("user", {}), O50, ("stop", dict(x=0.25))]), (1 / 3, [("user", {}), ("stop", dict(x=0.25))])],
}
rows = []
for name, var in V.items():
    EV = build(var); r = np.array([e["ret"] for e in EV]); y = np.array([e["year"] for e in EV])
    row = dict(name=name, mean=r.mean() * 100, med=np.median(r) * 100, win=(r > 0).mean() * 100, p50=(r >= 0.5).mean() * 100, m1=r[y <= 2021].mean() * 100, m2=r[y >= 2022].mean() * 100,
               days=np.mean([e["days"] for e in EV]), worst=r.min() * 100)
    for N in (3, 5):
        port = sim(EV, N); a = pstats(port, "2017-01-01", "2026-12-31"); row[f"N{N}_cagr"], row[f"N{N}_mdd"] = a
        row[f"N{N}_1721"] = pstats(port, "2017-01-01", "2021-12-31")[0]; row[f"N{N}_2226"] = pstats(port, "2022-01-01", "2026-12-31")[0]
        row[f"N{N}_min"] = min(pstats(port, f"{yy}-01-01", f"{yy}-12-31")[0] for yy in range(2017, 2027))
    rows.append(row); print(name, f"거래 평균 {row['mean']:.1f}% 3자리 연 {row['N3_cagr']:.1f}% 5자리 연 {row['N5_cagr']:.1f}%", flush=True)
R = pd.DataFrame(rows); R.to_pickle(T + "Q39.pkl"); pd.set_option("display.width", 260); pd.set_option("display.max_colwidth", 40)
print("\n거래 단위 (평균 / 중앙 / 승률 / +50%↑ / 2017~21 / 2022~26 / 보유일 / 최악)")
print(R[["name", "mean", "med", "win", "p50", "m1", "m2", "days", "worst"]].round(1).to_string(index=False))
print("\n3자리·5자리 순환(연수익 / 낙폭 / 2017~21 / 2022~26 / 해별 최저)")
print(R[["name", "N3_cagr", "N3_mdd", "N3_1721", "N3_2226", "N3_min", "N5_cagr", "N5_mdd", "N5_1721", "N5_2226", "N5_min"]].round(1).to_string(index=False))
