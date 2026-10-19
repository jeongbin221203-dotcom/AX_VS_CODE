"""수익일 때 팔아 손실을 줄이고(이익 지키기), 큰 상승은 끝까지 가져가는(고점 대비 하락·절반 익절) 매도 비교(2026-10-19).
시총 구간마다 지금 규칙(나눠 사기 포함) 위에 하나씩·둘씩 더해 본다. '2021년까지'로 고르고 2022년 이후로 확인.
받아들이는 기준: 두 기간 모두 평균이 지금보다 높고, 손실 거래 평균 손실이 지금보다 작을 것.
python tools/research/jb_profit.py 대형주"""
import json
import os
import pickle
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core import jb_rules, picks  # noqa: E402

GROUP = sys.argv[1] if len(sys.argv) > 1 else "대형주"
D = pickle.load(open(os.environ.get("JB_PKL") or os.path.join("data", "research", "jb_scores.pkl"), "rb"))
G = {k: v[0] for k, v in ((picks.load_today() or {}).get("scores") or {}).items()}
codes = [k for k in D if G.get(k) == GROUP]
MK = {k: jb_rules.market_dd(D[k][0]) for k in codes}
BASE = jb_rules.rule_for(GROUP)


def run(rule):
    rows = []
    for code in codes:
        t = D[code]; s = np.asarray(t[6], float); oos = np.asarray(t[7], bool)
        tr, _, _ = jb_rules.simulate(t[1], t[2], t[3], t[4], s, GROUP, ok=oos & np.isfinite(s), v=t[5], mkt_dd=MK[code], rule=rule)
        rows += [(t[0][b], r, j - b) for i, b, j, px, r, w in tr if j is not None]
    return rows


def st(rows, f=lambda d: True):
    r = np.array([x[1] for x in rows if f(x[0])]); d = np.array([x[2] for x in rows if f(x[0])], float)
    if not len(r):
        return {"n": 0, "mean": np.nan, "win": np.nan, "loss": 0.0, "big": np.nan, "days": np.nan, "worst": np.nan}
    return {"n": int(len(r)), "mean": float(r.mean()), "win": float((r > 0).mean()), "loss": float(r[r < 0].mean()) if (r < 0).any() else 0.0,
            "big": float((r >= 0.3).mean()), "days": float(d.mean()), "worst": float(r.min())}


E, L = (lambda d: d <= "2021"), (lambda d: d >= "2022")
fmt = lambda s: f"{s['n']:4d}건 {s['mean'] * 100:+6.2f}% 승률 {s['win'] * 100:4.0f}% 손실평균 {s['loss'] * 100:+6.2f}% 최악 {s['worst'] * 100:+5.0f}% +30%↑ {s['big'] * 100:3.0f}% {s['days']:4.0f}일"
VAR = {}
for t_, f_ in ((0.05, 0.01), (0.08, 0.02), (0.10, 0.02), (0.10, 0.05), (0.15, 0.05), (0.20, 0.08), (0.30, 0.15)):
    VAR[f"이익 지키기 +{t_ * 100:.0f}% 뒤 +{f_ * 100:.0f}% 아래면 매도"] = {"protect": (t_, f_)}
for t_, d_ in ((0.10, 0.07), (0.15, 0.10), (0.20, 0.10), (0.30, 0.12), (0.40, 0.15), (0.50, 0.20)):
    VAR[f"고점 대비: +{t_ * 100:.0f}% 뒤 고점서 −{d_ * 100:.0f}%"] = {"trail": (t_, d_)}
for tp in (0.10, 0.15, 0.20, 0.30):
    VAR[f"절반 익절 +{tp * 100:.0f}%"] = {"tp_half": tp}
for (pn, pv) in (((0.08, 0.02), "a"), ((0.10, 0.02), "b"), ((0.15, 0.05), "c")):
    for (tn, tv) in (((0.30, 0.12), "x"), ((0.50, 0.20), "y")):
        VAR[f"이익 지키기 +{pn[0] * 100:.0f}/+{pn[1] * 100:.0f} + 고점 대비 +{tn[0] * 100:.0f}/−{tn[1] * 100:.0f}"] = {"protect": pn, "trail": tn}
    for tp in (0.15, 0.20):
        VAR[f"이익 지키기 +{pn[0] * 100:.0f}/+{pn[1] * 100:.0f} + 절반 익절 +{tp * 100:.0f}%"] = {"protect": pn, "tp_half": tp}
# 기본 매도(보유 기간·AI 경계)를 늘려 큰 상승을 더 가져가기 + 이익 지키기
if BASE["exit"][0] == "hold":
    for N in (60, 80):
        for pn in ((0.08, 0.02), (0.10, 0.02)):
            VAR[f"{N}일 보유 + 이익 지키기 +{pn[0] * 100:.0f}/+{pn[1] * 100:.0f}"] = {"exit": ("hold", N), "protect": pn}
else:
    for (a, g) in ((0.25, 0.30), (0.30, 0.40), (0.20, 0.40)):
        for pn in ((0.08, 0.02), (0.10, 0.02)):
            VAR[f"AI {a * 100:.0f}·상단 +{g * 100:.0f}% + 이익 지키기 +{pn[0] * 100:.0f}/+{pn[1] * 100:.0f}"] = {"exit": ("ai_or_bb", a, g), "protect": pn}

cur = run(BASE)
C, CE, CL = st(cur), st(cur, E), st(cur, L)
print(f"{GROUP} 지금 규칙 | 전체 {fmt(C)}\n   ~2021 {fmt(CE)}\n   2022~ {fmt(CL)}")
res = []
for name, ov in VAR.items():
    rows = run({**BASE, **ov})
    res.append((name, ov, st(rows), st(rows, E), st(rows, L)))
ok = [x for x in res if x[3]["mean"] > CE["mean"] and x[4]["mean"] > CL["mean"] and x[2]["loss"] > C["loss"]]
print(f"\n기준 통과(두 기간 평균↑ · 손실 평균↓) {len(ok)}개 — 2021년까지 평균 순:")
for x in sorted(ok, key=lambda x: -x[3]["mean"])[:12]:
    print(f"  {x[0]:44s} | 전체 {fmt(x[2])} | ~2021 {x[3]['mean'] * 100:+6.2f}% | 2022~ {x[4]['mean'] * 100:+6.2f}%")
print("\n참고 — 손실 평균을 가장 많이 줄인 것:")
for x in sorted(res, key=lambda x: -x[2]["loss"])[:6]:
    print(f"  {x[0]:44s} | 전체 {fmt(x[2])} | ~2021 {x[3]['mean'] * 100:+6.2f}% | 2022~ {x[4]['mean'] * 100:+6.2f}%")
print("\n참고 — 전체 평균이 가장 높은 것:")
for x in sorted(res, key=lambda x: -x[2]["mean"])[:6]:
    print(f"  {x[0]:44s} | 전체 {fmt(x[2])} | ~2021 {x[3]['mean'] * 100:+6.2f}% | 2022~ {x[4]['mean'] * 100:+6.2f}%")
pick = max(ok, key=lambda x: x[3]["mean"]) if ok else None
print("\n▶ 고른 것(2021년까지 평균 최대):", (pick[0] + " → 2022~ " + fmt(pick[4])) if pick else "없음(지금 규칙 유지)")
json.dump({"group": GROUP, "cur": {"all": C, "early": CE, "late": CL}, "pick": pick and {"name": pick[0], "rule": {k: list(v) if isinstance(v, tuple) else v for k, v in pick[1].items()},
           "all": pick[2], "early": pick[3], "late": pick[4]}}, open(os.path.join("data", "research", f"jb_profit_{GROUP}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
