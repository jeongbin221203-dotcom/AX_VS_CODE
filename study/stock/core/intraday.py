"""분봉(1·5·15·30·60분) 시세와 단타용 차트·신호.

- 시세: Yahoo Finance 차트 API(키 없음, requests). 1분봉은 최근 7일, 5·15·30분봉은 최근 60일, 60분봉은 최근 2년 정도만 준다.
  제공처 사정으로 지연되거나 비는 봉이 있을 수 있다. 일봉(FinanceDataReader·토스)과는 다른 출처다.
- 신호: 일봉에서 쓰는 단테 기법 계산(core/techniques.py)을 그대로 분봉에 적용한다 — 봉 하나가 일봉의 하루 자리. 'N일선'은 'N봉선'이 된다.
  분봉 신호는 성과 검증을 하지 않았다(일봉 기법의 ✔ 와 다름). AI(상승 확률)는 일봉으로 학습해 분봉에는 없다.
- 화면 시각: 차트 라이브러리가 UTC 로 그리므로 한국 시각에 +9시간을 더한 값을 시각으로 쓴다.
"""
import threading
import time

import numpy as np
import pandas as pd
import requests

from core import ai, db, predict, techniques

INTERVALS = {"1m": ("1m", "7d", 30), "5m": ("5m", "60d", 60), "15m": ("15m", "60d", 90), "30m": ("30m", "60d", 120), "60m": ("60m", "730d", 180)}   # 값: (yahoo 단위, 기간, 캐시 초)
URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; stock-study)", "Accept": "application/json"}
KST = 9 * 3600
MA_PERIODS = (5, 14, 20, 28, 56, 60, 112, 120, 224, 448)
_cache: dict = {}
_lock = threading.Lock()


class IntradayError(Exception):
    pass


def is_intraday(tf: str) -> bool:
    return tf in INTERVALS


def _suffixes(code):
    with db.get_conn() as c:
        r = c.execute("SELECT market FROM symbols WHERE code=?", (code,)).fetchone()
    m = (r["market"] or "").upper() if r else ""
    return [".KQ", ".KS"] if "KOSDAQ" in m else [".KS", ".KQ"]


def _download(code, iv):
    yiv, rng, _ = INTERVALS[iv]
    last = None
    for suf in _suffixes(code):
        try:
            r = requests.get(URL.format(sym=code + suf), params={"interval": yiv, "range": rng, "includePrePost": "false"}, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                last = f"HTTP {r.status_code}"
                continue
            res = (r.json().get("chart") or {}).get("result")
            if not res or not res[0].get("timestamp"):
                last = "데이터 없음"
                continue
            q = res[0]["indicators"]["quote"][0]
            df = pd.DataFrame({"ts": res[0]["timestamp"], "open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"], "volume": q["volume"]})
            df = df.dropna(subset=["open", "high", "low", "close"])
            df["volume"] = df["volume"].fillna(0)
            df = df[df["close"] > 0].drop_duplicates("ts").sort_values("ts")
            if len(df):
                return df.reset_index(drop=True)
            last = "데이터 없음"
        except (requests.RequestException, ValueError, KeyError, IndexError) as e:
            last = type(e).__name__
    raise IntradayError(f"분봉 시세를 받지 못했습니다({last})")


def fetch(code: str, iv: str) -> pd.DataFrame:
    """분봉 DataFrame(열: ts·open·high·low·close·volume). 짧게 캐시하고, 받기 실패하면 직전 값을 쓴다."""
    if iv not in INTERVALS:
        raise IntradayError("지원하지 않는 분봉 단위")
    ttl = INTERVALS[iv][2]
    key = (code, iv)
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit["t"] < ttl:
            return hit["df"]
    try:
        df = _download(code, iv)
    except IntradayError:
        if hit:
            return hit["df"]
        raise
    with _lock:
        if len(_cache) > 40:
            _cache.pop(next(iter(_cache)))
        _cache[key] = {"t": time.time(), "df": df, "sig": None}
    return df


def _sma(c, n):
    s = pd.Series(c).rolling(n, min_periods=n).mean().to_numpy()
    return s


def payload(code: str, iv: str, bars: int) -> dict:
    """/api/chart 와 같은 모양(분봉). 시각은 한국 시각을 UTC 초로 쓴 정수."""
    df = fetch(code, iv)
    t = (df["ts"].to_numpy() + KST).astype(int)
    n = len(df)
    s = max(n - max(bars, 30), 0)
    o, h, l, c, v = (df[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
    candles = [{"time": int(t[i]), "open": float(o[i]), "high": float(h[i]), "low": float(l[i]), "close": float(c[i])} for i in range(s, n)]
    vols = [{"time": int(t[i]), "value": float(v[i]), "up": bool(c[i] >= o[i])} for i in range(s, n)]
    ma = {}
    for p in MA_PERIODS:
        a = _sma(c, p)
        ma[str(p)] = [{"time": int(t[i]), "value": round(float(a[i]), 4)} for i in range(s, n) if np.isfinite(a[i])]
    prev = float(c[-2]) if n > 1 else float(c[-1])
    return {"code": code, "tf": iv, "intraday": True, "candles": candles, "volume": vols, "ma": ma, "bb": {"upper": [], "mid": [], "lower": []}, "rsi": [],
            "macd": {"macd": [], "signal": [], "hist": []}, "stoch": {"k": [], "d": []}, "signals": [], "patterns": [], "levels": [],
            "last": {"date": time.strftime("%Y-%m-%d %H:%M", time.gmtime(int(t[-1]))), "close": float(c[-1]), "change": (float(c[-1]) / prev - 1) * 100 if prev else 0, "volume": float(v[-1])}}


def _signals(code, iv):
    """봉마다 단테 기법 신호(마지막으로 난 뒤 경과 봉 수, 0 = 그 봉) — 임시 날짜 인덱스로 일봉 계산을 그대로 쓴다."""
    df = fetch(code, iv)
    with _lock:
        hit = _cache.get((code, iv))
        if hit and hit.get("sig") is not None and hit["df"] is df:
            return df, hit["sig"]
    fake = pd.DataFrame({k: df[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume")}, index=pd.date_range("1990-01-01", periods=len(df), freq="D"))
    sig = ai.signal_features(fake)[:, :len(ai.SIGNAL_LABELS)] if len(df) >= 130 else np.full((len(df), len(ai.SIGNAL_LABELS)), 60.0)
    with _lock:
        if (code, iv) in _cache and _cache[(code, iv)]["df"] is df:
            _cache[(code, iv)]["sig"] = sig
    return df, sig


def _shown(ts):
    return time.strftime("%m-%d %H:%M", time.gmtime(int(ts) + KST))


def dante(code: str, iv: str, bars: int) -> dict:
    df, sig = _signals(code, iv)
    t = (df["ts"].to_numpy() + KST).astype(int)
    n = len(df)
    start = max(n - max(bars, 30), 0)
    markers = []
    for j in predict.DANTE_IDX:
        for i in np.flatnonzero(sig[start:, j] == 0) + start:
            markers.append({"date": int(t[i]), "shown": _shown(df["ts"].iloc[i]), "side": "buy", "label": ai.SIGNAL_LABELS[j], "why": predict.DANTE_WHY.get(ai.SIGNAL_LABELS[j], ""),
                            "good": False, "stat": "분봉 신호는 성과 검증 전"})
    markers.sort(key=lambda m: (m["date"], m["label"]))
    recent = []
    for j in predict.DANTE_IDX:
        ago = sig[-1, j]
        if np.isfinite(ago) and 0 <= ago <= 10:
            recent.append({"label": ai.SIGNAL_LABELS[j], "ago": int(ago), "good": False, "rule": "", "mean": None, "p_up": None})
    recent.sort(key=lambda r: r["ago"])
    unit = {"1m": "1분", "5m": "5분", "15m": "15분", "30m": "30분", "60m": "60분"}[iv]
    if recent:
        summary = f"최근 10봉({unit}봉) 단테 신호: " + ", ".join((f"{r['label']} {r['ago']}봉 전" if r["ago"] else f"{r['label']} 지금") for r in recent) + "."
    else:
        summary = f"최근 10봉({unit}봉) 안에 단테 기법 신호가 없습니다."
    return {"name": "예측", "kind": "dante", "markers": markers, "recent": recent, "summary": summary, "intraday": True,
            "note": f"{unit}봉에 일봉과 같은 단테 기법 계산을 적용했습니다('N일선'은 'N봉선'). 성과 검증은 일봉에서만 했고 분봉 신호는 검증 전이라 모두 회색으로 표시합니다. "
                    "시세는 제공처 사정으로 지연되거나 빠질 수 있습니다."}
