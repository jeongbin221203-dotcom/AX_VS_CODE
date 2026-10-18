"""모든 차트 지표를 한 표로 — 종목×날짜(5일 간격 표본) 특징 + 미래 수익률.
기본 특징(ai.compute_features, 시장 지수 포함) + 추가 지표: RSI·MACD·볼린저·스토캐스틱·ADX·CCI·MFI·일목·켈트너·돈치안·CMF·거래량 z·갭·캔들·신고저 거리·베타·상대강도·달력.
"""
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.getcwd())
from concurrent.futures import ProcessPoolExecutor

from core import ai, backtest as bt, db, service

COST = 0.003


def ewm(s, n):
    return s.ewm(span=n, adjust=False).mean()


def extra_features(df):
    o, h, l, c, v = (df[k] for k in ("open", "high", "low", "close", "volume"))
    out = {}
    ret = c.pct_change()
    # RSI14
    d = c.diff()
    up, dn = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean(), (-d).clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    out["rsi14"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    # MACD
    macd = ewm(c, 12) - ewm(c, 26)
    sig = ewm(macd, 9)
    out["macd_n"] = macd / c
    out["macd_hist_n"] = (macd - sig) / c
    out["macd_up"] = ((macd > sig) & (macd.shift() <= sig.shift())).astype(float)
    # 볼린저
    m20, s20 = c.rolling(20).mean(), c.rolling(20).std(ddof=0)
    out["bb_pctb"] = (c - (m20 - 2 * s20)) / (4 * s20).replace(0, np.nan)
    bw = 4 * s20 / m20
    out["bb_width"] = bw
    out["bb_width_pct"] = bw.rolling(120, min_periods=60).rank(pct=True)
    # 스토캐스틱
    ll, hh = l.rolling(14).min(), h.rolling(14).max()
    k = 100 * (c - ll) / (hh - ll).replace(0, np.nan)
    out["stoch_k"] = k
    out["stoch_d"] = k.rolling(3).mean()
    # ADX
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
    # CCI20, MFI14
    tp = (h + l + c) / 3
    mad = tp.rolling(20).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True) if False else (tp - tp.rolling(20).mean()).abs().rolling(20).mean()
    out["cci20"] = (tp - tp.rolling(20).mean()) / (0.015 * mad.replace(0, np.nan))
    rmf = tp * v
    pos = rmf.where(tp > tp.shift(), 0.0).rolling(14).sum()
    neg = rmf.where(tp < tp.shift(), 0.0).rolling(14).sum()
    out["mfi14"] = 100 - 100 / (1 + pos / neg.replace(0, np.nan))
    # 일목
    tenkan, kijun = (h.rolling(9).max() + l.rolling(9).min()) / 2, (h.rolling(26).max() + l.rolling(26).min()) / 2
    spa, spb = ((tenkan + kijun) / 2).shift(26), ((h.rolling(52).max() + l.rolling(52).min()) / 2).shift(26)
    top, bot = np.maximum(spa, spb), np.minimum(spa, spb)
    out["ichi_cloud"] = np.where(c > top, (c / top - 1), np.where(c < bot, (c / bot - 1), 0.0))
    out["ichi_tk"] = (tenkan - kijun) / c
    # 켈트너·돈치안
    out["keltner"] = (c - ewm(c, 20)) / atr14.replace(0, np.nan)
    for n in (20, 55):
        mn, mx = l.rolling(n).min(), h.rolling(n).max()
        out[f"donch{n}"] = (c - mn) / (mx - mn).replace(0, np.nan)
    # CMF20, 거래량
    mfm = ((c - l) - (h - c)) / (h - l).replace(0, np.nan)
    out["cmf20"] = (mfm * v).rolling(20).sum() / v.rolling(20).sum().replace(0, np.nan)
    lv = np.log1p(v)
    out["vol_z60"] = (lv - lv.rolling(60).mean()) / lv.rolling(60).std().replace(0, np.nan)
    out["vol_trend"] = v.rolling(20).mean() / v.rolling(120).mean().replace(0, np.nan)
    # 갭·캔들
    out["gap_pct"] = o / c.shift() - 1
    out["gap_n20"] = (out["gap_pct"].abs() > 0.03).rolling(20).sum()
    out["up_streak"] = (ret > 0).astype(int).groupby((ret <= 0).cumsum()).cumsum()
    out["dn_streak"] = (ret < 0).astype(int).groupby((ret >= 0).cumsum()).cumsum()
    out["range_pct"] = (h - l) / c
    # 신고저
    out["from_high250"] = c / h.rolling(250).max() - 1
    out["from_low250"] = c / l.rolling(250).min() - 1
    hi_age = h.rolling(250).apply(lambda x: len(x) - 1 - int(np.argmax(x)), raw=True)
    out["high_age"] = hi_age / 250
    # 수익률·변동성
    out["ret120"] = c / c.shift(120) - 1
    out["ret250"] = c / c.shift(250) - 1
    out["vol20_60"] = ret.rolling(20).std() / ret.rolling(60).std().replace(0, np.nan)
    out["skew60"] = ret.rolling(60).skew()
    out["mdd60"] = (c / c.rolling(60).max() - 1).rolling(60).min()
    out["updown60"] = (ret > 0).rolling(60).mean()
    out["downvol60"] = ret.clip(upper=0).rolling(60).std()
    return pd.DataFrame(out, index=df.index)


def w(code):
    try:
        df = service.load_prices(code)
        n = len(df)
        if n < ai.MIN_BARS + 130:
            return None
        o, h, l, c, v = (df[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
        X = ai.compute_features(df, ai.MKT_DF, False)
        D = pd.DataFrame(X, columns=ai.NAMES, index=df.index)
        D = D[[k for k in D.columns if not k.startswith("sig") and k not in ("dante_n10", "sig_n10")]]
        E = extra_features(df)
        D = pd.concat([D, E], axis=1)
        s = pd.Series(c, index=df.index)
        ret = s.pct_change()
        D["mom12_1"] = (s.shift(21) / s.shift(252) - 1)
        D["mom6_1"] = (s.shift(21) / s.shift(126) - 1)
        D["vol60"] = ret.rolling(60).std()
        D["above200"] = (s > s.rolling(200).mean()).astype(float)
        D["turn"] = (pd.Series(c * v, index=df.index).rolling(20).mean() / pd.Series(c * v, index=df.index).rolling(120).mean())
        D["liqv"] = pd.Series(c * v, index=df.index).rolling(20).mean()
        if ai.MKT_DF is not None:
            m = ai.MKT_DF.reindex(df.index.strftime("%Y-%m-%d")).set_axis(df.index)
            mr = m["mkt_ret"] if "mkt_ret" in m else None
            if mr is not None:
                cov = ret.rolling(60).cov(mr)
                D["beta60"] = cov / mr.rolling(60).var().replace(0, np.nan)
            D["rs60"] = D["ret60"] - D["mkt_r60"]
        i = np.arange(n)
        for hz in (5, 20, 60, 120):
            r = np.full(n, np.nan)
            ok = i + 1 + hz < n
            r[ok] = c[i[ok] + 1 + hz] / o[i[ok] + 1] - 1 - COST
            D[f"r{hz}"] = r
        lm = pd.Series(l).rolling(20, min_periods=20).min().shift(-20).to_numpy()
        mae = np.full(n, np.nan)
        okk = i + 21 < n
        mae[okk] = lm[i[okk]] / o[i[okk] + 1] - 1
        D["mae20"] = mae
        D["date"] = df.index.strftime("%Y-%m-%d")
        D["code"] = code
        D["dow"] = df.index.dayofweek
        D["month"] = df.index.month
        sel = (df.index.dayofyear.to_numpy() % 5 == 0) & (D["liqv"].to_numpy() >= 3e8) & (i >= 260) & np.isfinite(D["ret60"].to_numpy())
        return D[sel].reset_index(drop=True)
    except Exception:
        return None


if __name__ == "__main__":
    t0 = time.time()
    with db.get_conn() as cc:
        codes = [r[0] for r in cc.execute("select code from symbols")]
        mk = {r[0]: r[1] for r in cc.execute("select code,market from symbols")}
    with ProcessPoolExecutor(6, initializer=ai._init_worker, initargs=(bt.MKT_PATH,)) as ex:
        parts = [p for p in ex.map(w, codes, chunksize=20) if p is not None]
    D = pd.concat(parts, ignore_index=True)
    D["kosdaq"] = D["code"].map(mk).fillna("").str.startswith("KOSDAQ").astype("float32")
    for hz in (5, 20, 60, 120):
        D[f"x{hz}"] = D[f"r{hz}"] - D.groupby("date")[f"r{hz}"].transform("mean")
    D["yr"] = D.date.str[:4].astype(int)
    for c in D.columns:
        if D[c].dtype == "float64":
            D[c] = D[c].astype("float32")
    print(len(D), D.shape[1], round(time.time() - t0), flush=True)
    D.to_pickle(os.environ["TEMP"] + "/stk/D6.pkl")
