"""지표 계산. 입력 DataFrame 컬럼: open high low close volume (날짜 인덱스)."""
import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def _wilder(s: pd.Series, n: int) -> pd.Series:
    """Wilder 평활: 첫 n개 평균으로 시작, 이후 (이전*(n-1)+현재)/n."""
    v = s.to_numpy(float)
    out = np.full(len(v), np.nan)
    valid = np.flatnonzero(~np.isnan(v))
    if len(valid) >= n:
        k = valid[0] + n - 1
        out[k] = v[valid[0]:k + 1].mean()
        for i in range(k + 1, len(v)):
            out[i] = (out[i - 1] * (n - 1) + v[i]) / n
    return pd.Series(out, index=s.index)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = _wilder(d.clip(lower=0), n)
    dn = _wilder(-d.clip(upper=0), n)
    out = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    return out.where(dn != 0, 100.0).where(up.notna())


def macd(close: pd.Series, fast=12, slow=26, signal=9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)  # 모집단 표준편차
    return pd.DataFrame({"mid": mid, "upper": mid + k * sd, "lower": mid - k * sd})


def stochastic(df: pd.DataFrame, n=14, k_smooth=3, d_smooth=3) -> pd.DataFrame:
    lo = df["low"].rolling(n, min_periods=n).min()
    hi = df["high"].rolling(n, min_periods=n).max()
    raw = 100 * (df["close"] - lo) / (hi - lo).replace(0, np.nan)
    k = raw.rolling(k_smooth, min_periods=k_smooth).mean()
    return pd.DataFrame({"k": k, "d": k.rolling(d_smooth, min_periods=d_smooth).mean()})


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return _wilder(tr, n)


def obv(df: pd.DataFrame) -> pd.Series:
    sign = np.sign(df["close"].diff()).fillna(0)
    return (sign * df["volume"]).cumsum()


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """'W'(주봉) / 'M'(월봉). 일봉 → 상위 봉."""
    if rule == "D":
        return df
    r = {"W": "W-FRI", "M": "ME"}[rule]
    out = df.resample(r).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    return out.dropna(subset=["open"])
