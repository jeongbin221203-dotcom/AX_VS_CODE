"""'상승 가능한 종목을 사서 목표 수익이 날 때까지 기다리고, 그 뒤 다른 종목을 사는 일을 반복'(2026-10-18): 손절 없이 목표 수익(X%) 종가 도달 시 다음 날 시가에 정리.
후보 = 대형주 규칙의 그 날 매수 신호 종목(T4 제외, 거래대금 1억↑). 자리 수 N, 후보 우선순위(T1·T6 먼저, 낙폭 깊은 순). 비용 0.6%.
대형주 표본 90종목(현재 대형주 = 생존 편향이 큼) 2017~2026."""
import os, sys, json, itertools, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
import q7
T = os.environ["TEMP"] + "/stk/"
COST = 0.006
C = q7.C; CANDS = json.load(open(T + "q5/cands.json", encoding="utf-8"))
cal = pd.DatetimeIndex(q7.cal); cid = pd.Series(np.arange(len(cal)), index=cal)
TAGRANK = {"T1": 0, "T6": 1, "T3b": 2, "T3": 3, "돌파": 4}
DATA = {}
for code, v in C.items():
    a = np.array(v["c"]); dates = pd.DatetimeIndex(pd.to_datetime(a[:, 0] + 9 * 3600, unit="s").normalize()); c = a[:, 4]
    DATA[code] = dict(o=a[:, 1], c=c, dates=dates, ids=cid.loc[dates].to_numpy(), dd=c / pd.Series(c).rolling(60).max().to_numpy() - 1, liq=pd.Series(c * a[:, 5]).rolling(20).mean().to_numpy())
CAND = [x for x in CANDS if x["tag"] in TAGRANK and DATA.get(x["code"]) is not None and DATA[x["code"]]["liq"][x["i"]] >= 1e8 and x["i"] + 1 < len(DATA[x["code"]]["c"])]
print("후보", len(CAND), flush=True)


def events(X):
    out = []
    for cd in CAND:
        D = DATA[cd["code"]]; c, o = D["c"], D["o"]; i = cd["i"]; k = i + 1; n = len(c); ent = o[k]
        if not ent > 0: continue
        hit = np.flatnonzero(c[k:] >= ent * (1 + X))
        if len(hit):
            e = k + hit[0]; sell = e + 1 < n; last = e + 1 if sell else e; px = o[e + 1] if sell else c[e]
            rr = [c[k] / ent - 1] + list(c[k + 1:e + 1] / c[k:e] - 1) if e > k else [c[k] / ent - 1]
            if sell: rr.append(o[e + 1] / c[e] - 1)
            rr[-1] -= COST; hitf = True
        else:
            last = n - 1; rr = [c[k] / ent - 1] + list(c[k + 1:] / c[k:-1] - 1); hitf = False
        rr = np.array(rr); ids = D["ids"][k:k + len(rr)]
        out.append(dict(code=cd["code"], tag=cd["tag"], dd=D["dd"][i], start=ids[0], end=ids[-1], ids=ids, rr=rr, hit=hitf, days=len(rr), ret=np.prod(1 + rr) - 1, minpath=np.cumprod(1 + rr).min() - 1))
    return out


def sim(EV, N, lo="2017-01-01"):
    byd = {}
    for e in EV: byd.setdefault(e["start"], []).append(e)
    slots = [None] * N; port = np.zeros(len(cal)); taken = []
    for d in sorted(byd):
        if cal[d] < pd.Timestamp(lo): continue
        free = [i for i in range(N) if slots[i] is None or slots[i]["end"] <= d]
        if not free: continue
        held = {s["code"] for s in slots if s is not None and s["end"] > d}
        cands = sorted([e for e in byd[d] if e["code"] not in held], key=lambda e: (TAGRANK[e["tag"]], e["dd"]))
        for e, i in zip(cands, free):
            slots[i] = e; port[e["ids"]] += e["rr"] / N; taken.append(e)
    return port, taken


def stats(port, a, b):
    m = (cal >= pd.Timestamp(a)) & (cal <= pd.Timestamp(b)); r = port[m]; eq = np.cumprod(1 + r); yrs = m.sum() / 245
    return (eq[-1] ** (1 / yrs) - 1) * 100, (eq / np.maximum.accumulate(eq) - 1).min() * 100


for X in (0.03, 0.05, 0.07):
    EV = events(X)
    hit = [e for e in EV if e["hit"]]
    print(f"\n=== 목표 +{X*100:.0f}% 까지 기다림 (손절 없음): 후보 거래 {len(EV)}건")
    print(f"   목표 도달 {len(hit)/len(EV)*100:.0f}% | 도달까지 중앙 {np.median([e['days'] for e in hit]):.0f}거래일, 80% 분위 {np.percentile([e['days'] for e in hit], 80):.0f}일 | 기다리는 동안 최대 평가손실 중앙 {np.median([e['minpath'] for e in EV])*100:.0f}% / 최악 {min(e['minpath'] for e in EV)*100:.0f}%")
    nh = [e for e in EV if not e["hit"]]
    print(f"   끝까지 못 간 거래 {len(nh)}건: 평균 평가손익 {np.mean([e['ret'] for e in nh])*100:+.0f}% (끝 시점 기준, 대부분 최근 신호)")
    for N in (1, 3, 5):
        port, tk = sim(EV, N)
        a = stats(port, "2017-01-01", "2026-12-31"); yr = {y: stats(port, f"{y}-01-01", f"{y}-12-31")[0] for y in range(2017, 2027)}
        stuck = sum(1 for e in tk if (not e["hit"]) or e["days"] > 250)
        print(f"   N={N}: 연 {a[0]:+.1f}% 낙폭 {a[1]:.0f}% | 거래 {len(tk)}건(1년 넘게 기다린 {stuck}건) | " + " ".join(f"{y%100}:{v:+.0f}" for y, v in yr.items()))
