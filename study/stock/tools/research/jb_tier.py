"""시총 구간(대형·중형·소형·잡주)마다 가장 좋은 JB차트 AI 매수 조건 × 매도 규칙 고르기.
- 학습에 쓰지 않은 구간(oos)의 신호만, 한 종목에 한 거래씩, 비용 0.3%(jb_exit.py 와 같은 계산).
- 고르는 법: '2021년까지'로만 고르고(평균 수익 최대, 승률 60% 이상, 거래 60건 이상) → '2022년 이후'에서 그대로 확인.
  같은 표에 두 기간 중 낮은 평균(최소값) 기준 순위도 함께 보인다(두 기간을 다 보고 고른 것이라 낙관적).
python tools/research/jb_tier.py  → data/research/jb_tier.json(구간별 고른 규칙)"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jb_exit as J  # noqa: E402

J.setup()
bpos = lambda P, i: (P["c"][i] - P["lo"][i]) / (P["up"][i] - P["lo"][i]) if np.isfinite(P["up"][i]) and P["up"][i] > P["lo"][i] else np.nan
ENTRIES = {
    "조건 없음": None,
    "강한 신호(95점)만": lambda P, i: P["s"][i] >= J.TOP,
    "%B ≤ 0.5": lambda P, i: bpos(P, i) <= 0.5,
    "%B ≤ 0.8": lambda P, i: bpos(P, i) <= 0.8,
    "강한 신호 + %B ≤ 0.5": lambda P, i: P["s"][i] >= J.TOP and bpos(P, i) <= 0.5,
    "강한 신호 + %B ≤ 0.8": lambda P, i: P["s"][i] >= J.TOP and bpos(P, i) <= 0.8,
    "종가 > 224일선": lambda P, i: np.isfinite(P["m224"][i]) and P["c"][i] > P["m224"][i],
    "종가 < 224일선": lambda P, i: np.isfinite(P["m224"][i]) and P["c"][i] < P["m224"][i],
}
EXITS = {
    "20일 보유": J.fixed(20), "40일 보유": J.fixed(40), "60일 보유": J.fixed(60),
    "AI 40점 아래": J.score_exit(0.4), "AI 50점 아래": J.score_exit(0.5), "AI 60점 아래": J.score_exit(0.6), "AI 70점 아래": J.score_exit(0.7),
    "전날 볼린저 상단 지정가": J.bb_prev_limit(),
    "AI 50점 아래 또는 상단(수익 0%↑)": J.score_or_bb(0.5, 0.0), "AI 50점 아래 또는 상단(수익 10%↑)": J.score_or_bb(0.5, 0.10),
    "AI 50점 아래 또는 상단(수익 20%↑)": J.score_or_bb(0.5, 0.20), "AI 40점 아래 또는 상단(수익 20%↑)": J.score_or_bb(0.4, 0.20),
    "20일 보유 후 20일선 이탈": J.hold_then_ma(20, "m20"),
}


def stats(rows):
    if not rows:
        return None
    r = np.array([x[3] for x in rows]); d = np.array([x[4] for x in rows], float)
    return {"n": int(len(r)), "mean": float(r.mean()), "med": float(np.median(r)), "win": float((r > 0).mean()), "days": float(d.mean())}


res = {}
for en, ef in ENTRIES.items():
    for xn, xf in EXITS.items():
        rows = J.run(xf, ef)
        for t in ("대형", "중형", "소형", "잡주"):
            rt = [x for x in rows if x[1] == t]
            res[(t, en, xn)] = {"early": stats([x for x in rt if x[0] <= "2021"]), "late": stats([x for x in rt if x[0] >= "2022"]), "all": stats(rt)}
    print("매수 조건", en, "끝", flush=True)

fmt = lambda s: "-" if not s else f"{s['n']:5d}건 평균 {s['mean'] * 100:+6.2f}% 중앙 {s['med'] * 100:+6.2f}% 승률 {s['win'] * 100:4.1f}% 보유 {s['days']:4.0f}일"
ok = lambda s: s and s["n"] >= 60 and s["win"] >= 0.60
out = {}
for t in ("대형", "중형", "소형", "잡주"):
    keys = [k for k in res if k[0] == t]
    base = res[(t, "조건 없음", "20일 보유")]
    print(f"\n======== {t}주 ========")
    print(f"  지금 규칙(조건 없음 · 20일 보유) | ~2021 {fmt(base['early'])} | 2022~ {fmt(base['late'])}")
    cand = [k for k in keys if ok(res[k]["early"]) and res[k]["late"] and res[k]["late"]["n"] >= 30]
    if not cand:
        print("  조건을 채우는 규칙 없음")
        continue
    pick = max(cand, key=lambda k: res[k]["early"]["mean"])          # 2021년까지로만 고름
    robust = sorted(cand, key=lambda k: -min(res[k]["early"]["mean"], res[k]["late"]["mean"]))[:6]
    print(f"  ▶ 2021년까지로 고른 규칙: {pick[1]} · {pick[2]}")
    print(f"      ~2021 {fmt(res[pick]['early'])}")
    print(f"      2022~ {fmt(res[pick]['late'])}   ← 고를 때 안 본 기간")
    print("  두 기간 중 낮은 평균 기준 상위(참고):")
    for k in robust:
        print(f"    {k[1]:16s} · {k[2]:26s} | ~2021 {fmt(res[k]['early'])} | 2022~ {fmt(res[k]['late'])}")
    out[t] = {"entry": pick[1], "exit": pick[2], "early": res[pick]["early"], "late": res[pick]["late"], "base_early": base["early"], "base_late": base["late"],
              "robust": [{"entry": k[1], "exit": k[2], "early": res[k]["early"], "late": res[k]["late"]} for k in robust]}
json.dump(out, open(os.path.join("data", "research", "jb_tier.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("\n저장 data/research/jb_tier.json")
