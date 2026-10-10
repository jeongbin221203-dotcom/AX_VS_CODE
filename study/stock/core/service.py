"""차트 화면용 데이터 조립."""
import numpy as np
import pandas as pd

from core import db, indicators as ind, patterns, signals


def load_prices(code: str) -> pd.DataFrame:
    with db.get_conn() as c:
        rows = c.execute("SELECT date,open,high,low,close,volume FROM prices WHERE code=? ORDER BY date", (code,)).fetchall()
    df = pd.DataFrame([tuple(r) for r in rows], columns=["date", "open", "high", "low", "close", "volume"])
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")


def _line(s: pd.Series):
    v = s.to_numpy(float)
    keep = ~np.isnan(v)
    if not keep.any():
        return []
    times = s.index[keep].strftime("%Y-%m-%d")          # 한 번에 변환(날짜마다 strftime 은 느림)
    return [{"time": t, "value": x} for t, x in zip(times, np.round(v[keep], 4).tolist())]


def chart_payload(code: str, tf: str = "D", bars: int = 500) -> dict | None:
    df = load_prices(code)
    if df.empty:
        return None
    df = ind.resample(df, tf)
    c = df["close"]
    bb = ind.bollinger(c)
    m = ind.macd(c)
    st = ind.stochastic(df)
    sig = signals.detect(df)
    pat = patterns.candles(df)
    sr = patterns.support_resistance(df)
    start = df.index[-bars:][0]
    first = start.strftime("%Y-%m-%d")

    def cut(s):
        return s[s.index >= start]

    d = cut(df)
    times = d.index.strftime("%Y-%m-%d")
    o, h, lo, cl, vo = (d[k].to_numpy(float).tolist() for k in ("open", "high", "low", "close", "volume"))
    candles = [{"time": t, "open": a, "high": b, "low": c_, "close": e} for t, a, b, c_, e in zip(times, o, h, lo, cl)]
    vols = [{"time": t, "value": v, "up": e >= a} for t, v, e, a in zip(times, vo, cl, o)]
    last = float(c.iloc[-1])
    prev = float(c.iloc[-2]) if len(c) > 1 else last
    return {
        "code": code, "tf": tf, "candles": candles, "volume": vols,
        "ma": {str(n): _line(cut(ind.sma(c, n))) for n in (5, 14, 20, 28, 56, 60, 112, 120, 224, 448)},
        "bb": {k: _line(cut(bb[k])) for k in ("upper", "mid", "lower")},
        "rsi": _line(cut(ind.rsi(c))),
        "macd": {k: _line(cut(m[k])) for k in ("macd", "signal", "hist")},
        "stoch": {k: _line(cut(st[k])) for k in ("k", "d")},
        "signals": [s for s in sig if s["date"] >= first],
        "patterns": [p for p in pat if p["date"] >= first],
        "levels": sr,
        "last": {"date": df.index[-1].strftime("%Y-%m-%d"), "close": last,
                 "change": (last / prev - 1) * 100 if prev else 0, "volume": float(df["volume"].iloc[-1])},
    }
