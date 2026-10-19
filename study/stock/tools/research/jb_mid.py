"""중형주 JB 규칙 개선 연구(2026-10-19) — 지금 중형주 규칙(강한 신호 + %B ≤ 0.5 매수, AI 40점 아래 또는 수익 20%↑ 상단 매도)에서
종목별 성적을 하위·중간·상위 3묶음으로 나누고, 매수 당시 차트 특징(지표·단테 신호·시장)으로 손실 거래를 가려내는 조건과 매도 바꾸기를 시험한다.
받아들이는 기준: 2021년까지·2022년 이후 모두 평균이 오르고, 하위·중간 묶음이 오르며, 상위 묶음은 크게 줄지 않을 것(−1%p 이내).
python tools/research/jb_mid.py [구간=중형주]  → data/research/jb_mid_<구간>.json"""
import json
import os
import pickle
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core import ai, jb_rules, picks, service  # noqa: E402
from core import backtest as bt  # noqa: E402

GROUP = sys.argv[1] if len(sys.argv) > 1 else "중형주"
COST = 0.003
FEAT = ["dist20", "dist60", "dist224", "slope20", "slope60", "pos60", "aligned", "reversed", "ret5", "ret20", "ret60", "rs20", "atrp", "vol20", "body", "upper", "lower",
        "volratio", "vr20_60", "obv20", "liq", "mkt_r20", "mkt_r60", "mkt_dd250", "dd120", "low_age", "rebound60", "range20", "hh20_gap", "dante_n10"]


def _feat(code):
    """종목의 모든 날 특징(ai.compute_features) 중 쓸 것만 + 단테 신호 수(5봉 안)."""
    try:
        df = service.load_prices(code)
        X = ai.compute_features(df, ai.MKT_DF, True)
        cols = {k: X[:, ai.NAMES.index(k)] for k in FEAT if k in ai.NAMES}
        sig = [n for n in ai.NAMES if n.startswith("sig")]
        S = X[:, [ai.NAMES.index(n) for n in sig]]
        cols["sig_n5"] = np.sum((S <= 4) & np.isfinite(S), axis=1)       # 'sigXX' = 그 신호가 난 뒤 지난 봉 수 → 5봉 안 단테·기법 신호 수
        return code, df.index.strftime("%Y-%m-%d").to_numpy(), cols
    except Exception:
        return code, None, None


def simulate(P, ok_entry=None, exit_kind=("ai_or_bb", 0.40, 0.20), stop=None, trail=None, cap=jb_rules.CAP):
    """jb_rules.simulate 와 같은 규칙 + 매수 조건(ok_entry(i) → bool)·손절(stop)·고점 대비 이탈(trail)·보유 상한(cap)."""
    o, h, c, s, oos = P["o"], P["h"], P["c"], P["s"], P["oos"]
    n = len(c)
    sc = np.nan_to_num(s, nan=0.0)
    mid, up, lo = jb_rules.bands(c)
    sm5 = np.convolve(sc, np.ones(5) / 5, mode="full")[:n]
    out, busy = [], -1
    for i, t in jb_rules._crossings(sc, oos & np.isfinite(s)):
        if t != "강" or not np.isfinite(up[i]) or up[i] <= lo[i] or (c[i] - lo[i]) > 0.5 * (up[i] - lo[i]):
            continue
        if i <= busy or (ok_entry and not ok_entry(i)):
            continue
        b = i + 1
        if b >= n:
            continue
        e = o[b]; hi = e; sell = None
        for j in range(b + 1, min(b + cap, n)):
            u = up[j - 1]
            if exit_kind[0] == "ai_or_bb" and np.isfinite(u) and h[j] >= u and max(o[j], u) >= e * (1 + exit_kind[2]):
                sell = (j, max(o[j], u)); break
            if exit_kind[0] == "ai_or_bb" and j - b >= 5 and sm5[j] < exit_kind[1]:
                sell = (j, c[j]); break
            if exit_kind[0] == "hold" and j - b >= exit_kind[1]:
                sell = (j, c[j]); break
            if stop and c[j] <= e * (1 - stop):
                sell = (j, c[j]); break
            hi = max(hi, c[j])
            if trail and hi >= e * (1 + trail[0]) and c[j] <= hi * (1 - trail[1]):
                sell = (j, c[j]); break
        if sell is None:
            if b + cap < n:
                sell = (b + cap, c[b + cap])
            else:
                busy = n
                continue
        j, px = sell
        out.append((i, b, j, px / e - 1 - COST))
        busy = j
    return out


def main():
    t0 = time.time()
    today = picks.load_today() or {}
    codes = [k for k, v in (today.get("scores") or {}).items() if v[0] == GROUP]
    D = pickle.load(open(os.path.join("data", "research", "jb_scores.pkl"), "rb"))
    codes = [k for k in codes if k in D]
    print(GROUP, "종목", len(codes), flush=True)
    P = {}
    with ProcessPoolExecutor(max_workers=3, initializer=ai._init_worker, initargs=(bt.MKT_PATH,)) as ex:
        for code, dates, cols in ex.map(_feat, codes, chunksize=4):
            if cols is None:
                continue
            t = D[code]
            if len(t[0]) != len(dates):
                continue
            P[code] = {"d": t[0], "o": t[1], "h": t[2], "c": t[4], "s": np.asarray(t[6], float), "oos": np.asarray(t[7], bool), "f": cols}
    print("특징 계산", len(P), f"{time.time() - t0:.0f}초", flush=True)

    def run(entry=None, **kw):
        rows = []
        for code, p in P.items():
            ok = (lambda i, p=p: entry(p, i)) if entry else None
            for i, b, j, r in simulate(p, ok, **kw):
                rows.append((code, p["d"][b], r, j - b, i))
        return rows

    base = run()
    # 종목별 성적(로그 수익 합)으로 하위·중간·상위 묶음
    contrib = {}
    for code, d, r, hd, i in base:
        contrib[code] = contrib.get(code, 0.0) + np.log1p(r)
    order = sorted(contrib, key=contrib.get)
    k = len(order) // 3
    tier = {c: ("하위" if n < k else "중간" if n < 2 * k else "상위") for n, c in enumerate(order)}

    def stat(rows, cond=lambda x: True):
        r = np.array([x[2] for x in rows if cond(x)])
        return (len(r), float(r.mean()) if len(r) else np.nan, float((r > 0).mean()) if len(r) else np.nan)

    def report(name, rows):
        a, e, l = stat(rows), stat(rows, lambda x: x[1] <= "2021"), stat(rows, lambda x: x[1] >= "2022")
        g = {t: stat(rows, lambda x, t=t: tier.get(x[0]) == t) for t in ("하위", "중간", "상위")}
        return {"name": name, "all": a, "early": e, "late": l, **{t: g[t] for t in g}}

    fmt = lambda s: f"{s[0]:4d}건 {s[1] * 100:+6.2f}% {s[2] * 100:4.0f}%" if s[0] else "   없음"
    B = report("지금 중형주 규칙", base)

    def line(R):
        return (f"{R['name'][:34]:34s} | 전체 {fmt(R['all'])} | ~2021 {fmt(R['early'])} | 2022~ {fmt(R['late'])} | 하위 {fmt(R['하위'])} | 중간 {fmt(R['중간'])} | 상위 {fmt(R['상위'])}")
    print("\n" + line(B))

    # 1) 손실 거래 대 이익 거래 — 매수 당시 특징 중앙값
    feats = list(next(iter(P.values()))["f"].keys())
    los = [(c, i) for c, d, r, hd, i in base if r <= 0]
    win = [(c, i) for c, d, r, hd, i in base if r > 0]
    print(f"\n손실 거래 {len(los)}건 대 이익 거래 {len(win)}건 — 매수 신호 날 특징 중앙값")
    for f in feats:
        lv = np.nanmedian([P[c]["f"][f][i] for c, i in los]); wv = np.nanmedian([P[c]["f"][f][i] for c, i in win])
        print(f"  {f:10s} 손실 {lv:9.3f} | 이익 {wv:9.3f}")

    # 2) 매수 조건 후보(특징의 분위수 경계) — 2021년까지 거래로 경계를 정함
    early_vals = {f: np.array([P[c]["f"][f][i] for c, d, r, hd, i in base if d <= "2021"], float) for f in feats}
    cands = []
    for f in feats:
        v = early_vals[f][np.isfinite(early_vals[f])]
        if len(v) < 50:
            continue
        for q in (0.1, 0.2, 0.8, 0.9):
            thr = float(np.quantile(v, q))
            if q < 0.5:
                cands.append((f"{f} > {thr:.3g}", lambda p, i, f=f, thr=thr: not (p["f"][f][i] <= thr)))
            else:
                cands.append((f"{f} < {thr:.3g}", lambda p, i, f=f, thr=thr: not (p["f"][f][i] >= thr)))
    res = []
    for name, fn in cands:
        res.append(report("매수 " + name, run(fn)))
    # 3) 매도 바꾸기
    for name, kw in (("손절 −10%", {"stop": 0.10}), ("손절 −15%", {"stop": 0.15}), ("손절 −20%", {"stop": 0.20}),
                     ("+30% 뒤 고점 대비 −10%", {"trail": (0.30, 0.10)}), ("+20% 뒤 고점 대비 −8%", {"trail": (0.20, 0.08)}),
                     ("보유 상한 60봉", {"cap": 60}), ("보유 상한 90봉", {"cap": 90}),
                     ("AI 50점 아래 또는 상단(+20%)", {"exit_kind": ("ai_or_bb", 0.50, 0.20)}), ("AI 40점 아래 또는 상단(+30%)", {"exit_kind": ("ai_or_bb", 0.40, 0.30)}),
                     ("AI 30점 아래 또는 상단(+20%)", {"exit_kind": ("ai_or_bb", 0.30, 0.20)}), ("40일 보유", {"exit_kind": ("hold", 40)})):
        res.append(report("매도 " + name, run(**kw)))

    def good(R):
        return (R["early"][1] > B["early"][1] and R["late"][1] > B["late"][1] and R["하위"][1] > B["하위"][1] and R["중간"][1] > B["중간"][1]
                and R["상위"][1] >= B["상위"][1] - 0.01 and R["all"][0] >= B["all"][0] * 0.5)
    ok = [R for R in res if good(R)]
    ok.sort(key=lambda R: -R["all"][1])
    print(f"\n기준을 모두 통과한 바꾸기 {len(ok)}개 (전체 평균 순):")
    for R in ok[:15]:
        print(line(R))
    print("\n참고 — 하위 묶음을 가장 많이 올린 바꾸기(기준 통과 여부와 관계없이):")
    for R in sorted(res, key=lambda R: -(R["하위"][1] if R["하위"][0] else -9))[:10]:
        print(line(R) + ("  ✔" if good(R) else ""))
    # 4) 통과한 것 둘씩 묶기
    combos = []
    names = {R["name"]: R for R in ok[:6]}
    entry_fn = dict(("매수 " + n, f) for n, f in cands)
    exit_kw = {"매도 손절 −10%": {"stop": 0.10}, "매도 손절 −15%": {"stop": 0.15}, "매도 손절 −20%": {"stop": 0.20}, "매도 +30% 뒤 고점 대비 −10%": {"trail": (0.30, 0.10)},
               "매도 +20% 뒤 고점 대비 −8%": {"trail": (0.20, 0.08)}, "매도 보유 상한 60봉": {"cap": 60}, "매도 보유 상한 90봉": {"cap": 90},
               "매도 AI 50점 아래 또는 상단(+20%)": {"exit_kind": ("ai_or_bb", 0.50, 0.20)}, "매도 AI 40점 아래 또는 상단(+30%)": {"exit_kind": ("ai_or_bb", 0.40, 0.30)},
               "매도 AI 30점 아래 또는 상단(+20%)": {"exit_kind": ("ai_or_bb", 0.30, 0.20)}, "매도 40일 보유": {"exit_kind": ("hold", 40)}}
    ks = list(names)
    for a in range(len(ks)):
        for b in range(a + 1, len(ks)):
            ea, eb = entry_fn.get(ks[a]), entry_fn.get(ks[b])
            kw = {**exit_kw.get(ks[a], {}), **exit_kw.get(ks[b], {})}
            if ea and eb:
                fn = lambda p, i, ea=ea, eb=eb: ea(p, i) and eb(p, i)
            else:
                fn = ea or eb
            if (ks[a] in exit_kw and ks[b] in exit_kw) and set(exit_kw[ks[a]]) & set(exit_kw[ks[b]]):
                continue
            R = report(ks[a] + " + " + ks[b].replace("매수 ", "").replace("매도 ", ""), run(fn, **kw))
            combos.append(R)
    combos = [R for R in combos if good(R)]
    combos.sort(key=lambda R: -R["all"][1])
    print(f"\n둘씩 묶어 통과한 것 {len(combos)}개:")
    for R in combos[:10]:
        print(line(R))
    json.dump({"group": GROUP, "base": B, "pass": ok[:15], "combos": combos[:10], "tier_counts": {t: sum(1 for v in tier.values() if v == t) for t in ("하위", "중간", "상위")}},
              open(os.path.join("data", "research", f"jb_mid_{GROUP}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    print(f"\n끝 {time.time() - t0:.0f}초")


if __name__ == "__main__":
    main()
