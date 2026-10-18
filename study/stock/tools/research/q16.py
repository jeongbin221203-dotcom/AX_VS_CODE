"""게임 설계용: 대형주 전략의 '기간별 수익이 플러스였던 비율'(어느 날 시작해서 N개월 뒤 평가해도 이익이었던 비율)."""
import os, sys, json, subprocess, numpy as np, pandas as pd
os.environ["GROUP"] = "대형주"
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
import q3, q7, q15
T = os.environ["TEMP"] + "/stk/"
def daily(gs, gc): return np.where(gc > 0, gs / np.maximum(gc, 1), 0.0)
def roll(r, idx, lo, hi, months):
    m = (idx >= lo) & (idx <= hi); s = pd.Series(r[m]); n = int(months * 21)
    eq = (1 + s).cumprod(); w = eq / eq.shift(n) - 1; w = w.dropna()
    return (w > 0).mean() * 100, w.median() * 100, w.min() * 100, (w > -0.05).mean() * 100
# 지표 규칙만(앱 기본, 90종목) : 2017~
R = json.load(open(T + "q5/out14.json", encoding="utf-8"))
big = {c for c, v in q7.C.items() if v["g"] == "대형주"}
gs, gc, _ = q7.evaluate(R["앱 기본"], big); r1 = daily(gs, gc); idx1 = pd.DatetimeIndex(q7.cal)
# AI 연동(필터 0.5 + 청산 0.2) : 2022~
ai = q15.ai_daily(); q15.build_ranges(ai)
sets = {"f": {"__gate": 0.5}, "f6": {"__gate": 0.6}}
json.dump(sets, open(T + "q5/sets16.json", "w"))
subprocess.run(["node", T + "q5/run4.mjs", "cand_대형주.json", "sets16.json", "out16.json"], check=True)
R2 = json.load(open(T + "q5/out16.json", encoding="utf-8"))
out = {}
for nm in ("f", "f6"):
    gs2, gc2, _ = q15.evaluate(R2[nm], ai, 0.2, None); out[nm] = (daily(gs2, gc2), pd.DatetimeIndex(q15.cal))
print("시작 시점 무관, N개월 보유(전략 운용) 후 수익이 플러스였던 비율 / 중앙값 / 최악 / -5% 이내 비율")
for label, r, idx, lo, hi in (("지표 규칙만 2017~2026", r1, idx1, "2017-01-01", "2026-12-31"), ("지표 규칙만 2022~2026", r1, idx1, "2022-01-01", "2026-12-31"),
                              ("지표+AI(필터0.5·청산0.2) 2022~2026", *out["f"], "2022-01-01", "2026-12-31"), ("지표+AI(필터0.6·청산0.2) 2022~2026", *out["f6"], "2022-01-01", "2026-12-31")):
    print(label)
    for mth in (3, 6, 12, 24):
        p, med, mn, p5 = roll(r, idx, lo, hi, mth)
        print(f"   {mth:2d}개월: 플러스 {p:.0f}% | 중앙값 {med:+.1f}% | 최악 {mn:+.1f}% | 손실이 -5% 이내 {p5:.0f}%")
