"""분봉(1·5·15·30·60분) 시세와 단타용 차트·신호.

- 시세: 최근 며칠은 토스 1분봉(OAuth, 허용 IP 필요 — 최대 8쪽 약 4거래일)을 N분봉으로 합쳐 쓰고, 그보다 오래된 구간은 Yahoo Finance 차트 API
  (키 없음: 1분봉 7일, 5·15·30분봉 60일, 60분봉 2년)로 채운다. 토스가 안 되면(키·IP) Yahoo 만, Yahoo 가 안 되면 토스만 쓴다.
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
SOURCE: dict = {}        # (코드, 단위) → {"toss": 성공 여부, "err": 토스 실패 이유} — 화면·점검용(키·토큰 값은 담지 않음)
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


def _download_yahoo(code, iv):
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


TOSS_BUDGET = 8.0         # 초
TOSS_PAGES = 8            # 토스 1분봉은 요청당 200봉 — 8번이면 약 4거래일(정규장 390봉/일)
STEP = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "60m": 60}
SESSION_START, SESSION_END = 9 * 60, 15 * 60 + 30     # 정규장(분 단위, 한국 시각)


def resample_1m(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """1분봉 → N분봉. 봉 시각은 시작 시각, 09:00 부터 N분 단위로 묶는다(하루를 넘어 묶지 않음)."""
    if minutes == 1 or df.empty:
        return df
    kst = df["ts"].to_numpy() + KST
    day, minute = kst // 86400, (kst % 86400) // 60
    bucket = (minute - SESSION_START) // minutes
    key = day * 1000 + bucket
    g = df.assign(_k=key).groupby("_k", sort=True)
    out = g.agg(ts=("ts", "first"), open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"), volume=("volume", "sum")).reset_index(drop=True)
    out["ts"] = ((day[g.head(1).index] * 86400) + (SESSION_START + ((minute[g.head(1).index] - SESSION_START) // minutes) * minutes) * 60) - KST
    return out


def _download_toss(code):
    """토스 1분봉(정규장만). 키·IP 허용이 안 되면 TossError. 최신 봉부터 TOSS_PAGES 번 거슬러 올라간다."""
    from core import toss
    if not toss.configured():
        raise IntradayError("토스 키 없음")
    rows, before, t0 = [], None, time.time()
    for _ in range(TOSS_PAGES):
        if rows and time.time() - t0 > TOSS_BUDGET:      # 느린 응답이 화면을 오래 붙잡지 않게 — 받은 만큼만 쓴다
            break
        params = {"symbol": code, "interval": "1m", "count": 200, "adjusted": "true"}
        if before:
            params["before"] = before
        try:
            res = toss._get("/api/v1/candles", params, retries=1)
        except toss.TossError as e:
            if rows:
                break
            raise IntradayError(f"토스: {e}")
        cs = res.get("candles", [])
        rows.extend(cs)
        before = res.get("nextBefore")
        if not cs or not before:
            break
    if not rows:
        raise IntradayError("토스: 분봉 없음")
    stamps = pd.to_datetime([c["timestamp"] for c in rows], utc=True)
    secs = ((stamps - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(seconds=1)).to_numpy()     # pandas 버전에 따라 단위가 달라 초로 직접 환산
    df = pd.DataFrame({"ts": secs,
                       "open": [float(c["openPrice"]) for c in rows], "high": [float(c["highPrice"]) for c in rows], "low": [float(c["lowPrice"]) for c in rows],
                       "close": [float(c["closePrice"]) for c in rows], "volume": [float(c["volume"]) for c in rows]})
    df = df.drop_duplicates("ts").sort_values("ts")
    m = ((df["ts"] + KST) % 86400) // 60
    df = df[(m >= SESSION_START) & (m < SESSION_END) & (df["close"] > 0)]       # 시간외·NXT 제외 — Yahoo 정규장 봉과 맞춘다
    return df.reset_index(drop=True)


def _download(code, iv):
    """최근 구간은 토스 1분봉(실시간에 가까움)을 합쳐 만든 봉, 그보다 오래된 구간은 Yahoo 봉. 한쪽이 안 되면 다른 쪽만."""
    yahoo = toss_df = None
    err = None
    try:
        toss_df = _download_toss(code)
        SOURCE[(code, iv)] = {"toss": True, "err": ""}
    except IntradayError as e:
        err = e
        SOURCE[(code, iv)] = {"toss": False, "err": str(e)[:120]}
    except Exception as e:                                   # 토스 쪽 예기치 못한 오류도 Yahoo 로 계속
        err = IntradayError(f"토스: {type(e).__name__}")
        SOURCE[(code, iv)] = {"toss": False, "err": f"{type(e).__name__}: {str(e)[:100]}"}
    try:
        yahoo = _download_yahoo(code, iv)
    except IntradayError as e:
        err = err or e
    if toss_df is None or toss_df.empty:
        if yahoo is None:
            raise err or IntradayError("분봉 시세를 받지 못했습니다")
        return yahoo
    recent = resample_1m(toss_df, STEP[iv])
    if yahoo is None:
        return recent.reset_index(drop=True)
    first = int(recent["ts"].iloc[0])
    older = yahoo[yahoo["ts"] < first]                                   # 겹치는 구간은 토스 값을 쓴다
    return pd.concat([older, recent], ignore_index=True)


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
    return {"code": code, "tf": iv, "intraday": True, "source": SOURCE.get((code, iv), {"toss": None, "err": ""}), "candles": candles, "volume": vols, "ma": ma, "bb": {"upper": [], "mid": [], "lower": []}, "rsi": [],
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
