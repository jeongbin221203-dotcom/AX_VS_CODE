"""연습용 잡주 후보 — 2026-10-17 연구(tools/research)의 방식을 매일 쓸 수 있게 옮긴 것.

대상 '잡주' = 시가총액 301위 밖이면서(시총 1,000억 미만 또는 주가 1,000원 미만 또는 20일 평균 거래대금 5억 미만).
점수 = 잡주 전용 모델 4개(20일 순위·시장+8%p 확률·초과수익 회귀·60일 순위)의 잡주 안 순위 평균 ens_j 와
       전체 종목 모델 4개의 순위 평균(전체 안) 을 잡주 안 순위로 바꾼 값을 다시 평균: (ens_j + r_ens) / 2.
사용: 오늘 점수 상위 5종목을 다음 날 시가에 같은 금액으로 사서 20거래일 뒤 종가에 판다(손절은 성과를 낮춰서 쓰지 않음).

백테스트(해마다 다시 학습해 걸어가며 검증, 비용 0.3% + 추가 슬리피지 0.3%p): 상위 5종목 검증 2022~23 연 +67% · 시험 2024~26 연 +92%,
상위 10종목 +34% · +65%. 추가 비용 3.0%p 를 가정해도 상위 5종목 +18% · +37%. 연도별 2022 +42 · 2023 +86 · 2024 +116 · 2025 +147 · 2026 +8%.
유의: 한 종목 20일 수익 최악 −64%, 상장폐지·거래정지·상하한가는 반영 못 함, 현재 상장 종목만 있어 생존 편향이 있고(잡주에서 가장 큼),
최고값은 여러 설정을 비교한 뒤의 값이라 과대평가일 수 있다. 연습용·소액 가정이며 수익을 보장하지 않는다.
"""
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime

import numpy as np
import pandas as pd

import config
from core import ai, backtest as bt, db, service

MODEL_PATH = config.DATA_DIR / "picks_models.joblib"
TODAY_PATH = config.DATA_DIR / "picks_today.json"
COST = 0.003
MIN_LIQ = 3e8                    # 학습·점수에 쓰는 종목: 20일 평균 거래대금 3억 이상
TOP_N = 5                        # 연구에서 쓴 종목 수(상위 5). 화면에는 10개까지 보여 준다.
SHOW_N = 10
MAX_AGE_DAYS = 30                # 모델이 이보다 오래되면 다시 학습
META = ["date", "code", "yr", "r5", "r20", "r60", "r120", "x5", "x20", "x60", "x120", "mae20", "liqv", "dow", "month", "close", "marcap", "close_now", "mcap_est", "grp", "ret20_now", "name"]
_cache = {"mtime": None, "obj": None}


# ------------------------------------------------------------------ 차트 지표 (연구 build6 와 같은 계산)
def _ewm(s, n):
    return s.ewm(span=n, adjust=False).mean()


def extra_features(df: pd.DataFrame) -> pd.DataFrame:
    o, h, l, c, v = (df[k] for k in ("open", "high", "low", "close", "volume"))
    out = {}
    ret = c.pct_change()
    d = c.diff()
    up, dn = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean(), (-d).clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    out["rsi14"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    macd = _ewm(c, 12) - _ewm(c, 26)
    sig = _ewm(macd, 9)
    out["macd_n"] = macd / c
    out["macd_hist_n"] = (macd - sig) / c
    out["macd_up"] = ((macd > sig) & (macd.shift() <= sig.shift())).astype(float)
    m20, s20 = c.rolling(20).mean(), c.rolling(20).std(ddof=0)
    out["bb_pctb"] = (c - (m20 - 2 * s20)) / (4 * s20).replace(0, np.nan)
    bw = 4 * s20 / m20
    out["bb_width"] = bw
    out["bb_width_pct"] = bw.rolling(120, min_periods=60).rank(pct=True)
    ll, hh = l.rolling(14).min(), h.rolling(14).max()
    k = 100 * (c - ll) / (hh - ll).replace(0, np.nan)
    out["stoch_k"] = k
    out["stoch_d"] = k.rolling(3).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr14 = tr.ewm(alpha=1 / 14, adjust=False).mean()
    upm, dnm = h.diff(), -l.diff()
    pdm = np.where((upm > dnm) & (upm > 0), upm, 0.0)
    ndm = np.where((dnm > upm) & (dnm > 0), dnm, 0.0)
    pdi = 100 * pd.Series(pdm, index=df.index).ewm(alpha=1 / 14, adjust=False).mean() / atr14
    ndi = 100 * pd.Series(ndm, index=df.index).ewm(alpha=1 / 14, adjust=False).mean() / atr14
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    out["adx"] = dx.ewm(alpha=1 / 14, adjust=False).mean()
    out["di_diff"] = pdi - ndi
    tp = (h + l + c) / 3
    mad = (tp - tp.rolling(20).mean()).abs().rolling(20).mean()
    out["cci20"] = (tp - tp.rolling(20).mean()) / (0.015 * mad.replace(0, np.nan))
    rmf = tp * v
    pos = rmf.where(tp > tp.shift(), 0.0).rolling(14).sum()
    neg = rmf.where(tp < tp.shift(), 0.0).rolling(14).sum()
    out["mfi14"] = 100 - 100 / (1 + pos / neg.replace(0, np.nan))
    tenkan, kijun = (h.rolling(9).max() + l.rolling(9).min()) / 2, (h.rolling(26).max() + l.rolling(26).min()) / 2
    spa, spb = ((tenkan + kijun) / 2).shift(26), ((h.rolling(52).max() + l.rolling(52).min()) / 2).shift(26)
    top, bot = np.maximum(spa, spb), np.minimum(spa, spb)
    out["ichi_cloud"] = np.where(c > top, (c / top - 1), np.where(c < bot, (c / bot - 1), 0.0))
    out["ichi_tk"] = (tenkan - kijun) / c
    out["keltner"] = (c - _ewm(c, 20)) / atr14.replace(0, np.nan)
    for n in (20, 55):
        mn, mx = l.rolling(n).min(), h.rolling(n).max()
        out[f"donch{n}"] = (c - mn) / (mx - mn).replace(0, np.nan)
    mfm = ((c - l) - (h - c)) / (h - l).replace(0, np.nan)
    out["cmf20"] = (mfm * v).rolling(20).sum() / v.rolling(20).sum().replace(0, np.nan)
    lv = np.log1p(v)
    out["vol_z60"] = (lv - lv.rolling(60).mean()) / lv.rolling(60).std().replace(0, np.nan)
    out["vol_trend"] = v.rolling(20).mean() / v.rolling(120).mean().replace(0, np.nan)
    out["gap_pct"] = o / c.shift() - 1
    out["gap_n20"] = (out["gap_pct"].abs() > 0.03).rolling(20).sum()
    out["up_streak"] = (ret > 0).astype(int).groupby((ret <= 0).cumsum()).cumsum()
    out["dn_streak"] = (ret < 0).astype(int).groupby((ret >= 0).cumsum()).cumsum()
    out["range_pct"] = (h - l) / c
    out["from_high250"] = c / h.rolling(250).max() - 1
    out["from_low250"] = c / l.rolling(250).min() - 1
    out["high_age"] = h.rolling(250).apply(lambda x: len(x) - 1 - int(np.argmax(x)), raw=True) / 250
    out["ret120"] = c / c.shift(120) - 1
    out["ret250"] = c / c.shift(250) - 1
    out["vol20_60"] = ret.rolling(20).std() / ret.rolling(60).std().replace(0, np.nan)
    out["skew60"] = ret.rolling(60).skew()
    out["mdd60"] = (c / c.rolling(60).max() - 1).rolling(60).min()
    out["updown60"] = (ret > 0).rolling(60).mean()
    out["downvol60"] = ret.clip(upper=0).rolling(60).std()
    return pd.DataFrame(out, index=df.index)


def stock_frame(df: pd.DataFrame, kosdaq: float, mkt=None) -> pd.DataFrame:
    """한 종목의 모든 날짜 특징(연구 build6 와 같은 열). 시장 지수는 mkt, 없으면 ai.MKT_DF(워커 전역)에서 읽는다."""
    mkt = ai.MKT_DF if mkt is None else mkt
    X = ai.compute_features(df, mkt, False)
    D = pd.DataFrame(X, columns=ai.NAMES, index=df.index)
    D = D[[k for k in D.columns if not k.startswith("sig") and k not in ("dante_n10", "sig_n10")]]
    D = pd.concat([D, extra_features(df)], axis=1)
    c, v = df["close"], df["volume"]
    ret = c.pct_change()
    D["mom12_1"] = c.shift(21) / c.shift(252) - 1
    D["mom6_1"] = c.shift(21) / c.shift(126) - 1
    D["vol60"] = ret.rolling(60).std()
    D["above200"] = (c > c.rolling(200).mean()).astype(float)
    val = c * v
    D["turn"] = val.rolling(20).mean() / val.rolling(120).mean()
    D["liqv"] = val.rolling(20).mean()
    if mkt is not None:
        m = mkt.reindex(df.index.strftime("%Y-%m-%d")).set_axis(df.index)
        if "mkt_ret" in m:
            D["beta60"] = ret.rolling(60).cov(m["mkt_ret"]) / m["mkt_ret"].rolling(60).var().replace(0, np.nan)
        D["rs60"] = D["ret60"] - D["mkt_r60"]
    D["close"] = c
    D["kosdaq"] = kosdaq
    return D


def feature_columns(cols) -> list:
    return [c for c in cols if c not in META and not c.startswith("mkt_") and c not in ("price_level", "hist_bars")]


# ------------------------------------------------------------------ 학습 표본 / 오늘 행
def _kosdaq_map() -> dict:
    with db.get_conn() as c:
        return {r[0]: (1.0 if (r[1] or "").startswith("KOSDAQ") else 0.0) for r in c.execute("SELECT code, market FROM symbols")}


def _work_train(args):
    code, kq = args
    try:
        df = service.load_prices(code)
        n = len(df)
        if n < ai.MIN_BARS + 130:
            return None
        D = stock_frame(df, kq)
        o, c, h, l = (df[k].to_numpy(float) for k in ("open", "close", "high", "low"))
        i = np.arange(n)
        for hz in (5, 20, 60, 120):
            r = np.full(n, np.nan)
            ok = i + 1 + hz < n
            r[ok] = c[i[ok] + 1 + hz] / o[i[ok] + 1] - 1 - COST
            D[f"r{hz}"] = r
        D["date"] = df.index.strftime("%Y-%m-%d")
        D["code"] = code
        sel = (df.index.dayofyear.to_numpy() % 5 == 0) & (D["liqv"].to_numpy() >= MIN_LIQ) & (i >= 260) & np.isfinite(D["ret60"].to_numpy())
        return D[sel].reset_index(drop=True)
    except Exception:
        return None


def _work_today(args):
    code, kq = args
    try:
        df = service.load_prices(code)
        if len(df) < ai.MIN_BARS + 130:
            return None
        D = stock_frame(df, kq)
        r = D.iloc[[-1]].copy()
        if not np.isfinite(r["ret60"].iloc[0]) or not (r["liqv"].iloc[0] >= MIN_LIQ):
            return None
        r["date"] = df.index[-1].strftime("%Y-%m-%d")
        r["code"] = code
        r["ret20_now"] = float(df["close"].iloc[-1] / df["close"].iloc[-21] - 1) if len(df) > 21 else np.nan
        return r.reset_index(drop=True)
    except Exception:
        return None


def _pool(fn, items, workers):
    workers = workers or max(1, min(6, (os.cpu_count() or 2) - 2))
    with ProcessPoolExecutor(workers, initializer=ai._init_worker, initargs=(bt.MKT_PATH,)) as ex:
        return [p for p in ex.map(fn, items, chunksize=20) if p is not None]


def _mcap_info():
    """종목별 현재 시총·현재 종가 — 그때의 시총 추정(= 현재 시총 × 그때 종가 / 현재 종가)에 쓴다."""
    with db.get_conn() as c:
        sym = pd.DataFrame([dict(r) for r in c.execute("SELECT code, name, marcap FROM symbols")])
        last = pd.DataFrame([dict(r) for r in c.execute(
            "SELECT p.code, p.close AS close_now FROM prices p JOIN (SELECT code, MAX(date) d FROM prices GROUP BY code) m ON p.code=m.code AND p.date=m.d")])
    return sym.merge(last, on="code", how="left")


def assign_group(D: pd.DataFrame) -> pd.Series:
    """날짜마다 시총 순위로 대형(1~100)·중형(101~300)·소형·잡주(301위 밖이면서 시총 1,000억 미만 또는 주가 1,000원 미만 또는 거래대금 5억 미만)."""
    rk = D.groupby("date")["mcap_est"].rank(ascending=False, method="first")
    junk = (rk > 300) & ((D["mcap_est"] < 1000e8) | (D["close"] < 1000) | (D["liqv"] < 5e8))
    return pd.Series(np.where(rk <= 100, "대형주", np.where(rk <= 300, "중형주", np.where(junk, "잡주", "소형주"))), index=D.index)


def add_ranks(D: pd.DataFrame, cols, src=None):
    """날짜별 백분위 순위 특징(<이름>_r). src 를 주면 그 목록으로(오늘 한 날짜만 있을 때 값 종류가 적어도 학습 때와 같은 열이 나오게)."""
    src = src or [c for c in cols if D[c].nunique() > 10]
    R = D.groupby("date")[src].rank(pct=True).astype("float32")
    R.columns = [c + "_r" for c in src]
    return pd.concat([D, R], axis=1), src


def _fit_four(tr: pd.DataFrame, cols):
    """순위(20일) · 시장+8%p 확률 · 초과수익 회귀 · 60일 순위 모델 4개."""
    from sklearn.ensemble import HistGradientBoostingClassifier as HC, HistGradientBoostingRegressor as HR
    tr = tr.dropna(subset=["r20"]).copy()
    tr["y20"] = tr.groupby("date")["r20"].rank(pct=True).astype("float32")
    tr["y60"] = tr.groupby("date")["r60"].rank(pct=True).astype("float32")
    tr["up"] = (tr["x20"] >= 0.08).astype(int)
    tr["x20c"] = tr["x20"].clip(-0.5, 0.5)
    if len(tr) > 700_000:
        tr = tr.sample(700_000, random_state=1)
    X = tr[cols].astype("float32")
    t60 = tr.dropna(subset=["y60"])
    leaf = 150 if len(tr) < 500_000 else 200
    return {"rank": HR(max_iter=250, learning_rate=0.06, max_depth=6, min_samples_leaf=leaf, l2_regularization=2.0, random_state=0).fit(X, tr["y20"]),
            "up": HC(max_iter=250, learning_rate=0.07, max_depth=5, random_state=0).fit(X, tr["up"]),
            "reg": HR(max_iter=250, learning_rate=0.07, max_depth=5, min_samples_leaf=leaf, random_state=0).fit(X, tr["x20c"]),
            "r60": HR(max_iter=250, learning_rate=0.06, max_depth=6, min_samples_leaf=leaf, l2_regularization=2.0, random_state=0).fit(t60[cols].astype("float32"), t60["y60"])}


def _score_four(models, X: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({"rank": models["rank"].predict(X), "up": models["up"].predict_proba(X)[:, 1], "reg": models["reg"].predict(X), "r60": models["r60"].predict(X)}, index=X.index)


GROUPS = ("대형주", "중형주", "소형주", "잡주")
# 그룹별 최적 설정(2026-10-18, tools/research/o1~o3.py — 검증 2022~23년으로 고르고 시험 2024~26년으로 확인, 모두 20거래일 보유가 60·120일 보유보다 좋았다):
#   by = 점수 열(r_ens = 전체 모델 순위를 그룹 안 순위로, score = 그룹 모델과 전체 모델 평균), k = 상위 몇 개, risk = 하락 위험이 그룹 안 하위 50%인 종목만
#   대형주 상위 5 → 검증 +14.5% · 시험 +49.6%, 중형주 상위 10 → −7.7% · +22.4%(검증에서 수익 없음, 비권장), 소형주 상위 7 + 위험 필터 → +41.7% · +29.5%, 잡주 상위 5 → +61.1% · +84.1%
GROUP_CFG = {"대형주": {"by": "r_ens", "k": 5, "risk": False}, "중형주": {"by": "r_ens", "k": 10, "risk": False},
             "소형주": {"by": "r_ens", "k": 7, "risk": True}, "잡주": {"by": "score", "k": 5, "risk": False}}


def _quantile_table(models: dict, X: pd.DataFrame) -> dict:
    """차트용 모델 출력 → 0~1 점수로 바꾸는 표(학습 표본 출력의 0~100% 분위 101개)."""
    if len(X) > 200_000:
        X = X.sample(200_000, random_state=2)
    s = _score_four(models, X)
    return {k: np.quantile(s[k].to_numpy(), np.linspace(0, 1, 101)) for k in s.columns}


def train(workers: int = 0, log=print) -> dict:
    """전 종목 표본(약 3분) → 전체·그룹별(대형·중형·소형·잡주) 모델 4개씩 + 차트용(순위 특징 없이 88개 특징) 4개 학습(약 20분).
    결과가 확정된 날(오늘−30일)까지만 쓴다."""
    import joblib
    t0 = time.time()
    with db.get_conn() as c:
        codes = [r[0] for r in c.execute("SELECT code FROM symbols")]
    kq = _kosdaq_map()
    parts = _pool(_work_train, [(c, kq.get(c, 0.0)) for c in codes], workers)
    D = pd.concat(parts, ignore_index=True)
    for hz in (5, 20, 60, 120):
        D[f"x{hz}"] = D[f"r{hz}"] - D.groupby("date")[f"r{hz}"].transform("mean")
    info = _mcap_info()
    D = D.merge(info[["code", "marcap", "close_now"]], on="code", how="left")
    D["mcap_est"] = D["marcap"] * D["close"] / D["close_now"]
    D = D.dropna(subset=["mcap_est"])
    D["grp"] = assign_group(D)
    feats = feature_columns(D.columns)
    D, src = add_ranks(D, feats)
    cols = feats + [c + "_r" for c in src]
    cutoff = (datetime.now() - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    D = D[D["date"] < cutoff]
    log(f"표본 {len(D):,}행 · 특징 {len(cols)}개 · {time.time() - t0:.0f}초")
    obj = {"global": _fit_four(D, cols), "groups": {}}
    log(f"전체 모델 완료 {time.time() - t0:.0f}초")
    for g in GROUPS:
        sub = D[D["grp"] == g]
        obj["groups"][g] = _fit_four(sub, cols)
        log(f"{g} 모델 완료({len(sub):,}행) {time.time() - t0:.0f}초")
    chart = _fit_four(D, feats)
    obj.update({"chart": chart, "chart_q": _quantile_table(chart, D[feats].astype("float32")), "chart_cols": feats})
    obj.update({"cols": cols, "rank_src": src, "feats": feats, "trained": datetime.now().isoformat(timespec="seconds"), "through": str(D["date"].max()), "rows": int(len(D))})
    joblib.dump(obj, MODEL_PATH)
    log(f"학습 완료: {obj['through']}까지 · {time.time() - t0:.0f}초")
    return {k: obj[k] for k in ("trained", "through", "rows")}


def load_models():
    try:
        mt = MODEL_PATH.stat().st_mtime
    except OSError:
        return None
    if _cache["mtime"] != mt:
        import joblib
        _cache.update(mtime=mt, obj=joblib.load(MODEL_PATH))
    return _cache["obj"]


def models_age_days() -> float:
    try:
        return (time.time() - MODEL_PATH.stat().st_mtime) / 86400
    except OSError:
        return 1e9


# ------------------------------------------------------------------ 오늘의 후보
def score_today(D: pd.DataFrame, obj: dict) -> pd.DataFrame:
    """D: 오늘 행(특징·mcap_est·grp·date·code). 모든 종목에 점수 score(그룹 안 순위 기준, 0~1)와 그룹 안 순위를 달아 돌려준다.
    점수 = (그룹 모델 4개의 그룹 안 순위 평균 + 전체 모델 4개의 순위 평균을 그룹 안 순위로 바꾼 값) / 2."""
    D, _ = add_ranks(D, obj["feats"], obj.get("rank_src"))
    X = D[obj["cols"]].astype("float32")
    gs = _score_four(obj["global"], X)
    ens = pd.concat([gs[k].rank(pct=True) for k in ("rank", "up", "reg", "r60")], axis=1).mean(axis=1)        # 전체 종목 안 순위 평균
    parts = []
    for g in GROUPS:
        J = D[D["grp"] == g].copy()
        if J.empty:
            continue
        models = (obj.get("groups") or {}).get(g) or obj.get("junk")
        js = _score_four(models, X.loc[J.index])
        ens_j = pd.concat([js[k].rank(pct=True) for k in ("rank", "up", "reg", "r60")], axis=1).mean(axis=1)    # 그룹 안 순위 평균
        r_ens = ens.loc[J.index].rank(pct=True)
        J["score"] = (ens_j + r_ens) / 2
        J["ens_j"], J["r_ens"] = ens_j, r_ens
        J["grp_rank"] = J["score"].rank(ascending=False, method="first").astype(int)
        J["grp_n"] = len(J)
        parts.append(J)
    return pd.concat(parts).sort_values(["grp", "score"], ascending=[True, False]) if parts else D.iloc[0:0]


def _item(rank, r):
    return {"rank": rank, "code": r.code, "name": r.name, "close": float(r.close), "value_eok": round(float(r.liqv) / 1e8, 1), "mcap_eok": round(float(r.mcap_est) / 1e8),
            "score": round(float(r.score), 3), "ret20": (None if not np.isfinite(r.ret20_now) else round(float(r.ret20_now), 3)), "main": rank <= TOP_N, "kosdaq": bool(r.kosdaq)}


def refresh(workers: int = 0, log=print) -> dict:
    """오늘 그룹별 점수 상위 후보를 계산해 data/picks_today.json 에 저장. 모델이 없거나 30일이 넘었으면 먼저 학습한다."""
    if models_age_days() > MAX_AGE_DAYS:
        log("모델 학습 필요 — 먼저 학습합니다")
        train(workers, log)
    obj = load_models()
    with db.get_conn() as c:
        codes = [r[0] for r in c.execute("SELECT code FROM symbols")]
    kq = _kosdaq_map()
    rows = _pool(_work_today, [(c, kq.get(c, 0.0)) for c in codes], workers)
    D = pd.concat(rows, ignore_index=True)
    latest = D["date"].max()
    D = D[D["date"] == latest].reset_index(drop=True)                     # 마지막 거래일에 데이터가 있는 종목만
    info = _mcap_info()
    D = D.merge(info, on="code", how="left")
    D["mcap_est"] = D["marcap"] * D["close"] / D["close_now"]
    D = D.dropna(subset=["mcap_est"]).reset_index(drop=True)
    D["grp"] = assign_group(D)
    S = score_today(D, obj)
    S["rP"] = np.nan
    try:                                                    # 하락 위험(20일 안 −10%↓ 확률) — 그룹 안 순위. 소형주는 위험이 하위 50%인 종목만 후보
        from core import drop_risk
        mo = drop_risk.load()
        if mo is not None:
            S["risk_p"] = mo["model"].predict_proba(S[drop_risk.COLS].astype("float64"))[:, 1]
            S["rP"] = S.groupby("grp")["risk_p"].rank(pct=True)
    except Exception:
        pass
    groups = {}
    for g in GROUPS:
        cfg = GROUP_CFG[g]
        sub = S[S["grp"] == g]
        if cfg["risk"] and sub["rP"].notna().any():
            sub = sub[sub["rP"] <= 0.5]
        sub = sub.sort_values(cfg["by"], ascending=False).head(SHOW_N)
        sub = sub.assign(score=sub[cfg["by"]])
        items = [_item(i, r) for i, r in enumerate(sub.itertuples(), 1)]
        for it in items:
            it["main"] = it["rank"] <= cfg["k"]
        groups[g] = {"n": int((D["grp"] == g).sum()), "k": cfg["k"], "risk_filter": cfg["risk"], "picks": items}
    scores = {r.code: [r.grp, round(float(r.score), 3), int(r.grp_rank), int(r.grp_n)] for r in S.itertuples()}
    out = {"date": latest, "generated": datetime.now().isoformat(timespec="seconds"), "model_through": obj["through"], "universe": int(len(D)), "groups": groups, "scores": scores}
    out["picks"] = groups["잡주"]["picks"]                                  # 옛 화면 호환
    out["junk"] = groups["잡주"]["n"]
    TODAY_PATH.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    try:                                                    # AI 점수 필터 기록(대형주·중형주 그룹 안 순위)
        gate_update(S, str(latest)[:10])
    except Exception as e:
        log(f"AI 필터 기록 실패: {e}")
    return out


# ------------------------------------------------------------------ AI 점수 필터(차트 앱 stock_html 이 매수 신호를 거를 때 쓴다)
GATE_PATH = config.DATA_DIR / "picks_gate.json"
GATE_GROUPS = ("대형주", "중형주")        # 필터를 쓰는 그룹(대형주 상위 50% · 중형주 상위 20% 이상일 때만 매수)


def gate_load(path=None) -> dict:
    try:
        return json.loads((path or GATE_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"dates": [], "codes": {}}


def gate_update(S: pd.DataFrame, date: str, path=None, out=None) -> dict:
    """오늘 점수(S: code·grp·r_ens)를 날짜별 기록에 더한다. 값 = 그룹 안 전체 모델 순위(0~100), 대형주·중형주가 아니면 -1.
    기록은 data/picks_gate.json, out(또는 환경변수 STOCK_GATE_OUT)이 있으면 같은 내용을 그 경로에도 쓴다."""
    G = gate_load(path)
    dates = G["dates"]
    if date in dates:
        k = dates.index(date)
    else:
        dates.append(date); k = len(dates) - 1
        for v in G["codes"].values():
            v[1].append(-1)
    seen = set()
    for r in S.itertuples():
        seen.add(r.code)
        ent = G["codes"].setdefault(r.code, [r.grp, [-1] * len(dates)])
        ent[0] = r.grp
        ent[1][k] = int(round(float(r.r_ens) * 100)) if r.grp in GATE_GROUPS else -1
    for code, v in G["codes"].items():
        if code not in seen:
            v[1][k] = -1
    txt = json.dumps(G, ensure_ascii=False, separators=(",", ":"))
    (path or GATE_PATH).write_text(txt, encoding="utf-8")
    dest = out or os.environ.get("STOCK_GATE_OUT")
    if dest:
        from pathlib import Path
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_text(txt, encoding="utf-8")
    return G


def load_today():
    try:
        return json.loads(TODAY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ JB차트의 'AI 점수' · 매수/매도 표시
AI_TOP = 0.95              # 강한 매수 기준: AI 점수가 이 값(표본 상위 5%)을 처음 넘어서는 날
AI_WEAK = 0.90             # 약한 매수 기준(상위 10%)
HOLD_DAYS = 20             # 매도: 매수(다음 날 시가) 뒤 20거래일째 종가
SIGNAL_GAP = 10            # 같은 종목에서 매수 신호 사이 최소 간격(봉)
WF_PATH = config.DATA_DIR / "picks_wf.joblib"
WF_FIRST = 2016            # 해마다 다시 학습한 모델이 있는 첫 해 — 이 해부터의 과거 표시는 모두 학습에 쓰지 않은 구간
_wf_cache = {"mtime": None, "obj": None}
# 걸어가며 검증(2022~23 검증 · 2024~26 시험, 전 종목, 비용 0.3% 포함)에서 점수가 기준을 처음 넘은 날 다음 날 시가에 사서 20거래일 뒤 종가에 판 결과:
#   95점: 검증 1,068건 평균 +6.0%·승률 63%·−10%↓ 17% / 시험 1,651건 +5.3%·52%·24%,  90점: +4.9%·61%·17% / +3.7%·50%·23%  (아무 날 −0.9% · 0.0%).
BACKTEST_NOTE = ("과거 시험(전 종목): 95점(강) 처음 넘은 날 다음 날 시가에 사서 20거래일 뒤 종가에 팔면 2022~23년 평균 +6.0%(승률 63%), 2024~26년 +5.3%(승률 52%), "
                 "90점(약)은 +4.9%(61%) · +3.7%(50%)였고 20일 뒤 −10% 이하가 17~24%였습니다(아무 날 평균 −0.9% · 0.0%).")


def train_walkforward(workers: int = 0, log=print) -> dict:
    """해마다(WF_FIRST~올해) 전 해 9월 말까지의 데이터로 차트용 모델 4개를 따로 학습해 둔다 — 과거 차트의 AI 점수·매수/매도 표시를
    '그때 알 수 있던 정보만 쓴' 값으로 그리기 위해서(약 20분). 1년에 한 번이면 충분하다."""
    import joblib
    t0 = time.time()
    with db.get_conn() as c:
        codes = [r[0] for r in c.execute("SELECT code FROM symbols")]
    kq = _kosdaq_map()
    D = pd.concat(_pool(_work_train, [(c, kq.get(c, 0.0)) for c in codes], workers), ignore_index=True)
    for hz in (5, 20, 60, 120):
        D[f"x{hz}"] = D[f"r{hz}"] - D.groupby("date")[f"r{hz}"].transform("mean")
    feats = feature_columns(D.columns)
    log(f"표본 {len(D):,}행 · 특징 {len(feats)}개 · {time.time() - t0:.0f}초")
    years = {}
    for Y in range(WF_FIRST, datetime.now().year + 1):
        cut = f"{Y - 1}-09-30"                                   # 60일 뒤 결과까지 쓰는 목표가 해 초로 번지지 않게 여유를 둔다
        tr = D[D["date"] < cut]
        if len(tr) < 100_000:
            continue
        four = _fit_four(tr, feats)
        years[Y] = {"m": four, "q": _quantile_table(four, tr[feats].astype("float32")), "cut": cut}
        log(f"{Y}년용 모델 완료(학습 {len(tr):,}행, {cut} 이전) {time.time() - t0:.0f}초")
    joblib.dump({"years": years, "cols": feats, "built": datetime.now().isoformat(timespec="seconds")}, WF_PATH)
    return {"years": sorted(years), "built": datetime.now().isoformat(timespec="seconds")}


def wf_stale(max_days: int = 330) -> bool:
    """걸어가며 검증 모델이 없거나 1년 가까이 지났으면 True(해가 바뀌면 새 해용 모델이 필요)."""
    try:
        return (time.time() - WF_PATH.stat().st_mtime) / 86400 > max_days
    except OSError:
        return True


def load_wf():
    try:
        mt = WF_PATH.stat().st_mtime
    except OSError:
        return None
    if _wf_cache["mtime"] != mt:
        import joblib
        _wf_cache.update(mtime=mt, obj=joblib.load(WF_PATH))
    return _wf_cache["obj"]


def _calibrate(q: dict, out: pd.DataFrame) -> np.ndarray:
    """모델 출력 4개를 각자 학습 표본 분포의 백분위(0~1)로 바꿔 평균 — 종목 한 개만으로 계산할 수 있는 점수."""
    cols = [np.searchsorted(q[k], out[k].to_numpy(), side="right") / 100.0 for k in ("rank", "up", "reg", "r60")]
    return np.clip(np.mean(cols, axis=0), 0.0, 1.0)


def _score_series(df, F, obj, wf):
    """날짜별 AI 점수. 최종 모델 학습 끝(through) 이후는 최종 모델, 그 이전은 그 해용 걸어가며 검증 모델(없는 해는 점수 없음). oos=학습에 쓰지 않은 값인지."""
    n = len(df)
    X = F[obj["chart_cols"]].astype("float32")
    valid = np.isfinite(F["ret60"].to_numpy())
    dates = df.index.strftime("%Y-%m-%d").to_numpy()
    years = df.index.year.to_numpy()
    through = obj.get("through", "")
    score = np.full(n, np.nan)
    oos = np.zeros(n, bool)
    after = valid & (dates > through)
    if after.any():
        score[after] = _calibrate(obj["chart_q"], _score_four(obj["chart"], X[after]))
        oos[after] = True
    before = valid & (dates <= through)
    if wf and list(wf["cols"]) == list(obj["chart_cols"]):
        for Y in np.unique(years[before]):
            y = wf["years"].get(int(Y))
            m = before & (years == Y)
            if y and m.any():
                score[m] = _calibrate(y["q"], _score_four(y["m"], X[m]))
                oos[m] = True
    elif before.any():                                   # 걸어가며 검증 모델이 없으면 최종 모델로(학습 구간 포함 — 실제보다 좋게 보임)
        score[before] = _calibrate(obj["chart_q"], _score_four(obj["chart"], X[before]))
    return score, oos


def _stats(rets):
    if not rets:
        return None
    r = np.asarray(rets, float)
    return {"n": int(len(r)), "win": round(float((r > 0).mean()), 3), "mean": round(float(r.mean()), 4)}


def chart_score_recent(df: pd.DataFrame, code: str, mkt=None, last: int = 30, tail: int = 600):
    """최근 last 봉의 JB차트 AI 점수 [(날짜, 점수 0~1)] — 신호 화면(오늘 살 종목·매도 신호)용. 최종 모델만 쓴다(오늘·최근은 학습에 안 쓴 구간).
    특징은 마지막 tail 봉만으로 계산 — 전체 기록으로 계산한 값과 같음을 확인함(2026-10-19, 600봉). 모델이 없으면 None."""
    obj = load_models()
    if obj is None or "chart" not in obj or len(df) < ai.MIN_BARS:
        return None
    with db.get_conn() as c:
        r = c.execute("SELECT market FROM symbols WHERE code=?", (code,)).fetchone()
    kq = 1.0 if r and (r["market"] or "").startswith("KOSDAQ") else 0.0
    d = df.iloc[-tail:]
    F = stock_frame(d, kq, mkt)
    X = F[obj["chart_cols"]].astype("float32").iloc[-last:]
    ok = np.isfinite(F["ret60"].to_numpy()[-last:])
    s = np.full(len(X), np.nan)
    if ok.any():
        s[ok] = _calibrate(obj["chart_q"], _score_four(obj["chart"], X[ok]))
    return [(t.strftime("%Y-%m-%d"), float(v)) for t, v in zip(X.index, s)]


def ai_chart(df: pd.DataFrame, code: str, tf: str = "D", bars: int = 750, mkt=None):
    """JB차트의 AI 점수(0~100%)와 매수·매도 표시. 점수 = 차트 지표 88개로 만든 모델 4개의 평균 백분위.
    매수 = 점수가 95점(강)·90점(약)을 처음 넘은 다음 날 시가, 매도 = 매수 후 20거래일째 종가. 과거 표시는 해마다 다시 학습한 모델(학습에 안 쓴 구간)로 계산한다."""
    from core import predict
    obj = load_models()
    if obj is None or "chart" not in obj:
        return None
    wf = load_wf()
    with db.get_conn() as c:
        r = c.execute("SELECT market FROM symbols WHERE code=?", (code,)).fetchone()
    kq = 1.0 if r and (r["market"] or "").startswith("KOSDAQ") else 0.0
    F = stock_frame(df, kq, mkt)
    score, oos = _score_series(df, F, obj, wf)
    n = len(df)
    start = max(n - predict._daily_bars(tf, bars), 0)
    dates = df.index.strftime("%Y-%m-%d")
    bar_index = predict._bar_index(df, tf)
    bars_of = predict._to_bar(list(dates[start:]), bar_index)
    seen = {}
    for k, i in enumerate(range(start, n)):
        if np.isfinite(score[i]):
            seen[bars_of[k]] = float(score[i])
    prob = [{"time": d, "value": round(v, 4)} for d, v in seen.items()]
    # 매수·매도 = 시총 구간별 규칙(core/jb_rules) — 신호 화면의 '오늘 살 종목'·'매도 신호'와 같은 계산
    from core import jb_rules
    cur = load_today() or {}
    info = (cur.get("scores") or {}).get(code)
    grp = info[0] if info else None
    o_arr, h_arr, l_arr, c_arr = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    tr, pend_i, _bb = jb_rules.simulate(o_arr, h_arr, l_arr, c_arr, score, grp, ok=np.isfinite(score), v=df["volume"].to_numpy(float))   # 걸어가며 검증 모델이 없는 해는 학습 구간 점수(seen 표시)
    buy_txt, sell_txt = jb_rules.describe(grp)
    markers, trades, pending = [], [], None
    for i, b, j, px, ret, why in tr:
        if b >= start:
            markers.append({"date": predict._to_bar([dates[b]], bar_index)[0], "side": "buy", "tier": "강", "label": "매수",
                            "why": f"{dates[i]} AI 점수 {score[i] * 100:.0f}점(95점 상향 돌파) · 볼린저 가운데 아래 → {dates[b]} 시가 {o_arr[b]:,.0f}원에 매수"})
        if j is not None:
            trades.append({"buy": dates[b], "buy_px": float(o_arr[b]), "sell": dates[j], "sell_px": px, "ret": round(ret, 4), "tier": "강", "seen": bool(not oos[i])})
            if j >= start:
                markers.append({"date": predict._to_bar([dates[j]], bar_index)[0], "side": "sell", "tier": "강", "label": f"매도 {ret * 100:+.0f}%",
                                "why": f"{dates[b]} 매수 → {dates[j]} {why}, {px:,.0f}원에 매도 (비용 포함 {ret * 100:+.1f}%)"})
        else:
            pending = {"state": "holding", "tier": "강", "signal": dates[i], "buy": dates[b], "buy_px": float(o_arr[b]),
                       "now_ret": round(float(c_arr[-1] / o_arr[b] - 1), 4), "days": int(n - 1 - b)}
    if pend_i is not None:
        pending = {"state": "buy_tomorrow", "tier": "강", "signal": dates[pend_i], "score": round(float(score[pend_i]) * 100)}
    # 표시 구간의 정확도: 이 차트의 거래 결과 + 같은 기간 아무 날 사서 20일 보유한 평균
    valid_i = np.flatnonzero(np.isfinite(score[start:]))
    s0 = start + int(valid_i[0]) if len(valid_i) else start          # 점수가 있는 첫 날(걸어가며 검증 모델이 없는 해는 점수가 없다)
    shown = [t for t in trades if t["buy"] >= dates[s0]]
    idx = np.arange(s0, n - HOLD_DAYS - 1)
    base = float(np.mean(c_arr[idx + 1 + HOLD_DAYS] / o_arr[idx + 1] - 1 - 0.003)) if len(idx) else None
    accuracy = {"all": _stats([t["ret"] for t in shown]), "strong": _stats([t["ret"] for t in shown]), "weak": None,
                "base": None if base is None else round(base, 4), "oos": bool(shown and all(not t["seen"] for t in shown)), "from": dates[s0], "group": grp}
    now = float(score[-1]) if np.isfinite(score[-1]) else None
    group = {"name": info[0], "rank": info[2], "n": info[3], "date": cur.get("date")} if info else None
    if now is None:
        summary = "AI 점수를 계산할 데이터가 부족합니다(250봉 이상 필요)."
    else:
        summary = f"오늘 AI 점수 {now * 100:.0f}점" + (" — 매수 기준(95점) 위 ✔" if now >= AI_TOP else " (매수 기준 95점 + 볼린저 가운데선 아래)") + "."
        if group:
            summary += f" 그룹({group['name']}) 안 후보 순위 {group['rank']}위/{group['n']}종목(상위 {group['rank'] / group['n'] * 100:.0f}%, {group['date']} 기준)."
    through = obj.get("through", "")
    if pending:
        if pending["state"] == "buy_tomorrow":
            summary += f" ▶ 매수 신호: {pending['signal']} 점수 {pending['score']}점 → 다음 거래일 시가에 매수({grp or '구간 미정'} 규칙)."
        else:
            summary += f" ▶ 보유 중: {pending['buy']} 시가 {pending['buy_px']:,.0f}원 매수({pending['now_ret'] * 100:+.1f}%, {pending['days']}거래일째) — 매도 조건: {sell_txt}."
    elif now is not None and now < AI_TOP:
        summary += " ▶ 지금은 매수 신호가 없습니다(95점 미만)."
    a = accuracy["all"]
    if a:
        summary += (f" ▶ 이 차트({accuracy['from']}~)의 거래 {a['n']}건({grp or '소형주'} 규칙): 승률 {a['win'] * 100:.0f}% · 평균 {a['mean'] * 100:+.1f}%"
                    + (f" (같은 기간 아무 날 사서 20일 보유하면 평균 {accuracy['base'] * 100:+.1f}%)" if accuracy["base"] is not None else "")
                    + (" — 모두 학습에 쓰지 않은 구간(해마다 다시 학습한 모델)." if accuracy["oos"] else " — 일부는 모델이 학습에 쓴 구간이라 실제보다 좋게 나옵니다."))
    wf_note = (f"{WF_FIRST}년 이후의 과거 표시는 해마다 전 해 9월까지의 데이터로 다시 학습한 모델로 계산해 학습에 쓰지 않은 구간입니다(그 이전 해는 점수 없음)."
               if wf else f"모델은 {through}까지 결과가 확정된 데이터로 학습해 그 이전 구간은 모델이 본 데이터라 실제보다 좋아 보입니다(python manage.py picks-wf 로 걸어가며 검증 모델을 만들면 해결).")
    return {"name": "AI", "kind": "ai", "label": "AI 점수", "prob": prob, "thr": AI_TOP, "thr_weak": AI_WEAK, "markers": markers,
            "latest": {"prob": now, "top": bool(now is not None and now >= AI_TOP)},
            "trades": trades[-12:], "pending": pending, "accuracy": accuracy, "backtest": BACKTEST_NOTE, "wf": bool(wf),
            "group": group, "through": through, "summary": summary, "rule": {"group": grp, "buy": buy_txt, "sell": sell_txt, "note": jb_rules.STATUS.get(grp or "", "")},
            "note": "선 = 날짜마다 그날까지의 정보로 계산한 AI 점수(차트 지표 88개 · 20일 순위·시장+8%p 확률·초과수익·60일 순위 모델 4개의 평균 백분위). 분홍 점선 = 95점(강한 매수), "
                    f"▲ 매수 = {buy_txt}, ▼ 매도({grp or '소형주'} 규칙) = {sell_txt}(글자 = 그 거래의 수익률). "
                    "규칙은 시총 구간(대형·중형·소형·잡주)마다 따로 고른 것입니다(학습에 쓰지 않은 구간, 2017~2021년과 2022년 이후 모두 예전 '20일 보유' 규칙보다 나음 — 다만 두 기간을 보고 고른 규칙이라 실제보다 좋게 나올 수 있음). "
                    + jb_rules.STATUS.get(grp or "", "") + " " + wf_note}
