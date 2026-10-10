"""모든 신호를 한꺼번에 써서 '최적의 매수일'을 고를 수 있나.

  ① 통합 점수: 기존 AI 특징(가격·거래량·시장·패턴·기법 신호 76개) + 매집봉·거래량·가격 패턴(35) + 전조 특징(224/112일선·언덕·급등 전력 등) + 거래정지 위험 점수
     → 20일 뒤 시장 대비 플러스 / +20% 를 예측(~2021 학습, 2022~ 시험). 점수 상위 1·5·10%의 실제 성과를 기존 AI(76개)와 비교.
  ② 매수일: 점수 상위 종목에서 '오늘(다음날 시가)' vs '5일 뒤' vs '10일 뒤' vs '눌림 지정가' vs '확인 후' — 그리고 "앞으로 5일 중 시가가 가장 낮은 날"을 미리 알았다면(오라클 상한)
     얼마나 더 벌었을지, 그 날을 맞히는 타이밍 모델(오늘이 5일 중 최저 시가인가)로 얼마나 좁힐 수 있는지.
  ③ 해마다 다시 학습한 걸어가며 검증(2014~), 오늘 점수 상위 + 권장 매수일.
표본은 strategy_study 캐시(연중일 5일 간격, 거래 가능 종목) 행에 종목별 날짜로 특징을 붙인다.
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
from core import ai, avoid_study, combined_study as cs, db, pattern_study as pt, precursor_study as pre, strategy_study as st
from core import backtest as bt

BEST_PATH = config.DATA_DIR / "bestday.json"
EXTRA_PATH = config.DATA_DIR / "bestday_extra.npz"
PRE_KEYS = ["dist112", "ma_below_n", "ma_conv", "slope224", "near224", "above224_days", "below224_days", "low60", "updays10", "upstreak", "gapup5", "n_j250", "days_since", "hist_rate", "hill_gap", "ma5_20"]
PAT_KEYS = pt.PAT
LABEL = {**pre.LABEL, **pt.LABEL, "hill_gap": "직전 언덕 대비", "ma5_20": "5일선 ÷ 20일선 −1", "halt_p": "거래정지 위험 점수", "best5": "앞 5일 중 최저 시가"}
_DATES = {}
WF_START = 2014


def _init(path, dates):
    ai._init_worker(path)
    global _DATES
    _DATES = dates


def _work(code):
    try:
        want = _DATES.get(code)
        if not want:
            return None
        df = ai.service.load_prices(code)
        n = len(df)
        if n < 400:
            return None
        F, jump, c, v, vr = pre._features(df)
        P = pt._patterns(df, F)
        o, h, l = (df[k].to_numpy(float) for k in ("open", "high", "low"))
        s = pd.Series
        ma5, ma20, ma112 = (s(c).rolling(p, min_periods=p).mean().to_numpy() for p in (5, 20, 112))
        with np.errstate(divide="ignore", invalid="ignore"):
            hill = s(h).rolling(60, min_periods=30).max().shift(5).to_numpy()
            ex = {"hill_gap": c / hill - 1, "ma5_20": ma5 / ma20 - 1, "dist112": c / ma112 - 1}
            # 앞으로 5일(t+1..t+5) 시가 중 최저 → 오늘(t+1) 시가가 최저인가, 최저 시가에 샀다면 20일 뒤 수익
            Wo = sliding_window_view(np.r_[o, np.full(6, np.nan)], 5)[1:n + 1]          # o[t+1..t+5]
            mn = np.nanmin(Wo, axis=1)
            argmn = np.nanargmin(np.where(np.isnan(Wo), np.inf, Wo), axis=1)
            exit21 = np.r_[c[21:], np.full(21, np.nan)]
            best5 = (o[np.minimum(np.arange(n) + 1, n - 1)] <= mn * 1.01).astype(float)
            oracle_r20 = exit21 / mn - 1
            best_day = argmn.astype(float) + 1
        dates = df.index.strftime("%Y-%m-%d")
        pos = {d: i for i, d in enumerate(dates)}
        idx = np.array([pos[d] for d in want if d in pos])
        got = [d for d in want if d in pos]
        if len(idx) == 0:
            return None
        out = {"code": np.full(len(idx), code), "date": np.array(got)}
        for k in PRE_KEYS:
            src = ex.get(k, F.get(k))
            out[k] = src[idx].astype(np.float32)
        for k in PAT_KEYS:
            out[k] = P[k][idx].astype(np.float32)
        out["best5"] = best5[idx].astype(np.float32)
        out["best_day"] = best_day[idx].astype(np.float32)
        out["oracle_r20"] = oracle_r20[idx].astype(np.float32)
        return out
    except Exception:
        return None


def _stats(r, ex=None):
    r = np.asarray(r, float)
    ok = ~np.isnan(r)
    rr = r[ok]
    if len(rr) < 30:
        return None
    s = {"n": int(len(rr)), "mean": float(rr.mean()), "p_up": float((rr > 0).mean()), "p_big_up": float((rr >= 0.10).mean()), "p_dn10": float((rr <= -0.10).mean()), "q05": float(np.quantile(rr, 0.05)), "median": float(np.median(rr))}
    if ex is not None:
        s["excess"] = float(np.nanmean(np.asarray(ex, float)[ok]))
    return s


def _collect(codes, code_dates, workers, log, t0):
    parts = []
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(bt.MKT_PATH, code_dates)) as ex_:
        for i, out in enumerate(ex_.map(_work, codes, chunksize=8), 1):
            if out:
                parts.append(out)
            if i % 500 == 0:
                log(f"[특징 {i}/{len(codes)}] · {time.time() - t0:.0f}초")
    E = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    return E


def run(workers: int = 0, log=print, limit: int = 0, fresh: bool = False) -> dict:
    t0 = time.time()
    if not st.ROWS_PATH.exists():
        raise SystemExit("먼저 python manage.py strategy-train 으로 표본 캐시를 만드세요.")
    z = np.load(st.ROWS_PATH, allow_pickle=True)
    X, date, code = z["X"], z["date"], z["code"]
    T = {k: z["T_" + k] for k in st.ROW_KEYS}
    latest = list(z["latest"])
    with db.get_conn() as c:
        names = {r["code"]: r["name"] for r in c.execute("SELECT code,name FROM symbols")}
    codes = sorted(set(code))
    if limit:
        codes = codes[::max(len(codes) // limit, 1)][:limit]
        sel = np.isin(code, codes)
        X, date, code = X[sel], date[sel], code[sel]
        T = {k: v[sel] for k, v in T.items()}
    workers = workers or max(1, min(6, (os.cpu_count() or 2) - 2))
    # 추가 특징(캐시)
    if EXTRA_PATH.exists() and not fresh and not limit:
        ze = np.load(EXTRA_PATH, allow_pickle=True)
        E = {k: ze[k] for k in ze.files}
        log(f"추가 특징 캐시 {len(E['date']):,}행 · {time.time() - t0:.0f}초")
    else:
        code_dates = {}
        for cd, d in zip(code, date):
            code_dates.setdefault(cd, []).append(d)
        # 오늘 행도 특징이 필요
        for r in latest:
            code_dates.setdefault(r["code"], []).append(r["date"])
        E = _collect(codes if limit else sorted(code_dates), code_dates, workers, log, t0)
        if not limit:
            np.savez(EXTRA_PATH, **E)
        log(f"추가 특징 {len(E['date']):,}행 · {time.time() - t0:.0f}초")
    # 캐시 행과 맞추기(code, date)
    key_c = pd.Index(pd.Series(code).astype(str) + "|" + pd.Series(date).astype(str))
    key_e = pd.Series(E["code"]).astype(str) + "|" + pd.Series(E["date"]).astype(str)
    pos = pd.Series(np.arange(len(key_e)), index=key_e)
    pos = pos[~pos.index.duplicated()]
    m_idx = pos.reindex(key_c).to_numpy()
    okm = ~np.isnan(m_idx)
    m_idx = np.where(okm, m_idx, 0).astype(int)
    log(f"맞춘 행 {okm.sum():,}/{len(date):,} · {time.time() - t0:.0f}초")
    extra_cols = PRE_KEYS + PAT_KEYS
    XE = np.column_stack([np.where(okm, E[k][m_idx], np.nan) for k in extra_cols]).astype(np.float32)
    best5 = np.where(okm, E["best5"][m_idx], np.nan)
    best_day = np.where(okm, E["best_day"][m_idx], np.nan)
    oracle = np.where(okm, E["oracle_r20"][m_idx], np.nan)
    # 거래정지 위험 점수
    D = pd.DataFrame(X, columns=ai.NAMES)
    for k in ("zero_vol60", "gapdn20", "down_streak", "close_px", "gap1"):
        D[k] = T[k]
    halt_p = np.full(len(date), np.nan)
    try:
        import joblib
        from core import risk_study
        hm = joblib.load(risk_study.HALT_MODEL_PATH)
        ratio = json.loads(risk_study.RISK_PATH.read_text(encoding="utf-8"))["model"]["ratio"]
        ph = st._predict(hm, risk_study._feat_matrix(D))
        halt_p = ph / (ph + (1 - ph) * ratio)
    except Exception:
        pass
    XALL = np.column_stack([X, XE, halt_p]).astype(np.float32)
    names_all = list(ai.NAMES) + extra_cols + ["halt_p"]
    tr_, va_, te = ai._split_masks(date)
    tr = tr_ | va_
    year = pd.Series(date).str[:4].astype(int).to_numpy()
    r20 = T["r20"].astype(float)
    ok = ~np.isnan(r20) & okm
    r = r20 - ai.COST
    exs = (pd.Series(r20) - pd.Series(r20).groupby(pd.Series(date)).transform("mean")).to_numpy()
    rules = {n: f for n, f in avoid_study.RULES}
    avoid = np.zeros(len(date), bool)
    for nme in cs.AVOID_NAMES:
        avoid |= rules[nme](D).to_numpy()
    mk_under = (D["mkt_dd250"] < -0.10).to_numpy()
    gap_ok = T["gap1"] <= cs.GAP_MAX
    rep = {"meta": {"generated": datetime.now().isoformat(timespec="seconds"), "rows": int(ok.sum()), "features": len(names_all), "split": {"train_end": ai.VALID_END}, "cost": ai.COST}}

    # ---- ① 통합 점수 vs 기존 AI(76)
    from sklearn.metrics import roc_auc_score
    targets = {"시장 대비 플러스 (20일)": (exs > 0), "20일 +20% 이상": (r20 >= 0.20), "20일 플러스 (비용 차감)": (r > 0)}
    models = {}
    comp = []
    for tname, y in targets.items():
        y = y.astype(int)
        for fname, cols in (("기존 AI (76개 특징)", list(range(len(ai.NAMES)))), ("통합 (모든 신호·패턴·전조·위험)", list(range(XALL.shape[1])))):
            idx = st._sub(tr & ok, 500000, seed=1)
            mdl = st._fit_fast(XALL[idx][:, cols], y[idx], n_iter=200, seed=1)
            mte = te & ok
            p = st._predict(mdl, XALL[mte][:, cols])
            auc = float(roc_auc_score(y[mte], p))
            row = {"target": tname, "features": fname, "auc": auc, "base": float(y[mte].mean()), "n_te": int(mte.sum())}
            for q in (0.99, 0.95, 0.90):
                top = p >= np.quantile(p, q)
                row[f"top{int(round((1 - q) * 100))}"] = {"hit": float(y[mte][top].mean()), **(_stats(r[mte][top], exs[mte][top]) or {})}
            comp.append(row)
            if fname.startswith("통합"):
                models[tname] = (mdl, cols)
            log(f"[①] {tname} · {fname}: AUC {auc:.3f} · 상위 10% 평균 {row['top10'].get('mean', 0) * 100:+.2f}% · {time.time() - t0:.0f}초")
    rep["compare"] = comp
    # 메인 점수 = 시장 대비 플러스 통합 모델
    main_t = "시장 대비 플러스 (20일)"
    mdl, cols = models[main_t]
    P = np.full(len(date), np.nan)
    P[ok] = st._predict(mdl, XALL[ok][:, cols])
    thr10 = float(np.nanquantile(P[te & ok], 0.90))
    thr5 = float(np.nanquantile(P[te & ok], 0.95))
    yy = (exs[te & ok] > 0).astype(int)
    rng = np.random.default_rng(0)
    Xte = XALL[te & ok]
    base_auc = roc_auc_score(yy, st._predict(mdl, Xte))
    imp = []
    for j in rng.choice(len(names_all), size=min(60, len(names_all)), replace=False):
        Xp = Xte.copy()
        Xp[:, j] = rng.permutation(Xp[:, j])
        imp.append((names_all[j], float(base_auc - roc_auc_score(yy, st._predict(mdl, Xp)))))
    imp.sort(key=lambda x: -x[1])
    rep["importance"] = [{"name": nm, "label": {**ai.LABEL, **LABEL}.get(nm, nm), "gain": g} for nm, g in imp[:16]]
    # 조건을 더한 점수 상위
    stages = [("통합 점수 상위 10%", P >= thr10), ("+ 피함 제외", (P >= thr10) & ~avoid), ("+ 갭 +3% 이하", (P >= thr10) & ~avoid & gap_ok), ("+ 시장 52주 고점 −10% 아래", (P >= thr10) & ~avoid & gap_ok & mk_under),
              ("+ 거래정지 위험 높음 제외", (P >= thr10) & ~avoid & gap_ok & mk_under & ~(halt_p >= 0.03)), ("통합 점수 상위 5% + 위 조건 전부", (P >= thr5) & ~avoid & gap_ok & mk_under & ~(halt_p >= 0.03))]
    rep["stages"] = [{"name": nm, "tr": _stats(r[m & tr & ok], exs[m & tr & ok]), "te": _stats(r[m & te & ok], exs[m & te & ok]), "per_month_te": float((m & te & ok).sum() / max(len(pd.Series(date[te]).str[:7].unique()), 1))} for nm, m in stages]
    log(f"[①] 단계 · {time.time() - t0:.0f}초")

    # ---- ② 매수일: 점수 상위 10%(+피함 제외+시장 필터)에서 언제 사나
    base_m = (P >= thr10) & ~avoid & mk_under & ok
    def _s(v, m):
        return _stats(np.asarray(v, float)[m] - ai.COST)
    timing_rows = []
    for nm, key in (("오늘 (다음날 시가)", "r20"), ("5일 뒤 시가", "x5"), ("10일 뒤 시가", "x10"), ("3분할 (0·5·10일)", "split3"), ("눌림 지정가 (시가 −3%, 5일)", "pb_ret"), ("확인 후 (5일 안 +2%)", "conf_ret")):
        v = T[key].astype(float)
        cov = float(np.mean(~np.isnan(v[base_m & te])))
        s_tr, s_te = _s(v, base_m & tr), _s(v, base_m & te)
        timing_rows.append({"name": nm, "coverage": cov, "tr": s_tr, "te": s_te, "per_signal_te": float(cov * s_te["mean"]) if s_te else None})
    orc = _stats(oracle[base_m & te] - ai.COST)
    timing_rows.append({"name": "오라클: 앞 5일 중 최저 시가에 샀다면 (실현 불가 상한)", "coverage": 1.0, "tr": _stats(oracle[base_m & tr] - ai.COST), "te": orc, "per_signal_te": orc["mean"] if orc else None})
    rep["timing"] = timing_rows
    rep["best_day_dist"] = [{"day": int(d), "share": float(np.nanmean(best_day[base_m & te] == d))} for d in range(1, 6)]
    rep["best5_rate"] = float(np.nanmean(best5[base_m & te]))
    # 타이밍 모델: 오늘이 앞 5일 중 최저 시가인가(모든 표본으로 학습)
    yb = best5
    vb = ok & ~np.isnan(yb)
    idx = st._sub(tr & vb, 500000, seed=2)
    tm = st._fit_fast(XALL[idx], yb[idx].astype(int), n_iter=200, seed=2)
    pb = np.full(len(date), np.nan)
    pb[vb] = st._predict(tm, XALL[vb])
    auc_b = float(roc_auc_score(yb[te & vb].astype(int), pb[te & vb]))
    # 규칙: 타이밍 점수 높으면 오늘, 낮으면 5일 뒤 / 눌림 지정가
    thr_b = float(np.nanquantile(pb[te & vb], 0.5))
    choose = []
    for nm, alt in (("타이밍 점수 상위 절반은 오늘, 나머지는 5일 뒤 시가", "x5"), ("타이밍 점수 상위 절반은 오늘, 나머지는 눌림 지정가(미체결이면 5일 뒤)", "pbx5")):
        if alt == "pbx5":
            altv = np.where(np.isnan(T["pb_ret"].astype(float)), T["x5"].astype(float), T["pb_ret"].astype(float))
        else:
            altv = T[alt].astype(float)
        v = np.where(pb >= thr_b, r20, altv)
        choose.append({"name": nm, "tr": _s(v, base_m & tr), "te": _s(v, base_m & te)})
    hi = base_m & te & (pb >= np.nanquantile(pb[te & vb], 0.8))
    lo = base_m & te & (pb <= np.nanquantile(pb[te & vb], 0.2))
    rep["timing_model"] = {"auc": auc_b, "base_best5": float(np.nanmean(yb[te & vb])), "hi_best5": float(np.nanmean(yb[hi])), "lo_best5": float(np.nanmean(yb[lo])),
                           "hi_today": _s(r20, hi), "hi_x5": _s(T["x5"], hi), "lo_today": _s(r20, lo), "lo_x5": _s(T["x5"], lo), "rules": choose}
    log(f"[②] 타이밍 모델 AUC {auc_b:.3f} · {time.time() - t0:.0f}초")

    # ---- ③ 걸어가며 검증(통합 점수 상위 10% + 피함 제외 + 시장 필터)
    y_main = (exs > 0).astype(int)
    wf = []
    oos = np.zeros(len(date), bool)
    oos_base = np.zeros(len(date), bool)
    for Y in sorted(y for y in set(year) if y >= WF_START):
        past, cur = (year < Y) & ok, (year == Y) & ok
        if past.sum() < 50000 or cur.sum() < 500:
            continue
        idx = st._sub(past, 400000, seed=Y)
        m_ = st._fit_fast(XALL[idx], y_main[idx], n_iter=150, seed=Y)
        p = st._predict(m_, XALL[cur])
        top = np.zeros(len(date), bool)
        top[np.flatnonzero(cur)[p >= np.quantile(p, 0.9)]] = True
        f = top & ~avoid & mk_under
        oos |= f
        oos_base |= cur & ~avoid & mk_under
        wf.append({"year": int(Y), "final": _stats(r[f], exs[f]), "base": _stats(r[cur & ~avoid & mk_under], exs[cur & ~avoid & mk_under])})
        log(f"[③] {Y}: 상위 10% {wf[-1]['final']['p_up'] * 100 if wf[-1]['final'] else float('nan'):.0f}% (기준 {wf[-1]['base']['p_up'] * 100 if wf[-1]['base'] else float('nan'):.0f}%) · {time.time() - t0:.0f}초")
    rep["walk"] = {"rows": wf, "total": {"final": _stats(r[oos], exs[oos]), "base": _stats(r[oos_base], exs[oos_base])}, "years_better": int(sum(1 for x in wf if x["final"] and x["base"] and x["final"]["p_up"] > x["base"]["p_up"])), "years": len(wf)}

    # ---- 오늘
    rep["today"] = _today(latest, E, names, mdl, cols, tm, rules, halt_model=None, thr10=thr10, thr_b=thr_b)
    rep["text"] = narrate(rep)
    rep["meta"]["elapsed_sec"] = round(time.time() - t0)
    BEST_PATH.write_text(json.dumps(rep, ensure_ascii=False, default=st._js), encoding="utf-8")
    return rep


def _today(latest, E, names, mdl, cols, tm, rules, halt_model, thr10, thr_b, top=50):
    L = [x for x in latest if x["ok"] and np.isfinite(x["X"][ai.NAMES.index("ret60")]) and x["X"][ai.NAMES.index("liq")] >= math.log10(ai.TRADABLE_VALUE)]
    if not L:
        return {"date": None, "rows": []}
    key_e = pd.Series(E["code"]).astype(str) + "|" + pd.Series(E["date"]).astype(str)
    pos = pd.Series(np.arange(len(key_e)), index=key_e)
    pos = pos[~pos.index.duplicated()]
    keys = [f"{x['code']}|{x['date']}" for x in L]
    m_idx = pos.reindex(keys).to_numpy()
    okm = ~np.isnan(m_idx)
    L = [x for x, o_ in zip(L, okm) if o_]
    m_idx = m_idx[okm].astype(int)
    if not L:
        return {"date": None, "rows": []}
    XL = np.vstack([x["X"] for x in L])
    D = pd.DataFrame(XL, columns=ai.NAMES)
    for k in ("zero_vol60", "gapdn20", "down_streak", "close_px"):
        D[k] = [x[k] for x in L]
    D["gap1"] = np.nan
    avoid = np.zeros(len(L), bool)
    for nme in cs.AVOID_NAMES:
        avoid |= rules[nme](D).fillna(False).to_numpy()
    halt_p = np.full(len(L), np.nan)
    try:
        import joblib
        from core import risk_study
        hm = joblib.load(risk_study.HALT_MODEL_PATH)
        ratio = json.loads(risk_study.RISK_PATH.read_text(encoding="utf-8"))["model"]["ratio"]
        ph = hm.predict_proba(risk_study._feat_matrix(D))[:, 1]
        halt_p = ph / (ph + (1 - ph) * ratio)
    except Exception:
        pass
    XE = np.column_stack([E[k][m_idx] for k in PRE_KEYS + PAT_KEYS]).astype(np.float32)
    XA = np.column_stack([XL, XE, halt_p]).astype(np.float32)
    P = st._predict(mdl, XA[:, cols])
    PB = st._predict(tm, XA)
    try:
        mk_dd = float(pd.read_pickle(bt.MKT_PATH)["mkt_dd250"].dropna().iloc[-1])
    except Exception:
        mk_dd = float(np.nanmedian(D["mkt_dd250"]))
    date = max(x["date"] for x in L)
    order = np.argsort(-P)
    rows = []
    for i in order:
        if L[i]["date"] != date or P[i] < thr10:
            continue
        g = lambda k: float(D[k].iloc[i])  # noqa: E731
        rows.append({"code": L[i]["code"], "name": names.get(L[i]["code"], L[i]["code"]), "close": L[i]["close"], "score": float(P[i]), "timing": float(PB[i]), "buy_today": bool(PB[i] >= thr_b),
                     "avoid": bool(avoid[i]), "halt_p": None if np.isnan(halt_p[i]) else float(halt_p[i]), "ret20": g("ret20"), "nh250": g("nh250"), "atrp": g("atrp"), "dist20": g("dist20"),
                     "signals": [ai.SIGNAL_LABELS[j] for j in range(len(ai.SIGNAL_LABELS)) if XL[i, ai.NAMES.index(f"sig{j:02d}")] <= 3][:4],
                     "patterns": [pt.LABEL[k] for k in PAT_KEYS if k not in ("acc_60n", "acc_since", "ma_conv", "range20") and E[k][m_idx[i]] == 1][:4]})
        if len(rows) >= top:
            break
    return {"date": date, "n": len(L), "mkt_dd250": mk_dd, "mkt_under": bool(mk_dd < -0.10), "thr10": thr10, "rows": rows}


def narrate(rep):
    out = []
    cmp_ = {(x["target"], x["features"]): x for x in rep["compare"]}
    a = cmp_.get(("시장 대비 플러스 (20일)", "기존 AI (76개 특징)"))
    b = cmp_.get(("시장 대비 플러스 (20일)", "통합 (모든 신호·패턴·전조·위험)"))
    if a and b:
        out.append(f"모든 신호를 합친 통합 점수(특징 {rep['meta']['features']}개)로 '20일 뒤 시장보다 오를지'를 맞히는 AUC {b['auc']:.3f}(기존 AI {a['auc']:.3f}). 시험 기간 상위 10%의 20일 평균 {b['top10']['mean'] * 100:+.2f}%·상승 {b['top10']['p_up'] * 100:.0f}%(기존 {a['top10']['mean'] * 100:+.2f}%·{a['top10']['p_up'] * 100:.0f}%), 상위 1% {b['top1']['mean'] * 100:+.2f}%·{b['top1']['p_up'] * 100:.0f}%.")
    st_ = rep["stages"]
    f, l = st_[0], st_[-1]
    if f["te"] and l["te"]:
        out.append(f"조건을 더하면(시험 기간): 상위 10% 상승 {f['te']['p_up'] * 100:.0f}%·평균 {f['te']['mean'] * 100:+.2f}% → 피함 제외·갭·시장 필터·거래정지 위험 제외 + 상위 5%는 상승 {l['te']['p_up'] * 100:.0f}%·평균 {l['te']['mean'] * 100:+.2f}%(한 달 {l['per_month_te']:.0f}건).")
    tm = {x["name"]: x for x in rep["timing"]}
    today, orc = tm.get("오늘 (다음날 시가)"), next((x for x in rep["timing"] if x["name"].startswith("오라클")), None)
    if today and orc and today["te"] and orc["te"]:
        out.append(f"점수 상위 종목에서 매수일: 오늘(다음날 시가) {today['te']['mean'] * 100:+.2f}%·상승 {today['te']['p_up'] * 100:.0f}%. 앞으로 5일 중 가장 싼 시가에 샀다면(오라클, 실현 불가) {orc['te']['mean'] * 100:+.2f}%·{orc['te']['p_up'] * 100:.0f}% — 매수일을 완벽히 고르면 얻을 수 있는 최대치가 {(orc['te']['mean'] - today['te']['mean']) * 100:+.2f}%p.")
        best_real = max([x for x in rep["timing"] if not x["name"].startswith("오라클") and x["te"]], key=lambda x: x["per_signal_te"] or -9)
        out.append(f"실제로 쓸 수 있는 매수일 중 신호당 기대가 가장 큰 것: {best_real['name']} {best_real['te']['mean'] * 100:+.2f}%·상승 {best_real['te']['p_up'] * 100:.0f}%(체결 {best_real['coverage'] * 100:.0f}%).")
    tmd = rep["timing_model"]
    out.append(f"'오늘이 앞 5일 중 최저 시가인가'를 맞히는 타이밍 모델 AUC {tmd['auc']:.3f}: 점수 상위 20%는 실제 최저 {tmd['hi_best5'] * 100:.0f}%(기준 {tmd['base_best5'] * 100:.0f}%), 하위 20%는 {tmd['lo_best5'] * 100:.0f}%. "
               f"상위 20%는 오늘 사면 {tmd['hi_today']['mean'] * 100:+.2f}% vs 5일 뒤 {tmd['hi_x5']['mean'] * 100:+.2f}%, 하위 20%는 오늘 {tmd['lo_today']['mean'] * 100:+.2f}% vs 5일 뒤 {tmd['lo_x5']['mean'] * 100:+.2f}%." if tmd["hi_today"] and tmd["hi_x5"] and tmd["lo_today"] and tmd["lo_x5"] else "")
    for rr in tmd["rules"]:
        if rr["te"] and today and today["te"]:
            out.append(f"규칙 '{rr['name']}': 시험 {rr['te']['mean'] * 100:+.2f}%·상승 {rr['te']['p_up'] * 100:.0f}% (항상 오늘 {today['te']['mean'] * 100:+.2f}%·{today['te']['p_up'] * 100:.0f}%).")
    w = rep["walk"]["total"]
    if w.get("final") and w.get("base"):
        out.append(f"해마다 다시 학습한 걸어가며 검증({WF_START}~): 통합 점수 상위 10%(피함 제외·시장 필터) 평균 {w['final']['mean'] * 100:+.2f}%·상승 {w['final']['p_up'] * 100:.0f}% vs 같은 조건 전체 {w['base']['mean'] * 100:+.2f}%·{w['base']['p_up'] * 100:.0f}% ({rep['walk']['years_better']}/{rep['walk']['years']}년 우세).")
    td = rep["today"]
    if td.get("date"):
        out.append(f"{td['date']} 통합 점수 상위 10% 종목 {len(td['rows'])}개(시장 52주 고점 대비 {td['mkt_dd250'] * 100:+.1f}% → 시장 필터 {'켜짐' if td['mkt_under'] else '꺼짐'}), 종목마다 '오늘 사기 / 기다리기' 타이밍 판정을 붙였습니다.")
    return out


if __name__ == "__main__":
    run()
