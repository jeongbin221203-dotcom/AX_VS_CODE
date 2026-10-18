"""대형주 1주씩 매수했을 때 원화 손익(5100 앱 규칙, 대형주 표본 90종목 + 현대차·삼성전기·LG에너지솔루션).
신호 다음 날 시가에 1주 매수, 매도 신호 다음 날 시가에 매도, 왕복 비용 0.6%. 가격은 수정주가(액면분할·증자 반영)."""
import os, sys, json, subprocess, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
import q7
T = os.environ["TEMP"] + "/stk/"
json.dump({"app": {}}, open(T + "q5/sets13.json", "w"))
subprocess.run(["node", T + "q5/run3.mjs", "large.json", "sets13.json", "out13.json"], check=True)
sig = json.load(open(T + "q5/out13.json", encoding="utf-8"))["app"]
names = pd.read_pickle(T + "D6g.pkl") if False else None
rows = []
for code, ss in sig.items():
    a = np.array(q7.C[code]["c"]); dates = pd.DatetimeIndex(pd.to_datetime(a[:, 0] + 9 * 3600, unit="s").normalize()); o, c = a[:, 1], a[:, 4]; n = len(c); k = None
    for s in ss + [None]:
        if s is not None and s["side"] == "buy": k = s["i"] + 1; continue
        if k is None: continue
        e = s["i"] if s else n - 1
        if k >= n or k > e: k = None; continue
        buy = o[k]; sell = (o[e + 1] if (s is not None and e + 1 < n) else c[e]); out = dates[e + 1] if (s is not None and e + 1 < n) else dates[e]
        fee = (buy + sell) * 0.003
        rows.append(dict(code=code, buy_d=dates[k], sell_d=out, buy=buy, sell=sell, pnl=sell - buy - fee, closed=s is not None)); k = None
R = pd.DataFrame(rows)
R.to_pickle(T + "Q13.pkl")
def summ(df, label):
    cl = df[df.closed]; op = df[~df.closed]
    inv = df.buy.sum()
    print(f"{label}: 거래 {len(cl)}건(청산) + 보유중 {len(op)}건 | 청산 손익 {cl.pnl.sum():,.0f}원 (평균 {cl.pnl.mean():,.0f}) | 보유중 평가손익 {op.pnl.sum():,.0f}원 | 승률 {(cl.pnl>0).mean()*100:.0f}% | 총 매수금액 {inv:,.0f}원 → 수익률 {df.pnl.sum()/inv*100:+.1f}%")
summ(R, "전체(2014~2026)")
for lo, hi in (("2017-01-01", "2021-12-31"), ("2022-01-01", "2023-12-31"), ("2024-01-01", "2026-12-31")):
    summ(R[(R.buy_d >= lo) & (R.buy_d <= hi)], f"매수일 {lo[:4]}~{hi[:4]}")
for y in range(2022, 2027):
    summ(R[R.buy_d.dt.year == y], f"  {y}년 매수")
# 동시에 들고 있던 금액(1주씩)
cal = pd.date_range("2022-01-01", "2026-10-16", freq="B"); held = np.zeros(len(cal))
for r in R[R.sell_d >= "2022-01-01"].itertuples():
    m = (cal >= r.buy_d) & (cal <= r.sell_d); held[m] += r.buy
print(f"2022년 이후 동시 보유 금액: 평균 {held.mean():,.0f}원, 최대 {held.max():,.0f}원")
t = R[R.buy_d >= "2024-01-01"]
by = t.groupby("code").pnl.sum().sort_values(ascending=False)
print("2024~ 종목별 손익 상위5:", by.head(5).round(0).to_dict()); print("하위5:", by.tail(5).round(0).to_dict())
print("대표 3종목:")
for code, nm in (("005380", "현대차"), ("009150", "삼성전기"), ("373220", "LG에너지솔루션")):
    x = R[R.code == code]; print(f"  {nm}: 거래 {len(x)}건, 손익 합 {x.pnl.sum():,.0f}원, 1주 평균 매수가 {x.buy.mean():,.0f}원")
