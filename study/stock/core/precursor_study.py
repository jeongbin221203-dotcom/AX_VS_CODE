"""+20% 급등일 '전조' — 급등 전날까지의 224일선 등 이동평균·거래량·가격 모습이 아무 날과 어떻게 다르고, 그 조건이 있으면 20일 안 급등 확률이 얼마나 오르나.

  급등일 = 종가 등락 +20% 이상(거래 가능 종목, 상장 250일↑). 전조는 급등 '전날'(t−1) 종가까지의 값으로만 본다(급등일 자체는 안 봄).
  기준 = 같은 종목에서 11일 간격으로 뽑은 아무 날. 각 날에 '이후 5·20거래일 안 급등일이 있나'를 붙여 조건별 확률(전조 → 급등)을 계산한다.
  모델: 아무 날 표본으로 '20일 안 급등' 예측(~2021 학습, 2022~ 시험) — 전조만으로 얼마나 맞히나.
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
from core import ai, db, strategy_study as st
from core import backtest as bt

PRE_PATH = config.DATA_DIR / "precursor.json"
JUMP = 0.20
LIQ_MIN = math.log10(ai.TRADABLE_VALUE)
BASE_STEP = 11
PRE = 60
FEATS = ["dist5", "dist20", "dist60", "dist112", "dist224", "ma_below_n", "ma_conv", "slope224", "slope60", "slope20", "cross224_20", "touch224_10", "near224", "above224_days", "below224_days",
         "ret5", "ret20", "ret60", "nh250", "low60", "range20", "atrp", "vr5", "vr20_60", "vdry10", "spike20", "volratio", "obv20", "updays10", "upstreak", "gapup5", "hh20_gap",
         "n_j250", "days_since", "hist_rate", "price_level", "liq", "hist", "mkt_r20", "mkt_r60", "mkt_dd250"]
LABEL = {"dist5": "5일선 대비", "dist20": "20일선 대비", "dist60": "60일선 대비", "dist112": "112일선 대비", "dist224": "224일선 대비", "ma_below_n": "주가 위에 있는 이평선 수(20·60·112·224)",
         "ma_conv": "이평선 밀집도(20~224 최대−최소 ÷ 주가)", "slope224": "224일선 기울기(20일)", "slope60": "60일선 기울기(20일)", "slope20": "20일선 기울기(10일)",
         "cross224_20": "최근 20일 안 224일선 상향 돌파", "touch224_10": "최근 10일 안 224일선 터치", "near224": "224일선 ±3% 안", "above224_days": "224일선 위 연속일", "below224_days": "224일선 아래 연속일",
         "ret5": "5일 수익", "ret20": "20일 수익", "ret60": "60일 수익", "nh250": "52주 고점 대비", "low60": "60일 저점 대비", "range20": "20일 가격 폭", "atrp": "변동성(ATR÷종가)",
         "vr5": "5일 ÷ 20일 거래량", "vr20_60": "20일 ÷ 60일 거래량", "vdry10": "10일 최소 거래량 ÷ 20일 평균", "spike20": "20일 안 거래량 3배↑ 날 수", "volratio": "당일 거래량 ÷ 20일 평균",
         "obv20": "OBV 20일 변화", "updays10": "10일 중 오른 날", "upstreak": "연속 상승일", "gapup5": "5일 안 갭상승 3%↑ 횟수", "hh20_gap": "20일 고점 대비",
         "n_j250": "최근 250일 급등 횟수", "days_since": "마지막 급등 뒤 경과일", "hist_rate": "연평균 급등 횟수(과거)", "price_level": "주가(로그)", "liq": "거래대금(로그)", "hist": "상장 후 경과(로그)",
         "mkt_r20": "시장 20일", "mkt_r60": "시장 60일", "mkt_dd250": "시장 52주 고점 대비"}


def _features(df):
    o, h, l, c, v = (df[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
    n = len(c)
    s = pd.Series
    cs, vs = s(c), s(v)
    ma = {p: cs.rolling(p, min_periods=p).mean().to_numpy() for p in (5, 20, 60, 112, 224)}
    with np.errstate(divide="ignore", invalid="ignore"):
        ret1 = np.r_[np.nan, c[1:] / c[:-1] - 1]
        jump = (ret1 >= JUMP) & np.isfinite(ret1)
        a20 = vs.rolling(20, min_periods=20).mean().shift().to_numpy()
        avg5, avg60 = vs.rolling(5, min_periods=5).mean().to_numpy(), vs.rolling(60, min_periods=60).mean().to_numpy()
        vr = v / a20
        val = s(c * v).rolling(20, min_periods=20).mean().to_numpy()
        above = c > ma[224]
        cross = np.r_[False, above[1:] & ~above[:-1]]
        touch = (l <= ma[224]) & (h >= ma[224])
        up = np.r_[0.0, (c[1:] > c[:-1]).astype(float)]
        gapup = np.r_[0.0, (o[1:] / c[:-1] - 1 >= 0.03).astype(float)]
        obv = np.cumsum(np.where(ret1 > 0, v, np.where(ret1 < 0, -v, 0.0)))
        F = {"dist5": c / ma[5] - 1, "dist20": c / ma[20] - 1, "dist60": c / ma[60] - 1, "dist112": c / ma[112] - 1, "dist224": c / ma[224] - 1,
             "ma_below_n": sum((ma[p] > c).astype(float) for p in (20, 60, 112, 224)),
             "ma_conv": (np.maximum.reduce([ma[20], ma[60], ma[112], ma[224]]) - np.minimum.reduce([ma[20], ma[60], ma[112], ma[224]])) / c,
             "slope224": ma[224] / s(ma[224]).shift(20).to_numpy() - 1, "slope60": ma[60] / s(ma[60]).shift(20).to_numpy() - 1, "slope20": ma[20] / s(ma[20]).shift(10).to_numpy() - 1,
             "cross224_20": s(cross.astype(float)).rolling(20, min_periods=1).max().to_numpy(), "touch224_10": s(touch.astype(float)).rolling(10, min_periods=1).max().to_numpy(),
             "near224": (np.abs(c / ma[224] - 1) < 0.03).astype(float), "above224_days": ai._since(~above, cap=120), "below224_days": ai._since(above, cap=120),
             "ret5": c / cs.shift(5).to_numpy() - 1, "ret20": c / cs.shift(20).to_numpy() - 1, "ret60": c / cs.shift(60).to_numpy() - 1,
             "nh250": c / s(h).rolling(250, min_periods=120).max().to_numpy() - 1, "low60": c / s(l).rolling(60, min_periods=60).min().to_numpy() - 1,
             "range20": (s(h).rolling(20, min_periods=20).max().to_numpy() - s(l).rolling(20, min_periods=20).min().to_numpy()) / c,
             "atrp": ai.ind.atr(df).to_numpy(float) / c, "vr5": avg5 / a20, "vr20_60": a20 / avg60, "vdry10": vs.rolling(10, min_periods=10).min().to_numpy() / a20,
             "spike20": s((vr >= 3).astype(float)).rolling(20, min_periods=1).sum().to_numpy(), "volratio": vr, "obv20": (obv - s(obv).shift(20).to_numpy()) / (a20 * 20 + 1e-9),
             "updays10": s(up).rolling(10, min_periods=10).sum().to_numpy(), "upstreak": ai._since(up == 0, cap=20), "gapup5": s(gapup).rolling(5, min_periods=1).sum().to_numpy(),
             "hh20_gap": c / s(h).rolling(20, min_periods=20).max().to_numpy() - 1, "price_level": np.log10(np.maximum(c, 1.0)), "liq": np.log10(val + 1), "hist": np.log10(np.arange(n) + 1.0)}
        # 이전 급등 내역(그 날까지)
        jidx = np.flatnonzero(jump)
        cum = np.cumsum(jump.astype(float))
        F["n_j250"] = cum - np.r_[np.zeros(250), cum[:-250]]
        last = np.maximum.accumulate(np.where(jump, np.arange(n), -1))
        F["days_since"] = np.where(last >= 0, np.minimum(np.arange(n) - last, 2000), 2000).astype(float)
        F["hist_rate"] = cum / np.maximum(np.arange(n), 1) * 250
        # 앞으로 5·20일 안 급등
        fj = np.r_[jump[1:], False].astype(float)
        F["fwd5"] = s(fj[::-1]).rolling(5, min_periods=1).max().to_numpy()[::-1].copy()
        F["fwd20"] = s(fj[::-1]).rolling(20, min_periods=1).max().to_numpy()[::-1].copy()
        F["fwd5"][n - 5:] = np.nan
        F["fwd20"][n - 20:] = np.nan
    return F, jump, c, v, vr


def _work(code):
    try:
        df = ai.service.load_prices(code)
        n = len(df)
        if n < 400:
            return None
        F, jump, c, v, vr = _features(df)
        t_all = np.arange(n)
        ok = (F["liq"] >= LIQ_MIN) & (t_all >= 250) & (t_all + 21 < n)
        ev = t_all[jump & ok & (t_all >= 251)] - 1                              # 급등 전날
        bs = t_all[ok & (t_all % BASE_STEP == 0)]
        dates = df.index.strftime("%Y-%m-%d").to_numpy()
        out = {"code": code}
        for name, tt in (("ev", ev), ("bs", bs)):
            if len(tt) == 0:
                out[name] = None
                continue
            P = {k: F[k][tt].astype(np.float32) for k in FEATS if k in F}
            for k in ("fwd5", "fwd20"):
                P[k] = F[k][tt].astype(np.float32)
            if ai.MKT_DF is not None:
                m = ai.MKT_DF.reindex(dates[tt])
                for k in ("mkt_r20", "mkt_r60", "mkt_dd250"):
                    P[k] = m[k].to_numpy(np.float32)
            else:
                for k in ("mkt_r20", "mkt_r60", "mkt_dd250"):
                    P[k] = np.full(len(tt), np.nan, np.float32)
            P["date"] = dates[tt]
            P["code"] = np.full(len(tt), code)
            if name == "ev":
                P["path_d224"] = sliding_window_view(F["dist224"], PRE)[tt - PRE + 1].astype(np.float32)        # t−59..t
                P["path_v"] = np.clip(sliding_window_view(np.nan_to_num(vr, nan=1.0), PRE)[tt - PRE + 1], 0, 20).astype(np.float32)
                P["path_c"] = (sliding_window_view(c, PRE)[tt - PRE + 1] / c[tt][:, None]).astype(np.float32)
            out[name] = P
        # 최신 날(오늘 전조 점수용)
        if ok[n - 1 - 0] or True:
            t = n - 1
            out["latest"] = {"code": code, "date": dates[t], "close": float(c[t]), "ok": bool(F["liq"][t] >= LIQ_MIN and v[t] > 0), **{k: float(F[k][t]) for k in FEATS if k in F}}
            if ai.MKT_DF is not None and dates[t] in ai.MKT_DF.index:
                for k in ("mkt_r20", "mkt_r60", "mkt_dd250"):
                    out["latest"][k] = float(ai.MKT_DF.loc[dates[t], k])
            else:
                for k in ("mkt_r20", "mkt_r60", "mkt_dd250"):
                    out["latest"][k] = np.nan
        return out
    except Exception:
        return None


def _cat(parts, key):
    arrs = [p[key] for p in parts if p.get(key) is not None]
    if not arrs:
        return None
    return {k: np.concatenate([a[k] for a in arrs]) for k in arrs[0]}


def _conditions(D):
    return [
        ("224일선", "224일선 −50% 아래", D["dist224"] <= -0.5), ("224일선", "224일선 −50~−30%", (D["dist224"] > -0.5) & (D["dist224"] <= -0.3)), ("224일선", "224일선 −30~−10%", (D["dist224"] > -0.3) & (D["dist224"] <= -0.1)),
        ("224일선", "224일선 −10~0% (바로 아래)", (D["dist224"] > -0.1) & (D["dist224"] <= 0)), ("224일선", "224일선 0~+10% (바로 위)", (D["dist224"] > 0) & (D["dist224"] <= 0.1)),
        ("224일선", "224일선 +10~+30%", (D["dist224"] > 0.1) & (D["dist224"] <= 0.3)), ("224일선", "224일선 +30% 위", D["dist224"] > 0.3),
        ("224일선", "224일선 ±3% 안 (붙어 있음)", D["near224"] == 1), ("224일선", "최근 10일 안 224일선 터치", D["touch224_10"] == 1), ("224일선", "최근 20일 안 224일선 상향 돌파", D["cross224_20"] == 1),
        ("224일선", "224일선 아래 60일↑ 연속", D["below224_days"] >= 60), ("224일선", "224일선 위 60일↑ 연속", D["above224_days"] >= 60),
        ("224일선", "224일선 기울기 상승(20일 +2%↑)", D["slope224"] >= 0.02), ("224일선", "224일선 기울기 하락(−2%↓)", D["slope224"] <= -0.02),
        ("이평선 배열", "주가가 4개 이평선 모두 위", D["ma_below_n"] == 0), ("이평선 배열", "주가가 4개 이평선 모두 아래", D["ma_below_n"] == 4),
        ("이평선 배열", "이평선 밀집 (20~224 폭 5% 안)", D["ma_conv"] < 0.05), ("이평선 배열", "이평선 밀집 10% 안", D["ma_conv"] < 0.10), ("이평선 배열", "이평선 흩어짐 (폭 40%↑)", D["ma_conv"] >= 0.40),
        ("이평선 배열", "20일선 위", D["dist20"] > 0), ("이평선 배열", "20일선 −10% 아래", D["dist20"] <= -0.1), ("이평선 배열", "60일선 기울기 상승", D["slope60"] >= 0.02), ("이평선 배열", "60일선 기울기 하락", D["slope60"] <= -0.02),
        ("가격", "5일 +10%↑ (이미 들썩)", D["ret5"] >= 0.10), ("가격", "5일 −10%↓", D["ret5"] <= -0.10), ("가격", "20일 −20%↓ 급락", D["ret20"] <= -0.2), ("가격", "20일 +20%↑", D["ret20"] >= 0.2),
        ("가격", "52주 고점 −10% 이내", D["nh250"] >= -0.1), ("가격", "52주 고점 −50% 아래", D["nh250"] < -0.5), ("가격", "60일 저점 +5% 안", D["low60"] <= 0.05),
        ("가격", "20일 폭 10% 미만 (응축)", D["range20"] < 0.10), ("가격", "20일 폭 30%↑ (요동)", D["range20"] >= 0.30), ("가격", "변동성 6%↑", D["atrp"] >= 0.06), ("가격", "변동성 3% 미만", D["atrp"] < 0.03),
        ("가격", "연속 상승 3일↑", D["upstreak"] >= 3), ("가격", "10일 중 7일↑ 상승", D["updays10"] >= 7), ("가격", "5일 안 갭상승 3%↑ 있음", D["gapup5"] >= 1), ("가격", "20일 고점 −3% 안", D["hh20_gap"] >= -0.03),
        ("거래량", "당일 거래량 3배↑", D["volratio"] >= 3), ("거래량", "당일 거래량 2배↑", D["volratio"] >= 2), ("거래량", "5일 ÷ 20일 1.5배↑ (늘어나는 중)", D["vr5"] >= 1.5), ("거래량", "20일 ÷ 60일 1.5배↑", D["vr20_60"] >= 1.5),
        ("거래량", "10일 최소 0.3배↓ (마름)", D["vdry10"] < 0.3), ("거래량", "20일 안 3배↑ 급증 2번↑", D["spike20"] >= 2), ("거래량", "OBV 20일 증가(+0.5↑)", D["obv20"] >= 0.5),
        ("이전 급등", "250일 안 급등 1번↑", D["n_j250"] >= 1), ("이전 급등", "250일 안 급등 3번↑", D["n_j250"] >= 3), ("이전 급등", "마지막 급등 뒤 20일 안", D["days_since"] <= 20), ("이전 급등", "연평균 급등 2회↑", D["hist_rate"] >= 2),
        ("종목·시장", "주가 1,000원 미만", D["price_level"] < 3), ("종목·시장", "거래대금 3~10억", D["liq"] < 9), ("종목·시장", "시장 60일 −10%↓", D["mkt_r60"] <= -0.1), ("종목·시장", "시장 52주 고점 −20%↓", D["mkt_dd250"] <= -0.2),
    ]


def _cond_table(E, B, tr_b, te_b):
    De, Db = pd.DataFrame({k: E[k] for k in FEATS}), pd.DataFrame({k: B[k] for k in FEATS})
    f20, f5 = B["fwd20"].astype(float), B["fwd5"].astype(float)
    okb = ~np.isnan(f20)
    base20, base5 = float(np.nanmean(f20[okb])), float(np.nanmean(f5[okb & ~np.isnan(f5)]))
    b_tr, b_te = float(np.nanmean(f20[okb & tr_b])), float(np.nanmean(f20[okb & te_b]))
    rows = []
    for (grp, name, me), (_, _, mb) in zip(_conditions(De), _conditions(Db)):
        me, mb = np.asarray(me, bool), np.asarray(mb, bool) & okb
        if me.sum() < 50 or mb.sum() < 500:
            continue
        p20 = float(np.nanmean(f20[mb]))
        p_tr, p_te = float(np.nanmean(f20[mb & tr_b])) if (mb & tr_b).sum() >= 300 else None, float(np.nanmean(f20[mb & te_b])) if (mb & te_b).sum() >= 300 else None
        rows.append({"group": grp, "name": name, "share_pre": float(me.mean()), "share_base": float(mb.sum() / okb.sum()), "lift_share": float(me.mean() / (mb.sum() / okb.sum())),
                     "p20": p20, "lift20": p20 / base20, "p5": float(np.nanmean(f5[mb & ~np.isnan(f5)])), "n_pre": int(me.sum()), "n_base": int(mb.sum()),
                     "p_tr": p_tr, "p_te": p_te, "lift_tr": p_tr / b_tr if p_tr is not None and b_tr else None, "lift_te": p_te / b_te if p_te is not None and b_te else None,
                     "robust": bool(p_tr is not None and p_te is not None and p_tr >= b_tr * 1.3 and p_te >= b_te * 1.3)})
    return rows, {"p20": base20, "p5": base5, "p_tr": b_tr, "p_te": b_te}


def _paths(E):
    days = list(range(-PRE + 1, 1))
    d224, vv, cc = np.nanmedian(E["path_d224"], axis=0), np.nanmedian(E["path_v"], axis=0), np.nanmedian(E["path_c"], axis=0)
    v2 = (E["path_v"] >= 2).mean(axis=0)
    above = (E["path_d224"] > 0).mean(axis=0)
    return [{"d": d, "d224": float(d224[i]), "v": float(vv[i]), "v2": float(v2[i]), "c": float(cc[i]), "above224": float(above[i])} for i, d in enumerate(days)]


def _model(B, tr_b, te_b, log, t0):
    from sklearn.metrics import roc_auc_score
    X = np.column_stack([B[k] for k in FEATS]).astype(np.float32)
    y = B["fwd20"].astype(float)
    valid = ~np.isnan(y)
    out = {}
    for lab, cols in (("전조 특징 (이전 급등 내역 제외)", [i for i, k in enumerate(FEATS) if k not in ("n_j250", "days_since", "hist_rate")]), ("+ 이전 급등 내역", list(range(len(FEATS))))):
        mtr = st._sub(tr_b & valid, 600000, seed=1)
        mdl = st._fit_fast(X[mtr][:, cols], y[mtr].astype(int), n_iter=200, seed=1)
        mte = te_b & valid
        p = st._predict(mdl, X[mte][:, cols])
        auc = float(roc_auc_score(y[mte], p))
        q = pd.qcut(pd.Series(p).rank(method="first"), 10, labels=False).to_numpy()
        dec = [{"bin": int(k + 1), "p": float(p[q == k].mean()), "actual": float(y[mte][q == k].mean())} for k in range(10)]
        top5 = p >= np.quantile(p, 0.95)
        row = {"auc": auc, "base": float(y[mte].mean()), "deciles": dec, "top5": float(y[mte][top5].mean()), "top1": float(y[mte][p >= np.quantile(p, 0.99)].mean())}
        if cols == list(range(len(FEATS))):
            yy = y[mte].astype(int)
            rng = np.random.default_rng(0)
            imp = []
            Xte = X[mte]
            for j, nm in enumerate(FEATS):
                Xp = Xte.copy()
                Xp[:, j] = rng.permutation(Xp[:, j])
                imp.append((nm, float(auc - roc_auc_score(yy, st._predict(mdl, Xp)))))
            imp.sort(key=lambda x: -x[1])
            row["importance"] = [{"name": nm, "label": LABEL.get(nm, nm), "gain": g} for nm, g in imp[:15]]
            out["_model"] = mdl
        out[lab] = row
        log(f"[모델] {lab}: AUC {auc:.3f} · 상위 10% {dec[-1]['actual'] * 100:.1f}% · 상위 1% {row['top1'] * 100:.1f}% (기준 {row['base'] * 100:.1f}%) · {time.time() - t0:.0f}초")
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
        for i, out in enumerate(ex.map(_work, codes, chunksize=8), 1):
            if out:
                parts.append(out)
                if out.get("latest"):
                    latest.append(out["latest"])
            if i % 500 == 0:
                log(f"[종목 {i}/{len(codes)}] · {time.time() - t0:.0f}초")
    E, B = _cat(parts, "ev"), _cat(parts, "bs")
    del parts
    log(f"급등 전날 {len(E['date']):,}건 · 기준 {len(B['date']):,}건 · {time.time() - t0:.0f}초")
    tr_, va_, te_b = ai._split_masks(B["date"])
    tr_b = tr_ | va_
    rep = {"meta": {"generated": datetime.now().isoformat(timespec="seconds"), "jump": JUMP, "events": int(len(E["date"])), "baseline": int(len(B["date"])), "stocks": int(len(set(E["code"]))),
                    "date_min": str(E["date"].min()), "date_max": str(E["date"].max()), "split": {"train_end": ai.VALID_END}, "liq_min": ai.TRADABLE_VALUE, "base_step": BASE_STEP}}
    rep["conditions"], rep["base"] = _cond_table(E, B, tr_b, te_b)
    rep["paths"] = _paths(E)
    # 특징 중앙값 차이(급등 전날 vs 아무 날)
    diff = []
    for k in FEATS:
        a, b = E[k].astype(float), B[k].astype(float)
        sd = float(np.nanstd(b)) or 1.0
        diff.append({"name": k, "label": LABEL.get(k, k), "pre": float(np.nanmedian(a)), "base": float(np.nanmedian(b)), "d": float((np.nanmean(a) - np.nanmean(b)) / sd)})
    diff.sort(key=lambda x: -abs(x["d"]))
    rep["diff"] = diff[:18]
    mo = _model(B, tr_b, te_b, log, t0)
    mdl = mo.pop("_model")
    rep["model"] = mo
    # 오늘 전조 점수 상위
    L = [r for r in latest if r["ok"] and all(np.isfinite(r.get(k, np.nan)) for k in ("dist224", "ret60", "liq"))]
    if L:
        XL = np.array([[r.get(k, np.nan) for k in FEATS] for r in L], np.float32)
        P = st._predict(mdl, XL)
        order = np.argsort(-P)[:40]
        date = max(r["date"] for r in L)
        rep["today"] = {"date": date, "n": len(L), "base": rep["model"]["+ 이전 급등 내역"]["base"],
                        "rows": [{"code": L[i]["code"], "name": names.get(L[i]["code"], L[i]["code"]), "close": L[i]["close"], "prob": float(P[i]), "dist224": L[i]["dist224"], "dist20": L[i]["dist20"],
                                  "ma_conv": L[i]["ma_conv"], "ret20": L[i]["ret20"], "nh250": L[i]["nh250"], "vr5": L[i]["vr5"], "atrp": L[i]["atrp"], "n_j250": L[i]["n_j250"], "range20": L[i]["range20"]} for i in order if L[i]["date"] == date]}
    else:
        rep["today"] = {"date": None, "rows": [], "n": 0}
    rep["text"] = narrate(rep)
    rep["meta"]["elapsed_sec"] = round(time.time() - t0)
    PRE_PATH.write_text(json.dumps(rep, ensure_ascii=False, default=st._js), encoding="utf-8")
    return rep


def narrate(rep):
    b = rep["base"]
    out = [f"급등 전날 {rep['meta']['events']:,}건 vs 아무 날 {rep['meta']['baseline']:,}건. 아무 날 기준 20일 안 +20% 급등일이 나올 확률 {b['p20'] * 100:.1f}%(5일 안 {b['p5'] * 100:.1f}%)."]
    c224 = [r for r in rep["conditions"] if r["group"] == "224일선"]
    hi = sorted(c224, key=lambda r: -r["lift20"])[:3]
    lo = sorted(c224, key=lambda r: r["lift20"])[:2]
    out.append("224일선: 20일 안 급등 확률이 높은 자리 " + ", ".join(f"{r['name']} {r['p20'] * 100:.1f}%({r['lift20']:.1f}배)" for r in hi) + " / 낮은 자리 " + ", ".join(f"{r['name']} {r['p20'] * 100:.1f}%({r['lift20']:.1f}배)" for r in lo) + ".")
    rob = sorted([r for r in rep["conditions"] if r["robust"]], key=lambda r: -r["lift20"])[:6]
    out.append("두 시기 모두 1.3배↑로 확률을 올린 전조: " + (", ".join(f"{r['name']} {r['lift20']:.1f}배({r['p20'] * 100:.1f}%)" for r in rob) if rob else "없음") + ".")
    top = sorted(rep["conditions"], key=lambda r: -r["lift_share"])[:5]
    out.append("급등 전날에 아무 날보다 흔했던 모습: " + ", ".join(f"{r['name']} {r['lift_share']:.1f}배({r['share_pre'] * 100:.0f}%)" for r in top) + ".")
    p = {x["d"]: x for x in rep["paths"]}
    out.append(f"급등 전 60일 경로(중앙): 224일선 대비 −59일 {p[-59]['d224'] * 100:+.0f}% → −20일 {p[-20]['d224'] * 100:+.0f}% → −5일 {p[-5]['d224'] * 100:+.0f}% → 전날 {p[0]['d224'] * 100:+.0f}%(224일선 위 비율 전날 {p[0]['above224'] * 100:.0f}%), "
               f"거래량 배수 −20일 {p[-20]['v']:.2f} → −5일 {p[-5]['v']:.2f} → 전날 {p[0]['v']:.2f}(2배↑ 날 비율 전날 {p[0]['v2'] * 100:.0f}%), 종가(전날=1) −20일 {p[-20]['c']:.2f} → −5일 {p[-5]['c']:.2f}.")
    m = rep["model"]
    a, c = m["전조 특징 (이전 급등 내역 제외)"], m["+ 이전 급등 내역"]
    out.append(f"전조만으로 20일 안 급등을 맞히는 모델: AUC {a['auc']:.3f}(이전 내역 더하면 {c['auc']:.3f}), 점수 상위 10% 실제 {c['deciles'][-1]['actual'] * 100:.1f}%·상위 1% {c['top1'] * 100:.1f}% vs 기준 {c['base'] * 100:.1f}%, 하위 10% {c['deciles'][0]['actual'] * 100:.1f}%. "
               + "중요 특징: " + ", ".join(i["label"] for i in c["importance"][:6]) + ".")
    return out


if __name__ == "__main__":
    run()
