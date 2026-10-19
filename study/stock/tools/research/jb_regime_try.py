"""대형·중형·소형 규칙 변형 빠른 비교(상승 상태 조건 풀기·손절) — python tools/research/jb_regime_try.py 대형주 (study/stock 에서 실행)"""
import os, sys, pickle, numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core import jb_rules, picks
GROUP = sys.argv[1]
D = pickle.load(open("data/research/jb_scores.pkl", "rb"))
G = {k: v[0] for k, v in (picks.load_today() or {})["scores"].items()}
codes = [k for k in D if G.get(k) == GROUP]
MK = {k: jb_rules.market_dd(D[k][0]) for k in codes}
BASE = jb_rules.rule_for(GROUP)
def run(rule):
    rows = []
    for code in codes:
        t = D[code]; s = np.asarray(t[6], float); oos = np.asarray(t[7], bool)
        tr, _, _ = jb_rules.simulate(t[1], t[2], t[3], t[4], s, GROUP, ok=oos & np.isfinite(s), v=t[5], mkt_dd=MK[code], rule=rule)
        rows += [(t[0][b], r) for i, b, j, px, r, w in tr if j is not None]
    return rows
def show(name, rows):
    def st(f):
        r = np.array([x[1] for x in rows if f(x[0])]); return f"{len(r):4d}건 {r.mean()*100:+6.2f}% 승률 {(r>0).mean()*100:4.0f}% 손실평균 {r[r<0].mean()*100 if (r<0).any() else 0:+6.2f}%"
    print(f"{name:40s} | 전체 {st(lambda d: True)} | ~2021 {st(lambda d: d <= '2021')} | 2022~ {st(lambda d: d >= '2022')}")
show("지금 규칙", run(BASE))
for xn, xv in (("AI40·상단+40%", ("ai_or_bb", 0.4, 0.4)), ("AI30·상단+20%", ("ai_or_bb", 0.3, 0.2)), ("40일 보유", ("hold", 40)), ("지금 매도 그대로", BASE["exit"])):
    show("상승 상태만 매수조건 없음 · " + xn, run({**BASE, "regimes": {"up": {"ret60_max": None, "mkt_dd_max": None, "exit": xv}}}))
print("--- 손절")
for stp in (0.15, 0.20, 0.25, 0.30, 0.35):
    show(f"손절 1차 매수가 −{stp*100:.0f}%", run({**BASE, "stop": stp}))
