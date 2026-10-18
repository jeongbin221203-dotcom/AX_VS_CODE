"""사용자가 말한 패턴 지표를 일봉에서 만든다(2026-10-18): 이동평균 5/20/56/112/224(거리·기울기·정배열), 거래량 폭발인데 가격 변동 없음, 꾸준한 상승곡선(60·120일 추세 R²·기울기),
지지선 터치 후 반등(이평선·20일 저점), 볼린저·RSI, 하락 패턴(연속 하락·신저가 횟수·고점 대비). 표본 행(D6g의 종목×날짜)에 대해 계산 → %TEMP%/stk/F22.pkl
STOCK_SCHEDULER=0 python tools/research/q22.py"""
import os, sys, time, numpy as np, pandas as pd
from concurrent.futures import ProcessPoolExecutor
sys.path.insert(0, os.getcwd())
T = os.environ["TEMP"] + "/stk/"
MAS = (5, 20, 56, 112, 224)


def trend_fit(logp, n):
    """최근 n봉 로그가격의 직선 기울기(일평균 %)와 R²(얼마나 꾸준한 상승선인지)"""
    x = np.arange(n, dtype=float); x -= x.mean(); sx = (x ** 2).sum()
    w = np.lib.stride_tricks.sliding_window_view(logp, n)
    y = w - w.mean(axis=1, keepdims=True)
    b = (y * x).sum(axis=1) / sx
    ss_tot = (y ** 2).sum(axis=1); ss_res = ss_tot - b ** 2 * sx
    r2 = np.where(ss_tot > 0, 1 - ss_res / np.where(ss_tot > 0, ss_tot, 1), np.nan)
    pad = np.full(n - 1, np.nan)
    return np.concatenate([pad, b]), np.concatenate([pad, r2])


def feats(df):
    o, h, l, c, v = (df[k].astype(float) for k in ("open", "high", "low", "close", "volume"))
    out = {}
    ma = {k: c.rolling(k).mean() for k in MAS}
    for k in MAS:
        out[f"p_dist{k}"] = c / ma[k] - 1
        out[f"p_slope{k}"] = ma[k] / ma[k].shift(5) - 1
    order = [(5, 20), (20, 56), (56, 112), (112, 224)]
    out["p_align"] = sum((ma[a] > ma[b]).astype(float) for a, b in order)
    out["p_full_align"] = (out["p_align"] == 4).astype(float)
    out["p_full_rev"] = (out["p_align"] == 0).astype(float)
    ret = c.pct_change()
    volx = v / v.rolling(20).mean().shift(1)
    out["p_volx"] = volx
    flat_burst = ((volx >= 3) & (ret.abs() <= 0.02)).astype(float)
    out["p_burst_flat10"] = flat_burst.rolling(10).sum()
    out["p_burst_flat30"] = flat_burst.rolling(30).sum()
    out["p_burst_up10"] = ((volx >= 3) & (ret >= 0.03)).astype(float).rolling(10).sum()
    out["p_vol5_20"] = v.rolling(5).mean() / v.rolling(20).mean()
    lp = np.log(c.replace(0, np.nan)).to_numpy()
    for n in (60, 120):
        b, r2 = trend_fit(np.nan_to_num(lp, nan=0.0) if False else lp, n) if not np.isnan(lp).any() else (np.full(len(lp), np.nan), np.full(len(lp), np.nan))
        out[f"p_tslope{n}"] = b * 100; out[f"p_tr2_{n}"] = r2
    out["p_up_ratio20"] = (ret > 0).astype(float).rolling(20).mean()
    out["p_maxday20"] = ret.abs().rolling(20).max()
    # 지지선 터치 후 반등
    for k in (56, 112, 224):
        near = ((l.rolling(3).min() <= ma[k] * 1.015) & (l.rolling(3).min() >= ma[k] * 0.97)).astype(float)
        out[f"p_touch{k}"] = near
        out[f"p_bounce{k}"] = (near.rolling(3).max().astype(bool) & (c > ma[k]) & (c > o)).astype(float)
    low20 = l.rolling(20).min().shift(1)
    out["p_from_low20"] = c / low20 - 1
    out["p_swing_touch"] = ((l <= low20 * 1.01) & (l >= low20 * 0.97)).astype(float).rolling(3).max()
    out["p_swing_bounce"] = ((out["p_swing_touch"] > 0) & (c > o) & (c > c.shift(1))).astype(float)
    # 볼린저 / RSI
    m20, s20 = c.rolling(20).mean(), c.rolling(20).std(ddof=0)
    out["p_pctb"] = (c - (m20 - 2 * s20)) / (4 * s20).replace(0, np.nan)
    bw = 4 * s20 / m20; out["p_bw"] = bw; out["p_bw_pct"] = bw.rolling(120, min_periods=60).rank(pct=True)
    out["p_bandwalk5"] = (c > m20 + 2 * s20).astype(float).rolling(5).sum()
    d = c.diff(); up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean(); dn = (-d).clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan)); out["p_rsi"] = rsi; out["p_rsi_min10"] = rsi.rolling(10).min(); out["p_rsi_d5"] = rsi - rsi.shift(5)
    # 하락 패턴
    down = (ret < 0).astype(float); streak = down.groupby((down != down.shift()).cumsum()).cumsum() * down
    out["p_dn_streak"] = streak
    out["p_newlow10"] = (l <= l.rolling(20).min().shift(1)).astype(float).rolling(10).sum()
    out["p_dd60"] = c / c.rolling(60).max() - 1
    out["p_knife"] = ((out["p_slope56"] < 0) & (c < ma[56])).astype(float)
    return pd.DataFrame(out)


def work(args):
    code, dates = args
    try:
        from core import service
        df = service.load_prices(code)
        if len(df) < 260: return None
        F = feats(df); F.index = df.index.strftime("%Y-%m-%d")
        F = F.loc[F.index.isin(dates)]
        F.insert(0, "code", code); F.insert(1, "date", F.index)
        return F.reset_index(drop=True)
    except Exception:
        return None


if __name__ == "__main__":
    t0 = time.time()
    D = pd.read_pickle(T + "D6g.pkl")[["code", "date", "yr"]]; D = D[D.yr >= 2016]
    by = {c: set(g.date) for c, g in D.groupby("code")}
    with ProcessPoolExecutor(6) as ex:
        res = [r for r in ex.map(work, list(by.items()), chunksize=10) if r is not None]
    F = pd.concat(res, ignore_index=True); F.to_pickle(T + "F22.pkl")
    print(len(F), F.shape[1], f"{time.time() - t0:.0f}초")
