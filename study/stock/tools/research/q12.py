"""그룹별 지표 규칙 최적화(2026-10-18): AI 점수 없이, 5100 앱의 지표 규칙(볼린저·RSI·이동평균·거래량·OBV 등) 수치와 켜고 끄는 항목, 시장 동향(지수 N일선)을
그룹 표본 종목에 같은 값으로 적용해 비교한다. 선택 = 2017~2023(둘로 나눠 둘 다 좋은 것), 시험 = 2024~2026.
거래가 가능한 날만: 20일 평균 거래대금 1억 원 이상. GROUP=중형주 python q12.py [무작위 조합 수]"""
import os, sys, json, subprocess, numpy as np, pandas as pd
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
import q11
from q11 import T, C, DATES, ARR, merge, evaluate, PERS, q3, GROUP

LIQ = 1e8


def build_ranges():
    GR = {}
    liq = {}
    for code, a in ARR.items():
        c, v = a[:, 4], a[:, 5]
        t = pd.Series(c * v).rolling(20).mean().to_numpy()
        d = DATES[code]
        bad = d[~(t >= LIQ)]
        liq[code] = merge(d, bad)
    GR["liq1e8"] = liq
    M = pd.read_pickle("data/market_idx.pkl"); lvl = (1 + M["mkt_ret"].fillna(0)).cumprod(); lvl.index = pd.to_datetime(lvl.index)
    alld = lvl.index[lvl.index >= "2013-01-01"]
    for n in (60, 120, 200):
        below = lvl < lvl.rolling(n).mean(); GR[f"r{n}"] = merge(alld, alld[below.reindex(alld).to_numpy()])
    json.dump(GR, open(T + "q5/gate_ranges.json", "w"))


SPACE = {"sellRsi": [70, 73, 76], "t1Rsi": [35, 40, 45], "t1Drop": [-0.1, -0.15, -0.2], "deepRsi": [20, 25, 30], "deepDrop": [-0.25, -0.35, -0.45], "surgeK": [4, 5, 7],
         "hotRsi": [80, 85, 90], "engulfMinGain": [0.3, 0.5, 1.0], "chopBlock": ["T3", None, "all"], "stopLoss": [None, 0.15, 0.2, 0.25], "trendExit": ["ma20x2", "ma60", None],
         "strongDeadGap": [-3, -6, -10], "warmup": [0, 400], "useSurge": [True, False], "useDeep": [True, False], "useBigBear": [True, False], "useHot": [True, False],
         "holdMode": ["obv", None], "sell60Over": [0.008, 99], "timeStopBars": [None, 120, 250], "retouch": [True, False]}


def run_sets(sets, name, procs=6):
    names = list(sets); chunks = [dict((n, sets[n]) for n in names[i::procs]) for i in range(procs)]
    def one(i):
        if not chunks[i]: return {}
        json.dump(chunks[i], open(T + f"q5/sets_{name}_{i}.json", "w"))
        subprocess.run(["node", T + "q5/run4.mjs", f"cand_{GROUP}.json", f"sets_{name}_{i}.json", f"out_{name}_{i}.json"], check=True)
        return json.load(open(T + f"q5/out_{name}_{i}.json", encoding="utf-8"))
    with ThreadPoolExecutor(procs) as ex: parts = list(ex.map(one, range(procs)))
    R = {}
    for p in parts: R.update(p)
    rows = []
    for nm in names:
        gs, gc, res = evaluate(R[nm]); row = dict(name=nm, **sets[nm])
        for k, per in PERS.items():
            for kk, vv in q3.stats(gs, gc, res, q11.cal, per).items(): row[f"{k}_{kk}"] = vv
        rows.append(row)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    build_ranges()
    N = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    base = {"기본": {"__liq": "1e8"}, "시장 60일선": {"__liq": "1e8", "__reg": 60}, "시장 120일선": {"__liq": "1e8", "__reg": 120}, "시장 200일선": {"__liq": "1e8", "__reg": 200}}
    rng = np.random.RandomState(21); sets = dict(base)
    for i in range(N):
        p = {k: v[rng.randint(len(v))] for k, v in SPACE.items()}; p["__liq"] = "1e8"; p["__reg"] = [None, None, 120, 200][rng.randint(4)]; sets[f"c{i}"] = p
    df = run_sets(sets, "g12_" + GROUP); df.to_pickle(T + f"Q12_{GROUP}.pkl")
    q11.show(df.head(4))
    ok = df.iloc[4:][(df.iloc[4:].A_n >= 60) & (df.iloc[4:].V_n >= 20)].copy(); ok["sel"] = np.minimum(ok.A_sharpe, ok.V_sharpe)
    print("\n상위(선택=min(2017~21 샤프, 2022~23 샤프))"); q11.show(ok.sort_values("sel", ascending=False).head(6))
    t = ok.sort_values("sel", ascending=False).head(20)
    print("상위20 평균 시험 CAGR %.1f 낙폭 %.0f 샤프 %.2f | 전체 평균 %.1f | 기본 %.1f | 상관 %.2f" % (t.T_cagr.mean(), t.T_mdd.mean(), t.T_sharpe.mean(), ok.T_cagr.mean(), df.T_cagr[0], ok[["sel", "T_sharpe"]].corr().iloc[0, 1]))
