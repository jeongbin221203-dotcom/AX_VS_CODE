"""실시간 시세 — 차트의 마지막 봉을 장중에 계속 고치기 위한 서버 쪽.

- 체결: 같은 PC 의 토스 중계 서버(study/stock_html, 기본 http://127.0.0.1:5100)의 `/stream`(SSE, 실시간 체결)을 그대로 넘겨 준다.
  토스 토큰은 키당 하나만 살아 있어서, 이 앱이 따로 웹소켓·토큰을 만들면 중계 서버의 연결이 끊긴다 — 그래서 중계 서버가 켜져 있으면 그쪽을 쓴다.
- 중계 서버가 꺼져 있으면: 장 시간(평일 08:00~20:00, 넥스트레이드 포함)에만 POLL_SEC 초마다 토스 현재가를 받아 '체결'처럼 보낸다(거래량 없음).
- 오늘 일봉: 일봉 DB 는 장 마감 뒤(16:10)에 저장되므로 장중에는 오늘 봉이 없다 → 중계 서버(없으면 토스)의 오늘 일봉을 한 번 받아 차트에 붙인다.
키·토큰 값은 어디에도 출력하지 않는다.
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone

import requests

from core import toss

RELAY = os.environ.get("STOCK_RELAY_URL", "http://127.0.0.1:5100").rstrip("/")
POLL_SEC = 5
KST = timezone(timedelta(hours=9))
_today_cache: dict = {}     # 코드 → (받은 시각, 결과)


def now_kst() -> datetime:
    return datetime.now(KST)


def market_open(t: datetime | None = None) -> bool:
    """평일 08:00~20:00 — 넥스트레이드(NXT) 프리마켓 08:00~08:50·정규장 09:00~15:30·애프터마켓 15:40~20:00.
    공휴일은 따로 거르지 않는다(그날은 체결이 오지 않을 뿐)."""
    t = t or now_kst()
    if t.weekday() >= 5:
        return False
    m = t.hour * 60 + t.minute
    return 8 * 60 <= m <= 20 * 60


def sse(obj: dict) -> str:
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


def relay_ok(timeout: float = 1.5) -> bool:
    try:
        return requests.get(RELAY + "/api/status", timeout=timeout).status_code == 200
    except requests.RequestException:
        return False


def today_bar(code: str) -> dict | None:
    """오늘(한국 날짜) 일봉 {date, open, high, low, close, volume, src} — 오늘 봉이 아직 없으면 None. 10초 캐시."""
    hit = _today_cache.get(code)
    if hit and time.time() - hit[0] < 10:
        return hit[1]
    today = now_kst().strftime("%Y-%m-%d")
    out = None
    try:
        r = requests.get(RELAY + "/api/candles", params={"symbol": code, "interval": "1d", "count": 2}, timeout=4)
        if r.status_code == 200:
            for t, o, h, lo, c, v in r.json().get("rows", []):
                d = datetime.fromtimestamp(int(t), KST).strftime("%Y-%m-%d")  # 중계 서버 시각 = 한국 자정의 unix 초
                if d == today:
                    out = {"date": d, "open": o, "high": h, "low": lo, "close": c, "volume": v, "src": "relay"}
    except (requests.RequestException, ValueError, TypeError):
        out = None
    if out is None and toss.configured():
        try:
            df = toss.daily_candles(code, (now_kst() - timedelta(days=6)).date())
            if len(df) and df.index[-1].strftime("%Y-%m-%d") == today:
                x = df.iloc[-1]
                out = {"date": today, "open": float(x.open), "high": float(x.high), "low": float(x.low),
                       "close": float(x.close), "volume": float(x.volume), "src": "toss"}
        except toss.TossError:
            out = None
    _today_cache[code] = (time.time(), out)
    return out


def stream(code: str, stop_after: float | None = None):
    """SSE 줄을 만들어 내는 발생기. 중계 서버가 되면 그 체결을, 안 되면 장중 현재가 폴링을 보낸다.
    stop_after: 시험용(초) — 그 시간이 지나면 끝낸다."""
    t0 = time.time()
    alive = lambda: stop_after is None or time.time() - t0 < stop_after
    while alive():
        try:
            with requests.get(RELAY + "/stream", params={"symbols": code}, stream=True, timeout=(2, 70)) as r:
                if r.status_code == 200:
                    yield sse({"type": "source", "src": "relay"})
                    for line in r.iter_lines(decode_unicode=True):
                        if not alive():
                            return
                        if line is None:
                            continue
                        if line.startswith("data:") or line.startswith(":"):
                            yield line + "\n\n"
                    time.sleep(1)
                    continue                     # 중계 서버가 연결을 끊었으면 잠깐 뒤 다시 붙어 본다
        except requests.RequestException:
            pass
        # 중계 서버가 없으면 장중 현재가 폴링(1분 동안) 뒤 다시 중계 서버를 찾아본다
        yield sse({"type": "source", "src": "poll" if toss.configured() else "none"})
        until = time.time() + 60
        while time.time() < until and alive():
            if not toss.configured():
                yield sse({"type": "feed", "state": "nokey"})
                time.sleep(30)
                continue
            if not market_open():
                yield sse({"type": "feed", "state": "closed"})
                time.sleep(30)
                continue
            try:
                p = toss.prices([code]).get(code)
                if p:
                    yield sse({"type": "trade", "symbol": code, "price": p, "volume": 0, "time": int(time.time()), "src": "poll"})
            except toss.TossError as e:
                yield sse({"type": "feed", "state": "error", "detail": str(e)})
            time.sleep(POLL_SEC)
