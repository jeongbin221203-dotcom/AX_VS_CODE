"""장중 신호 — 오늘 미완성 일봉을 붙여 전 종목을 훑어 신호 화면에 '오늘(장중)'을 보여 준다(2026-10-19).

- 오늘 일봉: 같은 PC 의 토스 중계 서버(study/stock_html, 5100)의 /api/candles?interval=1d&count=2 로 받는다(없으면 토스 직접).
  토스 토큰은 키당 하나라 이 앱이 따로 많이 부르면 중계 서버 연결이 끊길 수 있어서 중계 서버를 먼저 쓴다.
- DB(prices)·이력(daily_checks)·확정 신호(signals.json)는 건드리지 않는다. 결과는 data/signals_live.json 에만 쓴다.
- 시장 지수(시장 필터·AI 특징)는 어제까지의 지수에 오늘 전 종목 평균 등락을 한 줄 덧붙여 계산한다(data/market_idx_live.pkl).
- 장중 값이라 마감 때까지 바뀐다(특히 거래량 조건은 장 초반에 불리). 마감 뒤(16:10) 확정 신호가 나오면 그쪽을 보여 준다.
"""
import json
import math
import os
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests

import config
from core import ai, db, live, service, signal_scan, toss
from core import backtest as bt

OUT_PATH = config.DATA_DIR / "signals_live.json"
TODAY_PATH = config.DATA_DIR / "live_today.json"
MKT_LIVE = config.DATA_DIR / "market_idx_live.pkl"
EVERY_MIN = 30                       # 장중 이 간격(분)마다 다시 훑는다
START, END = (9, 10), (15, 35)       # 정규장 안에서만(시작 직후 10분은 거래가 적어 건너뜀)
_state = {"running": False, "last": "", "msg": ""}
TODAY = {}                           # 워커 프로세스 전역: 코드 → [시, 고, 저, 종, 거래량]


def _fetch_one(code, today):
    try:
        r = requests.get(live.RELAY + "/api/candles", params={"symbol": code, "interval": "1d", "count": 2}, timeout=10)
        if r.status_code == 200:
            for t, o, h, lo, c, v in r.json().get("rows", []):
                if datetime.fromtimestamp(int(t), live.KST).strftime("%Y-%m-%d") == today and c and v:
                    return code, [float(o), float(h), float(lo), float(c), float(v)]
            return code, None
    except (requests.RequestException, ValueError, TypeError):
        pass
    if toss.configured():                     # 중계 서버가 없을 때만
        try:
            df = toss.daily_candles(code, (live.now_kst() - timedelta(days=6)).date())
            if len(df) and df.index[-1].strftime("%Y-%m-%d") == today:
                x = df.iloc[-1]
                return code, [float(x.open), float(x.high), float(x.low), float(x.close), float(x.volume)]
        except toss.TossError:
            pass
    return code, None


def fetch_today(codes, log=print, threads=8):
    today = live.now_kst().strftime("%Y-%m-%d")
    bars, t0 = {}, time.time()
    with ThreadPoolExecutor(max_workers=threads) as ex:
        for i, (code, b) in enumerate(ex.map(lambda c: _fetch_one(c, today), codes), 1):
            if b:
                bars[code] = b
            if i % 500 == 0:
                log(f"오늘 일봉 {i}/{len(codes)} · {time.time() - t0:.0f}초")
    return today, bars


def market_live(today, bars, prev_close):
    """어제까지의 시장 지수 + 오늘 한 줄(전 종목 등락 평균) → 시장 특징 다시 계산."""
    m = pd.read_pickle(bt.MKT_PATH)
    rets = [b[3] / prev_close[c] - 1 for c, b in bars.items() if prev_close.get(c)]
    if len(rets) < 30:
        return m
    ret = m["mkt_ret"].copy()
    ret.loc[today if isinstance(m.index[0], str) else pd.Timestamp(today)] = float(np.mean(rets))
    level = (1 + ret.fillna(0)).cumprod()
    out = pd.DataFrame({"mkt_r20": level / level.shift(20) - 1, "mkt_r60": level / level.shift(60) - 1,
                        "mkt_dd250": level / level.rolling(250, min_periods=120).max() - 1, "mkt_vol20": ret.rolling(20, min_periods=20).std(),
                        "mkt_ret": ret, "mkt_n": m["mkt_n"].reindex(ret.index).fillna(len(rets))})
    return out


def _init(mkt_path, today_path):
    global TODAY
    ai._init_worker(mkt_path)
    try:
        TODAY = json.loads(open(today_path, encoding="utf-8").read())["bars"]
    except (OSError, ValueError, KeyError):
        TODAY = {}


def _work(code):
    """signal_scan._work 와 같은 오늘 행(이력은 만들지 않음) — 오늘 미완성 봉을 붙여서."""
    try:
        b = TODAY.get(code)
        if not b:
            return None
        df = service.load_prices(code)
        if len(df) < ai.MIN_BARS:
            return None
        today = pd.Timestamp(TODAY_DATE[0]) if TODAY_DATE else None
        if today is None or df.index[-1] >= today:
            return None
        df.loc[today] = b
        X = ai.compute_features(df, ai.MKT_DF, True)
        x = X[-1]
        if not np.isfinite(x[ai.NAMES.index("ret60")]) or x[ai.NAMES.index("liq")] < math.log10(ai.TRADABLE_VALUE):
            return None
        row = {"code": code, "date": TODAY_DATE[0], "close": float(b[3]), "x": x.astype(float)}
        row.update(ai._avoid_latest(df))
        return row
    except Exception:
        return None


TODAY_DATE = []


def _init2(mkt_path, today_path, date):
    _init(mkt_path, today_path)
    TODAY_DATE[:] = [date]


def run(workers: int = 0, log=print, limit: int = 0, out=None) -> dict:
    if _state["running"]:
        return {"error": "이미 실행 중"}
    _state.update(running=True, msg="오늘 일봉 받는 중")
    t0 = time.time()
    try:
        with db.get_conn() as c:
            latest = (c.execute("SELECT MAX(date) d FROM prices").fetchone() or {"d": None})["d"]
            rows_db = c.execute("SELECT code, close FROM prices WHERE date=?", (latest,)).fetchall()
            names = {r["code"]: r["name"] for r in c.execute("SELECT code,name FROM symbols")}
        prev_close = {r["code"]: r["close"] for r in rows_db}
        codes = sorted(prev_close)[: limit or None]
        today, bars = fetch_today(codes, log=lambda m: _state.update(msg=m))
        if not bars or today <= (latest or ""):
            _state["msg"] = f"오늘({today}) 일봉이 없거나 이미 DB 에 있음"
            return {"error": _state["msg"]}
        TODAY_PATH.write_text(json.dumps({"date": today, "at": datetime.now().isoformat(timespec="seconds"), "bars": bars}), encoding="utf-8")
        mkt = market_live(today, bars, prev_close)
        mkt.to_pickle(MKT_LIVE)
        _state["msg"] = f"전 종목 훑는 중({len(bars)}종목)"
        workers = workers or max(1, min(3, (os.cpu_count() or 2) // 4))      # 장중에는 화면이 느려지지 않게 CPU 를 적게(3개까지)
        rows = []
        with ProcessPoolExecutor(max_workers=workers, initializer=_init2, initargs=(MKT_LIVE, TODAY_PATH, today)) as ex:
            for out in ex.map(_work, list(bars), chunksize=8):
                if out:
                    rows.append(out)
        if not rows:
            _state["msg"] = "훑은 결과가 없습니다"
            return {"error": _state["msg"]}
        rep = score(rows, names, today, mkt)
        rep["meta"].update(live=True, generated=datetime.now().isoformat(timespec="seconds"), asof=TODAY_PATH and json.loads(TODAY_PATH.read_text(encoding="utf-8"))["at"],
                           elapsed_sec=round(time.time() - t0), scanned=len(bars), prev=latest)
        (out or OUT_PATH).write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
        _state.update(last=rep["meta"]["generated"], msg=f"장중 신호 {today} {rep['meta']['generated'][11:16]} · {len(rows)}종목 · {time.time() - t0:.0f}초")
        log(_state["msg"])
        if out is None and os.environ.get("STOCK_PUBLISH") == "1":       # 휴대폰(Render)에도 같은 장중 신호
            try:
                from core import publish
                _state["msg"] += " · 휴대폰 게시 " + publish.push_live()
            except Exception as e:
                _state["msg"] += f" · 휴대폰 게시 실패: {e}"
        return rep
    except Exception as e:
        _state["msg"] = f"장중 신호 실패: {e}"
        return {"error": str(e)}
    finally:
        _state["running"] = False


def score(rows, names, date, mkt):
    """signal_scan.score 와 같은 점검 — 시장 필터는 오늘 줄을 붙인 시장 지수로."""
    from core import predict
    XL = np.vstack([r["x"] for r in rows])
    D = pd.DataFrame(XL, columns=ai.NAMES)
    for k in ("zero_vol60", "gapdn20", "down_streak", "close_px"):
        D[k] = [r[k] for r in rows]
    D["gap1"] = np.nan
    model = predict._model()
    P = model.predict_proba(XL)[:, 1] if model is not None else np.full(len(rows), np.nan)
    f = signal_scan._flags(D, P, signal_scan._halt(D))
    recs = [{"code": r["code"], "close": r["close"], **{k: (float(v[i]) if k in ("prob", "halt") else int(v[i])) for k, v in f.items()}} for i, r in enumerate(rows)]
    thr10, thr3, thr2 = signal_scan._quantiles(P)
    last = mkt.iloc[-1]
    return signal_scan._assemble(recs, date, thr10, thr3, thr2, float(last["mkt_dd250"]), float(last["mkt_r20"]), names, predict._tech_info(predict.plan()))


def load():
    """오늘 장중 신호(확정 신호보다 새것일 때만 의미). 없으면 None."""
    try:
        return json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def due(now=None) -> bool:
    """평일 정규장 안이고, 마지막 장중 훑기가 EVERY_MIN 분보다 오래됐으면 True."""
    now = now or live.now_kst()
    if now.weekday() >= 5:
        return False
    m = now.hour * 60 + now.minute
    if not (START[0] * 60 + START[1] <= m <= END[0] * 60 + END[1]):
        return False
    rep = load()
    if not rep or rep.get("meta", {}).get("date") != now.strftime("%Y-%m-%d"):
        return True
    try:
        last = datetime.fromisoformat(rep["meta"]["generated"])
    except (KeyError, ValueError):
        return True
    return (now.replace(tzinfo=None) - last).total_seconds() >= EVERY_MIN * 60


def start_thread():
    if _state["running"]:
        return False
    threading.Thread(target=run, daemon=True).start()
    return True
