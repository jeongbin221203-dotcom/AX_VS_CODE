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
KEEP = 400                  # 기록해 둘 최근 봉 수(규칙 시험에 120봉 보유 상한 + 여유)
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


def actions(log=print) -> dict:
    """전 종목에 구간별 규칙을 적용해 오늘 할 일을 모은다."""
    st = load_store()
    cur = picks.load_today() or {}
    grp_of = {k: v[0] for k, v in (cur.get("scores") or {}).items()}
    with db.get_conn() as c:
        names = {r["code"]: r["name"] for r in c.execute("SELECT code,name FROM symbols")}
    out = {g: {"buy": [], "hold": [], "sell": []} for g in GROUPS}
    last_date = ""
    for code, (ds, sc) in st.items():
        g = grp_of.get(code)
        if g not in out:
            continue
        df = service.load_prices(code)
        if len(df) < 60:
            continue
        df = df.iloc[-KEEP:]
        dates = df.index.strftime("%Y-%m-%d")
        m = dict(zip(ds, sc))
        s = np.array([m.get(d, np.nan) for d in dates], float)
        if not np.isfinite(s[-1]):
            continue                              # 마지막 봉 점수가 없으면(갱신 전) 건너뜀
        o, h, l, cl = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        tr, pend, _ = jb_rules.simulate(o, h, l, cl, s, g)
        last_date = max(last_date, dates[-1])
        base = {"code": code, "name": names.get(code, code), "close": float(cl[-1]), "score": round(float(s[-1]) * 100), "date": dates[-1]}
        if pend is not None:
            out[g]["buy"].append({**base, "signal": dates[pend]})
        if tr:
            i, b, j, px, ret, why = tr[-1]
            if j is None:
                out[g]["hold"].append({**base, "buy": dates[b], "buy_px": float(o[b]), "ret": round(float(cl[-1] / o[b] - 1), 4), "days": int(len(cl) - 1 - b)})
            elif j == len(cl) - 1:
                out[g]["sell"].append({**base, "buy": dates[b], "buy_px": float(o[b]), "sell_px": px, "ret": round(ret, 4), "why": why})
    for g in out:
        out[g]["buy"].sort(key=lambda r: -r["score"])
        out[g]["hold"].sort(key=lambda r: -r["ret"])
        out[g]["sell"].sort(key=lambda r: -r["ret"])
        b, s_ = jb_rules.describe(g)
        out[g]["rule"] = {"buy": b, "sell": s_, "note": jb_rules.STATUS.get(g, "")}
    rep = {"date": last_date, "generated": datetime.now().isoformat(timespec="seconds"), "groups": out,
           "note": "JB차트와 같은 시총 구간별 규칙. 매수·매도 신호는 그날 마감 값으로 나오고 다음 거래일 시가(매수)·그날 종가/지정가(매도)를 가정합니다. "
                   "'보유 중'은 규칙대로 샀다고 가정한 종목입니다. 과거 시험 결과이며 수익을 보장하지 않습니다."}
    OUT.write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
    log(f"오늘 할 일 {last_date}: " + ", ".join(f"{g} 매수 {len(out[g]['buy'])}·보유 {len(out[g]['hold'])}·매도 {len(out[g]['sell'])}" for g in GROUPS))
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
