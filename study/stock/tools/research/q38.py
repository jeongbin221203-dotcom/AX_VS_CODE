"""전문가 매도 규칙 시험(2026-10-18): 윌리엄 오닐(+20~25% 이익 실현·과열 정점 '8일 중 7일 상승'·200일선 +70% 이격), 와인스타인(30주=150일선 이탈/평탄), 미너비니식 50일선 거래량 이탈,
샹들리에(22일 고점 − 3×ATR), 단테식(상단 터치 + 음봉 + RSI/거래량)을 같은 거래 2,669건에 적용. 체결 다음 날 시가, 비용 0.6%. 구간 2017~21 / 2022~26 비교."""
import os, sys, json, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
src = open(os.path.join(os.path.dirname(__file__), "q35.py"), encoding="utf-8").read().split("# ---------- (1)")[0]
exec(compile(src, "q35_head", "exec"))
for code, D in S.items():
    cs = pd.Series(D["c"]); h = pd.Series(D["h"])
    D["m50"] = cs.rolling(50).mean().to_numpy(); D["m150"] = cs.rolling(150).mean().to_numpy(); D["m200"] = cs.rolling(200).mean().to_numpy()
    D["hh22"] = h.rolling(22).max().to_numpy(); D["up"] = (cs.diff() > 0).astype(float).to_numpy()
    D["vol_avg"] = (D["volx"] * 0 + 1)  # 자리표시(거래량 배수는 volx 사용)

def ex_index(D, k, rule, p):
    c, o, h, l = D["c"], D["o"], D["h"], D["l"]; n = len(c); end = min(k + CAPS, n - 1); ent = o[k]; run = ent
    for j in range(k, end):
        run = max(run, c[j]); g = c[j] / ent - 1; held = j - k
        if rule == "oneil_take":                         # +20~25% 이익 실현, 단 3주(15거래일) 안에 +20%가 나면 8주(40거래일) 보유
            if g >= p["x"]:
                fast = (np.nanmax(c[k:j + 1]) / ent - 1 >= 0.2) and held <= 15
                if not (fast and held < 40): return j
        elif rule == "oneil_climax":                     # 최근 8일 중 7일 상승 + 이익이 g 이상 → 과열 정점
            if g >= p["g"] and held >= 8 and D["up"][j - 7:j + 1].sum() >= 7: return j
        elif rule == "ext200":                           # 200일선 대비 +70% 이상 이격
            if D["m200"][j] == D["m200"][j] and c[j] / D["m200"][j] - 1 >= p["x"]: return j
        elif rule == "weinstein":                        # 이익 g 이상 난 뒤 30주(150일)선 아래로 마감하고 선이 평탄/하락
            if g >= p["g"] and D["m150"][j] == D["m150"][j] and c[j] < D["m150"][j] and D["m150"][j] <= D["m150"][j - 10] * 1.005: return j
        elif rule == "ma50_vol":                         # 50일선 아래로 거래량 동반 이탈(미너비니식), 이익 g 이상일 때
            if g >= p["g"] and D["m50"][j] == D["m50"][j] and c[j] < D["m50"][j] and D["volx"][j] >= p["v"]: return j
        elif rule == "chandelier":                       # 22일 고점 − k×ATR
            a = D["atr"][j] if D["atr"][j] == D["atr"][j] else 0.03
            if g >= p["g"] and c[j] <= D["hh22"][j] * (1 - p["k"] * a): return j
        elif rule == "dante_top":                        # 단테식: 상단 터치 + 음봉 + RSI + 거래량 감소(전일보다) + 20일선 이격
            if g >= p["g"] and h[j] >= D["upper"][j] and c[j] < o[j] and D["rsi"][j] >= p["r"] and (c[j] / D["m20"][j] - 1) >= p["e"]: return j
        elif rule == "dante_climax":                     # 단테식: 거래량 폭발 + 상단 이탈 + 음봉
            if g >= p["g"] and D["volx"][j] >= p["v"] and h[j] >= D["upper"][j] * 1.03 and c[j] < o[j]: return j
        elif rule == "hold":
            pass
    return end - 1 if end - 1 > k else None

def evaluate(rule, p):
    out = []
    for code, k, tag in TR:
        D = S[code]; n = len(D["c"]); j = ex_index(D, k, rule, p)
        closed = j is not None and j + 1 < n
        if j is None: j = min(k + CAPS, n - 1) - 1
        xp = D["o"][j + 1] if (j + 1 < n and closed) else D["c"][j]; ent = D["o"][k]; r = xp / ent - 1 - (COST if closed else 0)
        out.append((D["dates"][k].year, r, j + 1 - k, closed))
    return np.array(out, dtype=float)

rules = [("hold", {})] + [("oneil_take", dict(x=x)) for x in (0.2, 0.25)] + [("oneil_climax", dict(g=g)) for g in (0.2, 0.4)] + [("ext200", dict(x=x)) for x in (0.5, 0.7, 1.0)] \
    + [("weinstein", dict(g=g)) for g in (0.0, 0.2, 0.5)] + [("ma50_vol", dict(g=g, v=v)) for g in (0.2, 0.5) for v in (1.0, 1.5)] \
    + [("chandelier", dict(g=g, k=k)) for g in (0.2, 0.5) for k in (2, 3)] \
    + [("dante_top", dict(g=g, r=r, e=e)) for g in (0.2, 0.4) for r in (70, 75) for e in (0.15, 0.25)] + [("dante_climax", dict(g=g, v=v)) for g in (0.2, 0.4) for v in (3, 5)]
rows = []
for rule, p in rules:
    A = evaluate(rule, p); y = A[:, 0]; r = A[:, 1]
    rows.append(dict(rule=rule, p=json.dumps(p), mean=r.mean() * 100, med=np.median(r) * 100, win=(r > 0).mean() * 100, p50=(r >= 0.5).mean() * 100, m1=r[y <= 2021].mean() * 100, m2=r[y >= 2022].mean() * 100,
                     days=A[:, 2].mean(), closed=A[:, 3].mean() * 100, worst=r.min() * 100))
R = pd.DataFrame(rows); pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 50)
print("\n전문가 매도 규칙(거래당 평균 수익 / 중앙 / 승률 / +50%↑ / 2017~21 / 2022~26 / 평균 보유일 / 청산 비율 / 최악):")
print(R.sort_values("mean", ascending=False).round(1).to_string(index=False))
