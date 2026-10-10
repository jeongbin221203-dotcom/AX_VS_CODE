"""+20% 급등 전 '패턴' 공통점 — 매집봉(단테), 거래량 패턴, 가격 패턴, 캔들·기법 신호가 급등 전날에 얼마나 흔했고, 그 패턴이 있으면 20일 안 급등 확률이 얼마나 오르나.

precursor_study 와 같은 틀(급등 전날 vs 11일 간격 아무 날, 이후 20일 안 급등 확률, ~2021/2022~ 두 시기 확인)에 패턴 특징을 더한다.
기법 신호(매집봉 돌파·256·공구리·캔들 등)는 ai.signal_features(techniques 전부)를 그대로 써서 느리다(전 종목 약 20분).
"""
import json
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime

import numpy as np
import pandas as pd

import config
from core import ai, db, precursor_study as pre, strategy_study as st, techniques
from core import backtest as bt

PATTERN_PATH = config.DATA_DIR / "pattern.json"
BASE_STEP = pre.BASE_STEP
PAT = ["acc_today", "acc_5", "acc_20", "acc_60n", "acc_kind_big", "acc_kind_wick", "acc_kind_pierce", "acc_since", "acc_near_high", "acc_broken_20", "acc_pullback",
       "vol_surge_dry", "vol_rising3", "vol_doji", "vol_down", "vol_min60", "obv_high60", "val_spike", "vol_shrink", "vol_surge_flat",
       "box_top", "higher_lows", "double_bottom", "n_shape", "golden20_60", "golden5_20", "conv_expand", "gap_hold", "big_then_rest", "force_support", "trend_break", "near_high_vol", "three_up_small",
       "ma_conv", "range20"]
LABEL = {"acc_today": "매집봉 (당일)", "acc_5": "매집봉 5일 안", "acc_20": "매집봉 20일 안", "acc_60n": "60일 안 매집봉 수", "acc_kind_big": "매집봉 종류: 장대양봉형(20일 안)", "acc_kind_wick": "매집봉 종류: 윗꼬리형(20일 안)",
         "acc_kind_pierce": "매집봉 종류: 이평 찌름(20일 안)", "acc_since": "마지막 매집봉 뒤 경과", "acc_near_high": "매집봉 고가 바로 아래(−7~0%)", "acc_broken_20": "20일 안 매집봉 고가 돌파", "acc_pullback": "매집봉 뒤 눌림(고가 −7~−20%)",
         "vol_surge_dry": "거래량 급증(3배) 뒤 마름(20일 안·10일 최소 0.5배↓)", "vol_rising3": "거래량 3일 연속 증가 + 상승", "vol_doji": "대량 거래 도지(3배·몸통 1%↓)", "vol_down": "대량 거래 음봉(3배·−3%↓)",
         "vol_min60": "거래량 60일 최소", "obv_high60": "OBV 60일 신고 (주가는 고점 아님)", "val_spike": "거래대금 5일 ÷ 20일 2배↑", "vol_shrink": "거래량 20일 ÷ 120일 0.5배↓ (장기 감소)", "vol_surge_flat": "대량 거래 뒤 횡보(20일 안 3배 · 10일 폭 8%↓)",
         "box_top": "박스 상단(20일 고점 −3% 안 · 폭 15%↓)", "higher_lows": "저점 높이기(10일 저점 3번 상승)", "double_bottom": "쌍바닥(60일 저점 2번 · 지금은 위)", "n_shape": "N자(20일 +20%↑ 뒤 −5~−15% 눌림·5일선 위)",
         "golden20_60": "20/60 골든크로스 10일 안", "golden5_20": "5/20 골든크로스 5일 안", "conv_expand": "이평 수렴(10일 전 폭 8%↓) 뒤 확산", "gap_hold": "갭상승 3%↑ 뒤 유지(5일 안)", "big_then_rest": "장대양봉(5%↑·2배) 뒤 3일 눌림 −5~0%",
         "force_support": "세력선 7·15·33 지지(저가 터치·종가 위)", "trend_break": "하락 뒤 20일 고점 돌파(60일 −20%↓)", "near_high_vol": "52주 고점 −5% 안 + 거래량 2배↑", "three_up_small": "작은 양봉 3일 연속(각 +0~3%)",
         "ma_conv": "이평선 밀집도", "range20": "20일 가격 폭"}
SIG_WIN = 3


def _patterns(df, F):
    o, h, l, c, v = (df[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
    n = len(c)
    s = pd.Series
    acc, kind = techniques.acc_candles(df)
    acc = np.asarray(acc, bool)
    accf = acc.astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        a20 = s(v).rolling(20, min_periods=20).mean().shift().to_numpy()
        vr = v / a20
        ret1 = np.r_[np.nan, c[1:] / c[:-1] - 1]
        body = c / o - 1
        acc_high = np.where(acc, h, np.nan)
        last_acc_high = s(acc_high).ffill().to_numpy()
        since_acc = ai._since(acc, cap=250)
        kind_s = s(np.where(acc, kind, ""))
        P = {"acc_today": accf, "acc_5": s(accf).rolling(5, min_periods=1).max().to_numpy(), "acc_20": s(accf).rolling(20, min_periods=1).max().to_numpy(),
             "acc_60n": s(accf).rolling(60, min_periods=1).sum().to_numpy(), "acc_since": since_acc,
             "acc_kind_big": s((kind_s == "장대양봉형").astype(float)).rolling(20, min_periods=1).max().to_numpy(), "acc_kind_wick": s((kind_s == "윗꼬리형").astype(float)).rolling(20, min_periods=1).max().to_numpy(),
             "acc_kind_pierce": s((kind_s == "이평 찌름").astype(float)).rolling(20, min_periods=1).max().to_numpy(),
             "acc_near_high": ((since_acc <= 60) & (c / last_acc_high - 1 >= -0.07) & (c <= last_acc_high)).astype(float),
             "acc_pullback": ((since_acc <= 60) & (c / last_acc_high - 1 < -0.07) & (c / last_acc_high - 1 >= -0.20)).astype(float)}
        broke = (c > np.r_[np.nan, last_acc_high[:-1]]) & (since_acc >= 1) & (since_acc <= 60)
        P["acc_broken_20"] = s(broke.astype(float)).rolling(20, min_periods=1).max().to_numpy()
        spike3 = (vr >= 3).astype(float)
        spike3_20 = s(spike3).rolling(20, min_periods=1).max().to_numpy()
        vdry10 = s(v).rolling(10, min_periods=10).min().to_numpy() / a20
        P["vol_surge_dry"] = ((spike3_20 == 1) & (vdry10 < 0.5)).astype(float)
        up3 = (v > np.r_[np.nan, v[:-1]]) & (np.r_[np.nan, v[:-1]] > np.r_[np.nan, np.nan, v[:-2]]) & (c > np.r_[np.nan, np.nan, c[:-2]])
        P["vol_rising3"] = up3.astype(float)
        P["vol_doji"] = ((vr >= 3) & (np.abs(body) < 0.01)).astype(float)
        P["vol_down"] = ((vr >= 3) & (ret1 <= -0.03)).astype(float)
        P["vol_min60"] = (v <= s(v).rolling(60, min_periods=60).min().to_numpy()).astype(float)
        obv = np.cumsum(np.where(ret1 > 0, v, np.where(ret1 < 0, -v, 0.0)))
        obv_hi = obv >= s(obv).rolling(60, min_periods=60).max().to_numpy()
        hh60 = s(h).rolling(60, min_periods=60).max().to_numpy()
        P["obv_high60"] = (obv_hi & (c < hh60 * 0.95)).astype(float)
        val = c * v
        P["val_spike"] = (s(val).rolling(5, min_periods=5).mean().to_numpy() / s(val).rolling(20, min_periods=20).mean().to_numpy() >= 2).astype(float)
        P["vol_shrink"] = (s(v).rolling(20, min_periods=20).mean().to_numpy() / s(v).rolling(120, min_periods=120).mean().to_numpy() < 0.5).astype(float)
        range10 = (s(h).rolling(10, min_periods=10).max().to_numpy() - s(l).rolling(10, min_periods=10).min().to_numpy()) / c
        P["vol_surge_flat"] = ((spike3_20 == 1) & (range10 < 0.08)).astype(float)
        hh20, ll20 = s(h).rolling(20, min_periods=20).max().to_numpy(), s(l).rolling(20, min_periods=20).min().to_numpy()
        range20 = (hh20 - ll20) / c
        P["box_top"] = ((c / hh20 - 1 >= -0.03) & (range20 < 0.15)).astype(float)
        l10 = s(l).rolling(10, min_periods=10).min()
        P["higher_lows"] = ((l10 > l10.shift(10)) & (l10.shift(10) > l10.shift(20))).to_numpy().astype(float)
        ll60 = s(l).rolling(60, min_periods=60).min().to_numpy()
        near_low = (l <= ll60 * 1.05).astype(float)
        touches = s(near_low).rolling(60, min_periods=60).sum().to_numpy()
        P["double_bottom"] = ((touches >= 2) & (c > ll60 * 1.10) & (near_low == 0)).astype(float)
        ret20 = c / s(c).shift(20).to_numpy() - 1
        ma5 = s(c).rolling(5).mean().to_numpy()
        pull = c / hh20 - 1
        P["n_shape"] = ((s(ret20).shift(5).to_numpy() >= 0.20) & (pull <= -0.05) & (pull >= -0.15) & (c > ma5)).astype(float)
        ma = {p: s(c).rolling(p, min_periods=p).mean().to_numpy() for p in (5, 7, 15, 20, 33, 60, 112, 224)}
        g2060 = np.r_[False, (ma[20][1:] > ma[60][1:]) & (ma[20][:-1] <= ma[60][:-1])]
        g520 = np.r_[False, (ma[5][1:] > ma[20][1:]) & (ma[5][:-1] <= ma[20][:-1])]
        P["golden20_60"] = s(g2060.astype(float)).rolling(10, min_periods=1).max().to_numpy()
        P["golden5_20"] = s(g520.astype(float)).rolling(5, min_periods=1).max().to_numpy()
        conv = (np.maximum.reduce([ma[20], ma[60], ma[112], ma[224]]) - np.minimum.reduce([ma[20], ma[60], ma[112], ma[224]])) / c
        P["ma_conv"] = conv
        P["range20"] = range20
        P["conv_expand"] = ((s(conv).shift(10).to_numpy() < 0.08) & (c / ma[20] - 1 > 0.03)).astype(float)
        gap = o / np.r_[np.nan, c[:-1]] - 1
        gap_day = gap >= 0.03
        gap_low = np.where(gap_day, np.r_[np.nan, c[:-1]], np.nan)
        last_gap = s(gap_low).ffill().to_numpy()
        since_gap = ai._since(gap_day, cap=250)
        P["gap_hold"] = ((since_gap <= 5) & (c > last_gap)).astype(float)
        bigday = (body >= 0.05) & (vr >= 2)
        since_big = ai._since(bigday, cap=250)
        big_close = s(np.where(bigday, c, np.nan)).ffill().to_numpy()
        P["big_then_rest"] = ((since_big >= 1) & (since_big <= 3) & (c / big_close - 1 <= 0) & (c / big_close - 1 >= -0.05)).astype(float)
        fs = np.zeros(n, bool)
        for p in (7, 15, 33):
            fs |= (l <= ma[p]) & (c > ma[p]) & (ma[p] >= np.r_[np.nan, ma[p][:-1]])
        P["force_support"] = fs.astype(float)
        ret60 = c / s(c).shift(60).to_numpy() - 1
        prev_hh20 = np.r_[np.nan, hh20[:-1]]
        P["trend_break"] = ((s(ret60).shift(5).to_numpy() <= -0.20) & (c > prev_hh20)).astype(float)
        hh250 = s(h).rolling(250, min_periods=120).max().to_numpy()
        P["near_high_vol"] = ((c / hh250 - 1 >= -0.05) & (vr >= 2)).astype(float)
        small_up = (ret1 > 0) & (ret1 < 0.03)
        P["three_up_small"] = (small_up & np.r_[False, small_up[:-1]] & np.r_[False, False, small_up[:-2]]).astype(float)
    return P


def _work(code):
    try:
        df = ai.service.load_prices(code)
        n = len(df)
        if n < 400:
            return None
        F, jump, c, v, vr = pre._features(df)
        P = _patterns(df, F)
        S = ai.signal_features(df)                                    # (n, 26): 신호별 경과 봉 + 겹침 수
        t_all = np.arange(n)
        ok = (F["liq"] >= pre.LIQ_MIN) & (t_all >= 250) & (t_all + 21 < n)
        ev = t_all[jump & ok & (t_all >= 251)] - 1
        bs = t_all[ok & (t_all % BASE_STEP == 0)]
        dates = df.index.strftime("%Y-%m-%d").to_numpy()
        out = {"code": code}
        for name, tt in (("ev", ev), ("bs", bs)):
            if len(tt) == 0:
                out[name] = None
                continue
            R = {k: P[k][tt].astype(np.float32) for k in PAT}
            for k in ("fwd5", "fwd20", "n_j250", "days_since", "hist_rate", "atrp", "dist224", "nh250", "ret5", "ret20", "liq", "volratio", "vr5", "obv20", "vdry10"):
                R[k] = F[k][tt].astype(np.float32)
            for j, lab in enumerate(ai.SIGNAL_LABELS):
                R[f"sig{j:02d}"] = S[tt, j].astype(np.float32)
            R["sig_n10"] = S[tt, -1].astype(np.float32)
            R["date"] = dates[tt]
            R["code"] = np.full(len(tt), code)
            out[name] = R
        t = n - 1
        lat = {"code": code, "date": dates[t], "close": float(c[t]), "ok": bool(F["liq"][t] >= pre.LIQ_MIN and v[t] > 0)}
        lat.update({k: float(P[k][t]) for k in PAT})
        lat.update({k: float(F[k][t]) for k in ("n_j250", "days_since", "hist_rate", "atrp", "dist224", "nh250", "ret5", "ret20", "liq", "volratio", "vr5", "obv20", "vdry10")})
        for j in range(len(ai.SIGNAL_LABELS)):
            lat[f"sig{j:02d}"] = float(S[t, j])
        out["latest"] = lat
        return out
    except Exception:
        return None


def _specs(D):
    out = []
    for k in PAT:
        if k in ("acc_60n", "acc_since", "ma_conv", "range20"):
            continue
        out.append(("매집봉" if k.startswith("acc") else "거래량" if k.startswith(("vol", "obv", "val")) else "가격", LABEL[k], D[k] == 1))
    out += [("매집봉", "60일 안 매집봉 2개↑", D["acc_60n"] >= 2), ("매집봉", "마지막 매집봉 뒤 1~10일", (D["acc_since"] >= 1) & (D["acc_since"] <= 10)), ("매집봉", "마지막 매집봉 뒤 11~30일", (D["acc_since"] >= 11) & (D["acc_since"] <= 30)),
            ("매집봉", "매집봉 20일 안 + 거래량 마름(10일 최소 0.5배↓)", (D["acc_20"] == 1) & (D["vdry10"] < 0.5)), ("매집봉", "매집봉 20일 안 + 224일선 아래", (D["acc_20"] == 1) & (D["dist224"] < 0)),
            ("매집봉", "매집봉 20일 안 + 이전 급등 있음(250일)", (D["acc_20"] == 1) & (D["n_j250"] >= 1))]
    for j, lab in enumerate(ai.SIGNAL_LABELS):
        out.append(("기법 신호", f"{lab} (3봉 안)", D[f"sig{j:02d}"] <= SIG_WIN))
    out += [("기법 신호", "신호 3개↑ 겹침(10봉)", D["sig_n10"] >= 3), ("기법 신호", "신호 없음(10봉)", D["sig_n10"] == 0)]
    return out


def _table(E, B, tr_b, te_b):
    keys = [k for k in E if k not in ("date", "code")]
    De, Db = pd.DataFrame({k: E[k] for k in keys}), pd.DataFrame({k: B[k] for k in keys})
    f20 = B["fwd20"].astype(float)
    okb = ~np.isnan(f20)
    base = float(np.nanmean(f20[okb]))
    b_tr, b_te = float(np.nanmean(f20[okb & tr_b])), float(np.nanmean(f20[okb & te_b]))
    rows = []
    for (grp, name, me), (_, _, mb) in zip(_specs(De), _specs(Db)):
        me, mb = np.asarray(me, bool), np.asarray(mb, bool) & okb
        if me.sum() < 30 or mb.sum() < 300:
            continue
        p20 = float(np.nanmean(f20[mb]))
        p_tr = float(np.nanmean(f20[mb & tr_b])) if (mb & tr_b).sum() >= 200 else None
        p_te = float(np.nanmean(f20[mb & te_b])) if (mb & te_b).sum() >= 200 else None
        rows.append({"group": grp, "name": name, "share_pre": float(me.mean()), "share_base": float(mb.sum() / okb.sum()), "lift_share": float(me.mean() / (mb.sum() / okb.sum())),
                     "p20": p20, "lift20": p20 / base, "n_pre": int(me.sum()), "n_base": int(mb.sum()), "p_tr": p_tr, "p_te": p_te,
                     "lift_tr": p_tr / b_tr if p_tr is not None else None, "lift_te": p_te / b_te if p_te is not None else None,
                     "robust": bool(p_tr is not None and p_te is not None and p_tr >= b_tr * 1.3 and p_te >= b_te * 1.3)})
    return rows, {"p20": base, "p_tr": b_tr, "p_te": b_te}


def _combos(B, rows, tr_b, te_b, top=15):
    """두 시기 모두 1.3배↑였던 패턴끼리 겹쳤을 때(기준 표본 300↑)."""
    keys = [k for k in B if k not in ("date", "code")]
    Db = pd.DataFrame({k: B[k] for k in keys})
    specs = {name: np.asarray(m, bool) for _, name, m in _specs(Db)}
    f20 = B["fwd20"].astype(float)
    okb = ~np.isnan(f20)
    base = float(np.nanmean(f20[okb]))
    rob = [r["name"] for r in rows if r["robust"]]
    out = []
    for i in range(len(rob)):
        for j in range(i + 1, len(rob)):
            m = specs[rob[i]] & specs[rob[j]] & okb
            if m.sum() < 300 or (m & te_b).sum() < 100:
                continue
            out.append({"name": f"{rob[i]} + {rob[j]}", "n": int(m.sum()), "p20": float(np.nanmean(f20[m])), "lift": float(np.nanmean(f20[m]) / base),
                        "p_te": float(np.nanmean(f20[m & te_b])), "p_tr": float(np.nanmean(f20[m & tr_b])) if (m & tr_b).sum() >= 100 else None})
    out.sort(key=lambda x: -x["p20"])
    return out[:top]


def _model(E, B, tr_b, te_b, log, t0):
    from sklearn.metrics import roc_auc_score
    pat_cols = PAT + [f"sig{j:02d}" for j in range(len(ai.SIGNAL_LABELS))] + ["sig_n10"]
    num_cols = ["n_j250", "days_since", "hist_rate", "atrp", "dist224", "nh250", "ret5", "ret20", "liq", "volratio", "vr5", "obv20", "vdry10"]
    y = B["fwd20"].astype(float)
    valid = ~np.isnan(y)
    out = {}
    for lab, cols in (("패턴·신호만", pat_cols), ("패턴·신호 + 기본 특징(변동성·위치·이전 급등)", pat_cols + num_cols), ("기본 특징만 (비교)", num_cols)):
        X = np.column_stack([B[k] for k in cols]).astype(np.float32)
        mtr = st._sub(tr_b & valid, 600000, seed=1)
        mdl = st._fit_fast(X[mtr], y[mtr].astype(int), n_iter=200, seed=1)
        mte = te_b & valid
        p = st._predict(mdl, X[mte])
        auc = float(roc_auc_score(y[mte], p))
        q = pd.qcut(pd.Series(p).rank(method="first"), 10, labels=False).to_numpy()
        out[lab] = {"auc": auc, "base": float(y[mte].mean()), "deciles": [float(y[mte][q == k].mean()) for k in range(10)], "top1": float(y[mte][p >= np.quantile(p, 0.99)].mean())}
        if lab.startswith("패턴·신호 +"):
            yy = y[mte].astype(int)
            rng = np.random.default_rng(0)
            imp = []
            Xte = X[mte]
            for j, nm in enumerate(cols):
                Xp = Xte.copy()
                Xp[:, j] = rng.permutation(Xp[:, j])
                imp.append((nm, float(auc - roc_auc_score(yy, st._predict(mdl, Xp)))))
            imp.sort(key=lambda x: -x[1])
            names = {**LABEL, **{f"sig{j:02d}": ai.SIGNAL_LABELS[j] for j in range(len(ai.SIGNAL_LABELS))}, "sig_n10": "10봉 신호 수", **pre.LABEL}
            out[lab]["importance"] = [{"name": nm, "label": names.get(nm, nm), "gain": g} for nm, g in imp[:15]]
            out["_model"], out["_cols"] = mdl, cols
        log(f"[모델] {lab}: AUC {auc:.3f} · 상위 10% {out[lab]['deciles'][-1] * 100:.1f}% · {time.time() - t0:.0f}초")
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
                parts.append(out)
                latest.append(out["latest"])
            if i % 300 == 0:
                log(f"[종목 {i}/{len(codes)}] · {time.time() - t0:.0f}초")
    E, B = pre._cat(parts, "ev"), pre._cat(parts, "bs")
    del parts
    log(f"급등 전날 {len(E['date']):,}건 · 기준 {len(B['date']):,}건 · {time.time() - t0:.0f}초")
    tr_, va_, te_b = ai._split_masks(B["date"])
    tr_b = tr_ | va_
    rep = {"meta": {"generated": datetime.now().isoformat(timespec="seconds"), "jump": pre.JUMP, "events": int(len(E["date"])), "baseline": int(len(B["date"])), "stocks": int(len(set(E["code"]))),
                    "date_min": str(E["date"].min()), "date_max": str(E["date"].max()), "split": {"train_end": ai.VALID_END}, "sig_win": SIG_WIN + 1}}
    rep["rows"], rep["base"] = _table(E, B, tr_b, te_b)
    rep["combos"] = _combos(B, rep["rows"], tr_b, te_b)
    mo = _model(E, B, tr_b, te_b, log, t0)
    mdl, cols = mo.pop("_model"), mo.pop("_cols")
    rep["model"] = mo
    L = [r for r in latest if r["ok"] and np.isfinite(r.get("dist224", np.nan))]
    if L:
        XL = np.array([[r.get(k, np.nan) for k in cols] for r in L], np.float32)
        P = st._predict(mdl, XL)
        order = np.argsort(-P)[:40]
        date = max(r["date"] for r in L)
        pat_names = [k for k in PAT if k not in ("acc_60n", "acc_since", "ma_conv", "range20")]
        rep["today"] = {"date": date, "n": len(L), "rows": [{"code": L[i]["code"], "name": names.get(L[i]["code"], L[i]["code"]), "close": L[i]["close"], "prob": float(P[i]),
                                                            "patterns": [LABEL[k] for k in pat_names if L[i].get(k) == 1][:6], "signals": [ai.SIGNAL_LABELS[j] for j in range(len(ai.SIGNAL_LABELS)) if L[i].get(f"sig{j:02d}", 99) <= SIG_WIN][:5],
                                                            "n_j250": L[i]["n_j250"], "atrp": L[i]["atrp"], "dist224": L[i]["dist224"], "acc_60n": L[i]["acc_60n"]} for i in order if L[i]["date"] == date]}
    else:
        rep["today"] = {"date": None, "n": 0, "rows": []}
    rep["text"] = narrate(rep)
    rep["meta"]["elapsed_sec"] = round(time.time() - t0)
    PATTERN_PATH.write_text(json.dumps(rep, ensure_ascii=False, default=st._js), encoding="utf-8")
    return rep


def narrate(rep):
    b = rep["base"]
    out = [f"급등 전날 {rep['meta']['events']:,}건 vs 아무 날 {rep['meta']['baseline']:,}건. 아무 날 기준 20일 안 급등 확률 {b['p20'] * 100:.1f}%."]
    acc = [r for r in rep["rows"] if r["group"] == "매집봉"]
    a20 = next((r for r in acc if r["name"] == LABEL["acc_20"]), None)
    if a20:
        out.append(f"매집봉(단테 정의) 20일 안: 급등 전날 {a20['share_pre'] * 100:.0f}% vs 아무 날 {a20['share_base'] * 100:.0f}%({a20['lift_share']:.1f}배), 있으면 20일 안 급등 {a20['p20'] * 100:.1f}%({a20['lift20']:.1f}배, 학습 {a20['lift_tr']:.1f}·시험 {a20['lift_te']:.1f}).")
    best_acc = sorted([r for r in acc if r["robust"]], key=lambda r: -r["lift20"])[:3]
    out.append("매집봉 계열 중 두 시기 모두 1.3배↑: " + (", ".join(f"{r['name']} {r['p20'] * 100:.1f}%({r['lift20']:.1f}배)" for r in best_acc) if best_acc else "없음") + ".")
    for grp in ("거래량", "가격", "기법 신호"):
        rs = sorted([r for r in rep["rows"] if r["group"] == grp and r["robust"]], key=lambda r: -r["lift20"])[:4]
        worst = sorted([r for r in rep["rows"] if r["group"] == grp], key=lambda r: r["lift20"])[:2]
        out.append(f"{grp}: 확률을 올린 것 " + (", ".join(f"{r['name']} {r['p20'] * 100:.1f}%({r['lift20']:.1f}배)" for r in rs) if rs else "없음") + " / 오히려 낮은 것 " + ", ".join(f"{r['name']} {r['lift20']:.1f}배" for r in worst) + ".")
    if rep["combos"]:
        c = rep["combos"][0]
        out.append(f"패턴 겹침 최상위: {c['name']} — 20일 안 급등 {c['p20'] * 100:.1f}%({c['lift']:.1f}배, 시험 {c['p_te'] * 100:.1f}%, 표본 {c['n']:,}).")
    m = rep["model"]
    keys = list(m.keys())
    out.append("모델 AUC: " + ", ".join(f"{k} {m[k]['auc']:.3f}(상위 10% {m[k]['deciles'][-1] * 100:.1f}%)" for k in keys) + f" — 기준 {m[keys[0]]['base'] * 100:.1f}%.")
    return out


if __name__ == "__main__":
    run()
