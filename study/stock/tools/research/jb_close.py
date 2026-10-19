"""매수 시점 비교(2026-10-19, 사용자 "3시쯤이면 방향이 정해지니 3시에 매수 종목을 띄워"):
지금 = 신호일 마감 값으로 판단 → 다음 거래일 시가 매수 / 비교 = 같은 신호를 그날 종가(15:20~15:30 종가 단일가)에 매수.
(15시 값과 종가는 조금 다르다 — 15시에 판단하면 마감 때 신호가 사라지는 경우가 있음, 그 차이는 이 시험에 없음)
구간별로 '2021년까지'와 '2022년 이후' 모두 비교.  python tools/research/jb_close.py"""
import json
import os
import pickle
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core import jb_rules, picks  # noqa: E402

D = pickle.load(open(os.environ.get("JB_PKL") or os.path.join("data", "research", "jb_scores.pkl"), "rb"))
G = {k: v[0] for k, v in ((picks.load_today() or {}).get("scores") or {}).items()}
MK = {}


def run(group, close):
    rule = {**jb_rules.rule_for(group), **({"entry_close": True} if close else {})}
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


def st(rows, f=lambda d: True):
    r = np.array([x[1] for x in rows if f(x[0])])
    return {"n": int(len(r)), "mean": float(r.mean()), "med": float(np.median(r)), "win": float((r > 0).mean())} if len(r) else {"n": 0}


fmt = lambda s: f"{s['n']:5d}건 평균 {s['mean'] * 100:+6.2f}% 중앙 {s['med'] * 100:+6.2f}% 승률 {s['win'] * 100:3.0f}%" if s["n"] else "없음"
E, L = (lambda d: d <= "2021"), (lambda d: d >= "2022")
out = {}
for g in ("대형주", "중형주", "소형주", "잡주"):
    o = {}
    for k, close in (("다음 날 시가(지금)", False), ("그날 종가(15시 판단)", True)):
        rows = run(g, close)
        o[k] = {"all": st(rows), "early": st(rows, E), "late": st(rows, L)}
        print(f"{g} {k:16s} | 전체 {fmt(o[k]['all'])} | ~2021 {fmt(o[k]['early'])} | 2022~ {fmt(o[k]['late'])}", flush=True)
    out[g] = o
json.dump(out, open(os.path.join("data", "research", "jb_close.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
