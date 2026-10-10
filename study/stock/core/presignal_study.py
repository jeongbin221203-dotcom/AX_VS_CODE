"""단테 기법 신호가 나기 '한 주 전'에 살 수 있나 — 신호 5일 전 자리(셋업)를 미리 정의해 ① 그 자리에서 실제로 5일 안에 신호가 났는지(정확도), ② 그 자리에서 샀을 때 20일 수익이
신호일에 산 것보다 나았는지를 본다.

표본 = 거래 가능 종목의 5일 간격 날 + 모든 단테 신호의 5일 전 날. 특징은 그날 종가까지의 값. 진입 다음날 시가, 20거래일 보유(비용 0.3%).
셋업(기법별, 미리 정함):
  매집봉 돌파·역매공파 → 매집봉 고가 바로 아래(−7~0%, 60일 안 매집봉) / 224 돌파 → 224일선 −7~0% 아래 + 20일선 위 / 밥그릇 돌파 → 224일선 −10~0% 아래 + 224 아래 60일↑ + 60일 저점 +10%↑
  공구리 → 직전 언덕(5~65일 전 고가) −7~0% 아래 / 256 완성 → 5일선이 20일선 −3~0% 아래 + 20일선 기울기 상승 / 이평 때리기(112) → 112일선 −7~0% 아래 / 세력선 → 7·15·33일선 ±2% 안
모델: 단테 신호(11종 중 하나)가 5일 안에 날지 예측(~2021 학습, 2022~ 시험) → 점수 상위 구간의 실제 발생률과 20일 수익.
"""
import json
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

import config
from core import ai, db, pattern_study as pt, precursor_study as pre, strategy_study as st
from core import backtest as bt

PRESIG_PATH = config.DATA_DIR / "presignal.json"
STEP = 5
AHEAD = 5
WAIT_MAX = 10
DANTE = list(range(ai.N_DANTE))
FEATS = ["dist5", "dist20", "dist60", "dist112", "dist224", "ma_below_n", "ma_conv", "slope224", "slope60", "slope20", "near224", "above224_days", "below224_days", "ret5", "ret20", "ret60",
         "nh250", "low60", "range20", "atrp", "vr5", "vr20_60", "vdry10", "spike20", "volratio", "obv20", "updays10", "upstreak", "hh20_gap", "n_j250", "days_since", "hist_rate", "price_level",
         "liq", "hist", "mkt_r20", "mkt_r60", "mkt_dd250", "acc_near_high", "acc_20", "acc_60n", "acc_since", "acc_broken_20", "hill_gap", "ma5_20", "reversed", "aligned", "box_top", "higher_lows"]
LABEL = {**pre.LABEL, **pt.LABEL, "hill_gap": "직전 언덕(5~65일 전 고가) 대비", "ma5_20": "5일선 ÷ 20일선 −1", "reversed": "역배열", "aligned": "정배열", "dist112": "112일선 대비"}


def _setups(D):
    """(기법 이름, 셋업 이름, 불리언)"""
    acc = (D["acc_near_high"] == 1)
    return [
        ("매집봉 돌파", "매집봉 고가 바로 아래 (−7~0%, 60일 안 매집봉)", acc),
        ("매집봉 돌파", "매집봉 고가 바로 아래 + 거래량 마름 (10일 최소 0.5배↓)", acc & (D["vdry10"] < 0.5)),
        ("역매공파", "역배열 + 매집봉 고가 바로 아래", acc & (D["reversed"] == 1)),
        ("224 돌파", "224일선 −7~0% 아래 + 20일선 위", (D["dist224"] >= -0.07) & (D["dist224"] < 0) & (D["dist20"] > 0)),
        ("224 돌파", "224일선 −7~0% 아래 + 224일선 기울기 상승", (D["dist224"] >= -0.07) & (D["dist224"] < 0) & (D["slope224"] > 0)),
        ("밥그릇 돌파", "224일선 −10~0% 아래 + 224 아래 60일↑ + 60일 저점 +10%↑", (D["dist224"] >= -0.10) & (D["dist224"] < 0) & (D["below224_days"] >= 60) & (D["low60"] >= 0.10)),
        ("공구리(언덕 돌파)", "직전 언덕 −7~0% 아래", (D["hill_gap"] >= -0.07) & (D["hill_gap"] < 0)),
        ("공구리(언덕 돌파)", "직전 언덕 −7~0% 아래 + 20일선 위 + 거래량 늘어나는 중", (D["hill_gap"] >= -0.07) & (D["hill_gap"] < 0) & (D["dist20"] > 0) & (D["vr5"] >= 1.2)),
        ("256 완성(단기)", "5일선이 20일선 −3~0% 아래 + 20일선 기울기 상승", (D["ma5_20"] >= -0.03) & (D["ma5_20"] < 0) & (D["slope20"] > 0)),
        ("256 완성(단기)", "5일선이 20일선 −3~0% 아래 + 60일선 위", (D["ma5_20"] >= -0.03) & (D["ma5_20"] < 0) & (D["dist60"] > 0)),
        ("이평 때리기(112)", "112일선 −7~0% 아래", (D["dist112"] >= -0.07) & (D["dist112"] < 0)),
        ("이평 때리기(112)", "112일선 −7~0% 아래 + 20일선 위", (D["dist112"] >= -0.07) & (D["dist112"] < 0) & (D["dist20"] > 0)),
        ("256 자리(단기)", "20일선 −3~+3% 안 + 60일선 위 (눌림 자리)", (D["dist20"].abs() < 0.03) & (D["dist60"] > 0)),
        ("3번 자리(눌림)", "20일선 위 + 20일 고점 −10~−3% 눌림", (D["dist20"] > 0) & (D["hh20_gap"] <= -0.03) & (D["hh20_gap"] >= -0.10)),
        ("7일선 지지", "7·15·33일선 중 하나 ±2% 안 + 정배열", (D["ma_conv"] < 0.15) & (D["aligned"] == 1) & (D["dist5"].abs() < 0.02)),
    ]


def _work(code):
    try:
        df = ai.service.load_prices(code)
        n = len(df)
        if n < 400:
            return None
        F, jump, c, v, vr = pre._features(df)
        P = pt._patterns(df, F)
        S = ai.signal_features(df)
        o, h, l = (df[k].to_numpy(float) for k in ("open", "high", "low"))
        s = pd.Series
        ma5, ma20, ma112 = (s(c).rolling(p, min_periods=p).mean().to_numpy() for p in (5, 20, 112))
        with np.errstate(divide="ignore", invalid="ignore"):
            hill = s(h).rolling(60, min_periods=30).max().shift(5).to_numpy()
            extra = {"hill_gap": c / hill - 1, "ma5_20": ma5 / ma20 - 1, "dist112": c / ma112 - 1,
                     "reversed": ((ma5 < ma20) & (ma20 < s(c).rolling(60).mean().to_numpy()) & (s(c).rolling(60).mean().to_numpy() < ma112)).astype(float),
                     "aligned": ((ma5 > ma20) & (ma20 > s(c).rolling(60).mean().to_numpy()) & (s(c).rolling(60).mean().to_numpy() > ma112)).astype(float)}
        today = (S[:, :len(ai.SIGNAL_LABELS)] == 0)                                   # (n, 24) 그날 신호
        dante_today = today[:, DANTE].any(axis=1)
        fwd = {}
        for j in DANTE:
            col = today[:, j].astype(float)
            fwd[f"f{j:02d}"] = s(np.r_[col[1:], 0.0][::-1]).rolling(AHEAD, min_periods=1).max().to_numpy()[::-1].copy()
        fwd_any = s(np.r_[dante_today[1:].astype(float), 0.0][::-1]).rolling(AHEAD, min_periods=1).max().to_numpy()[::-1].copy()
        for k in list(fwd) + []:
            fwd[k][n - AHEAD:] = np.nan
        fwd_any[n - AHEAD:] = np.nan
        t_all = np.arange(n)
        ok = (F["liq"] >= pre.LIQ_MIN) & (t_all >= 250) & (t_all + 22 < n)
        pre_days = np.zeros(n, bool)
        for j in DANTE:
            idx = np.flatnonzero(today[:, j]) - AHEAD
            pre_days[idx[idx >= 0]] = True
        sel = ok & ((t_all % STEP == 0) | pre_days | dante_today)
        tt = t_all[sel]
        if len(tt) == 0:
            return None
        e = tt + 1
        entry = o[e]
        R = {k: F[k][tt].astype(np.float32) for k in FEATS if k in F}
        for k in ("acc_near_high", "acc_20", "acc_60n", "acc_since", "acc_broken_20", "box_top", "higher_lows"):
            R[k] = P[k][tt].astype(np.float32)
        for k, arr in extra.items():
            R[k] = arr[tt].astype(np.float32)
        for k, arr in fwd.items():
            R[k] = arr[tt].astype(np.float32)
        R["fwd_any"] = fwd_any[tt].astype(np.float32)
        R["pre_day"] = pre_days[tt].astype(np.float32)
        R["dante_today"] = dante_today[tt].astype(np.float32)
        R["sampled"] = (tt % STEP == 0).astype(np.float32)
        for j in DANTE:
            R[f"t{j:02d}"] = today[tt, j].astype(np.float32)
        R["r20"] = (c[e + 20] / entry - 1).astype(np.float32)
        R["r5"] = (c[e + 5] / entry - 1).astype(np.float32)
        Wh = sliding_window_view(h, 21)[e]
        Wl = sliding_window_view(l, 21)[e]
        R["touch20"] = (Wh.max(axis=1) / entry - 1 >= 0.20).astype(np.float32)
        R["mae20"] = (Wl.min(axis=1) / entry - 1).astype(np.float32)
        # 전주 매수 → 신호일 매도용: 10일 안 첫 신호가 난 날(t 기준 며칠 뒤), 진입(t+1 시가) 뒤 11일의 종가·시가·저가(진입가 대비)
        for j in DANTE:
            Wj = sliding_window_view(today[:, j].astype(np.int8), WAIT_MAX)[tt + 1]            # t+1..t+10
            R[f"sd{j:02d}"] = np.where(Wj.any(axis=1), Wj.argmax(axis=1) + 1, np.nan).astype(np.float32)
        Wa = sliding_window_view(dante_today.astype(np.int8), WAIT_MAX)[tt + 1]
        R["sd_any"] = np.where(Wa.any(axis=1), Wa.argmax(axis=1) + 1, np.nan).astype(np.float32)
        R["pc"] = (sliding_window_view(c, WAIT_MAX + 1)[e] / entry[:, None] - 1).astype(np.float32)        # 진입일(0)~10일째 종가
        R["po"] = (sliding_window_view(o, WAIT_MAX + 2)[e] / entry[:, None] - 1).astype(np.float32)        # 진입일(0)~11일째 시가
        R["pl"] = (sliding_window_view(l, WAIT_MAX + 1)[e] / entry[:, None] - 1).astype(np.float32)
        R["gap1"] = (entry / c[tt] - 1).astype(np.float32)
        R["date"] = df.index.strftime("%Y-%m-%d").to_numpy()[tt]
        R["code"] = np.full(len(tt), code)
        dates = R["date"]
        if ai.MKT_DF is not None:
            m = ai.MKT_DF.reindex(dates)
            for k in ("mkt_r20", "mkt_r60", "mkt_dd250"):
                R[k] = m[k].to_numpy(np.float32)
        else:
            for k in ("mkt_r20", "mkt_r60", "mkt_dd250"):
                R[k] = np.full(len(tt), np.nan, np.float32)
        t = n - 1
        lat = {"code": code, "date": df.index[t].strftime("%Y-%m-%d"), "close": float(c[t]), "ok": bool(F["liq"][t] >= pre.LIQ_MIN and v[t] > 0)}
        lat.update({k: float(F[k][t]) for k in FEATS if k in F})
        lat.update({k: float(P[k][t]) for k in ("acc_near_high", "acc_20", "acc_60n", "acc_since", "acc_broken_20", "box_top", "higher_lows")})
        lat.update({k: float(arr[t]) for k, arr in extra.items()})
        for k in ("mkt_r20", "mkt_r60", "mkt_dd250"):
            lat.setdefault(k, float(ai.MKT_DF.loc[lat["date"], k]) if ai.MKT_DF is not None and lat["date"] in ai.MKT_DF.index else np.nan)
        lat["dante_today"] = bool(dante_today[t])
        return {"rows": R, "latest": lat}
    except Exception:
        return None


def _stats(r, extra=None):
    r = np.asarray(r, float)
    ok = ~np.isnan(r)
    rr = r[ok]
    if len(rr) < 30:
        return None
    s = {"n": int(len(rr)), "mean": float(rr.mean()), "p_up": float((rr > 0).mean()), "p_big_up": float((rr >= 0.10).mean()), "p_dn10": float((rr <= -0.10).mean()), "q05": float(np.quantile(rr, 0.05))}
    if extra:
        for k, v in extra.items():
            vv = np.asarray(v, float)[ok]
            s[k] = float(np.nanmean(vv)) if np.isfinite(vv).any() else None
    return s


def _sell_at_signal(E, m, sd, tr, te, wait=5, stop=None):
    """셋업 자리(m)에서 다음날 시가 매수 → wait 일 안에 신호(sd: t 기준 며칠 뒤)가 나면 그날 종가 매도(또는 다음날 시가), 없으면 wait 일째 종가 매도. stop: 저가가 진입가×(1−stop) 이면 손절가 매도."""
    m = np.asarray(m, bool)
    if m.sum() < 100:
        return None
    d = sd[m].astype(float)
    pc, po, pl = E["pc"][m].astype(float), E["po"][m].astype(float), E["pl"][m].astype(float)
    came = ~np.isnan(d) & (d <= wait)
    dd = np.where(came, d, wait).astype(int)                       # 매도일(t 기준) → 진입 뒤 dd−1 일째
    rows = np.arange(len(d))
    r_close = pc[rows, dd - 1]                                      # 그날 종가
    r_open = po[rows, dd]                                           # 다음날 시가
    if stop:
        # 매도일 전까지 저가가 손절선에 닿았으면 손절(그날 시가가 더 낮으면 시가)
        hit = np.zeros(len(d), bool)
        r_stop = np.full(len(d), np.nan)
        for k in range(wait):
            at = (~hit) & (k <= dd - 1) & (pl[:, k] <= -stop)
            r_stop[at] = np.minimum(-stop, po[at, k])
            hit |= at
        r_close = np.where(hit, r_stop, r_close)
        r_open = np.where(hit, r_stop, r_open)
    out = {"n": int(m.sum()), "p_signal": float(came.mean()), "wait": wait, "stop": stop}
    for lab, r in (("close", r_close), ("open", r_open)):
        rr = r - ai.COST
        out[lab] = {"mean": float(np.nanmean(rr)), "p_up": float(np.nanmean(rr > 0)), "came": float(np.nanmean(rr[came])) if came.any() else None, "not": float(np.nanmean(rr[~came])) if (~came).any() else None,
                    "tr": float(np.nanmean(rr[tr[m]])) if tr[m].sum() >= 30 else None, "te": float(np.nanmean(rr[te[m]])) if te[m].sum() >= 30 else None,
                    "p_up_tr": float(np.nanmean(rr[tr[m]] > 0)) if tr[m].sum() >= 30 else None, "p_up_te": float(np.nanmean(rr[te[m]] > 0)) if te[m].sum() >= 30 else None,
                    "q05": float(np.nanquantile(rr, 0.05))}
    out["hold20"] = {"mean": float(np.nanmean(E["r20"][m].astype(float) - ai.COST)), "te": float(np.nanmean(E["r20"][m & te].astype(float) - ai.COST)) if (m & te).sum() >= 30 else None}
    return out


def run(workers: int = 0, log=print, limit: int = 0) -> dict:
    t0 = time.time()
    with db.get_conn() as c:
        codes = [r["code"] for r in c.execute("SELECT code FROM prices GROUP BY code HAVING COUNT(*) >= 400 ORDER BY code")]
        names = {r["code"]: r["name"] for r in c.execute("SELECT code,name FROM symbols")}
    if limit:
        codes = codes[::max(len(codes) // limit, 1)][:limit]
    workers = workers or max(1, min(6, (os.cpu_count() or 2) - 2))
    if not bt.MKT_PATH.exists():
        bt.build_market_index(codes, workers)
    parts, latest = [], []
    with ProcessPoolExecutor(max_workers=workers, initializer=ai._init_worker, initargs=(bt.MKT_PATH,)) as ex:
        for i, out in enumerate(ex.map(_work, codes, chunksize=4), 1):
            if out:
                parts.append(out["rows"])
                latest.append(out["latest"])
            if i % 300 == 0:
                log(f"[종목 {i}/{len(codes)}] · {time.time() - t0:.0f}초")
    E = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    del parts
    n = len(E["date"])
    log(f"행 {n:,} · {time.time() - t0:.0f}초")
    D = pd.DataFrame({k: E[k] for k in FEATS if k in E})
    tr_, va_, te = ai._split_masks(E["date"])
    tr = tr_ | va_
    r = E["r20"].astype(float) - ai.COST
    sampled = E["sampled"] == 1
    rep = {"meta": {"generated": datetime.now().isoformat(timespec="seconds"), "rows": int(n), "sampled": int(sampled.sum()), "ahead": AHEAD, "split": {"train_end": ai.VALID_END}, "cost": ai.COST}}
    # 1. 기법별: 신호일 매수 vs 5일 전 매수(실제 5일 전 날들) vs 셋업 매수
    techs = []
    for j in DANTE:
        lab = ai.SIGNAL_LABELS[j]
        at_sig = E[f"t{j:02d}"] == 1
        pre5 = (E["pre_day"] == 1) & (E[f"f{j:02d}"] == 1)                              # 실제로 5일 안에 이 신호가 난 날(사후)
        base = sampled & ~np.isnan(E[f"f{j:02d}"])
        rate = float(np.nanmean(E[f"f{j:02d}"][base]))
        row = {"label": lab, "n_sig": int(at_sig.sum()), "rate5": rate,
               "at_signal": {"tr": _stats(r[at_sig & tr]), "te": _stats(r[at_sig & te])},
               "pre5_actual": {"tr": _stats(r[pre5 & tr]), "te": _stats(r[pre5 & te])},
               "setups": []}
        for tech, name, m in _setups(D):
            if tech != lab:
                continue
            m = np.asarray(m, bool) & base
            if m.sum() < 300:
                continue
            came = m & (E[f"f{j:02d}"] == 1)
            srow = {"name": name, "n": int(m.sum()), "share": float(m.sum() / base.sum()), "precision": float(np.nanmean(E[f"f{j:02d}"][m])), "lift": float(np.nanmean(E[f"f{j:02d}"][m]) / rate) if rate else None,
                    "recall": float(np.asarray(m, bool)[pre5 & sampled].mean()) if (pre5 & sampled).sum() else None,
                    "tr": _stats(r[m & tr], {"touch20": E["touch20"][m & tr], "mae": E["mae20"][m & tr]}), "te": _stats(r[m & te], {"touch20": E["touch20"][m & te], "mae": E["mae20"][m & te]}),
                    "came": _stats(r[came]), "not_came": _stats(r[m & (E[f"f{j:02d}"] == 0)])}
            srow["robust"] = bool(srow["tr"] and srow["te"] and srow["tr"]["mean"] > 0 and srow["te"]["mean"] > 0 and srow["tr"]["p_up"] > 0.45 and srow["te"]["p_up"] > 0.45)
            row["setups"].append(srow)
        techs.append(row)
    rep["techs"] = techs
    # 전주 매수 → 신호일 매도
    sell = []
    for j in DANTE:
        lab = ai.SIGNAL_LABELS[j]
        base = sampled & ~np.isnan(E[f"f{j:02d}"])
        for tech, name, m in _setups(D):
            if tech != lab:
                continue
            m = np.asarray(m, bool) & base
            if m.sum() < 300:
                continue
            row = {"tech": lab, "setup": name, "variants": {}}
            for wait in (5, 10):
                for stop in (None, 0.05):
                    v = _sell_at_signal(E, m, E[f"sd{j:02d}"], tr, te, wait=wait, stop=stop)
                    if v:
                        row["variants"][f"w{wait}_s{int((stop or 0) * 100)}"] = v
            if row["variants"]:
                best = max(row["variants"].items(), key=lambda kv: kv[1]["close"]["mean"])
                row["best"] = {"key": best[0], **best[1]}
                row["robust"] = bool(best[1]["close"]["tr"] is not None and best[1]["close"]["te"] is not None and best[1]["close"]["tr"] > 0 and best[1]["close"]["te"] > 0)
                sell.append(row)
    rep["sell"] = sell
    rep["base"] = {"tr": _stats(r[sampled & tr]), "te": _stats(r[sampled & te]), "rate_any": float(np.nanmean(E["fwd_any"][sampled & ~np.isnan(E["fwd_any"])]))}
    # 2. 모델: 단테 신호 5일 안 발생 예측(5일 간격 표본으로 학습·시험)
    from sklearn.metrics import roc_auc_score
    X = np.column_stack([E[k] for k in FEATS if k in E]).astype(np.float32)
    y = E["fwd_any"].astype(float)
    valid = sampled & ~np.isnan(y)
    idx = st._sub(tr & valid, 600000, seed=1)
    mdl = st._fit_fast(X[idx], y[idx].astype(int), n_iter=200, seed=1)
    mte = te & valid
    p = st._predict(mdl, X[mte])
    auc = float(roc_auc_score(y[mte], p))
    q = pd.qcut(pd.Series(p).rank(method="first"), 10, labels=False).to_numpy()
    dec = [{"bin": int(k + 1), "p": float(p[q == k].mean()), "actual": float(y[mte][q == k].mean()), "r20": float(np.nanmean(r[mte][q == k])), "p_up": float(np.nanmean(r[mte][q == k] > 0))} for k in range(10)]
    # 점수 상위 10%에서 실제로 신호가 났을 때 / 안 났을 때
    top = q == 9
    rt = r[mte][top]
    yt = y[mte][top]
    rep["model"] = {"auc": auc, "base": float(y[mte].mean()), "deciles": dec, "top_came": _stats(rt[yt == 1]), "top_not": _stats(rt[yt == 0]),
                    "top_tr_check": None}
    # 학습 기간 안에서도 상위 10% 수익이 좋은지(과적합 참고)
    mtr = tr & valid
    ptr = st._predict(mdl, X[mtr])
    rep["model"]["top_tr_check"] = _stats(r[mtr][ptr >= np.quantile(ptr, 0.9)])
    top_mask = np.zeros(len(r), bool)
    top_mask[np.flatnonzero(mte)[top]] = True
    top_tr_mask = np.zeros(len(r), bool)
    top_tr_mask[np.flatnonzero(mtr)[ptr >= np.quantile(ptr, 0.9)]] = True
    allm = top_mask | top_tr_mask
    rep["sell_model"] = {f"w{w}_s{int((st_ or 0) * 100)}": _sell_at_signal(E, allm, E["sd_any"], tr, te, wait=w, stop=st_) for w in (5, 10) for st_ in (None, 0.05)}
    yy = y[mte].astype(int)
    rng = np.random.default_rng(0)
    imp = []
    Xte = X[mte]
    for jj, nm in enumerate([k for k in FEATS if k in E]):
        Xp = Xte.copy()
        Xp[:, jj] = rng.permutation(Xp[:, jj])
        imp.append((nm, float(auc - roc_auc_score(yy, st._predict(mdl, Xp)))))
    imp.sort(key=lambda x: -x[1])
    rep["model"]["importance"] = [{"name": nm, "label": LABEL.get(nm, nm), "gain": g} for nm, g in imp[:12]]
    log(f"[모델] 단테 신호 5일 안 AUC {auc:.3f} · 상위 10% 발생 {dec[-1]['actual'] * 100:.0f}% · 상위 10% 20일 {dec[-1]['r20'] * 100:+.2f}% · {time.time() - t0:.0f}초")
    # 3. 오늘: 신호 5일 안 발생 점수 상위 + 해당 셋업
    L = [x for x in latest if x["ok"] and np.isfinite(x.get("dist224", np.nan)) and not x["dante_today"]]
    if L:
        XL = np.array([[x.get(k, np.nan) for k in FEATS if k in E] for x in L], np.float32)
        PL = st._predict(mdl, XL)
        DL = pd.DataFrame({k: [x.get(k, np.nan) for x in L] for k in FEATS})
        setups_L = [(tech, name, np.asarray(m, bool)) for tech, name, m in _setups(DL)]
        good_setups = {(t["label"], s_["name"]) for t in techs for s_ in t["setups"] if s_["robust"]}
        order = np.argsort(-PL)[:60]
        date = max(x["date"] for x in L)
        rows = []
        for i in order:
            if L[i]["date"] != date:
                continue
            su = [f"{tech}: {name}" + (" ✔" if (tech, name) in good_setups else "") for tech, name, m in setups_L if m[i]]
            rows.append({"code": L[i]["code"], "name": names.get(L[i]["code"], L[i]["code"]), "close": L[i]["close"], "prob": float(PL[i]), "setups": su[:4], "dist224": L[i]["dist224"], "dist20": L[i]["dist20"],
                         "acc_near_high": L[i]["acc_near_high"], "hill_gap": L[i]["hill_gap"], "ret20": L[i]["ret20"], "nh250": L[i]["nh250"], "atrp": L[i]["atrp"]})
        rep["today"] = {"date": date, "n": len(L), "rows": rows}
    else:
        rep["today"] = {"date": None, "n": 0, "rows": []}
    rep["text"] = narrate(rep)
    rep["meta"]["elapsed_sec"] = round(time.time() - t0)
    PRESIG_PATH.write_text(json.dumps(rep, ensure_ascii=False, default=st._js), encoding="utf-8")
    return rep


def narrate(rep):
    out = []
    b = rep["base"]
    out.append(f"아무 날(5일 간격 표본)에 5일 안 단테 신호(11종 중 하나)가 날 확률 {b['rate_any'] * 100:.0f}%. 아무 날 20일 평균 {b['te']['mean'] * 100:+.2f}%·상승 {b['te']['p_up'] * 100:.0f}%(시험 기간).")
    for t in rep["techs"]:
        a, p5 = t["at_signal"]["te"], t["pre5_actual"]["te"]
        if not (a and p5):
            continue
        best = max([s_ for s_ in t["setups"] if s_["te"]], key=lambda s_: s_["te"]["mean"], default=None)
        line = f"{t['label']}: 신호일 다음날 매수 {a['mean'] * 100:+.2f}%·상승 {a['p_up'] * 100:.0f}% vs (사후) 신호 5일 전 매수 {p5['mean'] * 100:+.2f}%·{p5['p_up'] * 100:.0f}%"
        if best:
            line += f"; 미리 정한 셋업 '{best['name']}'은 5일 안 신호 발생 {best['precision'] * 100:.0f}%(기준 {t['rate5'] * 100:.1f}%, {best['lift']:.0f}배), 그 자리 매수 {best['te']['mean'] * 100:+.2f}%·상승 {best['te']['p_up'] * 100:.0f}%" + (" ✔" if best["robust"] else "")
        out.append(line + ".")
    sells = sorted([x for x in rep.get("sell", []) if x.get("best")], key=lambda x: -x["best"]["close"]["mean"])
    if sells:
        bst = sells[0]
        bb = bst["best"]
        out.append(f"전주 매수 → 신호일 종가 매도(가장 나은 셋업): {bst['tech']} '{bst['setup']}' — {bb['wait']}일 대기{'·손절 −5%' if bb['stop'] else ''}: 신호 발생 {bb['p_signal'] * 100:.0f}%, 평균 {bb['close']['mean'] * 100:+.2f}%·상승 {bb['close']['p_up'] * 100:.0f}%"
                   f"(학습 {bb['close']['tr'] * 100:+.2f}% · 시험 {bb['close']['te'] * 100:+.2f}%), 신호 왔을 때 {bb['close']['came'] * 100:+.2f}% / 안 왔을 때 {bb['close']['not'] * 100:+.2f}%, 20일 보유였다면 {bb['hold20']['mean'] * 100:+.2f}%. 두 시기 모두 플러스인 셋업 {sum(1 for x in sells if x['robust'])}개 / {len(sells)}개.")
    sm = rep.get("sell_model", {})
    if sm and sm.get("w5_s0"):
        v = sm["w5_s0"]
        out.append(f"모델 상위 10% 자리에서 사서 아무 단테 신호에 종가 매도(5일 대기): 신호 발생 {v['p_signal'] * 100:.0f}%, 평균 {v['close']['mean'] * 100:+.2f}%·상승 {v['close']['p_up'] * 100:.0f}%(시험 {v['close']['te'] * 100:+.2f}%), 신호 왔을 때 {v['close']['came'] * 100:+.2f}% / 안 왔을 때 {v['close']['not'] * 100:+.2f}%.")
    m = rep["model"]
    out.append(f"'5일 안 단테 신호' 예측 모델: AUC {m['auc']:.3f}, 점수 상위 10%의 실제 발생 {m['deciles'][-1]['actual'] * 100:.0f}%(기준 {m['base'] * 100:.0f}%), 그 자리에서 산 20일 평균 {m['deciles'][-1]['r20'] * 100:+.2f}%·상승 {m['deciles'][-1]['p_up'] * 100:.0f}%"
               + (f" — 신호가 실제로 온 경우 {m['top_came']['mean'] * 100:+.2f}%, 안 온 경우 {m['top_not']['mean'] * 100:+.2f}%" if m["top_came"] and m["top_not"] else "") + ".")
    return out


if __name__ == "__main__":
    run()
