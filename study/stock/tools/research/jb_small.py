"""소형주·잡주 개선(2026-10-19, 사용자 "소형주 잡주 개선해"). 지금 규칙(지정가 저가 매수 포함)에서 출발해 단계마다 하나씩 고른다(2021년까지 평균 최대,
승률은 지금보다 2%p 넘게 낮으면 제외, 거래 수는 지금의 50% 이상) → 2022년 이후로 확인.
  1단계 매수 조건: 볼린저 %B 기준(0.5→0.4·0.3·0.2), 20일 평균 거래대금 하한(5·10·20억)·상한(50·100억), 60일 하락(−10·−20%↓), 시장 52주 고점 대비(−5·−10%↓)
  2단계 지정가: 신호일 종가 −2~8% · 2~10일
  3단계 매도: AI 점수(5봉 평균) 매도 기준 30·40·50점 × 상단 지정가 매도 수익 기준 20·30·40%
python tools/research/jb_small.py 소형주|잡주"""
import json
import os
import pickle
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core import jb_rules, picks  # noqa: E402

D = pickle.load(open(os.environ.get("JB_PKL") or os.path.join("data", "research", "jb_scores.pkl"), "rb"))
G = {k: v[0] for k, v in ((picks.load_today() or {}).get("scores") or {}).items()}
TIERS = sys.argv[1:] or ["소형주", "잡주"]
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
        tr, _, _ = jb_rules.simulate(t[1], t[2], t[3], t[4], s, group, ok=ok, v=t[5], mkt_dd=MK[code], rule=rule)
        rows += [(t[0][b], float(r)) for i, b, j, px, r, w in tr if j is not None]
    return rows


def st(rows, f):
    a = np.array([x[1] for x in rows if f(x[0])])
    if not len(a):
        return {"n": 0, "mean": -1, "win": 0}
    return {"n": int(len(a)), "mean": float(a.mean()), "med": float(np.median(a)), "win": float((a > 0).mean()), "nloss": int((a < 0).sum()),
            "loss": float(a[a < 0].mean()) if (a < 0).any() else 0.0, "sum": float(a.sum())}


E, L = (lambda d: d <= "2021"), (lambda d: d >= "2022")
fmt = lambda s: (f"{s['n']:4d}건 평균 {s['mean'] * 100:+6.2f}% 중앙 {s['med'] * 100:+6.2f}% 승률 {s['win'] * 100:3.0f}% 손실 {s['nloss']:3d}건 {s['loss'] * 100:+5.1f}% 합 {s['sum']:+6.1f}"  # noqa: E731
                 if s["n"] else "없음")
out = {}
for g in TIERS:
    base = jb_rules.rule_for(g)
    print(f"\n== {g}", flush=True)
    res = {}

    def ev(rule):
        rows = run(g, rule)
        return {"early": st(rows, E), "late": st(rows, L)}

    cur = ev(base)
    res["지금"] = cur
    print(f"  지금 | ~2021 {fmt(cur['early'])} | 2022~ {fmt(cur['late'])}", flush=True)
    rule = dict(base)

    def stage(label, cands):
        global rule
        best_k, best = None, ev(rule) if label != "매수 조건" else cur
        ref = best
        for name, ch in cands.items():
            r = ev({**rule, **ch})
            res[f"{label}: {name}"] = r
            print(f"  {label}: {name:30s} | ~2021 {fmt(r['early'])} | 2022~ {fmt(r['late'])}", flush=True)
            if r["early"]["n"] >= 0.5 * cur["early"]["n"] and r["early"]["win"] >= cur["early"]["win"] - 0.02 and r["early"]["mean"] > best["early"]["mean"]:
                best_k, best = name, r
        if best_k:
            rule = {**rule, **cands[best_k]}
        print(f"  → {label} 고름: {best_k or '그대로'}", flush=True)
        return best_k

    picked = []
    for grp_name, cands in (
        ("매수 조건 %B", {f"%B ≤ {x}": {"pb_max": x} for x in (0.4, 0.3, 0.2)}),
        ("매수 조건 거래대금", {**{f"거래대금 ≥ {10 ** x / 1e8:.0f}억": {"liq_min": x} for x in (8.7, 9.0, 9.3)},
                          **{f"거래대금 < {10 ** x / 1e8:.0f}억": {"liq_max": x} for x in (9.7, 10.0)}}),
        ("매수 조건 하락", {f"60일 {x * 100:.0f}%↓": {"ret60_max": x} for x in (-0.10, -0.20)}),
        ("매수 조건 시장", {f"시장 고점 대비 {x * 100:.0f}%↓": {"mkt_dd_max": x} for x in (-0.05, -0.10)}),
    ):
        picked.append(stage(grp_name, cands))
    od = rule["order"]
    picked.append(stage("지정가", {f"−{k * 100:.0f}% · {W}일": {"order": {**od, "buy_k": k, "buy_wait": W}} for k in (0.02, 0.03, 0.05, 0.08) for W in (2, 3, 5, 10)
                                 if not (k == od["buy_k"] and W == od["buy_wait"])}))
    x = rule["exit"]
    picked.append(stage("매도", {f"AI {a * 100:.0f}점 · 상단 수익 {gn * 100:.0f}%": {"exit": ("ai_or_bb", a, gn)} for a in (0.3, 0.4, 0.5) for gn in (0.2, 0.3, 0.4)
                               if (a, gn) != (x[1], x[2])}))
    fin = ev(rule)
    print(f"  ▶ 고른 것(2021년까지): {picked}\n    ~2021 {fmt(fin['early'])}\n    2022~ {fmt(fin['late'])}  ← 확인\n    지금 2022~ {fmt(cur['late'])}", flush=True)
    out[g] = {"picked": picked, "rule": {k: (list(v) if isinstance(v, tuple) else v) for k, v in rule.items()}, "final": fin, "now": cur, "res": res}
json.dump(out, open(os.path.join("data", "research", f"jb_small_{'_'.join(TIERS)}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
