"""저가 매수·고가 매도 학습(2026-10-19, 사용자 "모든 차트를 학습·복습해서 저가매수 고가매도를 실현할 방법을 찾아 적용").

정답(봉마다, 그날 종가 기준 — 미래를 보는 것은 정답뿐):
  저점 = 그날 종가에 사면 다음 20봉 동안 저가가 종가보다 3% 넘게 내려가지 않음(더 싸게 살 기회가 거의 없던 날)
  고점 = 그날 종가에 팔면 다음 20봉 동안 고가가 종가보다 3% 넘게 오르지 않음(더 비싸게 팔 기회가 거의 없던 날)
특징(그날 종가까지만): 볼린저 %B·폭, RSI14, 1~60일 수익, 이평선(5·20·60·120·224) 거리, 60·250일 고점 대비·60일 저점 대비, ATR, 거래량 비율, 캔들 몸통·꼬리, 연속 하락,
  JB AI 점수(그날·5일 전 대비·5봉 평균), 시장 52주 고점 대비.
기간: 모델 학습 ~2018 → 기준값 고르기 2019~2021 → 확인 2022~ (모델·기준값 모두 본 적 없음).
적용: 지금 규칙의 매수 신호 뒤 W봉 안에 저점 확률이 기준 이상인 첫날 종가에 매수(없으면 W봉째 종가) / 보유 중 수익 g 이상이고 고점 확률이 기준 이상이면 종가 매도.
결과: data/research/jb_extreme.json, 확률 data/research/jb_extreme_p.pkl, 모델 data/research/jb_extreme_model.pkl
python tools/research/jb_extreme.py"""
import json
import os
import pickle
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core import jb_rules, picks  # noqa: E402

H, TOL = 20, 0.03
NAMES = ["pb", "bw", "rsi", "r1", "r3", "r5", "r10", "r20", "r60", "d5", "d20", "d60", "d120", "d224", "dd60", "dd250", "up60",
         "atr", "v1", "v5", "lw", "uw", "body", "dstreak", "s", "s5", "sm5", "mdd"]


def _ma(x, k):
    out = np.full(len(x), np.nan)
    if len(x) >= k:
        cs = np.cumsum(np.insert(x, 0, 0.0))
        out[k - 1:] = (cs[k:] - cs[:-k]) / k
    return out


def _roll(x, k, fn):
    out = np.full(len(x), np.nan)
    if len(x) >= k:
        out[k - 1:] = fn(np.lib.stride_tricks.sliding_window_view(x, k), axis=1)
    return out


def feats(o, h, l, c, v, s, mdd):
    n = len(c)
    m20 = _ma(c, 20)
    sd = _roll(c, 20, np.std)
    up, lo = m20 + 2 * sd, m20 - 2 * sd
    with np.errstate(divide="ignore", invalid="ignore"):
        pb = (c - lo) / (up - lo)
        bw = (up - lo) / m20
        d = np.diff(c, prepend=c[0])
        g, ls = np.where(d > 0, d, 0.0), np.where(d < 0, -d, 0.0)
        ag, al = _ma(g, 14), _ma(ls, 14)
        rsi = 100 - 100 / (1 + ag / al)
        R = lambda k: np.concatenate([np.full(k, np.nan), c[k:] / c[:-k] - 1])  # noqa: E731
        D = lambda k: c / _ma(c, k) - 1  # noqa: E731
        tr = np.maximum(h - l, np.maximum(abs(h - np.roll(c, 1)), abs(l - np.roll(c, 1))))
        atr = _ma(tr, 14) / c
        vm = _ma(v, 20)
        v1, v5 = v / vm, _ma(v, 5) / vm
        top, bot = np.maximum(o, c), np.minimum(o, c)
        lw, uw, body = (bot - l) / c, (h - top) / c, c / o - 1
        dd60 = c / _roll(h, 60, np.max) - 1
        dd250 = c / _roll(h, 250, np.max) - 1
        up60 = c / _roll(l, 60, np.min) - 1
    ds = np.zeros(n)
    for t in range(1, n):
        ds[t] = ds[t - 1] + 1 if c[t] < c[t - 1] else 0
    s = np.asarray(s, float)
    s5 = np.concatenate([np.full(5, np.nan), s[5:] - s[:-5]])
    sm5 = _ma(np.nan_to_num(s, nan=0.0), 5)
    X = np.column_stack([pb, bw, rsi, R(1), R(3), R(5), R(10), R(20), R(60), D(5), D(20), D(60), D(120), D(224), dd60, dd250, up60,
                         atr, v1, v5, lw, uw, body, ds, s, s5, sm5, mdd]).astype(np.float32)
    X[~np.isfinite(X)] = np.nan
    return X


def labels(h, l, c):
    n = len(c)
    fl, fh = np.full(n, np.nan), np.full(n, np.nan)
    if n > H:
        fl[:n - H] = np.lib.stride_tricks.sliding_window_view(l[1:], H).min(axis=1)[:n - H]
        fh[:n - H] = np.lib.stride_tricks.sliding_window_view(h[1:], H).max(axis=1)[:n - H]
    lo = np.where(np.isfinite(fl), (fl >= c * (1 - TOL)).astype(float), np.nan)
    hi = np.where(np.isfinite(fh), (fh <= c * (1 + TOL)).astype(float), np.nan)
    return lo, hi


if __name__ == "__main__":
    from sklearn.ensemble import HistGradientBoostingClassifier as HGB
    from sklearn.metrics import roc_auc_score
    t0 = time.time()
    D = pickle.load(open(os.environ.get("JB_PKL") or os.path.join("data", "research", "jb_scores.pkl"), "rb"))
    G = {k: v[0] for k, v in ((picks.load_today() or {}).get("scores") or {}).items()}
    codes = [k for k in D if G.get(k) in ("대형주", "중형주", "소형주", "잡주")]
    F, MK = {}, {}
    trX, trL, trH, vaX, vaL, vaH, teX, teL, teH = ([] for _ in range(9))
    rng = np.random.default_rng(0)
    for k, code in enumerate(codes):
        t = D[code]
        dates = np.asarray(t[0])
        o, h, l, c, v = (np.asarray(t[x], float) for x in (1, 2, 3, 4, 5))
        s = np.asarray(t[6], float); oos = np.asarray(t[7], bool)
        s = np.where(oos, s, np.nan)
        MK[code] = jb_rules.market_dd(dates)
        X = feats(o, h, l, c, v, s, MK[code])
        lo, hi = labels(h, l, c)
        F[code] = X
        ok = np.isfinite(lo) & np.isfinite(X[:, NAMES.index("s")]) & np.isfinite(X[:, NAMES.index("d224")])
        idx = np.flatnonzero(ok)
        for part, cond, keep in ((0, dates[idx] <= "2018-12-31", 0.25), (1, (dates[idx] >= "2019") & (dates[idx] <= "2021-12-31"), 0.25), (2, dates[idx] >= "2022", 0.25)):
            sel = idx[cond]
            sel = sel[rng.random(len(sel)) < keep]
            (trX, vaX, teX)[part].append(X[sel]); (trL, vaL, teL)[part].append(lo[sel]); (trH, vaH, teH)[part].append(hi[sel])
        if k % 500 == 0:
            print(f"특징 {k}/{len(codes)} · {time.time() - t0:.0f}초", flush=True)
    cat = lambda a: np.concatenate(a)  # noqa: E731
    trX, trL, trH, vaX, vaL, vaH, teX, teL, teH = map(cat, (trX, trL, trH, vaX, vaL, vaH, teX, teL, teH))
    print(f"표본 학습 {len(trX):,} · 고르기 {len(vaX):,} · 확인 {len(teX):,} · 저점 비율 {trL.mean():.2f} · 고점 비율 {trH.mean():.2f}", flush=True)
    mL = HGB(max_iter=300, learning_rate=0.06, max_depth=6, random_state=0).fit(trX, trL)
    mH = HGB(max_iter=300, learning_rate=0.06, max_depth=6, random_state=0).fit(trX, trH)
    auc = {"low": {"2019~21": roc_auc_score(vaL, mL.predict_proba(vaX)[:, 1]), "2022~": roc_auc_score(teL, mL.predict_proba(teX)[:, 1])},
           "high": {"2019~21": roc_auc_score(vaH, mH.predict_proba(vaX)[:, 1]), "2022~": roc_auc_score(teH, mH.predict_proba(teX)[:, 1])}}
    print("AUC", json.dumps(auc), flush=True)
    pickle.dump({"low": mL, "high": mH, "names": NAMES, "train_end": "2018-12-31"}, open(os.path.join("data", "research", "jb_extreme_model.pkl"), "wb"))
    P = {}
    for code in codes:
        X = F[code]
        okr = np.isfinite(X[:, NAMES.index("s")])
        pl, ph = np.full(len(X), np.nan), np.full(len(X), np.nan)
        if okr.any():
            pl[okr] = mL.predict_proba(X[okr])[:, 1]; ph[okr] = mH.predict_proba(X[okr])[:, 1]
        P[code] = (pl.astype(np.float32), ph.astype(np.float32))
    pickle.dump(P, open(os.path.join("data", "research", "jb_extreme_p.pkl"), "wb"))
    qL = {q: float(np.nanquantile(mL.predict_proba(vaX)[:, 1], q)) for q in (0.5, 0.7, 0.8, 0.9)}
    qH = {q: float(np.nanquantile(mH.predict_proba(vaX)[:, 1], q)) for q in (0.7, 0.8, 0.9, 0.95)}
    print("저점 확률 분위", qL, "고점 확률 분위", qH, f"· {time.time() - t0:.0f}초", flush=True)

    def run(group, rule):
        rows = []
        for code in codes:
            if G.get(code) != group:
                continue
            t = D[code]
            s = np.asarray(t[6], float); ok = np.asarray(t[7], bool) & np.isfinite(s)
            o, h, l, c = (np.asarray(t[x], float) for x in (1, 2, 3, 4))
            tr, _, aux = jb_rules.simulate(o, h, l, c, s, group, ok=ok, v=t[5], mkt_dd=MK[code], rule=rule, pl=P[code][0], ph=P[code][1])
            for i, b, j, px, r, w in tr:
                if j is None:
                    continue
                e = c[b] if (rule.get("entry_wait") or rule.get("entry_close")) else o[b]
                rows.append((t[0][b], float(r), e / jb_rules.near_low(l, i)[1] - 1, 1 - px / jb_rules.near_high(h, j)[1], j - b))
        return rows

    def st(rows, f):
        r = [x for x in rows if f(x[0])]
        if not r:
            return {"n": 0}
        a = np.array([x[1] for x in r])
        return {"n": len(r), "mean": float(a.mean()), "med": float(np.median(a)), "win": float((a > 0).mean()), "loss": float(a[a < 0].mean()) if (a < 0).any() else 0.0,
                "from_low": float(np.mean([x[2] for x in r])), "from_high": float(np.mean([x[3] for x in r])), "days": float(np.mean([x[4] for x in r]))}

    V, T = (lambda d: "2019" <= d <= "2021-12-31"), (lambda d: d >= "2022")
    fmt = lambda s: (f"{s['n']:4d}건 평균 {s['mean'] * 100:+6.2f}% 승률 {s['win'] * 100:3.0f}% 손실 {s['loss'] * 100:+6.1f}% · 저점보다 +{s['from_low'] * 100:4.1f}% · 고점보다 −{s['from_high'] * 100:4.1f}% · {s['days']:4.0f}일"  # noqa: E731
                     if s["n"] else "없음")
    out = {"auc": auc, "qL": qL, "qH": qH, "groups": {}}
    for g in ("대형주", "중형주", "소형주", "잡주"):
        base = jb_rules.rule_for(g)
        cand = {"지금": base}
        for W in (3, 5, 10):
            for q in (0.7, 0.8, 0.9):
                cand[f"매수: {W}봉 안 저점확률 상위{(1 - q) * 100:.0f}%"] = {**base, "entry_wait": W, "entry_thr": qL[q]}
        for q in (0.8, 0.9, 0.95):
            for gn in (0.05, 0.10, 0.20):
                cand[f"매도: 수익 {gn * 100:.0f}%↑ + 고점확률 상위{(1 - q) * 100:.0f}%"] = {**base, "exit_thr": qH[q], "exit_gain": gn}
        res = {}
        print(f"\n== {g}", flush=True)
        for name, rule in cand.items():
            rows = run(g, rule)
            res[name] = {"val": st(rows, V), "test": st(rows, T)}
            print(f"  {name:34s} | 19~21 {fmt(res[name]['val'])} | 22~ {fmt(res[name]['test'])}", flush=True)
        b = res["지금"]["val"]
        good = lambda k: res[k]["val"]["n"] >= 0.6 * b["n"] and res[k]["val"]["win"] >= b["win"] - 0.02  # noqa: E731
        be = max([k for k in res if k.startswith("매수") and good(k)] + ["지금"], key=lambda k: res[k]["val"]["mean"])
        bx = max([k for k in res if k.startswith("매도") and good(k)] + ["지금"], key=lambda k: res[k]["val"]["mean"])
        both = {**cand[be], **{k: v for k, v in cand[bx].items() if k in ("exit_thr", "exit_gain")}}
        rows = run(g, both)
        res["고른 매수+매도"] = {"val": st(rows, V), "test": st(rows, T), "entry": be, "exit": bx}
        print(f"  ▶ 고른 것(2019~21 기준): {be} / {bx}\n    19~21 {fmt(res['고른 매수+매도']['val'])}\n    22~   {fmt(res['고른 매수+매도']['test'])}  ← 확인 기간\n    지금 22~ {fmt(res['지금']['test'])}", flush=True)
        out["groups"][g] = res
    json.dump(out, open(os.path.join("data", "research", "jb_extreme.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"끝 {time.time() - t0:.0f}초")
