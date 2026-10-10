"""토스증권 Open API 클라이언트 (시세 조회 전용 — 주문 기능은 일부러 만들지 않음).

인증: OAuth2 client credentials. client 당 유효한 토큰은 1개라 재발급하면 이전 토큰이 무효가 되므로
프로세스 안에서 하나만 만들어 만료 직전까지 재사용한다.
키 설정(.env, git 제외) — 둘 중 하나:
  TOSS_CLIENT_ID=...  /  TOSS_CLIENT_SECRET=...
  toss=<client_id>:<client_secret>   (구분자 ':' ',' ';' 공백 '|' 도 허용, JSON {"client_id","client_secret"} 도 허용)
"""
import json
import os
import re
import threading
import time
from datetime import date, datetime

import pandas as pd
import requests

BASE = "https://openapi.tossinvest.com"
TIMEOUT = 15


class TossError(Exception):
    """메시지에는 키·토큰 값을 절대 넣지 않는다."""


def credentials() -> tuple[str, str] | None:
    cid, sec = os.environ.get("TOSS_CLIENT_ID"), os.environ.get("TOSS_CLIENT_SECRET")
    if cid and sec:
        return cid.strip(), sec.strip()
    raw = (os.environ.get("toss") or os.environ.get("TOSS") or "").strip()
    if not raw:
        return None
    if raw.startswith("{"):
        try:
            j = json.loads(raw)
            return str(j["client_id"]).strip(), str(j["client_secret"]).strip()
        except (ValueError, KeyError):
            raise TossError("toss 값의 JSON 형식이 올바르지 않습니다 (client_id, client_secret 필요)")
    parts = [p for p in re.split(r"[:,;|\s]+", raw.strip("\"'")) if p]
    if len(parts) == 2:
        return parts[0], parts[1]
    raise TossError("toss 값이 'client_id:client_secret' 형식이 아닙니다 "
                    "(또는 TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 두 줄로 나눠 적어 주세요)")


def configured() -> bool:
    try:
        return credentials() is not None
    except TossError:
        return False


MIN_INTERVAL = 0.08   # 초당 약 12건 — 서버 한도(일봉 20/s, 현재가 15/s)보다 낮게
_rate_lock = threading.Lock()
_last_call = [0.0]


def _throttle():
    with _rate_lock:
        wait = _last_call[0] + MIN_INTERVAL - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()


_lock = threading.Lock()
_token = {"value": None, "exp": 0.0}


def _get_token(force: bool = False) -> str:
    with _lock:
        if not force and _token["value"] and time.time() < _token["exp"] - 60:
            return _token["value"]
        cred = credentials()
        if not cred:
            raise TossError("토스증권 키가 설정되지 않았습니다 (.env 의 toss)")
        try:
            r = requests.post(f"{BASE}/oauth2/token", timeout=TIMEOUT, data={
                "grant_type": "client_credentials", "client_id": cred[0], "client_secret": cred[1]})
        except requests.RequestException as e:
            raise TossError(f"토큰 요청 연결 실패: {type(e).__name__}")
        if r.status_code != 200:
            err = ""
            try:
                err = r.json().get("error", "")
            except ValueError:
                pass
            raise TossError(f"토큰 발급 실패 (HTTP {r.status_code} {err})")
        j = r.json()
        _token["value"] = j["access_token"]
        _token["exp"] = time.time() + int(j.get("expires_in", 3600))
        return _token["value"]


def _get(path: str, params: dict, retries: int = 3):
    for attempt in range(retries + 1):
        token = _get_token(force=False)
        _throttle()
        try:
            r = requests.get(BASE + path, params=params, timeout=TIMEOUT,
                             headers={"Authorization": f"Bearer {token}"})
        except requests.RequestException as e:
            if attempt == retries:
                raise TossError(f"연결 실패: {type(e).__name__}")
            time.sleep(1 + attempt)
            continue
        if r.status_code == 401 and attempt < retries:
            _token["value"] = None  # 만료·무효 → 재발급
            continue
        if r.status_code == 429 and attempt < retries:
            time.sleep(min(float(r.headers.get("Retry-After", 1) or 1), 30))
            continue
        if r.status_code != 200:
            try:
                e = r.json().get("error", {})
                code, msg = e.get("code", ""), e.get("message", "")
            except (ValueError, AttributeError):
                code = msg = ""
            raise TossError(f"HTTP {r.status_code} {code} {msg}".strip())
        return r.json()["result"]
    raise TossError("재시도 초과")


def check() -> str:
    """키 확인: 토큰 발급 + 삼성전자 현재가. 값은 반환하지 않고 성공 문구만."""
    _get_token(force=True)
    res = _get("/api/v1/prices", {"symbols": "005930"})
    n = len(res) if isinstance(res, list) else len(res.get("prices", res)) if isinstance(res, dict) else 0
    return f"토큰 발급 성공, 현재가 조회 성공 ({n}건)"


def prices(symbols: list[str]) -> dict[str, float]:
    out = {}
    for i in range(0, len(symbols), 200):
        res = _get("/api/v1/prices", {"symbols": ",".join(symbols[i:i + 200])})
        items = res if isinstance(res, list) else res.get("prices", [])
        for it in items:
            out[it["symbol"]] = float(it["lastPrice"])
    return out


def daily_candles(symbol: str, start: date, adjusted: bool = True) -> pd.DataFrame:
    """일봉(수정주가). start 이후 전체를 페이지(200봉)씩 거슬러 올라가며 받는다."""
    rows, before = [], None
    gap = (date.today() - start).days
    count = 200 if gap > 250 else max(5, min(200, int(gap * 0.75) + 5))  # 며칠치만 필요하면 작게
    for _ in range(80):  # 안전장치: 최대 16,000봉(약 60년)
        params = {"symbol": symbol, "interval": "1d", "count": count, "adjusted": str(adjusted).lower()}
        if before:
            params["before"] = before
        res = _get("/api/v1/candles", params)
        candles = res.get("candles", [])
        rows.extend(candles)
        before = res.get("nextBefore")
        oldest = candles[-1]["timestamp"][:10] if candles else None
        if not before or not oldest or oldest <= start.isoformat():
            break
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame({
        "date": [pd.Timestamp(r["timestamp"][:10]) for r in rows],
        "open": [float(r["openPrice"]) for r in rows], "high": [float(r["highPrice"]) for r in rows],
        "low": [float(r["lowPrice"]) for r in rows], "close": [float(r["closePrice"]) for r in rows],
        "volume": [float(r["volume"]) for r in rows]}).set_index("date").sort_index()
    return df[df.index >= pd.Timestamp(start)]
