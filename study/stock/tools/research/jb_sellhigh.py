"""고가 매도(2026-10-19, 사용자 "저가 매수·고가 매도는 주식의 기본 — 무조건 적용"): 지정가 저가 매수(core/jb_rules.RULES 의 order) 위에서 매도만 비교.
  상단 타기(ride): 매도 조건(수익 +g 이상에서 볼린저 상단 도달)이 와도 팔지 않고 종가가 상단 아래(band)·5일선 아래(ma5)로 내려온 날 종가에 매도, g = 10·15·20·30%
  보유 연장(hold_ext, 대형주): 40일째에도 5일선 위면 더 들고 있다가 5일선 아래로 마감하는 날(최대 10·20·40봉 연장)
2021년까지 평균이 가장 높은 것(승률 2%p 넘게 낮아지면 제외) → 2022년 이후 확인.  python tools/research/jb_sellhigh.py [구간…]"""
import json
import os
import pickle
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core import jb_rules, picks  # noqa: E402

D = pickle.load(open(os.environ.get("JB_PKL") or os.path.join("data", "research", "jb_scores.pkl"), "rb"))
G = {k: v[0] for k, v in ((picks.load_today() or {}).get("scores") or {}).items()}
TIERS = sys.argv[1:] or ["대형주", "중형주", "소형주", "잡주"]
D = {k: v for k, v in D.items() if G.get(k) in TIERS}
MK = {}


def run(group, rule):
    rows = []
    for code, t in D.items():
        if G.get(code) != group:
            continue
        if code not in MK:
            MK[code] = jb_rules.market_dd(t[0])
        s = np.asarray(t[6], float); ok = np.asarray(t[7], bool) & np.isfinite(s)
        h, l = np.asarray(t[2], float), np.asarray(t[3], float)
        tr, _, aux = jb_rules.simulate(t[1], t[2], t[3], t[4], s, group, ok=ok, v=t[5], mkt_dd=MK[code], rule=rule)
        for i, b, j, px, r, w in tr:
            if j is not None:
                rows.append((t[0][b], float(r), 1 - px / jb_rules.near_high(h, j)[1], j - b))
    return rows


def st(rows, f):
    r = [x for x in rows if f(x[0])]
    if not r:
        return {"n": 0}
    a = np.array([x[1] for x in r])
    return {"n": len(r), "mean": float(a.mean()), "med": float(np.median(a)), "win": float((a > 0).mean()), "nloss": int((a < 0).sum()),
            "loss": float(a[a < 0].mean()) if (a < 0).any() else 0.0, "from_high": float(np.mean([x[2] for x in r])), "days": float(np.mean([x[3] for x in r]))}


E, L = (lambda d: d <= "2021"), (lambda d: d >= "2022")
fmt = lambda s: (f"{s['n']:4d}건 평균 {s['mean'] * 100:+6.2f}% 중앙 {s['med'] * 100:+6.2f}% 승률 {s['win'] * 100:3.0f}% 손실 {s['nloss']:3d}건 · 판 값이 고점보다 −{s['from_high'] * 100:4.1f}% · {s['days']:3.0f}일"  # noqa: E731
                 if s["n"] else "없음")
out = {}
for g in TIERS:
    base = jb_rules.rule_for(g)
    od = base["order"]
    cands = {"지금(저가 매수만)": base}
    if base["exit"][0] == "hold":
        for ext in (10, 20, 40):
            cands[f"보유 연장 최대 {ext}봉(5일선 아래 마감까지)"] = {**base, "order": {**od, "hold_ext": ext}}
    else:
        for gn in (0.10, 0.15, 0.20, 0.30):
            for ride in (None, "band", "ma5"):
                if gn == base["exit"][2] and ride is None:
                    continue
                cands[f"상단 도달 수익 +{gn * 100:.0f}%↑ · {'바로 지정가' if ride is None else '상단 아래 마감까지 타기' if ride == 'band' else '5일선 아래 마감까지 타기'}"] = \
                    {**base, "exit": (base["exit"][0], base["exit"][1], gn), "order": {**od, "ride": ride}}
    res = {}
    print(f"\n== {g}", flush=True)
    for name, rule in cands.items():
        rows = run(g, rule)
        res[name] = {"early": st(rows, E), "late": st(rows, L), "rule": {k: (list(v) if isinstance(v, tuple) else v) for k, v in rule.items()}}
        print(f"  {name:40s} | ~2021 {fmt(res[name]['early'])} | 2022~ {fmt(res[name]['late'])}", flush=True)
    b = res["지금(저가 매수만)"]["early"]
    best = max((k for k in res if res[k]["early"]["n"] and res[k]["early"]["win"] >= b["win"] - 0.02), key=lambda k: res[k]["early"]["mean"])
    print(f"  ▶ 고른 것(2021년까지): {best}\n    ~2021 {fmt(res[best]['early'])}\n    2022~ {fmt(res[best]['late'])}  ← 확인\n    지금 2022~ {fmt(res['지금(저가 매수만)']['late'])}", flush=True)
    out[g] = {"best": best, "res": res}
json.dump(out, open(os.path.join("data", "research", f"jb_sellhigh_{'_'.join(TIERS)}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
