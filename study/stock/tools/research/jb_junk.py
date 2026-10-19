"""잡주(시총 1,000억 미만) 전용 JB 규칙 고르기(2026-10-19). 공통 매수(강한 신호 + %B ≤ 0.5)·나눠 사기는 그대로, 추가 매수 조건 × 매도 규칙을 비교.
'2021년까지'로 고르고(평균 최대, 승률 70% 이상, 거래 50건 이상) 2022년 이후로 확인. 지금(소형주 규칙 임시 사용)과 비교.
python tools/research/jb_junk.py"""
import json
import os
import pickle
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core import jb_rules, picks  # noqa: E402

GROUP = "잡주"
D = pickle.load(open(os.environ.get("JB_PKL") or os.path.join("data", "research", "jb_scores.pkl"), "rb"))
G = {k: v[0] for k, v in ((picks.load_today() or {}).get("scores") or {}).items()}
codes = [k for k in D if G.get(k) == GROUP]
MK = {k: jb_rules.market_dd(D[k][0]) for k in codes}
BASE = jb_rules.rule_for(GROUP)
print("잡주 종목", len(codes))


def run(rule):
    rows = []
    for code in codes:
        t = D[code]; s = np.asarray(t[6], float); oos = np.asarray(t[7], bool)
        tr, _, _ = jb_rules.simulate(t[1], t[2], t[3], t[4], s, GROUP, ok=oos & np.isfinite(s), v=t[5], mkt_dd=MK[code], rule=rule)
        rows += [(t[0][b], r, j - b) for i, b, j, px, r, w in tr if j is not None]
    return rows


def st(rows, f=lambda d: True):
    r = np.array([x[1] for x in rows if f(x[0])])
    if not len(r):
        return {"n": 0, "mean": float("nan"), "win": float("nan"), "loss": 0.0, "worst": float("nan")}
    return {"n": int(len(r)), "mean": float(r.mean()), "win": float((r > 0).mean()), "loss": float(r[r < 0].mean()) if (r < 0).any() else 0.0, "worst": float(r.min())}


E, L = (lambda d: d <= "2021"), (lambda d: d >= "2022")
fmt = lambda s: f"{s['n']:4d}건 {s['mean'] * 100:+6.2f}% 승률 {s['win'] * 100:4.0f}% 손실평균 {s['loss'] * 100:+6.2f}% 최악 {s['worst'] * 100:+5.0f}%" if s["n"] else "없음"
ENTRY = {"조건 없음": {}, "거래대금 50억 미만": {"liq_max": 9.7}, "거래대금 100억 미만": {"liq_max": 10.0}, "거래대금 20억 미만": {"liq_max": 9.3},
         "60일 −20%↓": {"ret60_max": -0.20}, "60일 −30%↓": {"ret60_max": -0.30}, "시장 고점 −5%↓": {"mkt_dd_max": -0.05}}
EXIT = {"20일 보유": ("hold", 20), "40일 보유": ("hold", 40), "60일 보유": ("hold", 60), "AI 40·상단 +20%": ("ai_or_bb", 0.40, 0.20),
        "AI 30·상단 +20%": ("ai_or_bb", 0.30, 0.20), "AI 50·상단 +20%": ("ai_or_bb", 0.50, 0.20), "AI 40·상단 +30%": ("ai_or_bb", 0.40, 0.30), "AI 40·상단 +15%": ("ai_or_bb", 0.40, 0.15)}
cur = run(BASE)
print(f"지금(소형주 규칙) | 전체 {fmt(st(cur))} | ~2021 {fmt(st(cur, E))} | 2022~ {fmt(st(cur, L))}")
res = []
for en, ev in ENTRY.items():
    for xn, xv in EXIT.items():
        rule = {"entry": "strong_b50", **ev, "exit": xv}
        rows = run(rule)
        res.append((en, xn, rule, st(rows), st(rows, E), st(rows, L)))
cand = [x for x in res if x[4]["n"] >= 50 and x[4]["win"] >= 0.70 and x[5]["n"] >= 30]
print("\n2021년까지 평균 상위:")
for x in sorted(cand, key=lambda x: -x[4]["mean"])[:10]:
    print(f"  {x[0]:14s} · {x[1]:14s} | 전체 {fmt(x[3])} | ~2021 {x[4]['mean'] * 100:+6.2f}% | 2022~ {fmt(x[5])}")
pick = max(cand, key=lambda x: x[4]["mean"]) if cand else None
if pick:
    print(f"\n▶ 고른 것(2021년까지): {pick[0]} · {pick[1]}\n   ~2021 {fmt(pick[4])}\n   2022~ {fmt(pick[5])}  ← 고를 때 안 본 기간\n   지금  ~2021 {fmt(st(cur, E))} / 2022~ {fmt(st(cur, L))}")
json.dump({"cur": {"all": st(cur), "early": st(cur, E), "late": st(cur, L)}, "pick": pick and {"entry": pick[0], "exit": pick[1], "rule": {k: list(v) if isinstance(v, tuple) else v for k, v in pick[2].items()},
           "all": pick[3], "early": pick[4], "late": pick[5]}}, open(os.path.join("data", "research", "jb_junk.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
