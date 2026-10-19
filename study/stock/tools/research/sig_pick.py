"""신호 화면 '예측 추천'·'AI 추천'을 더 정확하게 고르는 방법 비교 — 2022-01 이후 날마다(daily_checks) 화면과 같은 점검을 다시 매기고,
고르는 방법별로 다음날 시가 매수 → 5일·20일 뒤 종가 수익을 잰다(연구와 같은 정의, 비용 0.3% 뺌).
시총 구간은 오늘 기준 그룹(core/picks.load_today — 생존 편향 있음). JB AI 점수는 tools/research/jb_scores.py 의 결과(학습에 안 쓴 구간만).
python tools/research/sig_pick.py"""
import os
import pickle
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core import db, picks, predict, signal_scan  # noqa: E402

COST = 0.003
today = picks.load_today() or {}
GRP = {k: v[0] for k, v in (today.get("scores") or {}).items()}
ORDER = ("대형주", "중형주", "소형주", "잡주")
pk = os.environ.get("JB_PKL") or os.path.join("data", "research", "jb_scores.pkl")
JB = {}
if os.path.exists(pk):
    for code, t in pickle.load(open(pk, "rb")).items():
        dates, sc, oos = t[0], t[6], t[7]
        JB[code] = {d: float(s) for d, s, o in zip(dates, sc, oos) if o and np.isfinite(s) and d >= "2021-12-01"}
print("JB 점수 종목", len(JB))

with db.get_conn() as c:
    hist = pd.read_sql("SELECT * FROM daily_checks", c)
    names = {r["code"]: r["name"] for r in c.execute("SELECT code,name FROM symbols")}
    px = pd.read_sql("SELECT code,date,open,close FROM prices WHERE date >= '2021-12-01' ORDER BY code,date", c)
PX = {}
for code, g in px.groupby("code", sort=False):
    PX[code] = (g["date"].to_numpy(), g["open"].to_numpy(float), g["close"].to_numpy(float))


def outcome(code, date):
    t = PX.get(code)
    if t is None:
        return None, None
    d, o, c = t
    i = np.searchsorted(d, date, side="right")          # 기준일 다음 거래일
    if i >= len(d) or not o[i]:
        return None, None
    r5 = c[i + 5] / o[i] - 1 - COST if i + 5 < len(d) else None
    r20 = c[i + 20] / o[i] - 1 - COST if i + 20 < len(d) else None
    return r5, r20


info = predict._tech_info(predict.plan())
days = sorted(hist["date"].unique())
rows = []          # (date, kind, code, grp, score, prob, jb, nsig, good, r5, r20)
for k, (d, g) in enumerate(hist.groupby("date", sort=True)):
    recs = g.to_dict("records")
    thr10, thr3, thr2 = signal_scan._quantiles([r["prob"] for r in recs])
    rep = signal_scan._assemble(recs, d, thr10, thr3, thr2, signal_scan._market_dd(d), signal_scan._market_dd(d, "mkt_r20"), names, info)
    for kind in ("pred", "ai"):
        for z in rep[kind]:
            if z["score"] < 0.8:
                continue
            r5, r20 = outcome(z["code"], d)
            jb = JB.get(z["code"], {}).get(d)
            rows.append((d, kind, z["code"], GRP.get(z["code"], "기타"), z["score"], z["prob"] or 0.0, jb, len(z["signals"]),
                         any(s["good"] for s in z["signals"]), r5, r20))
    if k % 200 == 0:
        print(d, len(rows), flush=True)
R = pd.DataFrame(rows, columns=["date", "kind", "code", "grp", "score", "prob", "jb", "nsig", "good", "r5", "r20"])
R.to_pickle(os.path.join("data", "research", "sig_pick_rows.pkl"))


def show(name, sel):
    s = sel.dropna(subset=["r20"])
    if not len(s):
        print(f"{name:38s} | 없음")
        return
    nd = sel["date"].nunique()
    parts = []
    for lab, m in (("22~23", s["date"] < "2024-01-01"), ("24~26", s["date"] >= "2024-01-01")):
        x = s[m]
        parts.append(f"{lab} {len(x):5d}건 20일 {x['r20'].mean() * 100:+5.2f}% 상승 {(x['r20'] > 0).mean() * 100:4.1f}%")
    print(f"{name:38s} | 하루 {len(sel) / max(nd, 1):4.1f}개 · 5일 {s['r5'].mean() * 100:+5.2f}%({(s['r5'] > 0).mean() * 100:4.1f}%) · 20일 {s['r20'].mean() * 100:+5.2f}%({(s['r20'] > 0).mean() * 100:4.1f}%) | " + " | ".join(parts))


def topk(sel, key, k, by_grp=True):
    sel = sel.sort_values(key, ascending=False)
    return sel.groupby(["date", "grp"] if by_grp else ["date"], sort=False).head(k)


for kind in ("pred", "ai"):
    base = R[(R["kind"] == kind) & (R["score"] >= 0.9)]
    print(f"\n==== {'예측' if kind == 'pred' else 'AI'} 추천 (점검 9/10↑) ====")
    show("지금(9/10 전부)", base)
    for g in ORDER:
        show("  " + g, base[base["grp"] == g])
    hasjb = base[base["jb"].notna()]
    show("JB 점수 있는 것만(비교 기준)", hasjb)
    for thr in (0.5, 0.7, 0.8, 0.9):
        show(f"+ JB AI 점수 {thr * 100:.0f}↑", hasjb[hasjb["jb"] >= thr])
    for k in (1, 3, 5):
        show(f"구간마다 JB 점수 상위 {k}", topk(hasjb, "jb", k))
        show(f"구간마다 옛 AI 확률 상위 {k}", topk(base, "prob", k))
    show("검증 기법(✔) 신호 + 신호 2개↑", base[base["good"] & (base["nsig"] >= 2)])
    show("10/10 만", base[base["score"] >= 0.999])
    for g in ORDER:
        x = hasjb[hasjb["grp"] == g]
        show(f"  {g} JB 상위 3", topk(x, "jb", 3))
    # 예측 + AI 둘 다(정합성): 같은 날 예측 추천이면서 AI 상위 10%
    if kind == "pred":
        both = base[base["prob"] > 0]
        aiset = set(zip(R[(R["kind"] == "ai") & (R["score"] >= 0.9)]["date"], R[(R["kind"] == "ai") & (R["score"] >= 0.9)]["code"]))
        show("예측 추천 ∩ AI 추천(같은 날 둘 다)", base[[(d, c) in aiset for d, c in zip(base["date"], base["code"])]])
all_ = R.drop_duplicates(["date", "code"])
print("\n(참고) 그날 점검 8/10↑ 종목 전체:")
show("8/10↑ 전체", all_)
