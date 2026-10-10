"""종목 목록·일봉 수집. 외부 데이터 소스는 이 파일에서만 다룬다(교체 가능)."""
import time
from datetime import date, datetime, timedelta

import pandas as pd

import config
from core import db


def _fdr():
    import FinanceDataReader as fdr
    return fdr


def update_symbols(market: str = "KRX") -> int:
    """종목 목록을 받아 symbols 에 저장한다. 반환: 저장 개수."""
    lst = _fdr().StockListing(market)
    now = datetime.now().isoformat(timespec="seconds")
    rows = []
    for r in lst.itertuples(index=False):
        code = str(getattr(r, "Code", "")).strip()
        name = str(getattr(r, "Name", "")).strip()
        if not code or not name:
            continue
        mk = str(getattr(r, "Market", market))
        marcap = getattr(r, "Marcap", None)
        marcap = None if marcap is None or pd.isna(marcap) else float(marcap)
        rows.append((code, name, mk, marcap, now))
    with db.get_conn() as c:
        c.executemany(
            "INSERT INTO symbols(code,name,market,marcap,updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(code) DO UPDATE SET name=excluded.name, market=excluded.market, "
            "marcap=excluded.marcap, updated_at=excluded.updated_at", rows)
    return len(rows)


def clean_prices(df: pd.DataFrame) -> pd.DataFrame:
    """결측·이상값 제거. 컬럼: open high low close volume, 인덱스: 날짜."""
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].copy()
    df = df.apply(pd.to_numeric, errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[(df[["open", "high", "low", "close"]] > 0).all(axis=1)]
    df = df[df["high"] >= df["low"]]
    df["volume"] = df["volume"].fillna(0)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


def fetch_daily(code: str, start: date) -> pd.DataFrame:
    """일봉 한 종목. 토스증권(키가 있을 때) → 실패하면 FinanceDataReader."""
    from core import toss
    if config.SOURCE in ("auto", "toss") and toss.configured():
        try:
            df = toss.daily_candles(code, start)
            if not df.empty:
                return df
        except toss.TossError as e:
            if config.SOURCE == "toss":
                raise
            LAST_FALLBACK["why"] = str(e)
    return _fdr().DataReader(code, start.isoformat())


LAST_FALLBACK = {"why": ""}


def _uses_toss() -> bool:
    from core import toss
    return config.SOURCE in ("auto", "toss") and toss.configured()


def last_trading_day(today: date | None = None) -> date:
    """오늘(장 마감 전이면 어제) 이전 가장 가까운 평일. 공휴일은 모르므로 그날 새 봉이 없으면 0행 저장으로 끝난다."""
    d = today or date.today()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def last_date(code: str) -> str | None:
    with db.get_conn() as c:
        row = c.execute("SELECT MAX(date) d FROM prices WHERE code=?", (code,)).fetchone()
    return row["d"]


def save_prices(code: str, df: pd.DataFrame) -> int:
    rows = [(code, idx.strftime("%Y-%m-%d"), float(r.open), float(r.high), float(r.low),
             float(r.close), int(r.volume)) for idx, r in zip(df.index, df.itertuples())]
    with db.get_conn() as c:
        c.executemany("INSERT OR REPLACE INTO prices VALUES(?,?,?,?,?,?,?)", rows)
    return len(rows)


FULL_START = date(1950, 1, 1)


def update_prices(code: str, years: int = config.DEFAULT_YEARS, full: bool = False) -> int:
    """증분 수집: DB 마지막 날짜 다음날부터만 받는다. full=True 면 상장 이후 전체 구간을
    (종목마다 한 번) 다시 받는다. 반환: 저장 행 수."""
    last = last_date(code)
    if full:
        with db.get_conn() as c:
            done = c.execute("SELECT 1 FROM meta WHERE key=?", (f"full:{code}",)).fetchone()
        if not done:
            df = fetch_daily(code, FULL_START)
            n = save_prices(code, clean_prices(df)) if df is not None and not df.empty else 0
            with db.get_conn() as c:
                c.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (f"full:{code}", datetime.now().isoformat(timespec="seconds")))
            return n
    if last:
        start = (datetime.strptime(last, "%Y-%m-%d") + timedelta(days=1)).date()
    else:
        start = date.today() - timedelta(days=365 * years)
    if start > last_trading_day():
        return 0  # 이미 최신(주말·휴일에는 호출하지 않음)
    df = fetch_daily(code, start)
    if df is None or df.empty:
        return 0
    return save_prices(code, clean_prices(df))


def update_many(codes, years: int = config.DEFAULT_YEARS, progress=None, full: bool = False) -> dict:
    ok = fail = 0
    for i, code in enumerate(codes, 1):
        try:
            update_prices(code, years, full)
            ok += 1
            msg = ""
        except Exception as e:  # 한 종목 실패가 전체를 막지 않게
            fail += 1
            msg = str(e)[:200]
        with db.get_conn() as c:
            c.execute("INSERT INTO collect_log(code,at,ok,message) VALUES(?,?,?,?)",
                      (code, datetime.now().isoformat(timespec="seconds"), 0 if msg else 1, msg))
        if progress:
            progress(i, len(codes), code, msg)
        time.sleep(0 if _uses_toss() else config.FETCH_DELAY)  # 토스는 toss._throttle 이 속도 조절
    return {"ok": ok, "fail": fail}
