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


_cache = {"mtime": None, "st": {}}


def load_store() -> dict:
    """기록(약 100MB)을 한 번 읽어 두고 파일이 바뀔 때만 다시 읽는다(차트를 열 때마다 읽으면 1초씩 걸림)."""
    try:
        mt = STORE.stat().st_mtime
    except OSError:
        return {}
    if _cache["mtime"] != mt:
        try:
            _cache.update(mtime=mt, st=pickle.load(open(STORE, "rb")))
        except (OSError, EOFError, pickle.UnpicklingError):
            return {}
    return _cache["st"]


def seed(log=print) -> int:
    """연구용 점수(학습에 쓰지 않은 구간)로 기록을 처음 채운다."""
    if not SEED.exists():
        return 0
    src = pickle.load(open(SEED, "rb"))
    st = dict(load_store())
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
    st = dict(load_store())
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
            q = f"SELECT code,date,open,high,low,close,volume FROM prices WHERE code IN ({','.join('?' * len(part))}) AND date >= '2019-01-01' ORDER BY code,date"
            df = pd.read_sql(q, c, params=part)
            for code, g in df.groupby("code", sort=False):
                out[code] = g
    return out


def _with_live(df, live_day, bar):
    """DB 일봉 + 오늘 장중 봉 한 줄(시·고·저·종·거래량)."""
    import pandas as pd
    if bar is None or df["date"].iloc[-1] >= live_day:
        return df
    row = pd.DataFrame([{"code": df["code"].iloc[-1], "date": live_day, "open": bar[0], "high": bar[1], "low": bar[2], "close": bar[3], "volume": bar[4]}])
    return pd.concat([df, row], ignore_index=True)


def compute(date: str | None = None, live: dict | None = None) -> dict:
    """그날(없으면 최신) 마감 기준 구간별 할 일. 규칙은 미래를 보지 않으므로 전체 기록으로 한 번 계산한 거래에서 그날 상태를 읽는다.
    지난 날이면 '그 뒤 결과'(규칙대로의 매도일·수익)도 붙인다.
    live = {"date", "bars": {코드: [시,고,저,종,량]}, "scores": {코드: 오늘 점수}, "mkt_dd": 오늘 시장 52주 고점 대비} — 장중(15시) 계산:
    오늘 미완성 봉을 붙이고, 오늘 점수를 계산하지 않은 종목은 어제 점수를 그대로 쓴다(매수 후보가 아니라 새 신호가 날 수 없는 종목)."""
    st = load_store()
    cur = picks.load_today() or {}
    grp_of = {k: v[0] for k, v in (cur.get("scores") or {}).items()}
    with db.get_conn() as c:
        names = {r["code"]: r["name"] for r in c.execute("SELECT code,name FROM symbols")}
    codes = [k for k in st if grp_of.get(k) in GROUPS]
    px = _prices_all(codes)
    out = {g: {"buy": [], "add": [], "hold": [], "sell": []} for g in GROUPS}
    day = ""
    for code in codes:
        g = grp_of[code]
        df = px.get(code)
        if df is None or len(df) < 60:
            continue
        if live and code in live["bars"]:          # 장중 가격을 못 받은 종목은 어제까지 값으로(보유 목록에서 빠지지 않게)
            df = _with_live(df, live["date"], live["bars"][code])
        dates = df["date"].to_numpy()
        k = int(np.searchsorted(dates, date, side="right")) - 1 if date else len(dates) - 1
        if k < 0:
            continue
        ds, sc = st[code]
        m = dict(zip(ds, sc))
        s = np.array([m.get(d, np.nan) for d in dates], float)
        if live and dates[-1] == live["date"]:
            if code in live["scores"]:
                s[-1] = live["scores"][code]
            elif not np.isfinite(s[-1]) and len(s) > 1:
                s[-1] = s[-2]                     # 오늘 점수를 계산하지 않은 종목(새 신호가 날 수 없음)은 어제 점수 그대로
        if not np.isfinite(s[k]):
            continue                              # 그날 점수가 없으면(갱신 전·기록 밖) 건너뜀
        o, h, l, cl = (df[x].to_numpy(float) for x in ("open", "high", "low", "close"))
        mdd = np.array(jb_rules.market_dd(dates), float)        # (pandas 가 준 배열은 읽기 전용일 수 있어 복사)
        if live and live.get("mkt_dd") is not None and dates[-1] == live["date"]:
            mdd[-1] = live["mkt_dd"]
        tr, pend, aux = jb_rules.simulate(o, h, l, cl, s, g, v=df["volume"].to_numpy(float), mkt_dd=mdd)
        day = max(day, dates[k])
        od = jb_rules.rule_for(g).get("order") or {}
        base = {"code": code, "name": names.get(code, code), "close": float(cl[k]), "score": round(float(s[k]) * 100), "date": dates[k]}
        if k == len(cl) - 1 and pend is not None:                 # 매수 신호(지정가면 아직 안 닿아 기다리는 중 — 주문 가격·남은 날)
            row = {**base, "signal": dates[pend]}
            if od.get("buy_k"):
                row.update(limit_px=round(float(cl[pend]) * (1 - od["buy_k"])), wait_left=int(pend + od.get("buy_wait", 1) - k))
            out[g]["buy"].append(row)
        for i, b, j, p2, ret, why in tr:
            fl = jb_rules.fills_of(aux, b, o)
            e = fl[0][1]
            later = {"exit": dates[j] if j is not None else None, "exit_ret": ret, "exit_why": why} if j is not None else {"exit": None, "now_ret": round(float(cl[-1] / jb_rules.avg_price(fl) - 1), 4)}
            if i == k and b < len(cl):                    # 그날 매수 신호 → (지난 날이면) 실제 산 날·가격과 그 뒤 결과
                out[g]["buy"].append({**base, "signal": dates[i], "buy": dates[b], "buy_px": e, **later})
            if b <= k and (j is None or j > k):           # 그날 보유 중(추가 매수했으면 그날까지의 평균 단가)
                avg = jb_rules.avg_price(fl, k)
                done_adds = [x for x in fl[1:] if x[0] <= k]
                row = {**base, "buy": dates[b], "buy_px": e, "avg": round(avg, 2), "add": dates[done_adds[-1][0]] if done_adds else None, "fills": 1 + len(done_adds),
                       "ret": round(float(cl[k] / avg - 1), 4), "days": int(k - b), **later}
                ladder = od.get("ladder", [jb_rules.SPLIT])
                if len(done_adds) < len(ladder):
                    row["next_add_px"] = round(e * (1 - ladder[len(done_adds)]))      # 다음 추가 매수 가격(지정가면 걸어 둘 주문)
                out[g]["hold"].append(row)
                # 추가 매수: 지정가는 그날 장중에 체결된 것, 예전 방식은 그날 마감 −10% → 다음 거래일 시가
                today_fill = [x for x in fl[1:] if (x[0] == k if od.get("add_limit") else x[0] - 1 == k)]
                if today_fill or (k == len(cl) - 1 and b in aux["add_pending"]):
                    out[g]["add"].append({**row, "add_px": today_fill[0][1] if today_fill else None})
            if j == k:                                    # 그날 매도 신호
                out[g]["sell"].append({**base, "buy": dates[b], "buy_px": e, "avg": round(jb_rules.avg_price(fl, j), 2), "sell_px": p2, "ret": round(ret, 4), "why": why})
    for g in out:
        out[g]["buy"].sort(key=lambda r: -r["score"])
        out[g]["hold"].sort(key=lambda r: -r["ret"])
        out[g]["sell"].sort(key=lambda r: -r["ret"])
        out[g]["add"].sort(key=lambda r: r["ret"])
        b, s_ = jb_rules.describe(g)
        out[g]["rule"] = {"buy": b + f" (나눠 사기: 1차 절반 → {jb_rules.SPLIT * 100:.0f}% 떨어지면 나머지 절반)", "sell": s_, "note": jb_rules.STATUS.get(g, "")}
    latest = max((px[c_]["date"].iloc[-1] for c_ in px), default="")
    if live:
        return {"date": day, "past": False, "live": True, "asof": live.get("asof", ""), "generated": datetime.now().isoformat(timespec="seconds"), "groups": out,
                "note": f"장중({live.get('asof', '')[11:16]} 가격) 기준 — 같은 시총 구간별 규칙을 오늘 미완성 봉으로 미리 계산했습니다. 살 종목은 신호일(오늘) 장중 가격보다 3~5% 낮은 지정가 주문입니다(마감 가격이 달라지면 16시 결과의 지정가로 고칠 것). "
                        "마감까지 가격이 바뀌면 신호가 사라지거나 새로 생길 수 있고, "
                        "16시 갱신 뒤에는 마감 값으로 다시 계산한 결과가 나옵니다. 과거 시험 결과이며 수익을 보장하지 않습니다."}
    return {"date": day, "past": bool(date and day < latest), "generated": datetime.now().isoformat(timespec="seconds"), "groups": out,
            "note": "JB차트와 같은 시총 구간별 규칙. 매수 신호는 그날 마감 값으로 나오고, 다음 날부터 신호일 종가보다 3~5% 낮은 지정가로 기다려 닿으면 삽니다(안 닿으면 사지 않음). 매도는 그날 종가/지정가. "
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



LIVE_OUT = config.DATA_DIR / "jb_actions_live.json"
LIVE_BAR = {}                        # 워커 전역: 오늘 장중 봉
LIVE_PREV_MIN = 0.60                 # 15시 계산에서 오늘 점수를 다시 매길 매수 후보의 어제 점수 하한 — 95점 첫 돌파의 전날 점수는 99%가 49점↑·95%가 70점↑(2016~ 24,746건)


def _live_init(mkt_path, bars, day):
    ai._init_worker(mkt_path)
    LIVE_BAR.clear(); LIVE_BAR.update(bars); LIVE_BAR["_day"] = day


def _live_score(code):
    """오늘 장중 봉을 붙인 일봉으로 오늘 AI 점수 하나."""
    try:
        import pandas as pd
        df = service.load_prices(code)
        day = LIVE_BAR["_day"]
        df = df[df.index < pd.Timestamp(day)].copy()          # (마감 값이 이미 있으면 장중 봉으로 바꿔 계산 — 점검용)
        if len(df) < ai.MIN_BARS:
            return code, None
        df.loc[pd.Timestamp(day)] = LIVE_BAR[code]
        r = picks.chart_score_recent(df, code, ai.MKT_DF, last=1)
        return code, (r[0][1] if r and r[0][0] == day and np.isfinite(r[0][1]) else None)
    except Exception:
        return code, None


def _needs_score(df, s_prev, g, bar, busy_hold):
    """오늘 점수가 있어야 결과가 바뀌는 종목만: ① 보유 중이고 매도가 AI 점수형(중형·소형·잡주) ② 보유 중이 아니고 오늘 매수 조건(점수 빼고)을 이미 만족.
    나머지는 오늘 점수와 상관없이 할 일이 같다(대형주 보유 = 40일 보유, 볼린저·구간 조건을 못 넘은 종목 = 새 매수 없음)."""
    r = jb_rules.rule_for(g)
    if busy_hold:
        return r["exit"][0] != "hold"
    if not np.isfinite(s_prev) or s_prev >= jb_rules.TOP or s_prev < LIVE_PREV_MIN:
        return False                              # 어제 이미 95점 이상이면 '처음 넘는' 신호가 날 수 없음 · 어제 60점 아래에서 하루에 95점을 넘는 경우는 드묾
    c = np.append(df["close"].to_numpy(float), bar[3])
    if len(c) < 61:
        return False
    w = c[-20:]
    m, sd = w.mean(), w.std()
    if sd <= 0 or (c[-1] - (m - 2 * sd)) > 0.5 * (4 * sd):     # 볼린저 %B ≤ 0.5
        return False
    if r.get("ret60_max") is not None and not (c[-1] / c[-61] - 1 < r["ret60_max"]):
        return False
    if r.get("liq_max"):
        v = np.append(df["volume"].to_numpy(float), bar[4])
        if not np.log10((c[-20:] * v[-20:]).mean() + 1) < r["liq_max"]:
            return False
    return True


def live(log=print, workers: int = 0) -> dict | None:
    """장중(15시) 할 일 — live_scan 이 받아 둔 오늘 봉(data/live_today.json)·시장 지수(data/market_idx_live.pkl)로 계산해 data/jb_actions_live.json."""
    import pandas as pd
    from core import live_scan
    try:
        t = json.loads(live_scan.TODAY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    day, bars, t0 = t["date"], t["bars"], time.time()
    st = load_store()
    cur = picks.load_today() or {}
    grp_of = {k: v[0] for k, v in (cur.get("scores") or {}).items()}
    codes = [k for k in st if grp_of.get(k) in GROUPS and k in bars]
    px = _prices_all(codes)
    need = []
    for code in codes:
        df = px.get(code)
        if df is None or len(df) < 60 or df["date"].iloc[-1] >= day:
            continue
        ds, sc = st[code]
        m = dict(zip(ds, sc))
        dates = df["date"].to_numpy()
        s = np.array([m.get(d, np.nan) for d in dates], float)
        o, h, l, c = (df[x].to_numpy(float) for x in ("open", "high", "low", "close"))
        tr, pend, _ = jb_rules.simulate(o, h, l, c, s, grp_of[code], v=df["volume"].to_numpy(float), mkt_dd=jb_rules.market_dd(dates))
        hold = bool(tr and tr[-1][2] is None) or pend is not None     # 어제 신호(오늘 시가 매수)도 오늘은 보유 중
        if _needs_score(df, s[-1], grp_of[code], bars[code], hold):
            need.append(code)
    log(f"장중 할 일: 점수 계산 {len(need)}/{len(codes)}종목")
    scores = {}
    if need:
        workers = workers or max(1, min(4, (os.cpu_count() or 2) // 2))
        with ProcessPoolExecutor(max_workers=workers, initializer=_live_init, initargs=(live_scan.MKT_LIVE, {k: bars[k] for k in need}, day)) as ex:
            for code, v in ex.map(_live_score, need, chunksize=4):
                if v is not None:
                    scores[code] = v
    try:
        mdd = float(pd.read_pickle(live_scan.MKT_LIVE)["mkt_dd250"].iloc[-1])
    except Exception:
        mdd = None
    rep = compute(live={"date": day, "bars": bars, "scores": scores, "mkt_dd": mdd, "asof": t.get("at", "")})
    rep["elapsed_sec"] = round(time.time() - t0)
    rep["scored"] = len(scores)
    LIVE_OUT.write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
    g = rep["groups"]
    log(f"장중 할 일 {day} ({t.get('at', '')[11:16]} 가격, {rep['elapsed_sec']}초): " + ", ".join(f"{k} 매수 {len(g[k]['buy'])}·매도 {len(g[k]['sell'])}" for k in GROUPS))
    return rep


def load() -> dict | None:
    """오늘 할 일 — 장중(15시) 계산이 마감 기준보다 새 날짜면 그것을."""
    try:
        rep = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rep = None
    try:
        lv = json.loads(LIVE_OUT.read_text(encoding="utf-8"))
        if lv.get("date") and (rep is None or lv["date"] > rep.get("date", "")):
            return lv
    except (OSError, ValueError):
        pass
    return rep


def run(log=print):
    if not STORE.exists():
        seed(log)
    update(log=log)
    return actions(log)


def series(code, st=None):
    """(날짜[], 점수[]) — 기록된 JB AI 점수."""
    st = st if st is not None else load_store()
    return st.get(code, ([], []))


def chart(code: str, df, bars: int = 750) -> dict | None:
    """JB차트 'AI 점수'(일봉)를 기록된 점수로 바로 만든다 — picks.ai_chart(특징·모델을 매번 다시 계산, 수 초~수십 초)와 같은 모양·같은 규칙.
    기록에 마지막 봉 점수가 없으면 None(그때는 느린 길로)."""
    ds, sc = series(code)
    if not ds or len(df) < 60:
        return None
    dates = df.index.strftime("%Y-%m-%d")
    m = dict(zip(ds, sc))
    s = np.array([m.get(d, np.nan) for d in dates], float)
    if not np.isfinite(s[-1]):
        return None
    cur = picks.load_today() or {}
    info = (cur.get("scores") or {}).get(code)
    g = info[0] if info else None
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    tr, pend, aux = jb_rules.simulate(o, h, l, c, s, g, v=df["volume"].to_numpy(float), mkt_dd=jb_rules.market_dd(dates))
    buy_txt, sell_txt = jb_rules.describe(g)
    n = len(dates)
    start = max(n - bars, 0)
    mk, trades = jb_rules.markers(list(dates), o, c, s, tr, aux, start=dates[start], h=h, l=l)
    hold = jb_rules.holding(list(dates), o, c, tr, aux)
    shown = [t["ret"] for t in trades if t["buy"] >= dates[start]]
    now = float(s[-1])
    group = {"name": info[0], "rank": info[2], "n": info[3], "date": cur.get("date")} if info else None
    summary = f"오늘 AI 점수 {now * 100:.0f}점" + (" — 매수 기준(95점) 위 ✔" if now >= 0.95 else " (매수 기준 95점 + 볼린저 가운데선 아래)") + "."
    if group:
        summary += f" 그룹({group['name']}) 안 후보 순위 {group['rank']}위/{group['n']}종목({group['date']} 기준)."
    if pend is not None:
        summary += " ▶ 매수 신호: " + jb_rules.action(list(dates), o, c, tr, pend, aux, s, g)["title"] + "."
    elif hold:
        summary += (f" ▶ 보유 중: {hold['buy']} 시가 {hold['buy_px']:,.0f}원 1차 매수" + (f", {hold['add']} 2차 매수(평균 단가 {hold['avg']:,.0f}원)" if hold["add"] else "")
                    + f" — 평균 단가 기준 {hold['ret'] * 100:+.1f}%, {hold['days']}거래일째" + (" · ▶ 오늘 마감 −10% → 다음 거래일 시가에 2차(나머지 절반) 매수" if hold["add_pending"] else "")
                    + f". 매도 조건: {sell_txt}.")
    else:
        summary += " ▶ 지금은 매수 신호가 없습니다."
    if shown:
        r = np.array(shown)
        summary += f" ▶ 이 차트({dates[start]}~)의 거래 {len(r)}건({g or '소형주'} 규칙): 승률 {(r > 0).mean() * 100:.0f}% · 평균 {r.mean() * 100:+.1f}% — 학습에 쓰지 않은 구간."
        fl = [t["from_low"] for t in trades if t["buy"] >= dates[start] and "from_low" in t]
        fh = [t["from_high"] for t in trades if t["buy"] >= dates[start] and "from_high" in t]
        if fl:
            summary += f" 저점·고점: 산 가격은 그 무렵 실제 저점보다 평균 +{np.mean(fl) * 100:.1f}%, 판 가격은 실제 고점보다 평균 −{np.mean(fh) * 100:.1f}%(차트의 작은 점 = 실제 저점·고점)."
    prob = [{"time": dates[i], "value": round(float(s[i]), 4)} for i in range(start, n) if np.isfinite(s[i])]
    return {"name": "AI", "kind": "ai", "label": "AI 점수", "prob": prob, "thr": jb_rules.TOP, "thr_weak": jb_rules.WEAK, "markers": mk,
            "latest": {"prob": now, "top": bool(now >= jb_rules.TOP)}, "trades": trades[-12:], "group": group, "summary": summary,
            "action": jb_rules.action(list(dates), o, c, tr, pend, aux, s, g),
            "rule": {"group": g, "buy": buy_txt, "sell": sell_txt, "note": jb_rules.STATUS.get(g or "", "")},
            "note": f"▲ 매수 = {buy_txt}, ▼ 매도({g or '소형주'} 규칙) = {sell_txt}(글자 = 그 거래의 수익률). 규칙은 시총 구간(대형·중형·소형·잡주)마다 따로 고른 것입니다"
                    "(학습에 쓰지 않은 구간으로 시험 — 두 기간을 보고 고른 규칙이라 실제보다 좋게 나올 수 있음). " + jb_rules.STATUS.get(g or "", ""), "fast": True}
