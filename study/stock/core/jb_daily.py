"""신호 화면의 '오늘 살 종목'·'보유 중'·'매도 신호' — JB차트와 같은 시총 구간별 규칙(core/jb_rules)을 전 종목에 적용(2026-10-19).

- 날짜별 AI 점수 기록: data/jb_scores_daily.pkl {코드: (날짜[], 점수[])}, 최근 KEEP 봉. 처음에는 연구 결과(data/research/jb_scores.pkl)로 채우고,
  장 마감 갱신 때마다 최근 몇 봉만 다시 계산해 덧붙인다(picks.chart_score_recent — 최종 모델).
- 결과: data/jb_actions.json — 구간별 오늘 살 종목(어제 마감 신호 → 오늘 시가 매수), 보유 중(규칙으로 산 뒤 아직 매도 조건이 안 온 종목), 매도 신호(오늘 마감에 매도 조건).
  '보유 중'은 규칙대로 샀다고 가정한 것이다(실제 보유 여부는 모름).
"""
import json
import os
import pickle
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime

import numpy as np

import config
from core import ai, db, jb_rules, picks, service
from core import backtest as bt

STORE = config.DATA_DIR / "jb_scores_daily.pkl"
OUT = config.DATA_DIR / "jb_actions.json"
SEED = config.DATA_DIR / "research" / "jb_scores.pkl"
KEEP = 1800                 # 기록해 둘 최근 봉 수(약 7년 — 휴대폰 스냅샷 차트(2021~)의 매수·매도 표시까지)
GROUPS = ("대형주", "중형주", "소형주", "잡주")


def load_store() -> dict:
    try:
        return pickle.load(open(STORE, "rb"))
    except (OSError, EOFError, pickle.UnpicklingError):
        return {}


def seed(log=print) -> int:
    """연구용 점수(학습에 쓰지 않은 구간)로 기록을 처음 채운다."""
    if not SEED.exists():
        return 0
    src = pickle.load(open(SEED, "rb"))
    st = load_store()
    for code, t in src.items():
        dates, sc, oos = t[0], np.asarray(t[6], float), np.asarray(t[7], bool)
        sc = np.where(oos, sc, np.nan)
        st[code] = (list(dates[-KEEP:]), list(sc[-KEEP:]))
    pickle.dump(st, open(STORE, "wb"))
    log(f"AI 점수 기록 채움 {len(st)}종목")
    return len(st)


def _recent(code):
    try:
        df = service.load_prices(code)
        if len(df) < ai.MIN_BARS:
            return code, None
        return code, picks.chart_score_recent(df, code, ai.MKT_DF, last=8)
    except Exception:
        return code, None


def update(codes=None, workers: int = 0, log=print) -> int:
    """최근 8봉 점수를 다시 계산해 기록에 합친다(장 마감 갱신 뒤)."""
    st = load_store()
    if codes is None:
        cur = picks.load_today() or {}
        codes = sorted(set((cur.get("scores") or {}).keys()) | set(st.keys()))
    workers = workers or max(1, min(6, (os.cpu_count() or 2) - 2))
    t0, n = time.time(), 0
    with ProcessPoolExecutor(max_workers=workers, initializer=ai._init_worker, initargs=(bt.MKT_PATH,)) as ex:
        for i, (code, rec) in enumerate(ex.map(_recent, codes, chunksize=8), 1):
            if not rec:
                continue
            d0, s0 = st.get(code, ([], []))
            m = dict(zip(d0, s0))
            for d, v in rec:
                m[d] = v
            ds = sorted(m)[-KEEP:]
            st[code] = (ds, [m[d] for d in ds])
            n += 1
            if i % 500 == 0:
                log(f"AI 점수 {i}/{len(codes)} · {time.time() - t0:.0f}초")
    pickle.dump(st, open(STORE, "wb"))
    return n


def _prices_all(codes):
    """여러 종목 일봉을 한 번에(종목마다 따로 읽으면 느림)."""
    import pandas as pd
    out = {}
    with db.get_conn() as c:
        for k in range(0, len(codes), 300):
            part = codes[k:k + 300]
            q = f"SELECT code,date,open,high,low,close FROM prices WHERE code IN ({','.join('?' * len(part))}) AND date >= '2019-01-01' ORDER BY code,date"
            df = pd.read_sql(q, c, params=part)
            for code, g in df.groupby("code", sort=False):
                out[code] = g
    return out


def compute(date: str | None = None) -> dict:
    """그날(없으면 최신) 마감 기준 구간별 할 일. 규칙은 미래를 보지 않으므로 전체 기록으로 한 번 계산한 거래에서 그날 상태를 읽는다.
    지난 날이면 '그 뒤 결과'(규칙대로의 매도일·수익)도 붙인다."""
    st = load_store()
    cur = picks.load_today() or {}
    grp_of = {k: v[0] for k, v in (cur.get("scores") or {}).items()}
    with db.get_conn() as c:
        names = {r["code"]: r["name"] for r in c.execute("SELECT code,name FROM symbols")}
    codes = [k for k in st if grp_of.get(k) in GROUPS]
    px = _prices_all(codes)
    out = {g: {"buy": [], "hold": [], "sell": []} for g in GROUPS}
    day = ""
    for code in codes:
        g = grp_of[code]
        df = px.get(code)
        if df is None or len(df) < 60:
            continue
        dates = df["date"].to_numpy()
        k = int(np.searchsorted(dates, date, side="right")) - 1 if date else len(dates) - 1
        if k < 0:
            continue
        ds, sc = st[code]
        m = dict(zip(ds, sc))
        s = np.array([m.get(d, np.nan) for d in dates], float)
        if not np.isfinite(s[k]):
            continue                              # 그날 점수가 없으면(갱신 전·기록 밖) 건너뜀
        o, h, l, cl = (df[x].to_numpy(float) for x in ("open", "high", "low", "close"))
        tr, pend, _ = jb_rules.simulate(o, h, l, cl, s, g)
        day = max(day, dates[k])
        base = {"code": code, "name": names.get(code, code), "close": float(cl[k]), "score": round(float(s[k]) * 100), "date": dates[k]}
        if k == len(cl) - 1 and pend is not None:
            out[g]["buy"].append({**base, "signal": dates[pend]})
        for i, b, j, p2, ret, why in tr:
            later = {"exit": dates[j] if j is not None else None, "exit_ret": ret, "exit_why": why} if j is not None else {"exit": None, "now_ret": round(float(cl[-1] / o[b] - 1), 4)}
            if i == k and b < len(cl):                    # 그날 매수 신호 → 다음 날 시가 매수(지난 날이면 그 뒤 결과까지)
                out[g]["buy"].append({**base, "signal": dates[i], "buy": dates[b], "buy_px": float(o[b]), **later})
            if b <= k and (j is None or j > k):           # 그날 보유 중
                out[g]["hold"].append({**base, "buy": dates[b], "buy_px": float(o[b]), "ret": round(float(cl[k] / o[b] - 1), 4), "days": int(k - b), **later})
            if j == k:                                    # 그날 매도 신호
                out[g]["sell"].append({**base, "buy": dates[b], "buy_px": float(o[b]), "sell_px": p2, "ret": round(ret, 4), "why": why})
    for g in out:
        out[g]["buy"].sort(key=lambda r: -r["score"])
        out[g]["hold"].sort(key=lambda r: -r["ret"])
        out[g]["sell"].sort(key=lambda r: -r["ret"])
        b, s_ = jb_rules.describe(g)
        out[g]["rule"] = {"buy": b, "sell": s_, "note": jb_rules.STATUS.get(g, "")}
    latest = max((px[c_]["date"].iloc[-1] for c_ in px), default="")
    return {"date": day, "past": bool(date and day < latest), "generated": datetime.now().isoformat(timespec="seconds"), "groups": out,
            "note": "JB차트와 같은 시총 구간별 규칙. 매수·매도 신호는 그날 마감 값으로 나오고 다음 거래일 시가(매수)·그날 종가/지정가(매도)를 가정합니다. "
                    "'보유 중'은 규칙대로 샀다고 가정한 종목입니다. 지난 날을 보면 '그 뒤 결과'는 그 뒤 실제 가격으로 규칙대로 판 결과입니다. 과거 시험 결과이며 수익을 보장하지 않습니다."}


def actions(log=print) -> dict:
    """최신 마감 기준 할 일 → data/jb_actions.json."""
    rep = compute()
    OUT.write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
    g = rep["groups"]
    log(f"오늘 할 일 {rep['date']}: " + ", ".join(f"{k} 매수 {len(g[k]['buy'])}·보유 {len(g[k]['hold'])}·매도 {len(g[k]['sell'])}" for k in GROUPS))
    return rep


DAYS_DIR = config.DATA_DIR / "jb_actions_days"


def for_date(date: str) -> dict | None:
    """지난 날 할 일(파일 캐시 — 같은 날을 다시 열면 빠름). 기록(점수)이 바뀌면 새로 계산."""
    DAYS_DIR.mkdir(parents=True, exist_ok=True)
    f = DAYS_DIR / f"{date}.json"
    try:
        if f.exists() and time.time() - f.stat().st_mtime < 86400:          # 하루 안에 만든 것은 그대로(그 뒤 결과는 하루에 한 번 갱신)
            return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    rep = compute(date)
    if not rep.get("date"):
        return None
    f.write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
    return rep



def load() -> dict | None:
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def run(log=print):
    if not STORE.exists():
        seed(log)
    update(log=log)
    return actions(log)


def series(code, st=None):
    """(날짜[], 점수[]) — 기록된 JB AI 점수."""
    st = st if st is not None else load_store()
    return st.get(code, ([], []))
