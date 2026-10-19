"""지정가 주문으로 저가 매수·고가 매도·손실 회복 매도(2026-10-19, 사용자 "저점 매수·고점 매도와 손실 매매 개선을 다시 개선해 무조건 적용").
core/jb_rules._trade_order 로 구간마다 단계별로 고른다(2021년까지 평균이 가장 높은 것, 승률은 지금보다 2%p 넘게 낮으면 제외) → 2022년 이후로 확인.
  1단계 매수: 신호일 종가보다 k% 아래 지정가로 W일 기다림(안 닿으면 마지막 날 종가 / 안 삼)
  2단계 추가 매수: 지금(종가 −10% → 다음 날 시가) / 장중 지정가 −10% / 3번에 나눠(−7·−14%, −5·−10%, −10·−20%)
  3단계 매도: 목표가(+20·30%) · 추가 매수 뒤 평균 단가 회복(+3·5·10%) · (대형주) 볼린저 상단 지정가(수익 10·20%↑)
python tools/research/jb_order.py → data/research/jb_order.json"""
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
D = {k: v for k, v in D.items() if G.get(k) in TIERS}        # 맡은 구간만 남겨 메모리를 줄임(구간마다 따로 동시에 돌림)
MK = {}


def run(group, rule):
    rows = []
    for code, t in D.items():
        if G.get(code) != group:
            continue
        if code not in MK:
            MK[code] = jb_rules.market_dd(t[0])
        s = np.asarray(t[6], float); ok = np.asarray(t[7], bool) & np.isfinite(s)
        o, h, l, c = (np.asarray(t[x], float) for x in (1, 2, 3, 4))
        tr, _, aux = jb_rules.simulate(o, h, l, c, s, group, ok=ok, v=t[5], mkt_dd=MK[code], rule=rule)
        fills = aux.get("fills", {})
        for i, b, j, px, r, w in tr:
            if j is None:
                continue
            f = fills.get(b) or [(b, o[b], 1.0)]
            avg = sum(x[1] * x[2] for x in f) / sum(x[2] for x in f)
            if b in aux["adds"] and b not in fills:
                avg = (o[b] + aux["adds"][b][1]) / 2
            lo_p = jb_rules.near_low(l, i)[1]; hi_p = jb_rules.near_high(h, j)[1]
            rows.append((t[0][b], float(r), avg / lo_p - 1, 1 - px / hi_p, j - b, "회복" in w, "목표가" in w or "지정가" in w))
    return rows


def st(rows, f):
    r = [x for x in rows if f(x[0])]
    if not r:
        return {"n": 0}
    a = np.array([x[1] for x in r])
    return {"n": len(r), "mean": float(a.mean()), "med": float(np.median(a)), "win": float((a > 0).mean()), "loss": float(a[a < 0].mean()) if (a < 0).any() else 0.0,
            "nloss": int((a < 0).sum()), "from_low": float(np.mean([x[2] for x in r])), "from_high": float(np.mean([x[3] for x in r])), "days": float(np.mean([x[4] for x in r])),
            "recover": int(sum(x[5] for x in r))}


E, L = (lambda d: d <= "2021"), (lambda d: d >= "2022")
fmt = lambda s: (f"{s['n']:4d}건 평균 {s['mean'] * 100:+6.2f}% 승률 {s['win'] * 100:3.0f}% 손실 {s['nloss']:3d}건 {s['loss'] * 100:+6.1f}% · 평단이 저점보다 +{s['from_low'] * 100:4.1f}% · 고점보다 −{s['from_high'] * 100:4.1f}%"  # noqa: E731
                 if s["n"] else "없음")


def ev(group, rule):
    rows = run(group, rule)
    return {"early": st(rows, E), "late": st(rows, L), "all": st(rows, lambda d: True)}


out = {}
for g in TIERS:
    base = jb_rules.rule_for(g)
    print(f"\n== {g}", flush=True)
    cur = ev(g, base)
    same = ev(g, {**base, "order": {}})
    print(f"  지금                 | ~2021 {fmt(cur['early'])} | 2022~ {fmt(cur['late'])}")
    print(f"  (주문 엔진 기본값 확인) | ~2021 {fmt(same['early'])} | 2022~ {fmt(same['late'])}", flush=True)
    res = {"지금": cur, "엔진 기본값": same}
    ok = lambda r, b: r["early"]["n"] >= 0.6 * b["early"]["n"] and r["early"]["win"] >= b["early"]["win"] - 0.02  # noqa: E731

    def pick(cands, ref, label):
        best_k, best = None, ref
        for name, od in cands.items():
            r = ev(g, {**base, "order": od})
            res[f"{label}: {name}"] = r
            print(f"  {label}: {name:28s} | ~2021 {fmt(r['early'])} | 2022~ {fmt(r['late'])}", flush=True)
            if ok(r, cur) and r["early"]["mean"] > best["early"]["mean"]:
                best_k, best = name, r
        return best_k, best

    od = {}
    c1 = {}
    for k in (0.02, 0.03, 0.05, 0.07):
        for W in (3, 5, 10):
            for fb in ("close", "skip"):
                c1[f"종가 −{k * 100:.0f}% 지정가 {W}일·{'안 닿으면 종가' if fb == 'close' else '안 닿으면 안 삼'}"] = {"buy_k": k, "buy_wait": W, "buy_fallback": fb}
    k1, b1 = pick(c1, same, "매수")
    if k1:
        od.update(c1[k1])
    c2 = {f"{'·'.join(f'−{d * 100:.0f}%' for d in lad)} {'장중 지정가' if lim else '종가→다음 날 시가'}": {**od, "ladder": lad, "add_limit": lim}
          for lad in ([0.10], [0.07, 0.14], [0.05, 0.10], [0.10, 0.20], [0.15]) for lim in (False, True)}
    k2, b2 = pick(c2, b1, "추가 매수")
    if k2:
        od = dict(c2[k2])
    c3 = {}
    for rc in (None, 0.03, 0.05, 0.10):
        for tp in (None, 0.20, 0.30):
            for bg in ((None, 0.10, 0.20) if base["exit"][0] == "hold" else (None,)):
                if rc is None and tp is None and bg is None:
                    continue
                c3[f"회복 {rc and f'+{rc * 100:.0f}%'} · 목표 {tp and f'+{tp * 100:.0f}%'} · 상단 {bg and f'+{bg * 100:.0f}%'}"] = {**od, "recover": rc, "tp": tp, "band_gain": bg}
    k3, b3 = pick(c3, b2, "매도")
    if k3:
        od = dict(c3[k3])
    fin = ev(g, {**base, "order": od})
    print(f"  ▶ 고른 것(2021년까지 기준): 매수 {k1} / 추가 {k2} / 매도 {k3}\n    ~2021 {fmt(fin['early'])}\n    2022~ {fmt(fin['late'])}  ← 확인 기간\n    지금 2022~ {fmt(cur['late'])}", flush=True)
    out[g] = {"order": od, "picked": [k1, k2, k3], "final": fin, "now": cur, "all": res}
json.dump(out, open(os.path.join("data", "research", f"jb_order_{'_'.join(TIERS)}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
