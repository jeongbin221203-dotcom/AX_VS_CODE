"""상승 꼭대기 분석과 최적 매도 자리 찾기(2026-10-18).
대상: 대형주 규칙의 매수 신호 거래 2,669건(대형주 표본 90종목, 2017~2026). 매수 뒤 보유 경로에서
(1) 수익이 +20% 이상 난 날 중 '꼭대기'(이후 20거래일 동안 +1% 넘는 신고가가 없고 −8% 이상 빠진 날)를 어떤 지표 모양이 가려내는지,
(2) 매도 규칙 후보(고점 대비 하락 추적 · ATR 추적 · 상단 이탈+RSI · 거래량 폭발 · 이동평균 이탈 · 목표 수익 · 단계별 추적)를 같은 거래에 적용해 평균 수익·꼭대기 대비 잡은 비율을 비교.
체결은 신호 다음 날 시가, 왕복 비용 0.6%. 선택은 2017~2021 거래, 확인은 2022~2026 거래. python q35.py"""
import os, sys, json, itertools, numpy as np, pandas as pd

sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
import q7
from sklearn.metrics import roc_auc_score

T = os.environ["TEMP"] + "/stk/"
COST = 0.006
CAPS = 500
C = q7.C
CANDS = json.load(open(T + "q5/cands.json", encoding="utf-8"))
if os.environ.get("UNI") == "mid":                         # 중형주 표본(150종목)과 그 매수 신호
    C = json.load(open(T + "q5/cand_중형주.json", encoding="utf-8")); CANDS = json.load(open(T + "q5/cands_mid.json", encoding="utf-8"))
TAGS = {"T1", "T6", "T3b", "T3", "돌파"}
S = {}
for code, v in C.items():
    a = np.array(v["c"]); dates = pd.DatetimeIndex(pd.to_datetime(a[:, 0] + 9 * 3600, unit="s").normalize())
    o, h, l, c, vol = a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5]; cs = pd.Series(c)
    d = cs.diff(); up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean(); dn = (-d).clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = (100 - 100 / (1 + up / dn.replace(0, np.nan))).to_numpy()
    m20 = cs.rolling(20).mean(); s20 = cs.rolling(20).std(ddof=0)
    ema12, ema26 = cs.ewm(span=12, adjust=False).mean(), cs.ewm(span=26, adjust=False).mean(); macd = ema12 - ema26; hist = (macd - macd.ewm(span=9, adjust=False).mean()).to_numpy()
    atr = (pd.Series(np.maximum(h - l, np.maximum(abs(h - np.r_[c[0], c[:-1]]), abs(l - np.r_[c[0], c[:-1]])))).rolling(14).mean() / cs).to_numpy()
    S[code] = dict(o=o, h=h, l=l, c=c, dates=dates, rsi=rsi, upper=(m20 + 2 * s20).to_numpy(), m5=cs.rolling(5).mean().to_numpy(), m20=m20.to_numpy(), m60=cs.rolling(60).mean().to_numpy(),
                   volx=vol / pd.Series(vol).rolling(20).mean().shift(1).to_numpy(), atr=atr, hist=hist, liq=pd.Series(c * vol).rolling(20).mean().to_numpy(),
                   hh20=cs.rolling(20).max().to_numpy(), ret5=cs.pct_change(5).to_numpy(), obv=np.cumsum(np.sign(np.diff(c, prepend=c[0])) * vol))
TR = []
for cd in CANDS:
    D = S.get(cd["code"])
    if D is None or cd["tag"] not in TAGS or cd["i"] + 1 >= len(D["c"]) or not D["liq"][cd["i"]] >= 1e8: continue
    k = cd["i"] + 1
    if not D["o"][k] > 0: continue
    TR.append((cd["code"], k, cd["tag"]))
print("거래", len(TR), flush=True)

# ---------- (1) 꼭대기 지표 분석
rows = []
for code, k, tag in TR:
    D = S[code]; n = len(D["c"]); c = D["c"]; ent = D["o"][k]; end = min(k + CAPS, n - 21)
    for j in range(k + 5, end):
        g = c[j] / ent - 1
        if g < 0.20: continue
        fut = c[j + 1:j + 21]
        top = (fut.max() <= c[j] * 1.01) and (fut.min() <= c[j] * 0.92)
        cont = fut.max() >= c[j] * 1.10
        ext20 = c[j] / D["m20"][j] - 1
        rows.append((D["dates"][j].year, int(top), int(cont), g, D["rsi"][j], c[j] / D["upper"][j] - 1, ext20, c[j] / D["m60"][j] - 1, D["volx"][j], D["hist"][j] / c[j], D["atr"][j], D["ret5"][j],
                     (c[j] >= D["hh20"][j] * 0.999) * 1.0, (D["obv"][j] < D["obv"][max(j - 20, 0)]) * 1.0 if c[j] >= c[max(j - 20, 0)] else 0.0, (D["h"][j] - max(D["o"][j], c[j])) / max(D["h"][j] - D["l"][j], 1e-9),
                     (c[j] < D["o"][j]) * 1.0, (D["rsi"][j] < np.nanmax(D["rsi"][max(j - 20, 0):j]) - 3) * 1.0 if c[j] >= np.max(c[max(j - 20, 0):j]) * 0.995 else 0.0, (c[j] < D["m5"][j]) * 1.0))
cols = ["yr", "top", "cont", "gain", "rsi", "vs_upper", "ext20", "ext60", "volx", "macd_h", "atr", "ret5", "at_high20", "obv_div", "wick", "red", "rsi_div", "below_m5"]
P = pd.DataFrame(rows, columns=cols).replace([np.inf, -np.inf], np.nan).dropna()
print(f"\n[수익 +20% 이상인 날] {len(P):,}일 | 꼭대기(이후 20일 신고가 없고 −8% 이상 하락) {P.top.mean()*100:.1f}% | 이후 20일 +10% 더 오름 {P.cont.mean()*100:.1f}%")
print("\n지표별 꼭대기 구분력(AUC, 0.5=무관) 학습(~2021)/확인(2022~):")
tr_, te_ = P[P.yr <= 2021], P[P.yr >= 2022]
for c in cols[3:]:
    a1 = roc_auc_score(tr_.top, tr_[c]); a2 = roc_auc_score(te_.top, te_[c])
    print(f"   {c:9s} {a1:.3f} / {a2:.3f}")
print("\n조건별: 꼭대기 비율(기준 %.1f%%) / 더 오른 비율(기준 %.1f%%) / 건수" % (P.top.mean() * 100, P.cont.mean() * 100))
def cond(mask, lab):
    s = P[mask]; print(f"   {lab:44s}: 꼭대기 {s.top.mean()*100:4.1f}% | 더 오름 {s.cont.mean()*100:4.1f}% | {len(s):6,}일")
cond(P.rsi >= 80, "RSI ≥ 80"); cond(P.rsi >= 85, "RSI ≥ 85"); cond(P.ext20 >= 0.25, "20일선 대비 +25% 이상"); cond(P.ext20 >= 0.4, "20일선 대비 +40% 이상")
cond(P.vs_upper >= 0.05, "상단밴드를 5% 이상 넘김"); cond(P.volx >= 3, "거래량 3배 이상"); cond((P.volx >= 3) & (P.red == 1), "거래량 3배 + 음봉")
cond((P.wick >= 0.5) & (P.volx >= 2), "윗꼬리 50%↑ + 거래량 2배"); cond(P.rsi_div == 1, "신고가 부근 + RSI 하락 다이버전스"); cond(P.obv_div == 1, "가격 상승인데 OBV 하락")
cond((P.rsi >= 75) & (P.red == 1) & (P.vs_upper >= 0), "RSI≥75 + 음봉 + 상단 위"); cond((P.ext20 >= 0.25) & (P.red == 1) & (P.volx >= 2), "20일선+25% + 음봉 + 거래량 2배")
cond(P.below_m5 == 1, "종가가 5일선 아래")

# ---------- (2) 매도 규칙 평가
def ex_index(D, k, rule, p):
    c, o, h, l = D["c"], D["o"], D["h"], D["l"]; n = len(c); end = min(k + CAPS, n - 1); ent = o[k]
    run = ent; j0 = k
    for j in range(k, end):
        run = max(run, c[j]); g = c[j] / ent - 1
        if rule == "trail":                                    # 고점 대비 X% 하락
            if c[j] <= run * (1 - p["x"]) and g > -1: return j
        elif rule == "atr":
            if c[j] <= run * (1 - p["k"] * (D["atr"][j] if D["atr"][j] == D["atr"][j] else 0.03)): return j
        elif rule == "rsi_band":                               # 상단 위 + RSI 과열(+ 음봉 선택)
            if c[j] >= D["upper"][j] and D["rsi"][j] >= p["r"] and (not p.get("red") or c[j] < o[j]) and g >= p.get("g", 0): return j
        elif rule == "climax":                                 # 거래량 폭발 + 음봉/긴 윗꼬리
            if D["volx"][j] >= p["v"] and (c[j] < o[j] or (h[j] - max(o[j], c[j])) / max(h[j] - l[j], 1e-9) >= 0.5) and g >= p["g"]: return j
        elif rule == "ma":                                     # 이익이 g 이상 난 뒤 N일 연속 이평선 아래
            m = D["m20"] if p["m"] == 20 else D["m60"] if p["m"] == 60 else D["m5"]
            if g >= p["g"] and j >= 1 and c[j] < m[j] and c[j - 1] < m[j - 1]: return j
        elif rule == "target":
            if g >= p["x"]: return j
        elif rule == "tier":                                   # 수익이 클수록 추적 폭을 좁힘
            gr = run / ent - 1; x = p["a"] if gr < 0.3 else p["b"] if gr < 0.6 else p["c"]
            if c[j] <= run * (1 - x): return j
        elif rule == "combo":                                  # 단계별 추적 + 상단·RSI 과열
            gr = run / ent - 1; x = p["a"] if gr < 0.3 else p["b"] if gr < 0.6 else p["c"]
            if c[j] <= run * (1 - x) or (c[j] >= D["upper"][j] and D["rsi"][j] >= p["r"] and c[j] < o[j] and g >= 0.15): return j
    return end - 1 if end - 1 > k else None

def evaluate(rule, p):
    out = []
    for code, k, tag in TR:
        D = S[code]; n = len(D["c"]); j = ex_index(D, k, rule, p)
        closed = j is not None and j + 1 < n
        if j is None: j = min(k + CAPS, n - 1) - 1
        xp = D["o"][j + 1] if (j + 1 < n and closed) else D["c"][j]
        ent = D["o"][k]; r = xp / ent - 1 - (COST if closed else 0)
        pk = D["c"][k:min(k + CAPS, n)].max() / ent - 1
        out.append((D["dates"][k].year, r, pk, (j + 1 - k)))
    A = np.array(out); return A

rules = [("trail", dict(x=x)) for x in (0.08, 0.10, 0.12, 0.15, 0.20, 0.25, 0.30)] + [("atr", dict(k=k)) for k in (2, 3, 4, 5)] \
    + [("rsi_band", dict(r=r, red=rd, g=g)) for r in (75, 80, 85) for rd in (False, True) for g in (0.0, 0.2)] \
    + [("climax", dict(v=v, g=g)) for v in (3, 5) for g in (0.1, 0.3)] + [("ma", dict(m=m, g=g)) for m in (5, 20, 60) for g in (0.2, 0.5)] \
    + [("target", dict(x=x)) for x in (0.1, 0.2, 0.3, 0.5, 0.8, 1.2)] + [("tier", dict(a=a, b=b, c=c)) for a, b, c in ((0.25, 0.15, 0.1), (0.2, 0.12, 0.08), (0.3, 0.2, 0.12), (0.25, 0.12, 0.06))] \
    + [("combo", dict(a=a, b=b, c=c, r=r)) for a, b, c in ((0.25, 0.15, 0.1), (0.3, 0.2, 0.12)) for r in (75, 80)]
res = []
for rule, p in rules:
    A = evaluate(rule, p); y = A[:, 0]; r = A[:, 1]
    cap = np.where(A[:, 2] > 0.05, np.clip(r / A[:, 2], -1, 1.2), np.nan)
    res.append(dict(rule=rule, p=json.dumps(p, ensure_ascii=False), mean=r.mean() * 100, med=np.median(r) * 100, win=(r > 0).mean() * 100, p50=(r >= 0.5).mean() * 100,
                    m1=r[y <= 2021].mean() * 100, m2=r[y >= 2022].mean() * 100, capture=np.nanmean(cap) * 100, days=A[:, 3].mean(), worst=r.min() * 100))
R = pd.DataFrame(res); R.to_pickle(T + "Q35.pkl")
pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 60)
print("\n매도 규칙별(전체 평균 수익 / 중앙 / 승률 / +50%↑ / 2017~21 / 2022~26 / 꼭대기 대비 잡은 비율 % / 평균 보유일 / 최악):")
print(R.sort_values("mean", ascending=False).head(14).round(1).to_string(index=False))
print("\n선택(2017~21 평균) 상위 5 → 확인(2022~26):"); print(R.sort_values("m1", ascending=False).head(5)[["rule", "p", "m1", "m2", "mean", "win", "capture", "days"]].round(1).to_string(index=False))
print("\n규칙군별 최고(전체 평균):"); print(R.loc[R.groupby("rule")["mean"].idxmax()][["rule", "p", "mean", "med", "win", "m1", "m2", "capture", "days"]].round(1).to_string(index=False))
