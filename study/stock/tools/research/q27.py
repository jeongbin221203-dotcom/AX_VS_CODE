"""대형주 순환 매매 최적화(2026-10-18): 매일 '그 날의 매수 신호 종목'(종목이 이미 보유 중이어도 신호는 독립적으로 계산)을 후보로, 비는 자리에 사고,
보유 종목은 그 종목의 매도 신호(5100 앱 규칙, 매수한 날부터 상태를 이어서 판단)가 나면 정리하는 일을 반복. 자리 수(N)·후보 우선순위·매수 종류를 바꿔 가며 비교.
비용 0.6%, 신호 다음 날 시가 체결, 자리마다 동일 비중, 빈 자리는 현금. 선택 = 2017~2023, 시험 = 2024~2026. python q27.py"""
import os, sys, json, itertools, numpy as np, pandas as pd

sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
import q7

T = os.environ["TEMP"] + "/stk/"
COST = 0.006
C = {c: v for c, v in q7.C.items()}                      # 대형주 표본 + 현대차·삼성전기·LG에너지솔루션
CANDS = json.load(open(T + "q5/cands.json", encoding="utf-8"))
cal = pd.DatetimeIndex(q7.cal); cid = pd.Series(np.arange(len(cal)), index=cal)
A = pd.read_pickle(T + "AO.pkl"); A["date"] = pd.to_datetime(A["date"]); A = A[A.code.isin(C)]
AIS = {c: g.set_index("date")["r_ens"].sort_index() for c, g in A.groupby("code")}
DATA = {}
for code, v in C.items():
    a = np.array(v["c"]); dates = pd.DatetimeIndex(pd.to_datetime(a[:, 0] + 9 * 3600, unit="s").normalize())
    c = a[:, 4]; d = pd.Series(c).diff(); up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean(); dn = (-d).clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    DATA[code] = dict(o=a[:, 1], c=c, dates=dates, rsi=(100 - 100 / (1 + up / dn.replace(0, np.nan))).to_numpy(), dd=c / pd.Series(c).rolling(60).max().to_numpy() - 1)
TAGRANK = {"T1": 0, "T6": 1, "T3b": 2, "T3": 3, "돌파": 4, "T4": 5}
EV = []
for cd in CANDS:
    D = DATA[cd["code"]]; i = cd["i"]; k = i + 1; n = len(D["c"])
    if k >= n: continue
    e = cd["sell"] if cd["sell"] is not None else n - 1
    if e < k: continue
    closed = cd["sell"] is not None; sell = closed and e + 1 < n
    o, c = D["o"], D["c"]
    rr = np.empty(e - k + 1 + (1 if sell else 0)); rr[0] = c[k] / o[k] - 1
    if e > k: rr[1:e - k + 1] = c[k + 1:e + 1] / c[k:e] - 1
    if sell: rr[-1] = o[e + 1] / c[e] - 1
    if closed: rr[-1] -= COST
    last = e + 1 if sell else e
    ids = cid.loc[D["dates"][k:last + 1]].to_numpy()
    sd = D["dates"][i]; g = AIS.get(cd["code"]); ai = np.nan
    if g is not None:
        gg = g[g.index <= sd]
        if len(gg) and (sd - gg.index[-1]).days <= 7: ai = float(gg.iloc[-1])
    EV.append(dict(code=cd["code"], tag=cd["tag"], start=ids[0], end=ids[-1], ids=ids, rr=rr, rsi=D["rsi"][i], dd=D["dd"][i], ai=ai, closed=closed))
print("후보 거래", len(EV), "종목", len({e['code'] for e in EV}), flush=True)
BYD = {}
for e in EV: BYD.setdefault(e["start"], []).append(e)
DAYS = sorted(BYD)


def prio(cands, rule, rng):
    if rule == "random": return [cands[i] for i in rng.permutation(len(cands))]
    if rule == "tag": return sorted(cands, key=lambda e: (TAGRANK.get(e["tag"], 9), e["dd"]))
    if rule == "dd": return sorted(cands, key=lambda e: e["dd"])
    if rule == "rsi": return sorted(cands, key=lambda e: e["rsi"] if e["rsi"] == e["rsi"] else 99)
    if rule == "ai": return sorted(cands, key=lambda e: -(e["ai"] if e["ai"] == e["ai"] else -1))


def simulate(N, rule, tags, ai_min, lo, hi, seed=0):
    rng = np.random.RandomState(seed); slots = [None] * N; port = np.zeros(len(cal)); tr = []
    for d in DAYS:
        if not (lo <= cal[d] <= hi): continue
        free = [i for i in range(N) if slots[i] is None or slots[i][0] <= d]
        if not free: continue
        held = {s[1] for s in slots if s is not None and s[0] > d}
        cands = [e for e in BYD[d] if e["tag"] in tags and e["code"] not in held and (ai_min is None or (e["ai"] == e["ai"] and e["ai"] >= ai_min))]
        if not cands: continue
        for e, i in zip(prio(cands, rule, rng), free):
            slots[i] = (e["end"], e["code"]); port[e["ids"]] += e["rr"] / N; tr.append(e)
    return port, tr


def stats(port, lo, hi):
    m = (cal >= lo) & (cal <= hi); r = port[m]; eq = np.cumprod(1 + r); yrs = m.sum() / 245
    return (eq[-1] ** (1 / yrs) - 1) * 100, (r.mean() / r.std() * np.sqrt(245) if r.std() > 0 else np.nan), (eq / np.maximum.accumulate(eq) - 1).min() * 100


P = {"A": (pd.Timestamp("2017-01-01"), pd.Timestamp("2021-12-31")), "V": (pd.Timestamp("2022-01-01"), pd.Timestamp("2023-12-31")), "T": (pd.Timestamp("2024-01-01"), pd.Timestamp("2026-12-31")),
     "AV": (pd.Timestamp("2017-01-01"), pd.Timestamp("2023-12-31")), "ALL": (pd.Timestamp("2017-01-01"), pd.Timestamp("2026-12-31"))}
TAGSETS = {"전체": set(TAGRANK), "T4 제외": {"T1", "T6", "T3b", "T3", "돌파"}, "T1·T6·T3": {"T1", "T6", "T3b", "T3"}}
rows = []
for N, rule, (tn, tags), ai_min in itertools.product((3, 5, 8, 10, 15, 20), ("random", "tag", "dd", "rsi"), TAGSETS.items(), (None,)):
    seeds = range(5) if rule == "random" else [0]
    res = [simulate(N, rule, tags, ai_min, *P["ALL"], s) for s in seeds]
    row = dict(N=N, rule=rule, tags=tn, ai=ai_min)
    for k, (lo, hi) in P.items():
        st = np.mean([stats(p, lo, hi) for p, _ in res], axis=0); row.update({f"{k}_cagr": st[0], f"{k}_sh": st[1], f"{k}_mdd": st[2]})
    row["trades_yr"] = len(res[0][1]) / 9.8; row["win"] = np.mean([(np.prod(1 + e["rr"]) - 1) > 0 for e in res[0][1]]) * 100
    rows.append(row)
R = pd.DataFrame(rows); R.to_pickle(T + "Q27.pkl")
pd.set_option("display.width", 250)
cols = ["N", "rule", "tags", "A_cagr", "V_cagr", "AV_cagr", "AV_sh", "AV_mdd", "T_cagr", "T_sh", "T_mdd", "ALL_cagr", "ALL_mdd", "trades_yr", "win"]
print("\n선택(2017~2023 샤프) 상위 8:"); print(R.sort_values("AV_sh", ascending=False).head(8)[cols].round(1).to_string(index=False))
print("\n시험(2024~26) 상위 8 (참고):"); print(R.sort_values("T_cagr", ascending=False).head(8)[cols].round(1).to_string(index=False))
print("\nN별 평균:"); print(R.groupby("N")[["AV_cagr", "T_cagr", "AV_mdd", "T_mdd", "trades_yr"]].mean().round(1).to_string())
print("규칙별 평균:"); print(R.groupby("rule")[["AV_cagr", "T_cagr", "AV_mdd", "T_mdd"]].mean().round(1).to_string())
print("매수 종류별 평균:"); print(R.groupby("tags")[["AV_cagr", "T_cagr", "AV_mdd", "T_mdd"]].mean().round(1).to_string())
print("상관(선택 샤프 vs 시험 샤프):", round(R[["AV_sh", "T_sh"]].corr().iloc[0, 1], 2))
