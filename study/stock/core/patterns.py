"""캔들 패턴(몸통·꼬리 비율 규칙)과 지지/저항."""
import numpy as np
import pandas as pd


def candles(df: pd.DataFrame) -> list[dict]:
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    body = np.abs(c - o)
    rng = np.maximum(h - l, 1e-9)
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l
    avg_body = pd.Series(body).rolling(10, min_periods=5).mean().shift().to_numpy()
    down_trend = pd.Series(c).rolling(5).apply(lambda x: x[-1] < x[0], raw=True).to_numpy() == 1
    up_trend = pd.Series(c).rolling(5).apply(lambda x: x[-1] > x[0], raw=True).to_numpy() == 1
    out = []

    def add(i, key, label, side):
        out.append({"date": df.index[i].strftime("%Y-%m-%d"), "key": key, "label": label, "side": side})

    for i in range(2, len(df)):
        if body[i] / rng[i] <= 0.1:
            add(i, "doji", "도지", "info")
        if down_trend[i - 1] and lower[i] >= 2 * body[i] and upper[i] <= body[i] and body[i] / rng[i] > 0.1:
            add(i, "hammer", "망치형", "buy")
        if up_trend[i - 1] and upper[i] >= 2 * body[i] and lower[i] <= body[i] and body[i] / rng[i] > 0.1:
            add(i, "shooting", "유성형", "sell")
        if c[i - 1] < o[i - 1] and c[i] > o[i] and o[i] <= c[i - 1] and c[i] >= o[i - 1] and body[i] > body[i - 1]:
            add(i, "bull_engulf", "상승 장악형", "buy")
        if c[i - 1] > o[i - 1] and c[i] < o[i] and o[i] >= c[i - 1] and c[i] <= o[i - 1] and body[i] > body[i - 1]:
            add(i, "bear_engulf", "하락 장악형", "sell")
        a = avg_body[i - 2] if not np.isnan(avg_body[i - 2]) else body[i - 2]
        if (c[i - 2] < o[i - 2] and body[i - 2] > a and body[i - 1] < 0.3 * body[i - 2]
                and c[i] > o[i] and c[i] > (o[i - 2] + c[i - 2]) / 2):
            add(i, "morning_star", "샛별형", "buy")
    return out


def support_resistance(df: pd.DataFrame, window: int = 5, lookback: int = 250, tol: float = 0.015, top: int = 4) -> list[dict]:
    """피벗 고·저점을 가격대(tol)로 묶어 터치 횟수가 많은 구간을 반환."""
    d = df.tail(lookback)
    hi, lo = d["high"].to_numpy(float), d["low"].to_numpy(float)
    pts = []
    for i in range(window, len(d) - window):
        if hi[i] == hi[i - window:i + window + 1].max():
            pts.append(hi[i])
        if lo[i] == lo[i - window:i + window + 1].min():
            pts.append(lo[i])
    pts.sort()
    groups, cur = [], []
    for p in pts:
        if cur and p > cur[0] * (1 + tol):
            groups.append(cur)
            cur = []
        cur.append(p)
    if cur:
        groups.append(cur)
    last = float(df["close"].iloc[-1])
    res = [{"price": round(float(np.mean(g)), 2), "touches": len(g),
            "kind": "resistance" if np.mean(g) > last else "support"} for g in groups if len(g) >= 2]
    res.sort(key=lambda x: -x["touches"])
    return sorted(res[:top], key=lambda x: x["price"])
