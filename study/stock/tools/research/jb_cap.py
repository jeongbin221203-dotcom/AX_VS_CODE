"""JB 규칙에서 '매도 조건이 끝내 오지 않아 120거래일 한도에 판' 거래가 얼마나 나쁜지, 그리고 한도·손실 정리를 바꾸면 어떻게 되는지(2026-10-19, 두산 000120 2024-01→2024-07 −24% 사례).
비교: 지금 / 한도 60·80봉 / '산 지 N봉이 지났는데 평균 단가보다 X% 아래면 정리' / '2차 매수 뒤 N봉 안에 평균 단가 회복 못 하면 정리'.
'2021년까지'로 고르고 2022년 이후로 확인(둘 다 좋아져야 채택). 중형·소형·잡주(매도가 AI 점수형인 구간)만 — 대형주는 40일 보유라 해당 없음.
python tools/research/jb_cap.py"""
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


def trades(group, cap=None, late=None):
    """late=(봉 수, 손실): 산 지 '봉 수'가 지났고 그날 종가가 평균 단가보다 '손실' 이상 아래면 그날 종가에 정리."""
    old = jb_rules.CAP
    if cap:
        jb_rules.CAP = cap
    rows = []
    try:
        for code, t in D.items():
            if G.get(code) != group:
                continue
            if code not in MK:
                MK[code] = jb_rules.market_dd(t[0])
            s = np.asarray(t[6], float); oos = np.asarray(t[7], bool)
            tr, _, aux = jb_rules.simulate(t[1], t[2], t[3], t[4], s, group, ok=oos & np.isfinite(s), v=t[5], mkt_dd=MK[code])
            c, o = np.asarray(t[4], float), np.asarray(t[1], float)
            for i, b, j, px, r, w in tr:
                if j is None:
                    continue
                if late:
                    e = o[b]; add = aux["adds"].get(b)
                    for k in range(b + late[0], j):
                        avg = (e + add[1]) / 2 if add and add[0] <= k else e
                        if c[k] / avg - 1 <= -late[1]:
                            r = c[k] / avg - 1 - jb_rules.COST; j = k; w = "late"
                            break
                rows.append((t[0][b], float(r), j - b, w))
    finally:
        jb_rules.CAP = old
    return rows


def st(rows, f=lambda d: True):
    r = np.array([x[1] for x in rows if f(x[0])])
    if not len(r):
        return {"n": 0}
    return {"n": int(len(r)), "mean": float(r.mean()), "med": float(np.median(r)), "win": float((r > 0).mean()), "loss": float(r[r < 0].mean()) if (r < 0).any() else 0.0}


E, L = (lambda d: d <= "2021"), (lambda d: d >= "2022")
fmt = lambda s: f"{s['n']:5d}건 평균 {s['mean'] * 100:+6.2f}% 중앙 {s['med'] * 100:+6.2f}% 승률 {s['win'] * 100:3.0f}% 손실평균 {s['loss'] * 100:+6.2f}%" if s["n"] else "없음"
out = {}
for g in ("중형주", "소형주", "잡주"):
    cur = trades(g)
    capd = [x for x in cur if "거래일 동안" in x[3]]
    print(f"\n== {g}: 지금 {fmt(st(cur))}")
    print(f"   한도(120봉) 매도 {fmt(st(capd))}  ← 비중 {len(capd) / max(1, len(cur)) * 100:.0f}%")
    res = {"지금": cur}
    for cap in (60, 80):
        res[f"한도 {cap}봉"] = trades(g, cap=cap)
    for nb in (40, 60, 80):
        for x in (0.10, 0.15, 0.20):
            res[f"{nb}봉 뒤 −{x * 100:.0f}%면 정리"] = trades(g, late=(nb, x))
    o = {}
    for k, rows in res.items():
        e, l = st(rows, E), st(rows, L)
        o[k] = {"all": st(rows), "early": e, "late": l}
        print(f"   {k:18s} | ~2021 {fmt(e)} | 2022~ {fmt(l)}")
    out[g] = o
json.dump(out, open(os.path.join("data", "research", "jb_cap.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
