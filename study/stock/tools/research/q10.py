import os, sys, json, subprocess, numpy as np
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
import q3, q7
T = os.environ["TEMP"] + "/stk/"
S = {"기본": {}, "'상단 터치+60일선 하락' 매도 끔": {"sell60Over": 99}, "강력 5/60 데드크로스 매도 끔": {"strongDeadGap": -999},
     "데드크로스 기준 -6→-10%p(더 강해야 매도)": {"strongDeadGap": -10}, "데드크로스 기준 -6→-3%p(더 쉽게 매도)": {"strongDeadGap": -3},
     "T4 매수 끔": {"only": ["T1", "T3", "T6", "돌파"]}, "T4·돌파 끔": {"only": ["T1", "T3", "T6"]},
     "T4 끔 + 60일선 하락 매도 끔": {"only": ["T1", "T3", "T6", "돌파"], "sell60Over": 99},
     "T4 끔 + 데드크로스 -10": {"only": ["T1", "T3", "T6", "돌파"], "strongDeadGap": -10},
     "T4 끔 + 60일선 매도 끔 + 데드크로스 -10": {"only": ["T1", "T3", "T6", "돌파"], "sell60Over": 99, "strongDeadGap": -10},
     "손절 25%(전 종류)": {"stopLoss": 0.25, "stopTags": ["T1", "T3", "T3b", "T4", "T6", "돌파"]}, "손절 15%(전 종류)": {"stopLoss": 0.15, "stopTags": ["T1", "T3", "T3b", "T4", "T6", "돌파"]},
     "시간 손절 250봉 손실이면": {"timeStopBars": 250}}
json.dump(S, open(T + "q5/sets10.json", "w"))
subprocess.run(["node", T + "q5/run3.mjs", "large.json", "sets10.json", "out10.json"], check=True)
R = json.load(open(T + "q5/out10.json", encoding="utf-8"))
big = {c for c, v in q7.C.items() if v["g"] == "대형주"}
for name, sig in R.items():
    gs, gc, res = q7.evaluate(sig, big); row = []
    for k, per in {"2017-21": q7.pers["A"], "2022-23": q7.pers["V"], "2024-26": q7.pers["T"]}.items():
        st = q3.stats(gs, gc, res, q7.cal, per); row.append(f"{k}: {st['cagr']:+.1f}% 낙폭{st['mdd']:.0f} 샤프{st['sharpe']:.2f} 승{st['win']:.0f}%")
    print(f"{name:34s} | " + " | ".join(row))
