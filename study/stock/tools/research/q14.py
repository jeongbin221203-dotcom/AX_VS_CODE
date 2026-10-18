import os, sys, json, subprocess, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
import q3, q7
T = os.environ["TEMP"] + "/stk/"
S = {"앱 기본": {}, "내 방법(T1·T3·T6 매수만)": {"only": ["T1", "T3", "T6"]}}
json.dump(S, open(T + "q5/sets14.json", "w"))
subprocess.run(["node", T + "q5/run3.mjs", "large.json", "sets14.json", "out14.json"], check=True)
R = json.load(open(T + "q5/out14.json", encoding="utf-8"))
big = {c for c, v in q7.C.items() if v["g"] == "대형주"}
# 비교용: 같은 종목 균등 보유(사서 보유)
rs = []
for c in big:
    a = np.array(q7.C[c]["c"]); d = pd.DatetimeIndex(pd.to_datetime(a[:, 0] + 9 * 3600, unit="s").normalize()); rs.append(pd.Series(a[:, 4], index=d).pct_change())
bh = pd.concat(rs, axis=1).mean(axis=1).fillna(0)
def cagr_bh(a, b):
    s = bh[a:b]; return (np.prod(1 + s) ** (245 / len(s)) - 1) * 100
def mdd_bh(a, b):
    e = (1 + bh[a:b]).cumprod(); return (e / e.cummax() - 1).min() * 100
yrs = [(str(y) + "-01-01", str(y) + "-12-31") for y in range(2017, 2027)] + [("2017-01-01", "2026-12-31"), ("2022-01-01", "2023-12-31"), ("2024-01-01", "2026-12-31")]
for name, sig in R.items():
    gs, gc, res = q7.evaluate(sig, big)
    print(f"\n== {name}")
    for a, b in yrs:
        st = q3.stats(gs, gc, res, q7.cal, (a, b))
        lab = f"{a[:4]}" if a[:4] == b[:4] else f"{a[:4]}~{b[:4]}"
        print(f"  {lab:10s} 규칙 {st['cagr']:+6.1f}% (낙폭 {st['mdd']:.0f}%) 샤프 {st['sharpe']:.2f} 거래 {int(st['n'])} 승률 {st['win']:.0f}% | 같은 종목 사서 보유 {cagr_bh(a, b):+6.1f}% (낙폭 {mdd_bh(a, b):.0f}%)")
    gs2 = np.where(gc > 0, gs / np.maximum(gc, 1), 0)
    m = np.array([("2017-01-01" <= str(d)[:10]) for d in q7.cal]); eq = np.cumprod(1 + gs2[m])
    print(f"  2017~2026 1원 → {eq[-1]:.2f}원 (보유 종목 균등 배분, 보유가 없는 날은 현금)")
