import numpy as np
import pandas as pd
import pytest

from core import indicators as ind

# Wilder 교재(StockCharts) 예제 종가
WILDER = [44.3389, 44.0902, 44.1497, 43.6124, 44.3278, 44.8264, 45.0955, 45.4245, 45.8433, 46.0826,
          45.8931, 46.0328, 45.6140, 46.2820, 46.2820, 46.0028, 46.0328, 46.4116, 46.2222, 45.6439,
          46.2122, 46.2521, 45.7137, 46.4515, 45.7835, 45.3548, 44.0288, 44.1783, 44.2181, 44.5672,
          43.4205, 42.6628, 43.1314]


def series(vals):
    return pd.Series(vals, index=pd.date_range("2025-01-01", periods=len(vals), freq="B"), dtype=float)


def test_sma():
    s = ind.sma(series([1, 2, 3, 4, 5]), 3)
    assert s.isna().sum() == 2
    assert s.iloc[-1] == 4


def test_ema_first_value_is_seeded_by_close():
    s = ind.ema(series([10, 11, 12, 13]), 3)
    # α=0.5: 10 → 10.5 → 11.25 → 12.125, 처음 n-1개는 NaN
    assert s.iloc[2] == pytest.approx(11.25)
    assert s.iloc[3] == pytest.approx(12.125)


def test_rsi_matches_wilder_example():
    r = ind.rsi(series(WILDER), 14)
    assert r.iloc[:14].isna().all()
    assert r.iloc[14] == pytest.approx(70.46, abs=0.1)
    assert r.iloc[15] == pytest.approx(66.25, abs=0.2)
    assert r.iloc[-1] == pytest.approx(37.8, abs=1.0)


def test_rsi_all_up_is_100():
    r = ind.rsi(series(range(1, 40)), 14)
    assert r.iloc[-1] == 100


def test_macd_hist_is_difference():
    s = series(np.linspace(100, 140, 80) + np.sin(np.arange(80)))
    m = ind.macd(s)
    ok = m.dropna()
    assert len(ok) > 0
    assert np.allclose(ok["hist"], ok["macd"] - ok["signal"])


def test_bollinger_population_std():
    s = series([2, 4, 4, 4, 5, 5, 7, 9])
    b = ind.bollinger(s, 8, 2)
    assert b["mid"].iloc[-1] == 5
    assert b["upper"].iloc[-1] == pytest.approx(9)  # 표준편차 2(모집단)
    assert b["lower"].iloc[-1] == pytest.approx(1)


def test_stochastic_range():
    df = pd.DataFrame({"high": np.arange(20) + 2.0, "low": np.arange(20) - 1.0, "close": np.arange(20) + 1.0},
                      index=pd.date_range("2025-01-01", periods=20, freq="B"))
    st = ind.stochastic(df)
    assert st["k"].dropna().between(0, 100).all()


def test_atr_constant_range():
    df = pd.DataFrame({"high": [11.0] * 30, "low": [9.0] * 30, "close": [10.0] * 30},
                      index=pd.date_range("2025-01-01", periods=30, freq="B"))
    assert ind.atr(df).iloc[-1] == pytest.approx(2.0)


def test_obv():
    df = pd.DataFrame({"close": [10, 11, 10, 10, 12], "volume": [100, 200, 300, 400, 500]},
                      index=pd.date_range("2025-01-01", periods=5, freq="B"))
    assert list(ind.obv(df)) == [0, 200, -100, -100, 400]


def test_resample_weekly():
    idx = pd.date_range("2025-01-06", periods=10, freq="B")  # 월~금 2주
    df = pd.DataFrame({"open": range(10), "high": np.arange(10) + 5, "low": np.arange(10) - 1,
                       "close": np.arange(10) + 1, "volume": [10] * 10}, index=idx)
    w = ind.resample(df, "W")
    assert len(w) == 2
    assert w["open"].iloc[0] == 0 and w["close"].iloc[0] == 5 and w["volume"].iloc[0] == 50
    assert w["high"].iloc[0] == 9 and w["low"].iloc[0] == -1
