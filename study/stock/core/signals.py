"""지표 → 신호. 각 신호는 (날짜, 종류, 방향, 근거 문장)."""
import pandas as pd

from core import indicators as ind

BUY, SELL, INFO = "buy", "sell", "info"


def _cross_up(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a.shift() <= b.shift()) & (a > b)


def _cross_down(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a.shift() >= b.shift()) & (a < b)


def detect(df: pd.DataFrame) -> list[dict]:
    """전체 기간의 신호 목록(오래된 순)."""
    c, v = df["close"], df["volume"]
    ma5, ma20 = ind.sma(c, 5), ind.sma(c, 20)
    r = ind.rsi(c, 14)
    m = ind.macd(c)
    vol_ma = ind.sma(v, 20).shift()  # 오늘 제외 직전 20일 평균
    hi52 = c.rolling(250, min_periods=120).max().shift()
    bb = ind.bollinger(c)
    width = (bb["upper"] - bb["lower"]) / bb["mid"]
    squeeze = width <= width.rolling(120, min_periods=60).quantile(0.10)

    rules = [
        ("golden", BUY, "골든크로스", _cross_up(ma5, ma20),
         lambda i: f"5일선({ma5.iloc[i]:,.0f})이 20일선({ma20.iloc[i]:,.0f})을 위로 돌파"),
        ("dead", SELL, "데드크로스", _cross_down(ma5, ma20),
         lambda i: f"5일선({ma5.iloc[i]:,.0f})이 20일선({ma20.iloc[i]:,.0f}) 아래로 하락"),
        ("rsi_up", BUY, "RSI 과매도 탈출", _cross_up(r, pd.Series(30.0, index=r.index)),
         lambda i: f"RSI {r.iloc[i - 1]:.1f} → {r.iloc[i]:.1f}, 30 위로 회복"),
        ("rsi_down", SELL, "RSI 과열 해소", _cross_down(r, pd.Series(70.0, index=r.index)),
         lambda i: f"RSI {r.iloc[i - 1]:.1f} → {r.iloc[i]:.1f}, 70 아래로 하락"),
        ("macd_up", BUY, "MACD 상향", _cross_up(m["macd"], m["signal"]),
         lambda i: "MACD선이 시그널선을 위로 돌파"),
        ("macd_down", SELL, "MACD 하향", _cross_down(m["macd"], m["signal"]),
         lambda i: "MACD선이 시그널선 아래로 하락"),
        ("vol_surge", INFO, "거래량 급증", (v >= vol_ma * 2) & (c >= df["open"]),
         lambda i: f"거래량 {v.iloc[i]:,.0f} (20일 평균의 {v.iloc[i] / vol_ma.iloc[i]:.1f}배) 양봉"),
        ("high52", INFO, "52주 신고가", c > hi52,
         lambda i: f"종가 {c.iloc[i]:,.0f}, 직전 52주 고가 {hi52.iloc[i]:,.0f} 돌파"),
        ("squeeze", INFO, "볼린저 수축", squeeze & ~squeeze.shift().fillna(False).astype(bool),
         lambda i: f"밴드폭 {width.iloc[i] * 100:.1f}%, 최근 120일 하위 10%"),
    ]
    out = []
    for key, side, label, mask, why in rules:
        for i in [k for k, x in enumerate(mask.fillna(False).to_numpy()) if x]:
            out.append({"date": df.index[i].strftime("%Y-%m-%d"), "key": key, "side": side,
                        "label": label, "why": why(i)})
    out.sort(key=lambda s: s["date"])
    return out
