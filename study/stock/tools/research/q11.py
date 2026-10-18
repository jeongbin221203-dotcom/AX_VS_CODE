"""중형주를 안정적으로(2026-10-18): 5100 앱 규칙 + AI 점수 필터(상위 N%) + 시장 동향 필터 + 규칙 수치를 표본 종목에 같은 값으로 적용해 비교.
선택은 2022~23(필터가 있는 구간)과 2017~21(필터 없는 규칙 자체), 시험은 2024~26. GROUP=중형주 python q11.py [무작위 조합 수]"""
import os, sys, json, subprocess, numpy as np, pandas as pd

sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
import q3

T = os.environ["TEMP"] + "/stk/"; COST = 0.006
GROUP = os.environ.get("GROUP", "중형주")
C = {k: v for k, v in json.load(open(T + "q5/candles.json", encoding="utf-8")).items() if v["g"] == GROUP}
json.dump(C, open(T + f"q5/cand_{GROUP}.json", "w"))
cal = pd.DatetimeIndex(sorted({pd.Timestamp(x[0] + 9 * 3600, unit="s").normalize() for v in C.values() for x in v["c"]})); cid = pd.Series(np.arange(len(cal)), index=cal)
DATES = {c: pd.DatetimeIndex(pd.to_datetime(np.array(v["c"])[:, 0] + 9 * 3600, unit="s").normalize()) for c, v in C.items()}
ARR = {c: np.array(v["c"]) for c, v in C.items()}


def merge(alld, bad):
    pos = {d: i for i, d in enumerate(alld)}; idx = sorted(pos[d] for d in bad); out = []
    for i in idx:
        if out and i == out[-1][1] + 1: out[-1][1] = i
        else: out.append([i, i])
    return [[alld[a].strftime("%Y-%m-%d"), alld[b].strftime("%Y-%m-%d")] for a, b in out]


def build_ranges():
    A = pd.read_pickle(T + "AO.pkl"); A["date"] = pd.to_datetime(A["date"]); col = "r_ens"
    A = A[A.code.isin(C)]
    GR = {}
    for thr in (0.5, 0.6, 0.7, 0.8, 0.9):
        out = {}
        for code in C:
            dates = DATES[code]; dates = dates[dates >= "2022-01-01"]
            g = A[A.code == code].set_index("date")[col].sort_index()
            if g.empty:
                out[code] = merge(dates, dates); continue
            u = g.index.union(dates); v = g.reindex(u).ffill().reindex(dates)
            last = pd.Series(g.index, index=g.index).reindex(u).ffill().reindex(dates); age = (dates - pd.DatetimeIndex(last)).days
            out[code] = merge(dates, dates[(v.fillna(-1).to_numpy() < thr) | (np.array(age) > 7)])
        GR[f"g{thr}"] = out
    M = pd.read_pickle("data/market_idx.pkl"); lvl = (1 + M["mkt_ret"].fillna(0)).cumprod(); lvl.index = pd.to_datetime(lvl.index)
    alld = lvl.index[lvl.index >= "2016-01-01"]
    for n in (60, 120, 200):
        below = lvl < lvl.rolling(n).mean(); GR[f"r{n}"] = merge(alld, alld[below.reindex(alld).to_numpy()])
    json.dump(GR, open(T + "q5/gate_ranges.json", "w"))


def evaluate(sig):
    gsum = np.zeros(len(cal)); gcnt = np.zeros(len(cal)); res = []
    for code, ss in sig.items():
        a = ARR[code]; dates = DATES[code]; o, c = a[:, 1], a[:, 4]; n = len(c); k = None
        for s in ss + [None]:
            if s is not None and s["side"] == "buy":
                k = s["i"] + 1; continue
            if k is None: continue
            e = s["i"] if s else n - 1
            if k >= n or k > e:
                k = None; continue
            sell = s is not None and e + 1 < n
            rr = np.empty(e - k + 1 + (1 if sell else 0)); rr[0] = c[k] / o[k] - 1
            if e > k: rr[1:e - k + 1] = c[k + 1:e + 1] / c[k:e] - 1
            if sell: rr[-1] = o[e + 1] / c[e] - 1
            if s is not None: rr[-1] -= COST
            last = e + 1 if sell else e
            ids = cid.loc[dates[k:last + 1]].to_numpy(); gsum[ids] += rr; gcnt[ids] += 1
            res.append((ids[0], np.prod(1 + rr) - 1, last - k + 1)); k = None
    return gsum, gcnt, res


PERS = {"A": ("2017-01-01", "2021-12-31"), "V": q3.PER["V"], "T": q3.PER["T"], "T1": q3.PER["T1"], "T2": q3.PER["T2"], "T3": q3.PER["T3"]}
space = {"sellRsi": [70, 73, 76], "t1Rsi": [35, 40, 45], "t1Drop": [-0.1, -0.15, -0.2], "deepRsi": [20, 25, 30], "deepDrop": [-0.25, -0.35, -0.45], "surgeK": [4, 5, 7],
         "hotRsi": [80, 85, 90], "engulfMinGain": [0.3, 0.5, 1.0], "chopBlock": ["T3", None, "all"], "stopLoss": [None, 0.15, 0.2, 0.25], "trendExit": ["ma20x2", "ma60", None],
         "strongDeadGap": [-3, -6, -10], "warmup": [0, 400]}


def run_sets(sets, name):
    json.dump(sets, open(T + f"q5/sets_{name}.json", "w"))
    subprocess.run(["node", T + "q5/run4.mjs", f"cand_{GROUP}.json", f"sets_{name}.json", f"out_{name}.json"], check=True)
    R = json.load(open(T + f"q5/out_{name}.json", encoding="utf-8")); rows = []
    for nm, sig in R.items():
        gs, gc, res = evaluate(sig); row = dict(name=nm, **sets[nm])
        for k, per in PERS.items():
            for kk, vv in q3.stats(gs, gc, res, cal, per).items(): row[f"{k}_{kk}"] = vv
        rows.append(row)
    return pd.DataFrame(rows)


def show(df, cols=("A", "V", "T"), extra=("T1", "T2", "T3")):
    for _, r in df.iterrows():
        s = " | ".join(f"{k}: {r[k + '_cagr']:+.1f}% 낙폭{r[k + '_mdd']:.0f} 샤프{r[k + '_sharpe']:.2f}" for k in cols)
        s += " | 해별 " + "/".join(f"{r[k + '_cagr']:+.0f}" for k in extra)
        print(f"{str(r['name'])[:30]:30s} | {s} | n{int(r['T_n'])} 승{r['T_win']:.0f}%")


if __name__ == "__main__":
    build_ranges()
    base = {"기본(필터 없음)": {}}
    for t in (0.5, 0.6, 0.7, 0.8, 0.9): base[f"AI 상위 {int(round((1 - t) * 100))}%"] = {"__gate": t}
    for n in (120, 200): base[f"시장 {n}일선 아래 매수금지"] = {"__reg": n}
    for t in (0.5, 0.7, 0.8):
        for n in (120, 200): base[f"AI 상위 {int(round((1 - t) * 100))}% + 시장 {n}"] = {"__gate": t, "__reg": n}
    df = run_sets(base, "base_" + GROUP); show(df)
    df.to_pickle(T + f"Q11_{GROUP}_base.pkl")
    N = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    rng = np.random.RandomState(5); sets = {}
    for i in range(N):
        p = {k: v[rng.randint(len(v))] for k, v in space.items()}; p["__gate"] = [0.7, 0.8][rng.randint(2)]; sets[f"c{i}"] = p
    R = run_sets(sets, "rand_" + GROUP); R.to_pickle(T + f"Q11_{GROUP}_rand.pkl")
    ok = R[(R.V_n >= 20)].copy(); ok["sel"] = np.minimum(ok.V_sharpe, ok.A_sharpe)
    print("\n무작위 상위(선택=min(2017~21 샤프, 2022~23 샤프)):"); show(ok.sort_values("sel", ascending=False).head(6))
    t = ok.sort_values("sel", ascending=False).head(20)
    print("상위20 평균 시험 CAGR %.1f 낙폭 %.0f 샤프 %.2f | 전체 평균 %.1f | 상관 %.2f" % (t.T_cagr.mean(), t.T_mdd.mean(), t.T_sharpe.mean(), ok.T_cagr.mean(), ok[["sel", "T_sharpe"]].corr().iloc[0, 1]))
