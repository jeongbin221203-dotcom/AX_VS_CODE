"""대형주 개별 종목 수익 50%↑ 분석(2026-10-18): 종목마다 5100 앱 규칙(매수→매도)을 따로 돌렸을 때 (1) 거래 하나가 +50% 이상인 비율, (2) 종목-연도별 수익이 +50% 이상인 비율,
(3) 어떤 매수 모양(지표)에서 +50% 이상 거래가 많았는지. 비용 0.6%, 신호 다음 날 시가."""
import os, sys, json, subprocess, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
import q7
T = os.environ["TEMP"] + "/stk/"
json.dump({"app": {}}, open(T + "q5/sets33.json", "w"))
subprocess.run(["node", T + "q5/run3.mjs", "large.json", "sets33.json", "out33.json"], check=True)
sig = json.load(open(T + "q5/out33.json", encoding="utf-8"))["app"]
big = [c for c, v in q7.C.items() if v["g"] == "대형주"]
rows = []; yrs = []
for code in big:
    a = np.array(q7.C[code]["c"]); dates = pd.DatetimeIndex(pd.to_datetime(a[:, 0] + 9 * 3600, unit="s").normalize()); o, h, l, c, v = a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5]; n = len(c)
    cs = pd.Series(c); d = cs.diff(); up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean(); dn = (-d).clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = (100 - 100 / (1 + up / dn.replace(0, np.nan))).to_numpy(); ma224 = cs.rolling(224).mean().to_numpy(); ma60 = cs.rolling(60).mean().to_numpy()
    dd = c / cs.rolling(60).max().to_numpy() - 1; hi250 = c / cs.rolling(250).max().to_numpy() - 1
    atr = (pd.Series(h - l).rolling(14).mean() / cs).to_numpy(); volx = v / pd.Series(v).rolling(20).mean().shift(1).to_numpy()
    r20 = cs.pct_change(20).to_numpy(); r60 = cs.pct_change(60).to_numpy()
    pos = np.zeros(n); r = np.zeros(n); k = None; tag = None; ti = None
    for s in sig[code] + [None]:
        if s is not None and s["side"] == "buy": k = s["i"] + 1; ti = s["i"]; tag = s["tag"]; continue
        if k is None: continue
        e = s["i"] if s else n - 1
        if k >= n or k > e: k = None; continue
        sell = s is not None and e + 1 < n
        rr = np.empty(e - k + 1 + (1 if sell else 0)); rr[0] = c[k] / o[k] - 1
        if e > k: rr[1:e - k + 1] = c[k + 1:e + 1] / c[k:e] - 1
        if sell: rr[-1] = o[e + 1] / c[e] - 1
        if s is not None: rr[-1] -= 0.006
        last = e + 1 if sell else e
        r[k:last + 1] = rr; pos[k:last + 1] = 1
        rows.append(dict(code=code, tag=tag, entry=dates[k], ret=np.prod(1 + rr) - 1, days=len(rr), closed=s is not None, rsi=rsi[ti], dd60=dd[ti], dist224=c[ti] / ma224[ti] - 1, hi250=hi250[ti], atr=atr[ti], volx=volx[ti], r20=r20[ti], r60=r60[ti], dist60=c[ti] / ma60[ti] - 1))
        k = None
    for y in range(2017, 2027):
        m = dates.year == y
        if m.sum() > 120: yrs.append(dict(code=code, y=y, strat=np.prod(1 + r[m]) - 1, bh=c[m][-1] / c[m][0] - 1))
Tr = pd.DataFrame(rows); Y = pd.DataFrame(yrs)
cl = Tr[Tr.closed]
print(f"거래 {len(Tr)}건(청산 {len(cl)}) | 거래 +50%↑ {(cl.ret>=0.5).mean()*100:.1f}% ({(cl.ret>=0.5).sum()}건) | +100%↑ {(cl.ret>=1).mean()*100:.1f}% | 승률 {(cl.ret>0).mean()*100:.0f}% | 평균 {cl.ret.mean()*100:+.0f}% 중앙 {cl.ret.median()*100:+.0f}%")
print("\n매수 종류별: 건수 / +50%↑ 비율 / 평균 / 중앙 / 평균 보유일")
print(cl.groupby("tag").agg(n=("ret", "size"), p50=("ret", lambda s: (s >= 0.5).mean() * 100), mean=("ret", lambda s: s.mean() * 100), med=("ret", lambda s: s.median() * 100), days=("days", "mean")).round(1).to_string())
cl = cl.assign(hold=pd.cut(cl.days, [0, 20, 60, 120, 250, 5000]))
print("\n보유 기간별: +50%↑ 비율"); print(cl.groupby("hold", observed=True).agg(n=("ret", "size"), p50=("ret", lambda s: (s >= 0.5).mean() * 100), mean=("ret", lambda s: s.mean() * 100)).round(1).to_string())
print(f"\n종목-연도 {len(Y)}건 | 규칙 수익 +50%↑ {(Y.strat>=0.5).mean()*100:.1f}% | 사서 보유 +50%↑ {(Y.bh>=0.5).mean()*100:.1f}% | 규칙 중앙 {Y.strat.median()*100:+.0f}%")
print("연도별 +50%↑ 종목 비율(규칙): " + " ".join(f"{y}:{(g.strat>=0.5).mean()*100:.0f}%" for y, g in Y.groupby("y")))
print("\n+50%↑ 거래가 된 매수 모양(지표) — 평균 비교(+50%↑ 거래 vs 나머지, 청산 거래)")
for c in ("rsi", "dd60", "dist224", "hi250", "atr", "volx", "r20", "r60", "dist60"):
    a, b = cl[cl.ret >= 0.5][c].median(), cl[cl.ret < 0.5][c].median()
    print(f"   {c:8s} +50%↑ 중앙 {a:+.3f} | 나머지 {b:+.3f}")
# 조건별 +50% 비율
def rate(mask, lab):
    s = cl[mask]; print(f"   {lab:42s}: 건수 {len(s):3d} | +50%↑ {((s.ret>=0.5).mean()*100 if len(s) else 0):4.1f}% | 평균 {s.ret.mean()*100:+.0f}% | 승률 {(s.ret>0).mean()*100:.0f}%")
print("\n조건별(진입 시점에 알 수 있는 값)")
rate(cl.ret == cl.ret, "전체")
rate(cl.dd60 <= -0.25, "60일 고점 대비 -25% 이하"); rate(cl.dd60 <= -0.35, "60일 고점 대비 -35% 이하")
rate(cl.dist224 <= -0.2, "224일선보다 20%↑ 아래"); rate(cl.hi250 <= -0.4, "250일 고점 대비 -40% 이하")
rate(cl.volx >= 2, "거래량 2배↑ 신호일"); rate(cl.rsi <= 30, "RSI 30 이하"); rate(cl.atr >= 0.04, "변동폭(ATR) 4% 이상")
rate((cl.tag == "T6") & (cl.dd60 <= -0.3), "T6 + 60일 낙폭 -30% 이하"); rate((cl.tag == "T1") & (cl.dd60 <= -0.2), "T1 + 60일 낙폭 -20% 이하")
Tr.to_pickle(T + "Q33.pkl")
