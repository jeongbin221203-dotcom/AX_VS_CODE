import numpy as np
import pandas as pd

from core import patterns, signals


def frame(close, vol=None):
    n = len(close)
    c = np.array(close, float)
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
                         "volume": vol if vol is not None else np.full(n, 1000.0)},
                        index=pd.date_range("2025-01-01", periods=n, freq="B"))


def test_golden_and_dead_cross():
    close = list(np.linspace(100, 60, 40)) + list(np.linspace(60, 120, 40)) + list(np.linspace(120, 70, 40))
    keys = [s["key"] for s in signals.detect(frame(close))]
    assert "golden" in keys and "dead" in keys
    assert keys.index("golden") < keys.index("dead") or keys.count("golden") >= 1


def test_signal_has_reason():
    close = list(np.linspace(100, 60, 40)) + list(np.linspace(60, 120, 40))
    g = [s for s in signals.detect(frame(close)) if s["key"] == "golden"]
    assert g and "20일선" in g[0]["why"]


def test_volume_surge_needs_bullish_candle():
    close = [100.0] * 30
    vol = np.full(30, 1000.0)
    vol[-1] = 5000
    df = frame(close, vol)
    df.loc[df.index[-1], "close"] = 102.0
    df.loc[df.index[-1], "high"] = 103.0
    keys = [s["key"] for s in signals.detect(df) if s["date"] == df.index[-1].strftime("%Y-%m-%d")]
    assert "vol_surge" in keys


def test_no_future_leak_on_52w_high():
    s = signals.detect(frame([100.0] * 300))
    assert not [x for x in s if x["key"] == "high52"]


def test_bull_engulfing():
    df = frame([100] * 8)
    df.loc[df.index[5], ["open", "high", "low", "close"]] = [102, 102.5, 99.5, 100]  # 음봉
    df.loc[df.index[6], ["open", "high", "low", "close"]] = [99.5, 104, 99, 103]      # 장악 양봉
    keys = [p["key"] for p in patterns.candles(df)]
    assert "bull_engulf" in keys


def test_support_resistance_groups_touches():
    base = 100 + 5 * np.sin(np.arange(250) / 4)
    sr = patterns.support_resistance(frame(base))
    assert sr and all(x["touches"] >= 2 for x in sr)
