"""전략 보강 — 종합 전략(combined_study) 위에 상승 확률을 더 올릴 수 있는지 하나씩 검증.

  A. 거래정지(거래량 0 이 20일↑ 이어짐) 종목 분석 — 상장폐지 대신 '거래정지에 들어갈 확률'로 생존 편향을 가늠
  B. 시장 타이밍 — 시장 상황별로 규칙을 켜고 끌 때
  C. 걸어가며 검증 — 해마다 그 해까지 데이터로 신호·AI 를 다시 고르고 다음 해만 시험
  D. 청산 규칙 — 보유 기간·목표가·트레일링·손절·20일선 이탈 비교
  E. 포트폴리오 시뮬레이션 — 동시 보유 상한·변동성 역가중 자산 곡선
  F. 종목 특성별(거래대금·가격대·위치·시장) 효과
  G. 조용한 매집 검증 — 가격은 제자리인데 거래량·OBV 가 느는 구간 뒤
  H. AI 점검 — 캘리브레이션·순위 학습·기간 앙상블·해마다 재학습
  I. 실패 원인 — 최종 규칙에서 크게 진 진입의 공통점 → 새 피함 후보
  J. 계절성 — 월·요일·월중 위치
  K. 보강한 최종 규칙 — 위에서 두 시기 모두 좋았던 것만 더해 상승 확률 변화
표본·진입·비용은 combined_study 와 같다(연중일 5일 간격, 다음날 시가, 20거래일, 비용 0.3%).
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
from core import ai, avoid_study, combined_study as cs, db
from core import backtest as bt

STRATEGY_PATH = config.DATA_DIR / "strategy.json"
ROWS_PATH = config.DATA_DIR / "strategy_rows.npz"
COST = ai.COST
HALT_MIN = 20                     # 거래량 0 이 이 봉 수 이상 이어지면 '거래정지'(사용자 지정 — 5일은 거래 없는 날이 섞임)
HALT_LONG = 60                    # 장기 거래정지
EXIT_KEYS = ["r10", "r40", "tgt10", "tgt10_d", "tgt20", "tgt20_d", "trail10", "trail10_d", "stop7", "stop10", "stop15", "ma20x", "ma20x_d", "ts20_10", "ts20_10_d",
             "surge60", "halt60", "halt120"]
WF_START = 2010
AI_SUB = 500000


# ------------------------------------------------------------------ 워커
def _exit_variants(df, t):
    """표본 행(t, 신호일)마다 청산 규칙별 수익(비용 전)과 보유일. 진입 e=t+1 시가, 창 60봉."""
    o, h, l, c, v = (df[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
    n, W = len(c), 60
    m = len(t)
    res = {k: np.full(m, np.nan) for k in EXIT_KEYS}
    e = t + 1
    ok = (e + W) < n
    if not ok.any():
        return res
    ee = e[ok]
    Wo, Wh, Wl, Wc = (sliding_window_view(a, W)[ee] for a in (o, h, l, c))
    entry = o[ee]
    rows = np.arange(len(ee))

    def first_hit(mask):
        anyh = mask.any(axis=1)
        return anyh, mask.argmax(axis=1)

    res["r10"][ok] = Wc[:, 9] / entry - 1
    res["r40"][ok] = Wc[:, 39] / entry - 1
    for x, key in ((0.10, "tgt10"), (0.20, "tgt20")):
        anyh, d = first_hit(Wh[:, :20] >= entry[:, None] * (1 + x))
        px = np.where(anyh, np.maximum(entry * (1 + x), Wo[rows, d]), Wc[:, 19])      # 갭으로 넘겼으면 시가
        res[key][ok] = px / entry - 1
        res[key + "_d"][ok] = np.where(anyh, d + 1, 20)
    cm = np.maximum.accumulate(Wc, axis=1)
    anyh, d = first_hit(Wc <= cm * 0.90)
    res["trail10"][ok] = np.where(anyh, Wc[rows, d], Wc[:, -1]) / entry - 1
    res["trail10_d"][ok] = np.where(anyh, d + 1, W)
    for s, key in ((0.07, "stop7"), (0.10, "stop10"), (0.15, "stop15")):
        anyh, d = first_hit(Wl[:, :20] <= entry[:, None] * (1 - s))
        px = np.where(anyh, np.minimum(entry * (1 - s), Wo[rows, d]), Wc[:, 19])
        res[key][ok] = px / entry - 1
    ma20 = pd.Series(c).rolling(20).mean().to_numpy()
    Wm = sliding_window_view(ma20, W)[ee]
    anyh, d = first_hit(Wc < Wm)
    res["ma20x"][ok] = np.where(anyh, Wc[rows, d], Wc[:, -1]) / entry - 1
    res["ma20x_d"][ok] = np.where(anyh, d + 1, W)
    # 목표 +20% / 손절 −10% 를 60일 안에(같은 날이면 손절 우선), 못 닿으면 60일 종가
    th = Wh >= entry[:, None] * 1.20
    ts = Wl <= entry[:, None] * 0.90
    anyt, dt = first_hit(th)
    anys, ds = first_hit(ts)
    stop_first = anys & (~anyt | (ds <= dt))
    px = np.where(stop_first, np.minimum(entry * 0.90, Wo[rows, ds]), np.where(anyt, np.maximum(entry * 1.20, Wo[rows, dt]), Wc[:, -1]))
    res["ts20_10"][ok] = px / entry - 1
    res["ts20_10_d"][ok] = np.where(stop_first, ds + 1, np.where(anyt, dt + 1, W))
    # 이후 60일 안 거래량 3배↑ 급증이 있었나 / 거래정지 시작이 있었나
    a20 = pd.Series(v).rolling(20).mean().shift().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        surge = (v / a20 >= 3).astype(float)
    res["surge60"][ok] = sliding_window_view(surge, W)[ee].max(axis=1)
    hs = _halt_starts(v)
    hs120 = np.r_[hs, np.zeros(120, bool)]
    res["halt60"][ok] = sliding_window_view(hs120, 60)[ee].any(axis=1).astype(float)
    res["halt120"][ok] = sliding_window_view(hs120, 120)[ee].any(axis=1).astype(float)
    return res


def _halt_starts(v):
    """거래량 0 이 HALT_MIN 봉 이상 이어지는 구간의 첫 봉."""
    z = (v <= 0).astype(int)
    run = pd.Series(z).groupby((pd.Series(z).diff() != 0).cumsum()).cumsum().to_numpy()   # 연속 0 길이(누적)
    n = len(v)
    start = np.zeros(n, bool)
    hit = np.flatnonzero((z == 1) & (run == HALT_MIN))
    start[np.maximum(hit - (HALT_MIN - 1), 0)] = True
    return start


def _halt_episodes(df, code):
    """거래정지 구간: 시작 전날 모습(20일 등락·거래량 배수·가격·52주 위치·거래대금)과 재개 뒤 수익."""
    c, v = df["close"].to_numpy(float), df["volume"].to_numpy(float)
    n = len(c)
    start = np.flatnonzero(_halt_starts(v))
    if not len(start):
        return []
    hh250 = pd.Series(df["high"].to_numpy(float)).rolling(250, min_periods=120).max().to_numpy()
    a20 = pd.Series(v).rolling(20).mean().shift().to_numpy()
    val = pd.Series(c * v).rolling(20).mean().to_numpy()
    dates = df.index.strftime("%Y-%m-%d").to_numpy()
    out = []
    for s in start:
        if s < 60:
            continue
        end = s
        while end < n and v[end] <= 0:
            end += 1
        length = end - s
        p = s - 1
        with np.errstate(divide="ignore", invalid="ignore"):
            row = {"code": code, "date": dates[s], "year": int(dates[s][:4]), "length": int(length), "ret20": float(c[p] / c[p - 20] - 1), "ret5": float(c[p] / c[p - 5] - 1),
                   "nh250": float(c[p] / hh250[p] - 1) if np.isfinite(hh250[p]) else np.nan, "price": float(c[p]), "liq": float(np.log10(val[p] + 1)) if np.isfinite(val[p]) else np.nan,
                   "volratio": float(v[p] / a20[p]) if np.isfinite(a20[p]) and a20[p] > 0 else np.nan,
                   "resume": float(c[end] / c[p] - 1) if end < n else np.nan, "resume20": float(c[min(end + 20, n - 1)] / c[p] - 1) if end + 20 < n else np.nan,
                   "ongoing": bool(end >= n)}
        out.append(row)
    return out


def _work(code):
    try:
        res = ai.build_rows(code, with_signals=True, extras=True)
        if res is None:
            return None
        df = ai.service.load_prices(code)
        t = res["t"].astype(int)
        res.update(_exit_variants(df, t))
        res["halts"] = _halt_episodes(df, code)
        return res
    except Exception:
        return None


# ------------------------------------------------------------------ 표본 모으기(캐시)
ROW_KEYS = cs.KEYS + EXIT_KEYS + ["r5", "r250", "mx60", "x5", "x10", "conf_ret", "pb_ret"]


def collect(codes, workers, log, t0):
    parts, latest, halts = [], [], []
    with ProcessPoolExecutor(max_workers=workers, initializer=ai._init_worker, initargs=(bt.MKT_PATH,)) as ex_:
        for i, out in enumerate(ex_.map(_work, codes, chunksize=8), 1):
            if out:
                latest.append(out["latest"])
                halts += out["halts"]
                if len(out["date"]):
                    parts.append(out)
            if i % 400 == 0:
                log(f"[표본 {i}/{len(codes)}] {sum(len(p['date']) for p in parts):,}행 · {time.time() - t0:.0f}초")
    X = np.concatenate([p["X"] for p in parts])
    date = np.concatenate([p["date"] for p in parts])
    code = np.concatenate([p["code"] for p in parts])
    T = {k: np.concatenate([p[k] for p in parts]).astype(np.float32) for k in ROW_KEYS}
    return X, date, code, T, latest, pd.DataFrame(halts)


def _stats(r, ex=None):
    r = np.asarray(r, float)
    ok = ~np.isnan(r)
    rr = r[ok]
    if len(rr) < 30:
        return None
    s = {"n": int(len(rr)), "mean": float(rr.mean()), "p_up": float((rr > 0).mean()), "p_big_up": float((rr >= 0.10).mean()), "p_dn10": float((rr <= -0.10).mean()),
         "p_dn20": float((rr <= -0.20).mean()), "q05": float(np.quantile(rr, 0.05)), "std": float(rr.std()), "median": float(np.median(rr))}
    if ex is not None:
        s["excess"] = float(np.nanmean(np.asarray(ex, float)[ok]))
    return s


def _both(name, m, r, ex, tr, te, extra=None):
    row = {"name": name, "n": int(m.sum()), "all": _stats(r[m], ex[m]), "tr": _stats(r[m & tr], ex[m & tr]), "te": _stats(r[m & te], ex[m & te])}
    a, b = row["tr"], row["te"]
    row["robust"] = bool(a and b and a["n"] >= 200 and b["n"] >= 200 and a["excess"] > 0 and b["excess"] > 0)
    if extra:
        row.update(extra)
    return row


def _fit_fast(X, y, n_iter=150, seed=0):
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(max_iter=n_iter, learning_rate=0.08, max_leaf_nodes=31, min_samples_leaf=200, l2_regularization=2.0,
                                          early_stopping=False, random_state=seed).fit(X, y)


def _sub(idx, cap, seed=0):
    idx = np.flatnonzero(idx)
    if len(idx) <= cap:
        return idx
    return np.random.default_rng(seed).choice(idx, cap, replace=False)


def _predict(model, X, step=200000):
    P = np.full(len(X), np.nan)
    for s in range(0, len(X), step):
        P[s:s + step] = model.predict_proba(X[s:s + step])[:, 1]
    return P


# ------------------------------------------------------------------ 본 분석
def run(workers: int = 0, log=print, limit: int = 0, fresh: bool = False) -> dict:
    t0 = time.time()
    with db.get_conn() as c:
        codes = [r["code"] for r in c.execute("SELECT code FROM prices GROUP BY code HAVING COUNT(*) >= ? ORDER BY code", (ai.MIN_BARS,))]
        names = {r["code"]: r["name"] for r in c.execute("SELECT code,name FROM symbols")}
        mkt_of = {r["code"]: (r["market"] or "") for r in c.execute("SELECT code,market FROM symbols")}
    if limit:
        codes = codes[::max(len(codes) // limit, 1)][:limit]
    workers = workers or max(1, min(6, (os.cpu_count() or 2) - 2))
    if not bt.MKT_PATH.exists():
        bt.build_market_index(codes, workers)
    cache_ok = ROWS_PATH.exists() and not fresh and not limit
    if cache_ok:
        z = np.load(ROWS_PATH, allow_pickle=True)
        X, date, code = z["X"], z["date"], z["code"]
        T = {k: z["T_" + k] for k in ROW_KEYS}
        halts = pd.DataFrame(json.loads(str(z["halts"])))
        latest = list(z["latest"])
        log(f"캐시 표본 {len(date):,}행 사용 · {time.time() - t0:.0f}초")
    else:
        X, date, code, T, latest, halts = collect(codes, workers, log, t0)
        if not limit:
            np.savez(ROWS_PATH, X=X, date=date, code=code, halts=json.dumps(halts.to_dict("records"), ensure_ascii=False), latest=np.array(latest, dtype=object),
                     **{"T_" + k: v for k, v in T.items()})
        log(f"표본 {len(date):,}행 · {time.time() - t0:.0f}초")
    ix = {n: i for i, n in enumerate(ai.NAMES)}
    D = pd.DataFrame(X, columns=ai.NAMES)
    for k in ("zero_vol60", "gapdn20", "down_streak", "close_px", "gap1"):
        D[k] = T[k]
    year = pd.Series(date).str[:4].astype(int).to_numpy()
    tr_, va_, te = ai._split_masks(date)
    tr = tr_ | va_
    r20 = T["r20"].astype(float)
    ok = ~np.isnan(r20)
    r = r20 - COST
    ex = (pd.Series(r20) - pd.Series(r20).groupby(pd.Series(date)).transform("mean")).to_numpy()
    mkt = pd.read_pickle(bt.MKT_PATH)
    level = (1 + mkt["mkt_ret"].fillna(0.0)).cumprod()
    mk_ma = {p: (level > level.rolling(p).mean()).reindex(date).to_numpy() for p in (60, 120, 200)}
    market = np.array([mkt_of.get(c_, "") for c_ in code])

    # 종합 규칙 재현
    rules = {n: f for n, f in avoid_study.RULES}
    avoid = np.zeros(len(date), bool)
    for n in cs.AVOID_NAMES:
        avoid |= rules[n](D).to_numpy()
    keep = ok & ~avoid
    gap_ok = T["gap1"] <= cs.GAP_MAX
    cands = {n: m for n, m, _ in cs.candidates(D)}
    try:
        chosen = json.loads(cs.COMBINED_PATH.read_text(encoding="utf-8"))["chosen"]
    except (OSError, ValueError, KeyError):
        chosen = []
    chosen = [n for n in chosen if n in cands] or ["52주 신고가", "정배열 신호"]
    sig_any = np.zeros(len(date), bool)
    for n in chosen:
        sig_any |= cands[n]
    final_sig = keep & sig_any & gap_ok
    model = cs._load_model()
    P = _predict(model, X) if model is not None else np.full(len(date), np.nan)
    ai_thr = float(np.nanquantile(P[te], 1 - cs.AI_TOP)) if model is not None else np.nan
    ai_top = P >= ai_thr if model is not None else np.zeros(len(date), bool)
    final_ai = keep & gap_ok & ai_top
    rep = {"meta": {"generated": datetime.now().isoformat(timespec="seconds"), "rows": int(len(date)), "stocks": int(len(set(code))), "cost": COST,
                    "split": {"train_end": ai.VALID_END, "date_min": str(date.min()), "date_max": str(date.max())}, "chosen": chosen, "ai": model is not None,
                    "halt_min": HALT_MIN, "gap_max": cs.GAP_MAX, "ai_top": cs.AI_TOP, "cached": bool(cache_ok)}}
    log(f"규칙 재현: 신호 규칙 {final_sig.sum():,}행 · AI 규칙 {final_ai.sum():,}행 · {time.time() - t0:.0f}초")

    rep["halt"] = _halt_study(halts, T, D, keep, final_sig, final_ai, avoid, ok, tr, te, rules)
    rep["market"] = _market_study(D, mk_ma, final_sig, final_ai, ok, r, ex, tr, te, keep & gap_ok)
    log(f"[A·B] 거래정지·시장 타이밍 · {time.time() - t0:.0f}초")
    rep["exits"] = _exit_study(T, final_sig, final_ai, ok, tr, te)
    rep["traits"] = _trait_study(D, market, final_sig, final_ai, r, ex, tr, te)
    rep["quiet"] = _quiet_study(D, T, ok, keep, r, ex, tr, te)
    rep["season"] = _season_study(date, final_sig, final_ai, ok, r, ex, tr, te)
    rep["fail"] = _fail_study(D, T, final_ai if model is not None else final_sig, r, ex, tr, te)
    log(f"[D·F·G·I·J] · {time.time() - t0:.0f}초")
    rep["walk"] = _walk_forward(X, D, T, date, year, code, keep, gap_ok, cands, r, ex, log, t0)
    rep["ai_check"] = _ai_study(X, T, P, ai_top, keep, gap_ok, r, ex, tr, te, log, t0)
    rep["improve"] = _improve(rep, D, T, mk_ma, date, final_ai if model is not None else final_sig, keep, gap_ok, ai_top, sig_any, r, ex, tr, te)
    rep["portfolio"] = _portfolio(rep, D, T, P, date, code, names, keep, gap_ok, ai_top, mk_ma, te)
    rep["improve"].pop("_mask", None)
    rep["text"] = narrate(rep)
    rep["meta"]["elapsed_sec"] = round(time.time() - t0)
    STRATEGY_PATH.write_text(json.dumps(rep, ensure_ascii=False, default=_js), encoding="utf-8")
    return rep


def _js(o):
    if isinstance(o, (np.floating, np.integer)):
        return None if isinstance(o, np.floating) and not np.isfinite(o) else o.item()
    if isinstance(o, np.bool_):
        return bool(o)
    raise TypeError(str(type(o)))


# ---- A. 거래정지
def _halt_study(H: pd.DataFrame, T, D, keep, final_sig, final_ai, avoid, ok, tr, te, rules):
    out = {"n": int(len(H))}
    if not len(H):
        return out
    H = H[H["length"] >= HALT_MIN]
    out["n"] = int(len(H))
    out["long"] = int((H["length"] >= HALT_LONG).sum())
    out["ongoing"] = int(H["ongoing"].sum())
    out["by_year"] = [{"year": int(y), "n": int(len(g)), "long": int((g["length"] >= HALT_LONG).sum())} for y, g in H.groupby("year") if y >= 2000]
    rs = H["resume"].dropna()
    out["resume"] = {"n": int(len(rs)), "mean": float(rs.mean()), "median": float(rs.median()), "p_dn20": float((rs <= -0.2).mean()), "p_dn50": float((rs <= -0.5).mean()),
                     "q05": float(rs.quantile(0.05)), "p_up": float((rs > 0).mean())}
    rs20 = H["resume20"].dropna()
    out["resume20"] = {"n": int(len(rs20)), "mean": float(rs20.mean()), "median": float(rs20.median()), "p_dn20": float((rs20 <= -0.2).mean())}
    long_rs = H.loc[H["length"] >= HALT_LONG, "resume"].dropna()
    out["resume_long"] = {"n": int(len(long_rs)), "mean": float(long_rs.mean()), "median": float(long_rs.median()), "p_dn20": float((long_rs <= -0.2).mean())} if len(long_rs) >= 30 else None
    # 정지 전 모습
    specs = [("20일 −20%↓ 급락 뒤", H["ret20"] <= -0.2), ("20일 +30%↑ 급등 뒤", H["ret20"] >= 0.3), ("주가 1,000원 미만", H["price"] < 1000),
             ("52주 고점 −70%↓", H["nh250"] <= -0.7), ("거래대금 20일 평균 1억 미만", H["liq"] < 8), ("마지막 날 거래량 3배↑", H["volratio"] >= 3)]
    out["before"] = [{"name": n, "share": float(m.fillna(False).mean()), "n": int(m.fillna(False).sum())} for n, m in specs]
    # 표본 행 기준: 120일 안 거래정지 확률(기준·피함 해당·규칙 통과)
    h120 = T["halt120"].astype(float)
    v = ~np.isnan(h120) & ok
    rows = []
    for name, m in (("거래 가능 종목 아무 날", ok), ("피함 규칙 해당", avoid & ok), ("피함 제외", keep), ("신호 규칙 통과", final_sig), ("AI 규칙 통과", final_ai)):
        mm = m & v
        if mm.sum() >= 100:
            rows.append({"name": name, "n": int(mm.sum()), "p120": float(h120[mm].mean()), "p60": float(np.nanmean(T["halt60"][mm])),
                         "p120_te": float(h120[mm & te].mean()) if (mm & te).sum() >= 100 else None})
    out["prob"] = rows
    base = h120[v].mean()
    rr = []
    for name, fn in avoid_study.RULES:
        m = fn(D).to_numpy() & v
        if m.sum() >= 500:
            rr.append({"name": name, "n": int(m.sum()), "p120": float(h120[m].mean()), "lift": float(h120[m].mean() / base) if base > 0 else None})
    rr.sort(key=lambda x: -(x["lift"] or 0))
    out["rules"] = rr
    out["base120"] = float(base)
    return out


# ---- B. 시장 타이밍
def _market_study(D, mk_ma, final_sig, final_ai, ok, r, ex, tr, te, keep_gap):
    specs = [("시장 60일 수익률 0 이상", (D["mkt_r60"] >= 0).to_numpy()), ("시장 60일 수익률 0 미만", (D["mkt_r60"] < 0).to_numpy()),
             ("시장 20일 수익률 0 이상", (D["mkt_r20"] >= 0).to_numpy()), ("시장 20일 수익률 0 미만", (D["mkt_r20"] < 0).to_numpy()),
             ("시장 52주 고점 −10% 이내", (D["mkt_dd250"] >= -0.10).to_numpy()), ("시장 52주 고점 −10% 아래", (D["mkt_dd250"] < -0.10).to_numpy()),
             ("시장 변동성 낮음 (20일 1.2% 미만)", (D["mkt_vol20"] < 0.012).to_numpy()), ("시장 변동성 높음 (20일 1.2% 이상)", (D["mkt_vol20"] >= 0.012).to_numpy()),
             ("시장 60일선 위", mk_ma[60] == True), ("시장 60일선 아래", mk_ma[60] == False),  # noqa: E712
             ("시장 120일선 위", mk_ma[120] == True), ("시장 120일선 아래", mk_ma[120] == False),  # noqa: E712
             ("시장 200일선 위", mk_ma[200] == True), ("시장 200일선 아래", mk_ma[200] == False)]  # noqa: E712
    out = {}
    for label, base in (("sig", final_sig), ("ai", final_ai), ("base", ok)):
        rows = []
        for name, m in specs:
            mm = base & m & ok
            if mm.sum() < 200:
                continue
            row = _both(name, mm, r, ex, tr, te, {"share": float(mm.sum() / max(base.sum(), 1))})
            rows.append(row)
        out[label] = rows
    # 시장 조건을 '켜는' 필터로 썼을 때 상승 확률 변화(AI 규칙 기준): 두 시기 모두 p_up 과 평균이 오르는 것
    base = final_ai if final_ai.sum() >= 500 else final_sig
    b_tr, b_te = _stats(r[base & tr]), _stats(r[base & te])
    best = []
    for name, m in specs:
        mm = base & m & ok
        s_tr, s_te = _stats(r[mm & tr]), _stats(r[mm & te])
        if s_tr and s_te and s_tr["n"] >= 200 and s_te["n"] >= 200:
            best.append({"name": name, "n_te": s_te["n"], "p_up_tr": s_tr["p_up"], "p_up_te": s_te["p_up"], "mean_tr": s_tr["mean"], "mean_te": s_te["mean"],
                         "d_up_tr": s_tr["p_up"] - b_tr["p_up"], "d_up_te": s_te["p_up"] - b_te["p_up"], "share_te": s_te["n"] / max(b_te["n"], 1),
                         "robust": s_tr["p_up"] > b_tr["p_up"] + 0.01 and s_te["p_up"] > b_te["p_up"] + 0.01 and s_tr["mean"] > b_tr["mean"] and s_te["mean"] > b_te["mean"]})
    best.sort(key=lambda x: -(x["d_up_te"] + x["d_up_tr"]))
    out["filters"] = best
    out["base"] = {"tr": b_tr, "te": b_te, "label": "AI 규칙" if base is final_ai else "신호 규칙"}
    return out


# ---- D. 청산 규칙
def _exit_study(T, final_sig, final_ai, ok, tr, te):
    variants = [("20일 뒤 종가 (기준)", "r20", None), ("10일 뒤 종가", "r10", None), ("40일 뒤 종가", "r40", None), ("60일 뒤 종가", "r60", None),
                ("목표 +10% 닿으면 매도, 아니면 20일", "tgt10", "tgt10_d"), ("목표 +20% 닿으면 매도, 아니면 20일", "tgt20", "tgt20_d"),
                ("트레일링 스톱 10% (종가 고점 대비, 최장 60일)", "trail10", "trail10_d"), ("20일선 종가 이탈 시 매도 (최장 60일)", "ma20x", "ma20x_d"),
                ("손절 −7% (20일)", "stop7", None), ("손절 −10% (20일)", "stop10", None), ("손절 −15% (20일)", "stop15", None),
                ("목표 +20% / 손절 −10% (최장 60일)", "ts20_10", "ts20_10_d")]
    out = {}
    for label, base in (("sig", final_sig), ("ai", final_ai)):
        rows = []
        for name, k, dk in variants:
            v = T[k].astype(float)
            days = T[dk].astype(float) if dk else np.full(len(v), {"r20": 20, "r10": 10, "r40": 40, "r60": 60}.get(k, 20), float)
            for lab, m in (("tr", base & tr), ("te", base & te)):
                mm = m & ~np.isnan(v)
                s = _stats(v[mm] - COST)
                if s:
                    s["days"] = float(np.nanmean(days[mm]))
                    s["per_day"] = s["mean"] / max(s["days"], 1) * 20            # 20일 기준으로 환산
                    row = next((x for x in rows if x["name"] == name), None)
                    if row is None:
                        row = {"name": name}
                        rows.append(row)
                    row[lab] = s
        out[label] = rows
    return out


# ---- F. 종목 특성별
def _trait_study(D, market, final_sig, final_ai, r, ex, tr, te):
    specs = {"거래대금 (20일 평균)": [("3~10억", (D["liq"] >= math.log10(3e8)) & (D["liq"] < 9)), ("10~50억", (D["liq"] >= 9) & (D["liq"] < math.log10(5e9))),
                                  ("50~200억", (D["liq"] >= math.log10(5e9)) & (D["liq"] < math.log10(2e10))), ("200억 이상", D["liq"] >= math.log10(2e10))],
             "주가": [("5,000원 미만", D["price_level"] < math.log10(5000)), ("5,000~20,000원", (D["price_level"] >= math.log10(5000)) & (D["price_level"] < math.log10(20000))),
                     ("20,000~100,000원", (D["price_level"] >= math.log10(20000)) & (D["price_level"] < 5)), ("100,000원 이상", D["price_level"] >= 5)],
             "52주 고점 대비": [("−10% 이내", D["nh250"] >= -0.10), ("−10~−30%", (D["nh250"] < -0.10) & (D["nh250"] >= -0.30)), ("−30~−50%", (D["nh250"] < -0.30) & (D["nh250"] >= -0.50)),
                             ("−50% 이하", D["nh250"] < -0.50)],
             "시장 구분": [("KOSPI", pd.Series(market == "KOSPI")), ("KOSDAQ", pd.Series(np.char.startswith(market.astype(str), "KOSDAQ"))), ("KONEX", pd.Series(market == "KONEX"))],
             "변동성 (ATR÷종가)": [("3% 미만", D["atrp"] < 0.03), ("3~5%", (D["atrp"] >= 0.03) & (D["atrp"] < 0.05)), ("5~8%", (D["atrp"] >= 0.05) & (D["atrp"] < 0.08))]}
    out = {}
    for label, base in (("sig", final_sig), ("ai", final_ai)):
        groups = []
        for g, items in specs.items():
            rows = []
            for name, m in items:
                mm = base & np.asarray(m, bool)
                if mm.sum() >= 200:
                    rows.append(_both(name, mm, r, ex, tr, te, {"share": float(mm.sum() / max(base.sum(), 1))}))
            groups.append({"group": g, "rows": rows})
        out[label] = groups
    return out


# ---- G. 조용한 매집
def _quiet_study(D, T, ok, keep, r, ex, tr, te):
    tight = (D["range20"] < 0.12) & (D["ret20"].abs() < 0.05)                              # 20일 가격 폭 12% 미만 · 등락 ±5% 안
    vol_up = D["vr20_60"] >= 1.3                                                             # 20일 평균 거래량이 60일 평균의 1.3배↑
    obv_up = D["obv20"] > 0.5                                                                # OBV 20일 증가(평균 거래량의 반 이상)
    upvol = D["upvol20"] >= 0.6                                                              # 오른 날에 거래량이 몰림
    specs = [("가격 제자리 (폭 12%↓·등락 ±5%)", tight), ("가격 제자리 + 거래량 증가 (20일/60일 1.3배↑)", tight & vol_up),
             ("가격 제자리 + OBV 증가", tight & obv_up), ("가격 제자리 + 거래량 증가 + OBV 증가", tight & vol_up & obv_up),
             ("가격 제자리 + 거래량 증가 + OBV 증가 + 상승일 거래량 60%↑", tight & vol_up & obv_up & upvol),
             ("가격 제자리 + 거래량 마름 (10일 최소 0.4배↓)", tight & (D["vdry10"] < 0.4)), ("가격 제자리 + 최근 60봉 매집봉", tight & (D["acc_recent"] == 1)),
             ("(비교) 거래량 증가 + OBV 증가, 가격 제자리 아님", ~tight & vol_up & obv_up)]
    mx60 = T["mx60"].astype(float)
    sg = T["surge60"].astype(float)
    r60 = T["r60"].astype(float) - COST
    base = ok & keep
    rows = []
    b = {"surge": float(np.nanmean(sg[base])), "up20": float(np.nanmean(mx60[base] >= 0.2)), "r60": float(np.nanmean(r60[base])), "r20": float(np.nanmean(r[base]))}
    for name, m in specs:
        mm = base & m.to_numpy()
        if mm.sum() < 300:
            continue
        row = _both(name, mm, r, ex, tr, te, {"share": float(mm.mean())})
        row.update({"surge60": float(np.nanmean(sg[mm])), "up20_60": float(np.nanmean(mx60[mm] >= 0.2)), "r60": float(np.nanmean(r60[mm])),
                    "up20_60_tr": float(np.nanmean(mx60[mm & tr] >= 0.2)), "up20_60_te": float(np.nanmean(mx60[mm & te] >= 0.2)) if (mm & te).sum() >= 100 else None,
                    "r60_te": float(np.nanmean(r60[mm & te])) if (mm & te).sum() >= 100 else None})
        rows.append(row)
    return {"base": b, "rows": rows, "base_up20_te": float(np.nanmean(mx60[base & te] >= 0.2)), "base_up20_tr": float(np.nanmean(mx60[base & tr] >= 0.2))}


# ---- J. 계절성
def _season_study(date, final_sig, final_ai, ok, r, ex, tr, te):
    dt = pd.to_datetime(pd.Series(date))
    mon, wd, dom = dt.dt.month.to_numpy(), dt.dt.weekday.to_numpy(), dt.dt.day.to_numpy()
    specs = {"월": [(f"{m}월", mon == m) for m in range(1, 13)], "요일": [(n, wd == i) for i, n in enumerate(["월", "화", "수", "목", "금"])],
             "월중 위치": [("월초 (1~7일)", dom <= 7), ("중순 (8~21일)", (dom > 7) & (dom <= 21)), ("월말 (22일~)", dom > 21)]}
    out = {}
    for label, base in (("base", ok), ("sig", final_sig), ("ai", final_ai)):
        groups = []
        for g, items in specs.items():
            rows = [_both(n, base & m, r, ex, tr, te) for n, m in items if (base & m).sum() >= 200]
            groups.append({"group": g, "rows": rows})
        out[label] = groups
    return out


# ---- I. 실패 원인
def _fail_study(D, T, base, r, ex, tr, te):
    m = base & ~np.isnan(r)
    lose = m & (r <= -0.10)
    rest = m & (r > -0.10)
    feats = ["atrp", "ret5", "ret20", "ret60", "nh250", "dist5", "dist20", "dist60", "dist224", "volratio", "vr5", "vdry10", "upper", "gap0", "body", "range1", "mkt_r20",
             "mkt_r60", "mkt_vol20", "liq", "price_level", "rs20", "vspike_n", "gapdn20", "down_streak", "hist_bars", "pos60", "range20", "vol20"]
    diff = []
    for f in feats:
        a, b = D.loc[lose & tr, f], D.loc[rest & tr, f]
        if len(a) < 100:
            continue
        sd = float(D.loc[m & tr, f].std()) or 1.0
        diff.append({"name": f, "label": ai.LABEL.get(f, f), "lose": float(a.median()), "rest": float(b.median()), "d": float((a.mean() - b.mean()) / sd)})
    diff.sort(key=lambda x: -abs(x["d"]))
    # 새 피함 후보: 학습 기간에서 손실 확률이 1.3배↑인 조건을 미리 정한 목록에서 고르고, 시험 기간에서 확인
    specs = [("5일 +15%↑ 급등 뒤", D["ret5"] >= 0.15), ("5일 +10%↑ 급등 뒤", D["ret5"] >= 0.10), ("당일 +8%↑ 장대양봉", D["body"] >= 0.08), ("당일 고저폭 10%↑", D["range1"] >= 0.10),
             ("거래량 5배↑", D["volratio"] >= 5), ("거래량 10배↑", D["volratio"] >= 10), ("5일 거래량 ÷ 20일 2배↑", D["vr5"] >= 2), ("60일 +60%↑", D["ret60"] >= 0.6),
             ("변동성 6~8%", (D["atrp"] >= 0.06) & (D["atrp"] < 0.08)), ("5일선 +10%↑ 이격", D["dist5"] >= 0.10), ("20일선 +20%↑ 이격", D["dist20"] >= 0.20),
             ("시장 변동성 높음 (20일 1.5%↑)", D["mkt_vol20"] >= 0.015), ("시장 20일 −5%↓", D["mkt_r20"] <= -0.05), ("상장 2년 미만", D["hist_bars"] < math.log10(500)),
             ("거래대금 3~5억", D["liq"] < math.log10(5e8)), ("20일 중 거래량 2배↑ 날 5일↑", D["vspike_n"] >= 5), ("60일 범위 최상단 (pos60 0.95↑)", D["pos60"] >= 0.95)]
    base_tr = float((r[m & tr] <= -0.10).mean())
    base_te = float((r[m & te] <= -0.10).mean())
    cands = []
    for name, c in specs:
        mm = m & c.to_numpy()
        a, b = mm & tr, mm & te
        if a.sum() < 200 or b.sum() < 100:
            continue
        p_tr, p_te = float((r[a] <= -0.10).mean()), float((r[b] <= -0.10).mean())
        s_te_rest = _stats(r[m & te & ~c.to_numpy()])
        cands.append({"name": name, "n_tr": int(a.sum()), "n_te": int(b.sum()), "share_te": float(b.sum() / max((m & te).sum(), 1)), "p_lose_tr": p_tr, "p_lose_te": p_te,
                      "lift_tr": p_tr / base_tr if base_tr else None, "lift_te": p_te / base_te if base_te else None,
                      "mean_tr": float(r[a].mean()), "mean_te": float(r[b].mean()), "excess_tr": float(np.nanmean(ex[a])), "excess_te": float(np.nanmean(ex[b])),
                      "robust": p_tr >= base_tr * 1.3 and p_te >= base_te * 1.3 and np.nanmean(ex[a]) < 0 and np.nanmean(ex[b]) < 0,
                      "p_up_rest_te": s_te_rest["p_up"] if s_te_rest else None})
    cands.sort(key=lambda x: -(x["lift_te"] or 0))
    return {"n_lose_tr": int((lose & tr).sum()), "n_tr": int((m & tr).sum()), "base_lose_tr": base_tr, "base_lose_te": base_te, "diff": diff[:14], "cands": cands}


# ---- C. 걸어가며 검증 (+ H.d AI 해마다 재학습)
def _walk_forward(X, D, T, date, year, code, keep, gap_ok, cands, r, ex, log, t0):
    years = sorted(y for y in set(year) if y >= WF_START)
    y20 = (T["r20"] >= ai.TARGET)
    valid = ~np.isnan(r)
    rows = []
    oos_sig = np.zeros(len(date), bool)
    oos_ai = np.zeros(len(date), bool)
    insample_sig = np.zeros(len(date), bool)
    chosen_hist = []
    for Y in years:
        past = (year < Y) & valid
        cur = (year == Y) & valid
        if past.sum() < 50000 or cur.sum() < 500:
            continue
        early, late = past & (year < Y - 3), past & (year >= Y - 3)
        picked = []
        for name, m in cands.items():
            mm = m & keep
            a, b = mm & early, mm & late
            if a.sum() >= 300 and b.sum() >= 200 and np.nanmean(ex[a]) > 0 and np.nanmean(ex[b]) > 0:
                picked.append(name)
        sig = np.zeros(len(date), bool)
        for n in picked:
            sig |= cands[n]
        m_sig = cur & keep & gap_ok & sig
        oos_sig |= m_sig
        chosen_hist.append({"year": int(Y), "picked": picked})
        # AI: 그 해 전까지로 학습(최대 AI_SUB 행), 그 해 상위 10%
        idx = _sub(past, AI_SUB, seed=Y)
        mdl = _fit_fast(X[idx], y20[idx].astype(int), seed=Y)
        p = _predict(mdl, X[cur])
        thr = np.nanquantile(p, 1 - cs.AI_TOP)
        top = np.zeros(len(date), bool)
        top[np.flatnonzero(cur)[p >= thr]] = True
        m_ai = cur & keep & gap_ok & top
        oos_ai |= m_ai
        s_sig, s_ai, s_base = _stats(r[m_sig], ex[m_sig]), _stats(r[m_ai], ex[m_ai]), _stats(r[cur], ex[cur])
        rows.append({"year": int(Y), "n_picked": len(picked), "sig": s_sig, "ai": s_ai, "base": s_base})
        log(f"[C] {Y}: 신호 {len(picked)}개 {s_sig['excess'] * 100 if s_sig else float('nan'):+.2f}%p · AI {s_ai['excess'] * 100 if s_ai else float('nan'):+.2f}%p · {time.time() - t0:.0f}초")
    tot = {"sig": _stats(r[oos_sig], ex[oos_sig]), "ai": _stats(r[oos_ai], ex[oos_ai]), "base": _stats(r[valid & (year >= WF_START)], ex[valid & (year >= WF_START)])}
    for k in ("sig", "ai"):
        m = oos_sig if k == "sig" else oos_ai
        tot[k + "_years_pos"] = int(sum(1 for x in rows if x[k] and x[k]["excess"] > 0))
        tot[k + "_years"] = int(sum(1 for x in rows if x[k]))
    return {"rows": rows, "total": tot, "chosen_hist": chosen_hist, "start": WF_START}


# ---- H. AI 점검
def _ai_study(X, T, P, ai_top, keep, gap_ok, r, ex, tr, te, log, t0):
    if np.isnan(P).all():
        return None
    out = {}
    y20 = (T["r20"] >= ai.TARGET).astype(int)
    valid = ~np.isnan(r)
    # (a) 캘리브레이션: 시험 기간 점수 10구간별 실제 +20% 비율·평균 수익
    m = te & valid
    q = pd.qcut(pd.Series(P[m]).rank(method="first"), 10, labels=False).to_numpy()
    out["calibration"] = [{"bin": int(k + 1), "p_mean": float(P[m][q == k].mean()), "hit": float(y20[m][q == k].mean()), "mean": float(r[m][q == k].mean()),
                           "p_up": float((r[m][q == k] > 0).mean()), "excess": float(np.nanmean(ex[m][q == k]))} for k in range(10)]
    # (b) 순위(회귀) 학습 · (c) 기간 앙상블 — 학습 ≤2021, 시험 2022~
    from sklearn.ensemble import HistGradientBoostingRegressor
    idx = _sub(tr & valid, AI_SUB, seed=1)
    target = np.clip(ex[idx], -0.5, 0.5)
    reg = HistGradientBoostingRegressor(max_iter=150, learning_rate=0.08, max_leaf_nodes=31, min_samples_leaf=200, l2_regularization=2.0, random_state=0).fit(X[idx], target)
    p_reg = np.full(len(r), np.nan)
    p_reg[m] = reg.predict(X[m])
    log(f"[H] 순위 학습 완료 · {time.time() - t0:.0f}초")
    models = {}
    for key, (hold, thr, _) in ai.HORIZONS.items():
        if key not in ("short", "mid", "midlong"):
            continue
        rr = T[f"r{hold}"].astype(float)
        v = tr & ~np.isnan(rr)
        idx = _sub(v, AI_SUB, seed=2)
        models[key] = _fit_fast(X[idx], (rr[idx] >= thr).astype(int))
    log(f"[H] 기간 모델 3개 학습 · {time.time() - t0:.0f}초")
    ranks = []
    for key, mdl in models.items():
        p = np.full(len(r), np.nan)
        p[m] = _predict(mdl, X[m])
        ranks.append(pd.Series(p[m]).rank(pct=True).to_numpy())
    p_ens = np.full(len(r), np.nan)
    p_ens[m] = np.mean(ranks, axis=0)
    p_mid = np.full(len(r), np.nan)
    p_mid[m] = ranks[1]
    rows = []
    for name, p in (("기존 모델 (20일 +20% 분류)", P), ("20일 모델 다시 학습 (비교용, 반복 150)", p_mid), ("순위 학습 (시장 대비 수익 회귀)", p_reg), ("기간 앙상블 (5·20·60일 순위 평균)", p_ens)):
        thr = np.nanquantile(p[m], 1 - cs.AI_TOP)
        top = m & (p >= thr)
        for lab, mm in (("상위 10%", top), ("상위 10% + 피함 제외 + 갭", top & keep & gap_ok)):
            s = _stats(r[mm], ex[mm])
            if s:
                rows.append({"name": f"{name} · {lab}", "model": name, "filter": lab, **s})
    out["models"] = rows
    return out


# ---- K. 보강 규칙
def _improve(rep, D, T, mk_ma, date, base, keep, gap_ok, ai_top, sig_any, r, ex, tr, te):
    steps = [("출발: 피함 제외 + 갭 + AI 상위 10%" if rep["meta"]["ai"] else "출발: 피함 제외 + 갭 + 채택 신호", base)]
    m = base.copy()
    used = []
    # 시장 필터(robust 중 상승 확률 개선 최대 1개)
    mf = [f for f in rep["market"]["filters"] if f["robust"]]
    mspecs = {"시장 60일 수익률 0 이상": (D["mkt_r60"] >= 0).to_numpy(), "시장 20일 수익률 0 이상": (D["mkt_r20"] >= 0).to_numpy(), "시장 52주 고점 −10% 이내": (D["mkt_dd250"] >= -0.10).to_numpy(),
              "시장 변동성 낮음 (20일 1.2% 미만)": (D["mkt_vol20"] < 0.012).to_numpy(), "시장 60일선 위": mk_ma[60] == True, "시장 120일선 위": mk_ma[120] == True, "시장 200일선 위": mk_ma[200] == True,  # noqa: E712
              "시장 60일 수익률 0 미만": (D["mkt_r60"] < 0).to_numpy(), "시장 20일 수익률 0 미만": (D["mkt_r20"] < 0).to_numpy(), "시장 52주 고점 −10% 아래": (D["mkt_dd250"] < -0.10).to_numpy(),
              "시장 변동성 높음 (20일 1.2% 이상)": (D["mkt_vol20"] >= 0.012).to_numpy(), "시장 60일선 아래": mk_ma[60] == False, "시장 120일선 아래": mk_ma[120] == False, "시장 200일선 아래": mk_ma[200] == False}  # noqa: E712
    if mf:
        f = mf[0]
        m = m & mspecs[f["name"]]
        used.append(f["name"])
        steps.append((f"+ 시장 필터: {f['name']}", m.copy()))
    # 새 피함 후보(robust, 최대 2개)
    for c in [c for c in rep["fail"]["cands"] if c["robust"]][:2]:
        spec = dict((n, cm) for n, cm in _fail_specs(D))
        m = m & ~spec[c["name"]].to_numpy()
        used.append("피함: " + c["name"])
        steps.append((f"+ 새 피함: {c['name']} 제외", m.copy()))
    # 계절성(robust 한 '월중 위치'나 '요일' 중 상승 확률 개선 폭 최대 1개 — 월별은 우연이 많아 제외)
    dt = pd.to_datetime(pd.Series(date))
    wd, dom = dt.dt.weekday.to_numpy(), dt.dt.day.to_numpy()
    sspec = {"월초 (1~7일)": dom <= 7, "중순 (8~21일)": (dom > 7) & (dom <= 21), "월말 (22일~)": dom > 21, **{n: wd == i for i, n in enumerate(["월", "화", "수", "목", "금"])}}
    lab = "ai" if rep["meta"]["ai"] else "sig"
    b_tr, b_te = _stats(r[m & tr]), _stats(r[m & te])
    best, best_gain = None, 0.0
    for g in rep["season"][lab]:
        if g["group"] == "월":
            continue
        for row in g["rows"]:
            mm = m & sspec[row["name"]]
            s_tr, s_te = _stats(r[mm & tr]), _stats(r[mm & te])
            if s_tr and s_te and s_te["n"] >= 200 and s_tr["p_up"] > b_tr["p_up"] + 0.01 and s_te["p_up"] > b_te["p_up"] + 0.01 and s_tr["mean"] > b_tr["mean"] and s_te["mean"] > b_te["mean"]:
                gain = s_te["p_up"] - b_te["p_up"]
                if gain > best_gain:
                    best, best_gain = row["name"], gain
    if best:
        m = m & sspec[best]
        used.append("진입일: " + best)
        steps.append((f"+ 진입일: {best}만", m.copy()))
    # 신호 겹침(AI 규칙에 채택 신호가 같이 있으면)
    if rep["meta"]["ai"]:
        mm = m & sig_any
        s_tr, s_te = _stats(r[mm & tr]), _stats(r[mm & te])
        if s_tr and s_te and s_te["n"] >= 200 and s_tr["p_up"] > _stats(r[m & tr])["p_up"] and s_te["p_up"] > _stats(r[m & te])["p_up"]:
            m = mm
            used.append("채택 신호 겹침")
            steps.append(("+ 채택 신호도 같이 있을 때만", m.copy()))
    rows = []
    for name, mm in steps:
        row = _both(name, mm, r, ex, tr, te)
        row["per_month_te"] = float((mm & te).sum() / max(len(pd.Series(date[te]).str[:7].unique()), 1))
        row["split3_te"] = _stats(T["split3"][mm & te].astype(float) - COST)
        rows.append(row)
    # 청산 규칙: 보강 규칙 행에서 두 시기 모두 기준(20일)보다 상승 확률·평균이 높은 것
    ex_rows = []
    for name, k in (("20일 뒤 종가 (기준)", "r20"), ("목표 +10% 닿으면 매도", "tgt10"), ("목표 +20% 닿으면 매도", "tgt20"), ("트레일링 10%", "trail10"), ("20일선 이탈 매도", "ma20x"),
                    ("손절 −10%", "stop10"), ("목표 +20% / 손절 −10%", "ts20_10"), ("40일 뒤 종가", "r40")):
        v = T[k].astype(float) - COST
        s_tr, s_te = _stats(v[m & tr]), _stats(v[m & te])
        if s_tr and s_te:
            ex_rows.append({"name": name, "tr": s_tr, "te": s_te})
    b = next(x for x in ex_rows if x["name"] == "20일 뒤 종가 (기준)")
    for x in ex_rows:
        x["robust"] = x["tr"]["p_up"] > b["tr"]["p_up"] and x["te"]["p_up"] > b["te"]["p_up"] and x["tr"]["mean"] > b["tr"]["mean"] and x["te"]["mean"] > b["te"]["mean"]
    return {"steps": rows, "used": used, "exits": ex_rows, "final_mask_n": int(m.sum()), "_mask": m}


def _fail_specs(D):
    return [("5일 +15%↑ 급등 뒤", D["ret5"] >= 0.15), ("5일 +10%↑ 급등 뒤", D["ret5"] >= 0.10), ("당일 +8%↑ 장대양봉", D["body"] >= 0.08), ("당일 고저폭 10%↑", D["range1"] >= 0.10),
            ("거래량 5배↑", D["volratio"] >= 5), ("거래량 10배↑", D["volratio"] >= 10), ("5일 거래량 ÷ 20일 2배↑", D["vr5"] >= 2), ("60일 +60%↑", D["ret60"] >= 0.6),
            ("변동성 6~8%", (D["atrp"] >= 0.06) & (D["atrp"] < 0.08)), ("5일선 +10%↑ 이격", D["dist5"] >= 0.10), ("20일선 +20%↑ 이격", D["dist20"] >= 0.20),
            ("시장 변동성 높음 (20일 1.5%↑)", D["mkt_vol20"] >= 0.015), ("시장 20일 −5%↓", D["mkt_r20"] <= -0.05), ("상장 2년 미만", D["hist_bars"] < math.log10(500)),
            ("거래대금 3~5억", D["liq"] < math.log10(5e8)), ("20일 중 거래량 2배↑ 날 5일↑", D["vspike_n"] >= 5), ("60일 범위 최상단 (pos60 0.95↑)", D["pos60"] >= 0.95)]


# ---- E. 포트폴리오 시뮬레이션
def _portfolio(rep, D, T, P, date, code, names, keep, gap_ok, ai_top, mk_ma, te, max_pos=10, hold=20):
    """표본 날짜(연중일 5일 간격 ≈ 주 1회 점검)마다 규칙에 걸린 종목을 AI 점수 순으로 새로 사고 20거래일 뒤 판다. 자금은 max_pos 로 나눔(변동성 역가중 변형도).
    같은 날 겹치는 표본이 여러 종목이므로 '매 점검일에 빈 자리만큼 산다'로 단순화. 시험 기간(2022~)."""
    m_final = rep["improve"]["_mask"]
    out = {}
    for label, m in (("종합 규칙 (피함 제외 + 갭 + AI 상위 10%)", keep & gap_ok & ai_top), ("보강 규칙", m_final)):
        m = m & te & ~np.isnan(T["r20"])
        if m.sum() < 50:
            continue
        df = pd.DataFrame({"date": date[m], "code": code[m], "r": T["r20"][m].astype(float) - COST, "p": P[m] if not np.isnan(P).all() else 0.0, "atrp": D["atrp"].to_numpy()[m],
                           "split": T["split3"][m].astype(float) - COST})
        df = df.sort_values(["date", "p"], ascending=[True, False])
        for wmode in ("equal", "atr"):
            cash, equity, open_pos, curve, trades = 1.0, 1.0, [], [], 0
            dates_all = sorted(df["date"].unique())
            by_day = {d: g for d, g in df.groupby("date")}
            for d in dates_all:
                # 만기 청산
                still = []
                for pos in open_pos:
                    if d >= pos["exit"]:
                        cash += pos["w"] * (1 + pos["r"])
                    else:
                        still.append(pos)
                open_pos = still
                g = by_day[d]
                slots = max_pos - len(open_pos)
                if slots > 0 and len(g):
                    picks = g.head(slots)
                    if wmode == "atr":
                        inv = 1.0 / np.maximum(picks["atrp"].to_numpy(), 0.01)
                        w_each = inv / inv.sum() * min(slots, len(picks)) / max_pos
                    else:
                        w_each = np.full(len(picks), 1.0 / max_pos)
                    for (_, row), w in zip(picks.iterrows(), w_each):
                        w = min(w * equity, cash)
                        if w <= 0:
                            break
                        cash -= w
                        i = dates_all.index(d)
                        exit_d = dates_all[min(i + 4, len(dates_all) - 1)]      # 점검일 간격 ≈ 5거래일 → 4번 뒤 ≈ 20거래일
                        open_pos.append({"w": w, "r": float(row["r"]), "exit": exit_d})
                        trades += 1
                equity = cash + sum(p["w"] for p in open_pos)
                curve.append((d, equity))
            eq = pd.Series([v for _, v in curve], index=pd.to_datetime([d for d, _ in curve]))
            dd = (eq / eq.cummax() - 1).min()
            years = (pd.to_datetime(eq.index[-1]) - pd.to_datetime(eq.index[0])).days / 365.25
            out[f"{label} · {'같은 비중' if wmode == 'equal' else '변동성 역가중'}"] = {
                "final": float(eq.iloc[-1]), "cagr": float(eq.iloc[-1] ** (1 / max(years, 0.1)) - 1), "mdd": float(dd), "trades": trades,
                "curve": [{"d": d.strftime("%Y-%m-%d"), "v": float(v)} for d, v in eq.iloc[::max(len(eq) // 60, 1)].items()], "months_pos": float((eq.resample("ME").last().pct_change().dropna() > 0).mean()) if len(eq) > 40 else None}
    # 시장(같은 날 전체 평균) 비교
    base = te & ~np.isnan(T["r20"])
    mk = pd.DataFrame({"date": date[base], "r": T["r20"][base].astype(float)}).groupby("date")["r"].mean()
    out["market"] = {"mean20": float(mk.mean()), "curve_final": float(np.prod(1 + mk.to_numpy()[::4]))}
    return out


def narrate(rep: dict) -> list[str]:
    out = []
    h = rep["halt"]
    if h.get("prob") and h.get("resume"):
        pr = {x["name"]: x for x in h["prob"]}
        b, av = pr.get("거래 가능 종목 아무 날"), pr.get("피함 규칙 해당")
        if b and av:
            out.append(f"거래정지(거래량 0 이 {HALT_MIN}일↑) 구간 {h['n']:,}건({HALT_LONG}일↑ {h['long']:,}건). 아무 날 뒤 120일 안 거래정지 확률 {b['p120'] * 100:.1f}% vs 피함 규칙 해당 {av['p120'] * 100:.1f}%"
                       + (f", AI 규칙 통과 {pr['AI 규칙 통과']['p120'] * 100:.1f}%" if "AI 규칙 통과" in pr else "") + f". 재개 첫날 수익 중앙 {h['resume']['median'] * 100:+.1f}%, −20%↓ {h['resume']['p_dn20'] * 100:.0f}%.")
    mf = [f for f in rep["market"]["filters"] if f["robust"]]
    bm = rep["market"]["base"]
    if mf:
        f = mf[0]
        out.append(f"시장 필터 중 두 시기 모두 좋아진 것: '{f['name']}' — {bm['label']} 상승 확률 {bm['te']['p_up'] * 100:.0f}% → {f['p_up_te'] * 100:.0f}%(시험), 평균 {bm['te']['mean'] * 100:+.2f}% → {f['mean_te'] * 100:+.2f}%, 신호의 {f['share_te'] * 100:.0f}%만 남음.")
    else:
        out.append("시장 필터는 두 시기 모두 상승 확률과 평균을 같이 올린 것이 없었습니다.")
    w = rep["walk"]["total"]
    if w.get("sig") and w.get("ai"):
        out.append(f"걸어가며 검증({rep['walk']['start']}~): 해마다 다시 고른 신호 규칙은 시장 대비 {w['sig']['excess'] * 100:+.2f}%p({w['sig_years_pos']}/{w['sig_years']}년 플러스), 해마다 재학습한 AI 상위 10%는 {w['ai']['excess'] * 100:+.2f}%p({w['ai_years_pos']}/{w['ai_years']}년), 상승 확률 {w['ai']['p_up'] * 100:.0f}%(기준 {w['base']['p_up'] * 100:.0f}%).")
    ac = rep.get("ai_check")
    if ac:
        best = max(ac["models"], key=lambda x: x["excess"])
        out.append(f"AI 변형 중 시험 기간 시장 대비가 가장 높은 것: {best['name']} {best['excess'] * 100:+.2f}%p(평균 {best['mean'] * 100:+.2f}%, 상승 {best['p_up'] * 100:.0f}%).")
    q = rep["quiet"]
    best_q = max((x for x in q["rows"] if x["robust"] and x["up20_60"] > q["base"]["up20"] and (x["up20_60_te"] or 0) > q["base_up20_te"]), key=lambda x: x["up20_60"], default=None)
    if best_q:
        out.append(f"조용한 매집: '{best_q['name']}' 뒤 60일 안 +20% {best_q['up20_60'] * 100:.0f}%(기준 {q['base']['up20'] * 100:.0f}%), 60일 안 거래량 급증 {best_q['surge60'] * 100:.0f}%(기준 {q['base']['surge'] * 100:.0f}%).")
    else:
        out.append("조용한 매집 조건은 두 시기 모두 시장보다 좋은 것이 없었습니다.")
    fc = [c for c in rep["fail"]["cands"] if c["robust"]]
    out.append("새 피함 후보(두 시기 모두 큰 손실 1.3배↑·시장보다 나쁨): " + (", ".join(f"{c['name']}({c['lift_te']:.1f}배)" for c in fc[:3]) if fc else "없음") + ".")
    st = rep["improve"]["steps"]
    a, b = st[0], st[-1]
    if a.get("te") and b.get("te"):
        out.append(f"보강 결과(시험 기간): {a['name']} 상승 {a['te']['p_up'] * 100:.0f}% · 평균 {a['te']['mean'] * 100:+.2f}% → 보강 뒤 상승 {b['te']['p_up'] * 100:.0f}% · 평균 {b['te']['mean'] * 100:+.2f}%(시장 대비 {b['te']['excess'] * 100:+.2f}%p, 한 달 {b['per_month_te']:.0f}건). 더한 것: "
                   + (", ".join(rep["improve"]["used"]) if rep["improve"]["used"] else "없음(두 시기 모두 좋아지는 조건이 없었음)") + ".")
    exr = [x for x in rep["improve"]["exits"] if x["robust"]]
    if exr:
        e = max(exr, key=lambda x: x["te"]["mean"])
        out.append(f"청산 규칙 중 두 시기 모두 20일 고정보다 나은 것: {e['name']} — 시험 기간 평균 {e['te']['mean'] * 100:+.2f}%, 상승 {e['te']['p_up'] * 100:.0f}%.")
    pf = rep.get("portfolio") or {}
    for k, v in pf.items():
        if k != "market" and "보강" in k and "같은 비중" in k:
            out.append(f"포트폴리오(시험 기간, 동시 10종목, 20일 보유, 보강 규칙): 최종 {v['final']:.2f}배, 연 {v['cagr'] * 100:+.1f}%, 최대 낙폭 {v['mdd'] * 100:.0f}%, 거래 {v['trades']}회.")
    return out


if __name__ == "__main__":
    run()
