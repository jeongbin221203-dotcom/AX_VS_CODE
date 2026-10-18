"""연기금(국민연금 등) 매매와 상승 꼭대기(2026-10-18): 토스 투자자별 매매동향의 '연기금' 일별 순매수(국민연금 포함, 다른 연기금도 섞임)로
(1) 연기금 순매수가 이후 수익과 어떤 관계인지, (2) 상승 꼭대기 직전에 연기금이 팔았는지, (3) 연기금 매도를 매도 규칙으로 썼을 때 다른 규칙보다 나은지.
기간: 2019-04 이후. 연기금 지표 = 최근 N일 순매수 합 ÷ 같은 기간 거래량 합(%). python q37.py"""
import os, sys, json, glob, numpy as np, pandas as pd
from sklearn.metrics import roc_auc_score

sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
T = os.environ["TEMP"] + "/stk/"
INV = os.path.join(os.environ["TEMP"], "investor")
src = json.load(open(os.path.join(os.environ["TEMP"], "rotation_candles.json"), encoding="utf-8"))["stocks"]
COST = 0.006
S = {}
for f in glob.glob(os.path.join(INV, "*.json")):
    code = os.path.basename(f)[:-5]; rows = json.load(open(f))
    if code not in src or len(rows) < 500: continue
    I = pd.DataFrame(rows, columns=["date", "pen", "pen_b", "pen_s", "inst", "frn", "ind"]).drop_duplicates("date").set_index("date").sort_index()
    a = np.array(src[code]["c"]); dates = pd.to_datetime(a[:, 0] + 9 * 3600, unit="s").normalize().strftime("%Y-%m-%d")
    P = pd.DataFrame({"o": a[:, 1], "h": a[:, 2], "l": a[:, 3], "c": a[:, 4], "v": a[:, 5]}, index=dates)
    P = P[P.index >= "2019-04-01"].join(I, how="left").fillna({"pen": 0, "pen_b": 0, "pen_s": 0, "inst": 0, "frn": 0, "ind": 0})
    for k in ("pen", "inst", "frn", "ind"):
        for n in (5, 20, 60):
            P[f"{k}{n}"] = P[k].rolling(n).sum() / P.v.rolling(n).sum().replace(0, np.nan) * 100
    P["pen_sellstreak"] = (P.pen < 0).astype(int).groupby(((P.pen >= 0).cumsum())).cumsum()
    c = P.c; d = c.diff(); up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean(); dn = (-d).clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    P["rsi"] = 100 - 100 / (1 + up / dn.replace(0, np.nan)); P["m20"] = c.rolling(20).mean(); P["upper"] = P.m20 + 2 * c.rolling(20).std(ddof=0); P["volx"] = P.v / P.v.rolling(20).mean().shift(1)
    P["low60"] = c.rolling(60).min(); P["code"] = code
    S[code] = P
print("종목", len(S), "연기금 합계 일수", sum(len(p) for p in S.values()), flush=True)
A = pd.concat(S.values()); A.index.name = "date"; A = A.reset_index()

# (1) 연기금 순매수 → 이후 20일 수익
fwd = []
for code, P in S.items():
    c = P.c.to_numpy(); n = len(c); r20 = np.full(n, np.nan); r20[:-20] = c[20:] / c[:-20] - 1
    P["r20"] = r20; fut = pd.Series(c[::-1]).rolling(20).max().shift(1)[::-1].to_numpy(); fmin = pd.Series(c[::-1]).rolling(20).min().shift(1)[::-1].to_numpy()
    P["top"] = ((fut <= c * 1.01) & (fmin <= c * 0.92)).astype(float); P.loc[P.index[-21:], "top"] = np.nan
A = pd.concat(S.values()).reset_index().rename(columns={"index": "date"})
A = A.dropna(subset=["r20"])
print(f"\n[1] 투자자별 순매수(최근 N일, 거래량 대비 %)와 이후 20일 수익의 순위 상관(날짜별 평균): 표본 {len(A):,}일")
for k, nm in (("pen", "연기금"), ("inst", "기관 전체"), ("frn", "외국인"), ("ind", "개인")):
    out = []
    for n in (5, 20, 60):
        col = f"{k}{n}"; ics = []
        for d, g in A[["date", col, "r20"]].dropna().groupby("date"):
            if len(g) > 40 and g[col].nunique() > 5: ics.append(g[col].rank().corr(g.r20.rank()))
        out.append(f"{n}일 {np.nanmean(ics):+.3f}")
    print(f"   {nm:6s}: " + " | ".join(out))

# (2) 상승 구간 꼭대기와 연기금
R = A[(A.c >= A.low60 * 1.25) & A.top.notna()].copy()
print(f"\n[2] 60일 저점 대비 +25% 이상 상승 구간 {len(R):,}일 | 꼭대기 비율 {R.top.mean()*100:.1f}%")
for col in ("pen5", "pen20", "pen60", "inst20", "frn20", "ind20", "pen_sellstreak"):
    d = R[[col, "top"]].dropna(); print(f"   {col:14s} AUC(높을수록 꼭대기) {roc_auc_score(d.top, d[col]):.3f}")
def cond(mask, lab):
    s = R[mask]; print(f"   {lab:44s}: 꼭대기 {s.top.mean()*100:4.1f}% | 이후 20일 평균 {s.r20.mean()*100:+.1f}% | {len(s):6,}일")
cond(R.pen20 <= -2, "연기금 20일 순매도 (거래량의 2% 이상)"); cond(R.pen20 <= -5, "연기금 20일 순매도 (5% 이상)"); cond(R.pen20 >= 2, "연기금 20일 순매수 (2% 이상)")
cond(R.pen_sellstreak >= 5, "연기금 5일 연속 순매도"); cond(R.pen_sellstreak >= 10, "연기금 10일 연속 순매도")
cond((R.pen20 <= -2) & (R.rsi >= 70), "연기금 순매도 + RSI 70↑"); cond((R.pen20 <= -2) & (R.c >= R.upper), "연기금 순매도 + 상단밴드 위")
cond((R.pen60 <= -3) & (R.c / R.m20 - 1 >= 0.15), "연기금 60일 순매도 + 20일선 +15%↑")
cond(R.pen20 == R.pen20, "상승 구간 전체(기준)")

# (3) 매도 규칙 비교(매수 신호 거래 중 연기금 자료가 있는 구간)
import q7
C7 = q7.C; CANDS = json.load(open(T + "q5/cands.json", encoding="utf-8"))
TR = [(c["code"], c["i"] + 1) for c in CANDS if c["code"] in S and c["tag"] in {"T1", "T6", "T3b", "T3", "돌파"}]
dates7 = {c: pd.to_datetime(np.array(C7[c]["c"])[:, 0] + 9 * 3600, unit="s").normalize().strftime("%Y-%m-%d") for c in S if c in C7}
def run(rule, p):
    out = []
    for code, k in TR:
        if code not in dates7: continue
        D = dates7[code]; arr = np.array(C7[code]["c"]); o, c = arr[:, 1], arr[:, 4]; n = len(c)
        if D[k] < "2019-06-01" or k + 1 >= n: continue
        P = S[code].reindex(D)
        ent = o[k]; run_hi = ent; end = min(k + 500, n - 1); j = None
        for t in range(k, end):
            run_hi = max(run_hi, c[t]); g = c[t] / ent - 1
            row = P.iloc[t]
            if rule == "pen_sell" and g >= p["g"] and row.pen20 == row.pen20 and row.pen20 <= p["x"]: j = t; break
            if rule == "pen_streak" and g >= p["g"] and row.pen_sellstreak >= p["n"]: j = t; break
            if rule == "pen_ext" and g >= p["g"] and row.pen20 == row.pen20 and row.pen20 <= p["x"] and row.rsi >= p["r"]: j = t; break
            if rule == "climax" and g >= p["g"] and row.volx >= 5 and (c[t] < o[t]): j = t; break
            if rule == "rsi_band" and g >= p["g"] and c[t] >= row.upper and row.rsi >= p["r"] and c[t] < o[t]: j = t; break
            if rule == "trail" and c[t] <= run_hi * (1 - p["x"]): j = t; break
        closed = j is not None and j + 1 < n
        if j is None: j = end - 1
        xp = o[j + 1] if closed else c[j]; out.append((xp / ent - 1 - (COST if closed else 0), j + 1 - k))
    a = np.array(out); return a[:, 0].mean() * 100, np.median(a[:, 0]) * 100, (a[:, 0] > 0).mean() * 100, a[:, 1].mean(), len(a)
print("\n[3] 매도 규칙 비교(2019-06 이후 진입 거래): 평균 수익 / 중앙 / 승률 / 평균 보유일 / 거래 수")
for rule, p in (("trail", dict(x=0.15)), ("trail", dict(x=0.25)), ("rsi_band", dict(g=0.2, r=85)), ("climax", dict(g=0.3)),
                ("pen_sell", dict(g=0.2, x=-2)), ("pen_sell", dict(g=0.2, x=-5)), ("pen_sell", dict(g=0.3, x=-2)), ("pen_streak", dict(g=0.2, n=5)), ("pen_streak", dict(g=0.2, n=10)),
                ("pen_ext", dict(g=0.2, x=-2, r=70)), ("pen_ext", dict(g=0.3, x=-1, r=75))):
    m = run(rule, p); print(f"   {rule:10s} {json.dumps(p):34s}: 평균 {m[0]:+6.1f}% | 중앙 {m[1]:+6.1f}% | 승률 {m[2]:.0f}% | 보유 {m[3]:.0f}일 | {m[4]}건")
