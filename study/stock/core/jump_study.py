"""하루 +20% 이상 급등한 날 전부 분석 — 그 뒤 어떻게 되나, 같은 종목의 '이전 급등 내역'이 결과를 더 잘 맞히나.

  사건 = 종가 등락 +20% 이상인 날(거래 가능 종목, 상장 250일↑). 표본 추출 없이 전부.
  진입은 다음날 시가. 결과: 다음날·5·20·60일 수익, 20일 안 +20% 터치, 20일 안 최대 하락, 급등일 시가 아래로 되돌림, 60·250일 안 또 급등.
  이전 내역(그 종목의 과거만, 미래 정보 없음): 최근 250·750일 급등 횟수, 마지막 급등 뒤 경과일, 마지막 급등의 20일 결과, 상장 이후 연평균 급등 횟수.
  정확성: 가격·거래량·시장 특징만 쓴 모델 vs 이전 내역을 더한 모델(~2021 학습, 2022~ 시험)의 AUC·상위 10% 적중을 비교.
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

JUMP_PATH = config.DATA_DIR / "jump.json"
JUMP = 0.20
LIQ_MIN = math.log10(ai.TRADABLE_VALUE)
POST = 250
BASE_FEATS = ["jump", "gap0", "close_pos", "upper", "volratio", "vr5", "vr20_60", "nh250", "ret5_prev", "ret20_prev", "ret60_prev", "dist20", "dist60", "dist224", "atrp",
              "price_level", "liq", "hist", "mkt_r20", "mkt_r60", "mkt_dd250", "prev_day_up", "range20", "spike20"]
HIST_FEATS = ["n_j250", "n_j750", "days_since", "prev_r20", "prev_touch", "prev_giveback", "hist_rate", "n_j20"]
LABEL = {"jump": "급등일 등락률", "gap0": "급등일 시가 갭", "close_pos": "종가 위치(고저 폭 안)", "upper": "윗꼬리 비율", "volratio": "거래량 ÷ 20일 평균", "vr5": "5일 ÷ 20일 거래량", "vr20_60": "20일 ÷ 60일 거래량",
         "nh250": "52주 고점 대비(전날)", "ret5_prev": "급등 전 5일 수익", "ret20_prev": "급등 전 20일 수익", "ret60_prev": "급등 전 60일 수익", "dist20": "20일선 대비", "dist60": "60일선 대비", "dist224": "224일선 대비",
         "atrp": "변동성(ATR÷종가)", "price_level": "주가(로그)", "liq": "거래대금(로그)", "hist": "상장 후 경과", "mkt_r20": "시장 20일", "mkt_r60": "시장 60일", "mkt_dd250": "시장 52주 고점 대비",
         "prev_day_up": "전날 +10%↑ (연속 급등)", "range20": "20일 가격 폭", "spike20": "20일 안 거래량 3배↑ 날 수",
         "n_j250": "최근 250일 급등 횟수", "n_j750": "최근 750일 급등 횟수", "days_since": "마지막 급등 뒤 경과일", "prev_r20": "마지막 급등의 20일 결과", "prev_touch": "마지막 급등 뒤 +20% 터치",
         "prev_giveback": "마지막 급등 되돌림", "hist_rate": "연평균 급등 횟수(과거)", "n_j20": "최근 20일 급등 횟수"}


def _work(code):
    try:
        df = ai.service.load_prices(code)
        o, h, l, c, v = (df[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
        n = len(c)
        if n < 300:
            return None
        ret1 = np.r_[np.nan, c[1:] / c[:-1] - 1]
        jump = (ret1 >= JUMP) & np.isfinite(ret1)
        val = pd.Series(c * v).rolling(20, min_periods=20).mean().to_numpy()
        with np.errstate(divide="ignore", invalid="ignore"):
            liq = np.log10(val + 1)
        t_all = np.arange(n)
        ev = t_all[jump & (liq >= LIQ_MIN) & (t_all >= 250) & (t_all + 2 < n)]
        if len(ev) == 0:
            return None
        jidx = t_all[jump]
        a20 = pd.Series(v).rolling(20, min_periods=20).mean().shift().to_numpy()
        avg5 = pd.Series(v).rolling(5, min_periods=5).mean().to_numpy()
        avg60 = pd.Series(v).rolling(60, min_periods=60).mean().to_numpy()
        hh250 = pd.Series(h).rolling(250, min_periods=120).max().shift().to_numpy()
        hh20 = pd.Series(h).rolling(20, min_periods=20).max().to_numpy()
        ll20 = pd.Series(l).rolling(20, min_periods=20).min().to_numpy()
        ma = {p: pd.Series(c).rolling(p, min_periods=p).mean().to_numpy() for p in (20, 60, 224)}
        tr = pd.Series(np.maximum(h - l, np.maximum(np.abs(h - np.r_[c[0], c[:-1]]), np.abs(l - np.r_[c[0], c[:-1]])))).rolling(14).mean().to_numpy()
        with np.errstate(divide="ignore", invalid="ignore"):
            vr = v / a20
            spike = pd.Series((vr >= 3).astype(float)).rolling(20, min_periods=1).sum().to_numpy()
        # 결과(진입 = 다음날 시가)
        res = {}
        e = ev + 1
        entry = o[e]
        pad = lambda a, k: np.r_[a, np.full(k, np.nan)]  # noqa: E731
        Wh = sliding_window_view(pad(h, POST + 2), POST + 1)[e]           # e..e+250 (뒤는 NaN)
        Wl = sliding_window_view(pad(l, POST + 2), POST + 1)[e]
        Wc = sliding_window_view(pad(c, POST + 2), POST + 1)[e]
        Wr = sliding_window_view(pad(ret1, POST + 2), POST + 1)[e]
        res["r1"] = Wc[:, 0] / entry - 1
        for k in (5, 20, 60):
            res[f"r{k}"] = Wc[:, k - 1] / entry - 1
        with np.errstate(invalid="ignore"):
            res["touch20"] = (np.nanmax(Wh[:, :20], axis=1) / entry - 1 >= 0.20).astype(float)
            res["touch20"][np.isnan(Wh[:, 19])] = np.nan
            res["dd20"] = np.nanmin(Wl[:, :20], axis=1) / entry - 1
            res["giveback"] = (np.nanmin(Wc[:, :20], axis=1) < o[ev]).astype(float)                  # 급등일 시가 아래로
            res["giveback"][np.isnan(Wc[:, 19])] = np.nan
            res["next_jump60"] = (Wr[:, 1:61] >= JUMP).any(axis=1).astype(float)
            res["next_jump60"][np.isnan(Wr[:, 60])] = np.nan
            res["next_jump250"] = (Wr[:, 1:] >= JUMP).any(axis=1).astype(float)
            res["next_jump250"][np.isnan(Wr[:, POST - 1])] = np.nan
        # 이전 내역(사건 t 기준 과거만)
        pos = np.searchsorted(jidx, ev)                                     # jidx[:pos] 가 과거 급등(ev 자신 제외)
        n_j250 = np.array([((jidx[:p] >= t - 250) & (jidx[:p] < t)).sum() for p, t in zip(pos, ev)], float)
        n_j750 = np.array([((jidx[:p] >= t - 750) & (jidx[:p] < t)).sum() for p, t in zip(pos, ev)], float)
        n_j20 = np.array([((jidx[:p] >= t - 20) & (jidx[:p] < t)).sum() for p, t in zip(pos, ev)], float)
        last = np.array([jidx[p - 1] if p > 0 else -1 for p in pos])
        days_since = np.where(last >= 0, np.minimum(ev - last, 2000), 2000).astype(float)
        prev_r20 = np.full(len(ev), np.nan)
        prev_touch = np.full(len(ev), np.nan)
        prev_giveback = np.full(len(ev), np.nan)
        okp = (last >= 0) & (last + 21 < n) & (last + 21 <= ev)              # 이전 급등 결과가 이미 확정된 경우만(20일 지난 뒤)
        lp = last[okp]
        prev_r20[okp] = c[lp + 20] / o[lp + 1] - 1
        prev_touch[okp] = [float(h[x + 1:x + 21].max() / o[x + 1] - 1 >= 0.20) for x in lp]
        prev_giveback[okp] = [float(c[x + 1:x + 21].min() < o[x]) for x in lp]
        hist_rate = np.array([(jidx[:p] < t).sum() / max(t, 1) * 250 for p, t in zip(pos, ev)], float)
        with np.errstate(divide="ignore", invalid="ignore"):
            F = {"jump": ret1[ev], "gap0": o[ev] / c[ev - 1] - 1, "close_pos": (c[ev] - l[ev]) / np.maximum(h[ev] - l[ev], 1e-9), "upper": (h[ev] - c[ev]) / np.maximum(h[ev] - l[ev], 1e-9),
                 "volratio": vr[ev], "vr5": avg5[ev] / a20[ev], "vr20_60": a20[ev] / avg60[ev], "nh250": c[ev - 1] / hh250[ev] - 1, "ret5_prev": c[ev - 1] / c[ev - 6] - 1,
                 "ret20_prev": c[ev - 1] / c[ev - 21] - 1, "ret60_prev": c[ev - 1] / c[ev - 61] - 1, "dist20": c[ev] / ma[20][ev] - 1, "dist60": c[ev] / ma[60][ev] - 1, "dist224": c[ev] / ma[224][ev] - 1,
                 "atrp": tr[ev] / c[ev], "price_level": np.log10(np.maximum(c[ev], 1)), "liq": liq[ev], "hist": np.log10(ev + 1.0), "prev_day_up": (ret1[ev - 1] >= 0.10).astype(float),
                 "range20": (hh20[ev - 1] - ll20[ev - 1]) / c[ev - 1], "spike20": spike[ev - 1],
                 "n_j250": n_j250, "n_j750": n_j750, "days_since": days_since, "prev_r20": prev_r20, "prev_touch": prev_touch, "prev_giveback": prev_giveback, "hist_rate": hist_rate, "n_j20": n_j20}
        dates = df.index.strftime("%Y-%m-%d").to_numpy()
        out = {"code": np.full(len(ev), code), "date": dates[ev], "close": c[ev], "entry_gap": o[e] / c[ev] - 1}
        if ai.MKT_DF is not None:
            m = ai.MKT_DF.reindex(out["date"])
            for k in ("mkt_r20", "mkt_r60", "mkt_dd250"):
                F[k] = m[k].to_numpy(float)
        else:
            for k in ("mkt_r20", "mkt_r60", "mkt_dd250"):
                F[k] = np.full(len(ev), np.nan)
        out.update(F)
        out.update(res)
        return out
    except Exception:
        return None


def _stats(r, extra=None):
    r = np.asarray(r, float)
    ok = ~np.isnan(r)
    rr = r[ok]
    if len(rr) < 30:
        return None
    s = {"n": int(len(rr)), "mean": float(rr.mean()), "median": float(np.median(rr)), "p_up": float((rr > 0).mean()), "p_big_up": float((rr >= 0.10).mean()), "p_dn10": float((rr <= -0.10).mean()),
         "p_dn20": float((rr <= -0.20).mean()), "q05": float(np.quantile(rr, 0.05))}
    if extra:
        for k, v in extra.items():
            vv = np.asarray(v, float)[ok]
            s[k] = float(np.nanmean(vv)) if np.isfinite(vv).any() else None
    return s


def _group(E, r, tr, te, specs, extra_keys=("touch20", "giveback", "next_jump60", "next_jump250", "r1", "r60")):
    rows = []
    for name, m in specs:
        m = np.asarray(m, bool)
        if m.sum() < 100:
            continue
        ex = {k: E[k][m] for k in extra_keys}
        row = {"name": name, "n": int(m.sum()), "share": float(m.mean()), "all": _stats(r[m], ex), "tr": _stats(r[m & tr], {k: E[k][m & tr] for k in extra_keys}),
               "te": _stats(r[m & te], {k: E[k][m & te] for k in extra_keys})}
        rows.append(row)
    return rows


def _model(E, X_base, X_hist, y, valid, tr, te, log, t0, name):
    from sklearn.metrics import roc_auc_score
    out = {"target": name, "rows": []}
    r20 = E["r20"]
    for lab, X in (("가격·거래량·시장 특징", X_base), ("+ 이전 급등 내역", np.column_stack([X_base, X_hist]))):
        mtr, mte = tr & valid, te & valid
        mdl = st._fit_fast(X[mtr], y[mtr].astype(int), n_iter=200, seed=1)
        p = mdl.predict_proba(X[mte])[:, 1]
        auc = float(roc_auc_score(y[mte], p))
        thr = np.quantile(p, 0.9)
        top = p >= thr
        bot = p <= np.quantile(p, 0.1)
        row = {"name": lab, "auc": auc, "n_te": int(mte.sum()), "base": float(y[mte].mean()), "top10_hit": float(y[mte][top].mean()), "bot10_hit": float(y[mte][bot].mean()),
               "top10_r20": float(np.nanmean(r20[mte][top])), "bot10_r20": float(np.nanmean(r20[mte][bot])), "top10_up": float(np.nanmean(r20[mte][top] > 0)), "all_up": float(np.nanmean(r20[mte] > 0))}
        # 연도별 상위 10% 적중(시험 기간)
        yrs = pd.Series(E["date"][mte]).str[:4].to_numpy()
        row["yearly"] = [{"year": yv, "n": int((top & (yrs == yv)).sum()), "hit": float(y[mte][top & (yrs == yv)].mean()), "base": float(y[mte][yrs == yv].mean())} for yv in sorted(set(yrs)) if (top & (yrs == yv)).sum() >= 20]
        out["rows"].append(row)
        log(f"[모델 {name}] {lab}: AUC {auc:.3f} · 상위 10% {row['top10_hit'] * 100:.1f}% (기준 {row['base'] * 100:.1f}%) · {time.time() - t0:.0f}초")
        last = (mdl, p, mte, top)
    # 이전 내역 특징의 중요도: 시험 표본에서 열 하나를 섞었을 때 AUC 가 얼마나 떨어지나
    try:
        mdl, p, mte, top = last
        X = np.column_stack([X_base, X_hist])[mte]
        yy = y[mte].astype(int)
        base_auc = roc_auc_score(yy, p)
        rng = np.random.default_rng(0)
        names = BASE_FEATS + HIST_FEATS
        imp = []
        for j, nm in enumerate(names):
            Xp = X.copy()
            Xp[:, j] = rng.permutation(Xp[:, j])
            imp.append((nm, float(base_auc - roc_auc_score(yy, mdl.predict_proba(Xp)[:, 1]))))
        imp.sort(key=lambda x: -x[1])
        out["importance"] = [{"name": nm, "label": LABEL.get(nm, nm), "hist": nm in HIST_FEATS, "gain": g} for nm, g in imp[:14]]
    except Exception:
        out["importance"] = []
    return out, last


def run(workers: int = 0, log=print, limit: int = 0) -> dict:
    t0 = time.time()
    with db.get_conn() as c:
        codes = [r["code"] for r in c.execute("SELECT code FROM prices GROUP BY code HAVING COUNT(*) >= 300 ORDER BY code")]
        names = {r["code"]: r["name"] for r in c.execute("SELECT code,name FROM symbols")}
    if limit:
        codes = codes[::max(len(codes) // limit, 1)][:limit]
    workers = workers or max(1, min(6, (os.cpu_count() or 2) - 2))
    if not bt.MKT_PATH.exists():
        bt.build_market_index(codes, workers)
    parts = []
    with ProcessPoolExecutor(max_workers=workers, initializer=ai._init_worker, initargs=(bt.MKT_PATH,)) as ex:
        for i, out in enumerate(ex.map(_work, codes, chunksize=8), 1):
            if out:
                parts.append(out)
    E = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    del parts
    n = len(E["date"])
    log(f"+{JUMP * 100:.0f}%↑ 급등일 {n:,}건 · {time.time() - t0:.0f}초")
    year = pd.Series(E["date"]).str[:4].astype(int).to_numpy()
    tr_, va_, te = ai._split_masks(E["date"])
    tr = tr_ | va_
    r = E["r20"] - ai.COST
    valid = ~np.isnan(r)
    rep = {"meta": {"generated": datetime.now().isoformat(timespec="seconds"), "jump": JUMP, "events": int(n), "stocks": int(len(set(E["code"]))), "date_min": str(E["date"].min()),
                    "date_max": str(E["date"].max()), "split": {"train_end": ai.VALID_END}, "cost": ai.COST, "liq_min": ai.TRADABLE_VALUE}}
    ex_keys = ("touch20", "giveback", "next_jump60", "next_jump250", "r1", "r60")
    rep["overall"] = {"all": _stats(r[valid], {k: E[k][valid] for k in ex_keys}), "tr": _stats(r[valid & tr], {k: E[k][valid & tr] for k in ex_keys}), "te": _stats(r[valid & te], {k: E[k][valid & te] for k in ex_keys}),
                      "entry_gap": float(np.nanmean(E["entry_gap"])), "entry_gap_med": float(np.nanmedian(E["entry_gap"])), "r1_up": float(np.nanmean(E["r1"] > 0)),
                      "r5": float(np.nanmean(E["r5"])), "dd20": float(np.nanmedian(E["dd20"]))}
    rep["by_year"] = [{"year": int(y), "n": int((year == y).sum()), "r20": float(np.nanmean(r[year == y])), "p_up": float(np.nanmean(r[year == y] > 0)), "touch": float(np.nanmean(E["touch20"][year == y]))} for y in sorted(set(year)) if y >= 2005]
    # 이전 내역별
    hist_specs = [("처음 급등 (250일 안 없음)", E["n_j250"] == 0), ("250일 안 1번", E["n_j250"] == 1), ("250일 안 2번", E["n_j250"] == 2), ("250일 안 3번 이상", E["n_j250"] >= 3),
                  ("최근 20일 안 급등 있음 (연속)", E["n_j20"] >= 1), ("마지막 급등 뒤 5일 이내", E["days_since"] <= 5), ("마지막 급등 뒤 6~20일", (E["days_since"] > 5) & (E["days_since"] <= 20)),
                  ("마지막 급등 뒤 21~120일", (E["days_since"] > 20) & (E["days_since"] <= 120)), ("마지막 급등 뒤 120일 넘음", (E["days_since"] > 120) & (E["days_since"] < 2000)),
                  ("이전 급등의 20일 결과 플러스", E["prev_r20"] > 0), ("이전 급등의 20일 결과 마이너스", E["prev_r20"] <= 0), ("이전 급등 뒤 +20% 더 올랐음", E["prev_touch"] == 1),
                  ("이전 급등 시가 아래로 되돌렸음", E["prev_giveback"] == 1), ("연평균 급등 1회 미만 (조용한 종목)", E["hist_rate"] < 1), ("연평균 급등 3회 이상 (급등 잦은 종목)", E["hist_rate"] >= 3)]
    rep["history"] = _group(E, r, tr, te, hist_specs)
    ctx_specs = [("급등 +20~25%", (E["jump"] >= 0.20) & (E["jump"] < 0.25)), ("급등 +25~29%", (E["jump"] >= 0.25) & (E["jump"] < 0.295)), ("상한가 (+29.5%↑)", E["jump"] >= 0.295),
                 ("종가가 고가 근처 (위 10% 안)", E["close_pos"] >= 0.9), ("윗꼬리 큼 (고점에서 30%↑ 밀림)", E["upper"] >= 0.3), ("시가 갭 +10%↑", E["gap0"] >= 0.10), ("시가 갭 없음 (±2%)", E["gap0"].__abs__() < 0.02),
                 ("거래량 10배↑", E["volratio"] >= 10), ("거래량 3배 미만", E["volratio"] < 3), ("전날도 +10%↑ (연속)", E["prev_day_up"] == 1), ("급등 전 20일 −20%↓", E["ret20_prev"] <= -0.2),
                 ("급등 전 20일 +30%↑", E["ret20_prev"] >= 0.3), ("52주 고점 근처 (−10% 이내)", E["nh250"] >= -0.10), ("52주 고점 −50% 아래", E["nh250"] < -0.5), ("224일선 아래", E["dist224"] < 0),
                 ("주가 1,000원 미만", E["price_level"] < 3), ("주가 10,000원 이상", E["price_level"] >= 4), ("거래대금 50억↑", E["liq"] >= math.log10(5e9)), ("시장 60일 −10%↓", E["mkt_r60"] <= -0.1), ("시장 60일 +10%↑", E["mkt_r60"] >= 0.1)]
    rep["context"] = _group(E, r, tr, te, ctx_specs)
    # 또 급등하나: 이전 횟수별 60·250일 안 재급등 확률(기준 = 처음 급등)
    rep["repeat"] = [{"name": nm, "n": int(np.asarray(m, bool).sum()), "p60": float(np.nanmean(E["next_jump60"][m])), "p250": float(np.nanmean(E["next_jump250"][m])), "r20": float(np.nanmean(r[m]))}
                     for nm, m in hist_specs[:4] + hist_specs[13:15] if np.asarray(m, bool).sum() >= 100]
    # 모델
    Xb = np.column_stack([np.nan_to_num(E[k], nan=np.nan) for k in BASE_FEATS]).astype(np.float32)
    Xh = np.column_stack([E[k] for k in HIST_FEATS]).astype(np.float32)
    models = []
    m1, last1 = _model(E, Xb, Xh, (r > 0).astype(int), valid, tr, te, log, t0, "20일 뒤 플러스 (비용 차감)")
    models.append(m1)
    v2 = ~np.isnan(E["touch20"])
    m2, _ = _model(E, Xb, Xh, (E["touch20"] == 1).astype(int), v2, tr, te, log, t0, "20일 안 +20% 더 상승")
    models.append(m2)
    v3 = ~np.isnan(E["next_jump250"])
    m3, _ = _model(E, Xb, Xh, (E["next_jump250"] == 1).astype(int), v3, tr, te, log, t0, "250일 안 또 +20% 급등")
    models.append(m3)
    rep["models"] = models
    # 오늘: 최근 10거래일 급등 + 이전 내역 + 모델 점수(20일 플러스 확률, 전체 데이터로 다시 학습)
    mdl_all = st._fit_fast(np.column_stack([Xb, Xh])[valid], (r[valid] > 0).astype(int), n_iter=200, seed=1)
    dmax = str(E["date"].max())
    recent_cut = sorted(set(E["date"]))[-10] if len(set(E["date"])) >= 10 else dmax
    rec = np.flatnonzero(E["date"] >= recent_cut)
    P = mdl_all.predict_proba(np.column_stack([Xb, Xh])[rec])[:, 1] if len(rec) else np.array([])
    rows = []
    for i, p in zip(rec, P):
        rows.append({"code": E["code"][i], "name": names.get(E["code"][i], E["code"][i]), "date": E["date"][i], "close": float(E["close"][i]), "jump": float(E["jump"][i]), "prob": float(p),
                     "n_j250": int(E["n_j250"][i]), "days_since": None if E["days_since"][i] >= 2000 else int(E["days_since"][i]), "prev_r20": None if np.isnan(E["prev_r20"][i]) else float(E["prev_r20"][i]),
                     "hist_rate": float(E["hist_rate"][i]), "volratio": float(E["volratio"][i]), "nh250": float(E["nh250"][i]), "close_pos": float(E["close_pos"][i]),
                     "r1": None if np.isnan(E["r1"][i]) else float(E["r1"][i])})
    rows.sort(key=lambda z: (-z["prob"], z["date"]))
    rep["today"] = {"since": recent_cut, "rows": rows[:50], "n": len(rows)}
    rep["text"] = narrate(rep)
    rep["meta"]["elapsed_sec"] = round(time.time() - t0)
    JUMP_PATH.write_text(json.dumps(rep, ensure_ascii=False, default=st._js), encoding="utf-8")
    return rep


def narrate(rep):
    o = rep["overall"]
    a, te = o["all"], o["te"]
    out = [f"하루 +{rep['meta']['jump'] * 100:.0f}% 이상 급등일 {rep['meta']['events']:,}건({rep['meta']['stocks']:,}종목). 다음날 시가는 급등일 종가보다 평균 {o['entry_gap'] * 100:+.1f}%(중앙 {o['entry_gap_med'] * 100:+.1f}%)이고, "
           f"다음날 시가에 사면 20일 뒤 평균 {a['mean'] * 100:+.1f}%(시험 기간 {te['mean'] * 100:+.1f}%), 상승 {a['p_up'] * 100:.0f}%, 20일 안 +20% 더 오른 비율 {a['touch20'] * 100:.0f}%, 급등일 시가 아래로 되돌린 비율 {a['giveback'] * 100:.0f}%, 20일 안 최대 하락 중앙 {o['dd20'] * 100:+.0f}%."]
    h = {x["name"]: x for x in rep["history"]}
    f, m3 = h.get("처음 급등 (250일 안 없음)"), h.get("250일 안 3번 이상")
    if f and m3:
        out.append(f"같은 종목의 이전 내역: 처음 급등은 20일 뒤 {f['all']['mean'] * 100:+.1f}%·상승 {f['all']['p_up'] * 100:.0f}%, 250일 안 3번 이상 급등했던 종목은 {m3['all']['mean'] * 100:+.1f}%·{m3['all']['p_up'] * 100:.0f}%. "
                   f"250일 안에 또 급등할 확률은 처음 {f['all']['next_jump250'] * 100:.0f}% vs 3번 이상 {m3['all']['next_jump250'] * 100:.0f}%.")
    pp, pn = h.get("이전 급등의 20일 결과 플러스"), h.get("이전 급등의 20일 결과 마이너스")
    if pp and pn:
        out.append(f"이전 급등이 20일 뒤 플러스였던 종목은 이번에도 {pp['all']['mean'] * 100:+.1f}%·상승 {pp['all']['p_up'] * 100:.0f}%, 마이너스였던 종목은 {pn['all']['mean'] * 100:+.1f}%·{pn['all']['p_up'] * 100:.0f}%(시험 기간 {pp['te']['p_up'] * 100:.0f}% vs {pn['te']['p_up'] * 100:.0f}%).")
    for mdl in rep["models"]:
        b, w = mdl["rows"]
        out.append(f"모델 '{mdl['target']}': 가격·거래량·시장 특징만 AUC {b['auc']:.3f} → 이전 내역 더하면 {w['auc']:.3f}, 상위 10% 적중 {b['top10_hit'] * 100:.0f}% → {w['top10_hit'] * 100:.0f}%(기준 {w['base'] * 100:.0f}%, 하위 10% {w['bot10_hit'] * 100:.0f}%).")
    ctx = sorted([x for x in rep["context"] if x["tr"] and x["te"]], key=lambda x: -x["te"]["p_up"])
    good = [x for x in ctx if x["tr"]["p_up"] > a["p_up"] and x["te"]["p_up"] > te["p_up"]][:3]
    bad = [x for x in ctx[::-1] if x["tr"]["p_up"] < a["p_up"] and x["te"]["p_up"] < te["p_up"]][:3]
    out.append("급등일 모습 중 두 시기 모두 상승 확률이 높은 것: " + (", ".join(f"{x['name']} {x['te']['p_up'] * 100:.0f}%" for x in good) if good else "없음") + " / 낮은 것: " + (", ".join(f"{x['name']} {x['te']['p_up'] * 100:.0f}%" for x in bad) if bad else "없음") + ".")
    td = rep["today"]
    out.append(f"{td['since']} 이후 급등일 {td['n']}건 — 화면에 종목별 이전 내역과 20일 플러스 확률을 붙였습니다.")
    return out


if __name__ == "__main__":
    run()
