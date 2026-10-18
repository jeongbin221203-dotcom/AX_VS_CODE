"""게임 조건 점검(2026-10-18): 종목별 1년 구간(달력 연도)을 오른 종목/횡보 종목/하락 종목으로 나누고, 5100 앱 규칙의 매매 수익이
(1) 오른 종목: 사서 보유 수익의 5배 이상인지 (2) 횡보 종목: +80% 이상인지 (3) 하락 종목: +50% 이상인지 센다. 이론상 최대(미래를 아는 매매)도 함께 계산.
기준: 오른 종목 = 보유 수익 +20% 이상, 횡보 = -10%~+10%, 하락 = -20% 이하. 비용: 체결 다음 날 시가, 왕복 0.6%."""
import os, sys, json, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
import q7
T = os.environ["TEMP"] + "/stk/"
R = json.load(open(T + "q5/out14.json", encoding="utf-8"))
big = {c for c, v in q7.C.items() if v["g"] == "대형주"}
COST = 0.003
def hindsight(c, cost=COST):
    """종가 기준, 미래를 아는 최대 수익(매수·매도 횟수 제한 없음, 한쪽 비용 cost)"""
    cash, hold = 1.0, -1e9
    h = 1.0 * (1 - cost) * 0 + 0
    cash, hold = 1.0, 0.0   # hold: 주식 보유 평가액(0이면 없음)
    cash, hold = 1.0, -1.0
    best_cash, best_hold = 1.0, 0.0
    for i, p in enumerate(c):
        if i == 0:
            best_cash, best_hold = 1.0, (1 - cost); prev = p; continue
        r = p / prev; prev = p
        nh = max(best_hold * r, best_cash * (1 - cost) * r if False else 0)
        nc = max(best_cash, best_hold * (1 - cost))
        nh = max(best_hold * r, best_cash * (1 - cost) * r)
        best_cash, best_hold = nc, nh
    return max(best_cash, best_hold * (1 - cost)) - 1
rows = []
for name in ("앱 기본", "T1T3T6"):
    pass
sig = R["앱 기본"]
for code in big:
    a = np.array(q7.C[code]["c"]); dates = pd.DatetimeIndex(pd.to_datetime(a[:, 0] + 9 * 3600, unit="s").normalize()); o, c = a[:, 1], a[:, 4]; n = len(c)
    pos = np.zeros(n); r = np.zeros(n); k = None
    for s in sig[code] + [None]:
        if s is not None and s["side"] == "buy": k = s["i"] + 1; continue
        if k is None: continue
        e = s["i"] if s else n - 1
        if k >= n or k > e: k = None; continue
        sell = s is not None and e + 1 < n
        r[k] = c[k] / o[k] - 1; pos[k] = 1
        if e > k: r[k + 1:e + 1] = c[k + 1:e + 1] / c[k:e] - 1; pos[k + 1:e + 1] = 1
        if sell: r[e + 1] = o[e + 1] / c[e] - 1 - 0.006; pos[e + 1] = 1
        k = None
    for y in range(2017, 2027):
        m = (dates.year == y)
        if m.sum() < 120: continue
        cc = c[m]; bh = cc[-1] / cc[0] - 1
        strat = np.prod(1 + r[m]) - 1
        rows.append(dict(code=code, year=y, bh=bh, strat=strat, hind=hindsight(cc), inpos=pos[m].mean()))
D = pd.DataFrame(rows)
D["cat"] = np.where(D.bh >= 0.2, "상승", np.where(D.bh <= -0.2, "하락", np.where(D.bh.abs() <= 0.1, "횡보", "기타")))
D.to_pickle(T + "Q17.pkl")
print("종목-연도", len(D)); print(D.cat.value_counts().to_dict())
for cat, rule in (("상승", lambda d: d.strat >= 5 * d.bh), ("횡보", lambda d: d.strat >= 0.8), ("하락", lambda d: d.strat >= 0.5)):
    d = D[D.cat == cat]
    print(f"\n[{cat}] {len(d)}건 | 보유수익 중앙 {d.bh.median()*100:+.0f}% | 규칙 수익 평균 {d.strat.mean()*100:+.0f}% 중앙 {d.strat.median()*100:+.0f}% | 조건 달성 {rule(d).mean()*100:.0f}% | 미래를 아는 최대 중앙 {d.hind.median()*100:+.0f}%, 조건 달성 {(rule(d.assign(strat=d.hind))).mean()*100:.0f}%")
    print(f"      규칙이 보유보다 나음 {(d.strat > d.bh).mean()*100:.0f}% | 규칙이 플러스 {(d.strat > 0).mean()*100:.0f}% | 평균 보유 비중 {d.inpos.mean()*100:.0f}%")
