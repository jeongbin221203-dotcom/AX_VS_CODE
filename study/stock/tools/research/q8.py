"""대형주 공통 값 탐색(2026-10-18): 5100 앱 규칙의 수치(RSI·낙폭·거래량 배수·손절 등)를 대형주 90종목 전체에 같은 값으로 바꿔 보며 비교.
선택 = 2017~2023년(앞 구간 2017~21 + 2022~23 둘 다 좋은 것), 시험 = 2024~2026년. python q8.py [조합 수]"""
import os, sys, json, subprocess, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
import q3, q7
T = os.environ["TEMP"] + "/stk/"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 300
rng = np.random.RandomState(11)
space = {"sellRsi": [70, 73, 76, 80], "rsiExt20Min": [0.1, 0.2, 0.3], "trendExit": ["ma20x2", "ma60", "dead520", None], "holdMode": ["obv", "cmf", "macd", "obv+macd", None],
         "t1Rsi": [35, 40, 45], "t1Drop": [-0.1, -0.15, -0.2], "deepRsi": [20, 25, 30], "deepDrop": [-0.25, -0.35, -0.45], "surgeK": [4, 5, 7], "bigBearBody": [-0.03, -0.04, -0.06],
         "hotRsi": [80, 85, 90], "engulfMinGain": [0.3, 0.5, 1.0], "retouchExt60": [0.5, 0.8, 1.2], "chopBlock": ["T3", None, "all"], "stopLoss": [None, 0.15, 0.2, 0.25]}
sets = {"기본": {}}
for i in range(N): sets[f"c{i}"] = {k: v[rng.randint(len(v))] for k, v in space.items()}
json.dump(sets, open(T + "q5/sets8.json", "w"))
subprocess.run(["node", T + "q5/run3.mjs", "large.json", "sets8.json", "out8.json"], check=True)
R = json.load(open(T + "q5/out8.json", encoding="utf-8"))
big = {c for c, v in q7.C.items() if v["g"] == "대형주"}
rows = []
for name, sig in R.items():
    gs, gc, res = q7.evaluate(sig, big); row = dict(name=name, **sets[name])
    for k, per in {"A": q7.pers["A"], "V": q7.pers["V"], "T": q7.pers["T"], "AV": ("2017-01-01", "2023-12-31"), "T1": q3.PER["T1"], "T2": q3.PER["T2"], "T3": q3.PER["T3"]}.items():
        for kk, vv in q3.stats(gs, gc, res, q7.cal, per).items(): row[f"{k}_{kk}"] = vv
    rows.append(row)
Rd = pd.DataFrame(rows); Rd.to_pickle(T + "Q8.pkl")
b = Rd.iloc[0]; print("기본: AV %+.1f%%(%.0f) 샤프%.2f | 시험 %+.1f%%(%.0f) 샤프%.2f" % (b.AV_cagr, b.AV_mdd, b.AV_sharpe, b.T_cagr, b.T_mdd, b.T_sharpe))
ok = Rd.iloc[1:][(Rd.iloc[1:].A_n >= 100)].copy(); ok["sel"] = np.minimum(ok.A_sharpe, ok.V_sharpe)
top = ok.sort_values("sel", ascending=False).head(8)
pd.set_option("display.width", 250)
print(top[["name"] + list(space) + ["A_cagr", "V_cagr", "A_mdd", "V_mdd", "T_cagr", "T_mdd", "T_sharpe", "T_win"]].round(2).to_string())
t = ok.sort_values("sel", ascending=False).head(20)
print("상위20 평균 시험: CAGR %.1f 샤프 %.2f 낙폭 %.0f | 전체 평균 %.1f | 기본보다 시험 높은 비율 상위20 %.0f%% 전체 %.0f%% | 상관(선택점수-시험샤프) %.2f" % (t.T_cagr.mean(), t.T_sharpe.mean(), t.T_mdd.mean(), ok.T_cagr.mean(), (t.T_cagr > b.T_cagr).mean() * 100, (ok.T_cagr > b.T_cagr).mean() * 100, ok[["sel", "T_sharpe"]].corr().iloc[0, 1]))
