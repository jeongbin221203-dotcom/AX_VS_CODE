"""추세 상태(상승·횡보·하락)별 JB 규칙 고르기(2026-10-19). 상태 = 매수 신호 날의 224일선 방향·위치(core/jb_rules.regime).
상태마다 매수 조건 × 매도 규칙을 따로 시험해 '2021년까지'로 고르고(평균 최대, 승률 65% 이상, 거래 15건 이상, 손실이면 그 상태는 사지 않음) 2022년 이후로 확인.
마지막에 고른 것을 합쳐 지금 규칙과 비교한다. 나눠 사기(−10%)는 모두 켜져 있음. 학습에 쓰지 않은 구간만, 비용 0.3%.
python tools/research/jb_regime.py 대형주"""
import json
import os
import pickle
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core import jb_rules, picks  # noqa: E402

GROUP = sys.argv[1] if len(sys.argv) > 1 else "대형주"
D = pickle.load(open(os.path.join("data", "research", "jb_scores.pkl"), "rb"))
G = {k: v[0] for k, v in ((picks.load_today() or {}).get("scores") or {}).items()}
CODES = [k for k in D if G.get(k) == GROUP]
BASE = jb_rules.rule_for(GROUP)
MK = {k: jb_rules.market_dd(D[k][0]) for k in CODES}
ENTRY = {"조건 없음": {"ret60_max": None, "mkt_dd_max": None}, "60일 −10%↓": {"ret60_max": -0.10, "mkt_dd_max": None},
         "60일 −16.5%↓": {"ret60_max": -0.165, "mkt_dd_max": None}, "시장 고점 −5.4%↓": {"ret60_max": None, "mkt_dd_max": -0.054},
         "60일 −16.5%↓ + 시장 −5.4%↓": {"ret60_max": -0.165, "mkt_dd_max": -0.054}, "60일 −25%↓": {"ret60_max": -0.25, "mkt_dd_max": None}}
EXIT = {"20일 보유": ("hold", 20), "40일 보유": ("hold", 40), "60일 보유": ("hold", 60), "AI 30 또는 상단(+20%)": ("ai_or_bb", 0.30, 0.20),
        "AI 40 또는 상단(+20%)": ("ai_or_bb", 0.40, 0.20), "AI 50 또는 상단(+30%)": ("ai_or_bb", 0.50, 0.30), "AI 40 또는 상단(+40%)": ("ai_or_bb", 0.40, 0.40)}


def run(rule):
    rows = []
    for code in CODES:
        t = D[code]
        s = np.asarray(t[6], float); oos = np.asarray(t[7], bool)
        tr, _, _ = jb_rules.simulate(t[1], t[2], t[3], t[4], s, GROUP, ok=oos & np.isfinite(s), v=t[5], mkt_dd=MK[code], rule=rule)
        rows += [(t[0][b], r) for i, b, j, px, r, w in tr if j is not None]
    return rows


def st(rows, f=lambda d: True):
    r = np.array([x[1] for x in rows if f(x[0])])
    return {"n": int(len(r)), "mean": float(r.mean()) if len(r) else float("nan"), "win": float((r > 0).mean()) if len(r) else float("nan"),
            "loss": float(r[r < 0].mean()) if (r < 0).any() else 0.0}


fmt = lambda s: f"{s['n']:4d}건 {s['mean'] * 100:+6.2f}% 승률 {s['win'] * 100:4.0f}% 손실평균 {s['loss'] * 100:+6.2f}%" if s["n"] else "   없음"
E, L = (lambda d: d <= "2021"), (lambda d: d >= "2022")
cur = run(BASE)
print(f"{GROUP} 지금 규칙 | 전체 {fmt(st(cur))} | ~2021 {fmt(st(cur, E))} | 2022~ {fmt(st(cur, L))}")
picked = {}
for reg in ("up", "side", "down"):
    name = {"up": "상승", "side": "횡보", "down": "하락"}[reg]
    others = {k: {"skip": True} for k in ("up", "side", "down") if k != reg}
    res = []
    for en, ev in ENTRY.items():
        for xn, xv in EXIT.items():
            rule = {**BASE, "regimes": {**others, reg: {**ev, "exit": xv}}}
            rows = run(rule)
            res.append((en, xn, ev, xv, st(rows), st(rows, E), st(rows, L)))
    now = run({**BASE, "regimes": others})
    print(f"\n==== {name} 상태 — 지금 규칙: 전체 {fmt(st(now))} | ~2021 {fmt(st(now, E))} | 2022~ {fmt(st(now, L))}")
    cand = [x for x in res if x[5]["n"] >= 15 and x[5]["win"] >= 0.65 and x[6]["n"] >= 10]
    for x in sorted(res, key=lambda x: -(x[5]["mean"] if x[5]["n"] >= 15 else -9))[:8]:
        print(f"  {x[0]:20s} · {x[1]:18s} | ~2021 {fmt(x[5])} | 2022~ {fmt(x[6])}")
    if cand:
        best = max(cand, key=lambda x: x[5]["mean"])
        if best[5]["mean"] <= 0:
            picked[reg] = {"skip": True}
            print(f"  ▶ 고른 것: 이 상태에서는 사지 않음(가장 좋은 것도 2021년까지 평균 {best[5]['mean'] * 100:+.1f}%)")
        else:
            picked[reg] = {**best[2], "exit": best[3]}
            print(f"  ▶ 고른 것(2021년까지): {best[0]} · {best[1]} → 2022~ {fmt(best[6])}")
    else:
        print("  ▶ 조건을 채우는 것이 없어 지금 규칙 그대로")
new_rule = {**BASE, "regimes": picked}
new = run(new_rule)
print(f"\n합친 새 규칙 | 전체 {fmt(st(new))} | ~2021 {fmt(st(new, E))} | 2022~ {fmt(st(new, L))}")
print(f"지금 규칙    | 전체 {fmt(st(cur))} | ~2021 {fmt(st(cur, E))} | 2022~ {fmt(st(cur, L))}")
json.dump({"group": GROUP, "regimes": picked, "new": {"all": st(new), "early": st(new, E), "late": st(new, L)}, "cur": {"all": st(cur), "early": st(cur, E), "late": st(cur, L)}},
          open(os.path.join("data", "research", f"jb_regime_{GROUP}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
