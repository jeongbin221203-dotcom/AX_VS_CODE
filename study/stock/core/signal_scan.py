"""첫 화면 '신호' — 전 종목을 훑어 단테 기법 신호(예측)와 AI 상승 확률을 매기고, 점검 항목 충족률로 추천.

- 오늘: data/signals.json (왼쪽 '예측' = 최근 RECENT 봉 안에 단테 계열 신호가 난 종목, 오른쪽 'AI' = AI 확률 상위 10%).
- 기준일 조회: 같은 훑기에서 HIST_START 이후 모든 날짜의 신호·AI 확률·점검 값을 DB 표 daily_checks 에 넣어 두고,
  for_date(날짜) 가 그날의 목록을 같은 방식으로 매긴다(지난 날짜면 다음날 시가 매수 뒤 5일·20일 수익도 붙임).
- 종목마다 점검 항목 10가지를 대 보고 충족률을 매긴다. RECOMMEND(90%) 이상 = 추천.
  항목은 매매 계획·피함·거래정지 연구에서 '처음 보는 해'에도 통했던 조건들이며, 어느 항목이 빠졌는지 화면에 그대로 보인다.
- 전 종목 특징 계산이 종목당 0.5~2초라 웹 요청 때 하지 않고, 일봉 자동 갱신 뒤(또는 python manage.py signals-scan) 한 번 만들어 둔다.
"""
import json
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

import config
from core import ai, avoid_study, backtest as bt, combined_study as cs, db, predict, risk_study, service

SIGNALS_PATH = config.DATA_DIR / "signals.json"
CAL_PATH = config.DATA_DIR / "calibration.json"     # AI 확률 구간 × 시장·피함 조건별 실제 상승 비율(2022년 이후)
BANDS = [(0.99, "상위 1%"), (0.98, "상위 1~2%"), (0.97, "상위 2~3%"), (0.95, "상위 3~5%"), (0.90, "상위 5~10%"), (0.80, "상위 10~20%"), (0.0, "나머지")]
CAL_MIN = 200            # 이보다 표본이 적은 칸은 조건을 줄여 가며 합친다
RECOMMEND = 0.9          # 점검 10개 중 9개 이상
RECENT = 5               # 예측 목록: 단테 신호가 이 봉 수 안에 난 종목
LIQ_GOOD = 1e9           # 거래대금 10억 이상이면 '쉽게 사고팔 수 있음'
HIST_START = "2022-01-01"   # 기준일 조회가 가능한 범위(AI 모델이 보지 않은 시험 기간)
CHECKS_PRED = ["신호가 오늘·어제(2봉 안)", "검증 통과 기법(✔)의 신호", "피함 조건 없음", "시장 필터 통과(52주 고점 −10% 아래)", "AI 상위 10%",
               "종가가 20일선 위", "20일선이 60일선 위", "거래량이 20일 평균 이상", "거래정지 위험 낮음(1.2% 미만)", "거래대금 10억 이상"]
CHECKS_AI = ["AI 상위 10%", "AI 상위 3%", "피함 조건 없음", "시장 필터 통과(52주 고점 −10% 아래)", "기법 신호가 10봉 안에 있음",
             "종가가 20일선 위", "20일선이 60일선 위", "거래량이 20일 평균 이상", "거래정지 위험 낮음(1.2% 미만)", "거래대금 10억 이상"]
HIGH_NOTE = ("상승 확률 높은 추천 = AI 상위 2% + 피함 제외 + 시장 필터 통과(52주 고점 −10% 아래). 2022~2023년으로 조건을 고르고 2024년 이후로 검증: 다음날 시가 매수 시 5일 뒤 상승 56%·20일 뒤 58%"
             "(아무 종목 45%), 하루 평균 약 8종목이고 시장 필터를 통과한 날(전체의 약 40%)에만 나옵니다. '강' = 여기에 AI 상위 3%·시장 20일 −5% 미만을 더한 것으로 검증 구간 5일 63%·20일 64%(하루 약 10종목, 시장이 급히 빠진 날에만).")
HIST_COLS = ("code", "date", "close", "prob", "halt", "avoid", "above20", "ma20_60", "volup", "liq_ok", "sig0", "sig1", "sig5", "sign10")
_halt_cache = {"mtime": None, "model": None, "ratio": None}


def _halt_model():
    """거래정지(120일 안) 모델 + 학습 때 음성 추출 비율. 프로세스마다 한 번만 읽는다."""
    try:
        mt = risk_study.HALT_MODEL_PATH.stat().st_mtime
    except OSError:
        return None, None
    if _halt_cache["mtime"] != mt:
        try:
            import joblib
            ratio = json.loads(risk_study.RISK_PATH.read_text(encoding="utf-8"))["model"]["ratio"]
            _halt_cache.update(mtime=mt, model=joblib.load(risk_study.HALT_MODEL_PATH), ratio=ratio)
        except Exception:
            return None, None
    return _halt_cache["model"], _halt_cache["ratio"]


def _halt(D):
    hm, ratio = _halt_model()
    if hm is None:
        return np.full(len(D), np.nan)
    try:
        p = hm.predict_proba(risk_study._feat_matrix(D))[:, 1]
        return p / (p + (1 - p) * ratio)
    except Exception:
        return np.full(len(D), np.nan)


def _day_extras(df):
    """피함 규칙에 필요한 날짜별 값(거래 없는 날 수·갭 하락 횟수·연속 하락일·종가) — ai._avoid_latest 의 모든 날짜 판."""
    c = df["close"].to_numpy(float)
    v = df["volume"].to_numpy(float)
    o = df["open"].to_numpy(float)
    pc = np.r_[np.nan, c[:-1]]
    gap_dn = ((o / pc - 1) <= -0.05).astype(float)
    down = np.r_[0.0, (c[1:] < c[:-1]).astype(float)]
    return {"zero_vol60": pd.Series((v <= 0).astype(float)).rolling(60, min_periods=1).sum().to_numpy(),
            "gapdn20": pd.Series(gap_dn).rolling(20, min_periods=1).sum().to_numpy(),
            "down_streak": ai._since(down == 0, cap=20), "close_px": c}


def _avoid_mask(D):
    rules = dict(avoid_study.RULES)
    avoid = np.zeros(len(D), bool)
    for n in cs.AVOID_NAMES:
        avoid |= rules[n](D).fillna(False).to_numpy()
    return avoid


def _flags(D, P, halt):
    """점검에 쓰는 날짜별 값(표 daily_checks 한 행에 들어가는 것)."""
    sig = np.column_stack([D[f"sig{j:02d}"].to_numpy() for j in range(len(ai.SIGNAL_LABELS))])
    bits = (1 << np.arange(len(ai.SIGNAL_LABELS))).astype(np.int64)
    mask = lambda k: ((sig <= k) & np.isfinite(sig)).astype(np.int64) @ bits  # noqa: E731
    return {"prob": P, "halt": halt, "avoid": _avoid_mask(D).astype(int),
            "above20": (D["dist20"] > 0).to_numpy().astype(int), "ma20_60": (D["dist20"] < D["dist60"]).to_numpy().astype(int),
            "volup": (D["volratio"] >= 1).to_numpy().astype(int), "liq_ok": (D["liq"] >= math.log10(LIQ_GOOD)).to_numpy().astype(int),
            "sig0": mask(0), "sig1": mask(1), "sig5": mask(RECENT), "sign10": (D["sig_n10"] >= 1).to_numpy().astype(int)}


def _work(code):
    try:
        df = service.load_prices(code)
        if len(df) < ai.MIN_BARS or df["volume"].iloc[-1] <= 0:
            return None
        X = ai.compute_features(df, ai.MKT_DF, True)
        x = X[-1]
        if not np.isfinite(x[ai.NAMES.index("ret60")]) or x[ai.NAMES.index("liq")] < math.log10(ai.TRADABLE_VALUE):
            return None
        row = {"code": code, "date": df.index[-1].strftime("%Y-%m-%d"), "close": float(df["close"].iloc[-1]), "x": x.astype(float)}
        row.update(ai._avoid_latest(df))
        # 기준일 조회용 이력: HIST_START 이후 거래 가능한 날
        dates = df.index.strftime("%Y-%m-%d").to_numpy()
        ok = (dates >= HIST_START) & np.isfinite(X[:, ai.NAMES.index("ret60")]) & (X[:, ai.NAMES.index("liq")] >= math.log10(ai.TRADABLE_VALUE)) & (df["volume"].to_numpy(float) > 0)
        if ok.any():
            D = pd.DataFrame(X[ok], columns=ai.NAMES)
            ex = _day_extras(df)
            for k, v in ex.items():
                D[k] = v[ok]
            D["gap1"] = np.nan
            model = predict._model()
            P = model.predict_proba(X[ok])[:, 1] if model is not None else np.full(int(ok.sum()), np.nan)
            f = _flags(D, P, _halt(D))
            row["hist"] = {"date": dates[ok], "close": df["close"].to_numpy(float)[ok], **f}
        return row
    except Exception:
        return None


def _market_dd(date=None, col="mkt_dd250"):
    mkt = predict._mkt()
    try:
        s = mkt[col].dropna()
        return float(s.iloc[-1]) if date is None else float(s[s.index <= date].iloc[-1])
    except Exception:
        return float("nan")


_cal_cache = {"mtime": None, "rows": None}


def _band(pr):
    for lo, name in BANDS:
        if pr >= lo:
            return name
    return BANDS[-1][1]


def calibration():
    try:
        mt = CAL_PATH.stat().st_mtime
    except OSError:
        return None
    if _cal_cache["mtime"] != mt:
        try:
            d = json.loads(CAL_PATH.read_text(encoding="utf-8"))
            _cal_cache.update(mtime=mt, rows={(r["band"], r["avoid"], r["mk"], r["weak"]): r for r in d["rows"]}, meta=d.get("meta"))
        except (OSError, ValueError, KeyError):
            return None
    return _cal_cache["rows"]


def _estimate(cal, pr, avoid, mk_ok, weak):
    """같은 AI 구간·같은 조건이었던 날들의 실제 5일·20일 상승 비율. 표본이 모자라면 조건을 줄여 합친다."""
    if not cal or pr is None:
        return None
    b = _band(pr)
    for key in ((b, int(avoid), int(mk_ok), int(weak)), (b, int(avoid), int(mk_ok), -1), (b, int(avoid), -1, -1), (b, -1, -1, -1)):
        r = cal.get(key)
        if r and r["n"] >= CAL_MIN:
            return {"p5": r["up5"], "p20": r["up20"], "m5": r["m5"], "m20": r["m20"], "n": r["n"], "band": b}
    return None


def calibrate(log=print):
    """daily_checks(날짜별 AI 확률·피함) + 실제 가격으로 '구간별 실제 상승 비율' 표를 만든다. 전 종목 훑기 뒤 자동 실행."""
    with db.get_conn() as c:
        H = pd.read_sql("SELECT code,date,prob,avoid FROM daily_checks WHERE prob IS NOT NULL", c)
        P = pd.read_sql("SELECT code,date,open,close FROM prices WHERE date >= '2021-12-01'", c)
    if H.empty:
        return None
    P = P.sort_values(["code", "date"])
    g = P.groupby("code")
    P["entry"] = g["open"].shift(-1)
    P["r5"] = g["close"].shift(-5) / P["entry"] - 1
    P["r20"] = g["close"].shift(-20) / P["entry"] - 1
    H = H.merge(P[["code", "date", "r5", "r20"]], on=["code", "date"], how="left").dropna(subset=["r5"])
    mk = pd.read_pickle(bt.MKT_PATH)
    H["mk"] = (H["date"].map(mk["mkt_dd250"]) < -0.10).astype(int)
    H["weak"] = (H["date"].map(mk["mkt_r20"]) < -0.05).astype(int)
    H["pr"] = H.groupby("date")["prob"].rank(pct=True)
    H["band"] = [_band(v) for v in H["pr"]]
    rows = []
    def add(df, band, avoid, mkv, weak):
        s5, s20 = df["r5"], df["r20"].dropna()
        if len(s5) >= 30:
            rows.append({"band": band, "avoid": avoid, "mk": mkv, "weak": weak, "n": int(len(s5)), "up5": float((s5 > 0).mean()), "up20": float((s20 > 0).mean()) if len(s20) else None,
                         "m5": float(s5.mean()), "m20": float(s20.mean()) if len(s20) else None})
    for b, gb in H.groupby("band"):
        add(gb, b, -1, -1, -1)
        for a, ga in gb.groupby("avoid"):
            add(ga, b, int(a), -1, -1)
            for m_, gm in ga.groupby("mk"):
                add(gm, b, int(a), int(m_), -1)
                for w, gw in gm.groupby("weak"):
                    add(gw, b, int(a), int(m_), int(w))
    out = {"meta": {"generated": datetime.now().isoformat(timespec="seconds"), "from": str(H["date"].min()), "to": str(H["date"].max()), "rows": int(len(H))}, "rows": rows}
    CAL_PATH.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    log(f"보정표 저장 {CAL_PATH} · {len(rows)}칸")
    return out


def _assemble(recs, date, thr10, thr3, thr2, mk_dd, mk_r20, names, info):
    """점검 값이 있는 종목 레코드 → 예측·AI 목록(충족률 매김). recs: dict(code, close, prob, halt, avoid, above20, ma20_60, volup, liq_ok, sig0, sig1, sig5, sign10, [r5, r20])."""
    mk_ok = bool(np.isfinite(mk_dd) and mk_dd < -0.10)
    pred, ai_rows, high = [], [], []
    mk_weak = bool(np.isfinite(mk_r20) and mk_r20 < -0.05)
    cal = calibration()
    probs = pd.Series([r["prob"] if r["prob"] is not None else np.nan for r in recs], dtype=float)
    ranks = probs.rank(pct=True).to_numpy() if len(recs) else []
    for i, r in enumerate(recs):
        p = r["prob"]
        ai_top = bool(p is not None and np.isfinite(p) and np.isfinite(thr10) and p >= thr10)
        ai_top2 = bool(p is not None and np.isfinite(p) and np.isfinite(thr2) and p >= thr2)
        ai_top3 = bool(p is not None and np.isfinite(p) and np.isfinite(thr3) and p >= thr3)
        h = r["halt"]
        halt_ok = bool(h is not None and np.isfinite(h) and h < risk_study.RISK_MID)
        sigs = []
        for j in predict.DANTE_IDX:
            if r["sig5"] >> j & 1:
                lab = ai.SIGNAL_LABELS[j]
                ago = 0 if r["sig0"] >> j & 1 else 1 if r["sig1"] >> j & 1 else 2
                sigs.append({"label": lab, "ago": ago, "good": bool(info.get(lab, {}).get("good"))})
        sigs.sort(key=lambda s: (s["ago"], not s["good"]))
        common = [not r["avoid"], mk_ok, bool(r["above20"]), bool(r["ma20_60"]), bool(r["volup"]), halt_ok, bool(r["liq_ok"])]
        base = {"code": r["code"], "name": names.get(r["code"], r["code"]), "close": r["close"], "prob": None if p is None or not np.isfinite(p) else float(p),
                "halt_p": None if h is None or not np.isfinite(h) else float(h), "avoid": bool(r["avoid"]), "signals": sigs, "ai_top": ai_top,
                "r5": r.get("r5"), "r20": r.get("r20"),
                "est": _estimate(cal, float(ranks[i]) if np.isfinite(ranks[i]) else None, r["avoid"], mk_ok, mk_weak)}
        if sigs:
            ch = [any(s["ago"] <= 1 for s in sigs), any(s["good"] for s in sigs), common[0], common[1], ai_top] + common[2:]
            pred.append({**base, "checks": ch, "score": sum(ch) / len(ch)})
        if ai_top:
            ch = [ai_top, ai_top3, common[0], common[1], bool(r["sign10"])] + common[2:]
            row = {**base, "checks": ch, "score": sum(ch) / len(ch)}
            ai_rows.append(row)
            if ai_top2 and not r["avoid"] and mk_ok:
                high.append({**row, "tier": "강" if (ai_top3 and mk_weak) else "상"})
    pred.sort(key=lambda z: (-z["score"], -(z["prob"] or 0), z["name"]))
    ai_rows.sort(key=lambda z: (-z["score"], -(z["prob"] or 0)))
    high.sort(key=lambda z: (z["tier"] != "강", -(z["prob"] or 0)))
    rec_p = [z for z in pred if z["score"] >= RECOMMEND]
    rec_a = [z for z in ai_rows if z["score"] >= RECOMMEND]
    text = [f"{date} 거래 가능 종목 {len(recs)}개. 시장은 52주 고점 대비 {mk_dd * 100:+.1f}%{'(시장 필터 통과)' if mk_ok else '(시장 필터 미통과 — 모든 종목이 한 항목을 잃음)'}.",
            f"예측(단테 기법 신호 {RECENT}봉 안) {len(pred)}개 중 점검 {int(RECOMMEND * 10)}/10 이상 {len(rec_p)}개, AI 상위 10% {len(ai_rows)}개 중 {len(rec_a)}개.",
            "점검 항목은 매매 계획·피함·거래정지 연구에서 처음 보는 해에도 통했던 조건이며, 과거 통계일 뿐 상승을 보장하지 않습니다."]
    return {"meta": {"date": date, "rows": len(recs), "thr10": thr10, "thr3": thr3, "thr2": thr2, "mkt_dd250": mk_dd, "mkt_r20": mk_r20, "recommend": RECOMMEND, "recent": RECENT},
            "checks": {"pred": CHECKS_PRED, "ai": CHECKS_AI}, "pred": pred, "ai": ai_rows, "high": high, "high_note": HIGH_NOTE, "strong_mkt": _last_strong(date), "text": text}


def _last_strong(date):
    """'강' 조건의 시장 부분(52주 고점 −10% 아래 + 20일 −5% 미만)이 마지막으로 충족된 날(기준일 포함)과 2022년 이후 그런 날의 비율."""
    mkt = predict._mkt()
    try:
        m = mkt[mkt.index >= HIST_START]
        ok = m[(m["mkt_dd250"] < -0.10) & (m["mkt_r20"] < -0.05)]
        past = ok[ok.index <= date]
        return {"last": str(past.index[-1]) if len(past) else None, "share": float(len(ok) / max(len(m), 1))}
    except Exception:
        return {"last": None, "share": None}


def _quantiles(probs):
    p = np.array([v for v in probs if v is not None and np.isfinite(v)], float)
    if not len(p):
        return float("nan"), float("nan"), float("nan")
    return float(np.quantile(p, 0.9)), float(np.quantile(p, 0.97)), float(np.quantile(p, 0.98))


def score(rows, names, date):
    """오늘 행(특징 벡터)에 피함·AI·거래정지 위험을 붙이고 점검 항목을 매긴다."""
    XL = np.vstack([r["x"] for r in rows])
    D = pd.DataFrame(XL, columns=ai.NAMES)
    for k in ("zero_vol60", "gapdn20", "down_streak", "close_px"):
        D[k] = [r[k] for r in rows]
    D["gap1"] = np.nan
    model = predict._model()
    P = model.predict_proba(XL)[:, 1] if model is not None else np.full(len(rows), np.nan)
    f = _flags(D, P, _halt(D))
    recs = [{"code": r["code"], "close": r["close"], **{k: (float(v[i]) if k in ("prob", "halt") else int(v[i])) for k, v in f.items()}} for i, r in enumerate(rows)]
    thr10, thr3, thr2 = _quantiles(P)
    return _assemble(recs, date, thr10, thr3, thr2, _market_dd(), _market_dd(col="mkt_r20"), names, predict._tech_info(predict.plan()))


def _market_fresh(codes, workers, latest_px, log):
    """시장 지수가 일봉보다 오래됐으면 다시 만든다(시장 필터 값이 어제 것이 되지 않게)."""
    try:
        last = str(pd.read_pickle(bt.MKT_PATH).index[-1])
    except Exception:
        last = ""
    if latest_px and last < latest_px:
        log(f"시장 지수 갱신 {last} → {latest_px}")
        bt.build_market_index(codes, workers)
        predict._mkt_cache.update(mtime=None)


def _hist_rows(r):
    h = r.get("hist")
    if not h:
        return []
    out = []
    for i in range(len(h["date"])):
        out.append((r["code"], str(h["date"][i]), float(h["close"][i]), None if np.isnan(h["prob"][i]) else float(h["prob"][i]), None if np.isnan(h["halt"][i]) else float(h["halt"][i]),
                    int(h["avoid"][i]), int(h["above20"][i]), int(h["ma20_60"][i]), int(h["volup"][i]), int(h["liq_ok"][i]), int(h["sig0"][i]), int(h["sig1"][i]), int(h["sig5"][i]), int(h["sign10"][i])))
    return out


def run(workers: int = 0, log=print, limit: int = 0, path=None) -> dict:
    t0 = time.time()
    db.init_db()
    with db.get_conn() as c:
        codes = [r["code"] for r in c.execute("SELECT code FROM prices GROUP BY code HAVING COUNT(*) >= ? ORDER BY code", (ai.MIN_BARS,))]
        names = {r["code"]: r["name"] for r in c.execute("SELECT code,name FROM symbols")}
        latest_px = (c.execute("SELECT MAX(date) d FROM prices").fetchone() or {"d": None})["d"]
    if limit:
        codes = codes[:limit]
    workers = workers or max(1, min(6, (os.cpu_count() or 2) - 2))
    if len(codes) >= 30:
        _market_fresh(codes, workers, latest_px, log)
    rows, n_hist = [], 0
    conn = db.connect()
    try:
        conn.execute("DELETE FROM daily_checks")
        def take(out):
            nonlocal n_hist
            if not out:
                return
            hr = _hist_rows(out)
            if hr:
                conn.executemany(f"INSERT OR REPLACE INTO daily_checks VALUES({','.join('?' * len(HIST_COLS))})", hr)
                n_hist += len(hr)
            out.pop("hist", None)
            rows.append(out)
        if workers == 1:
            ai._init_worker(bt.MKT_PATH)
            for code in codes:
                take(_work(code))
        else:
            with ProcessPoolExecutor(max_workers=workers, initializer=ai._init_worker, initargs=(bt.MKT_PATH,)) as ex:
                for i, out in enumerate(ex.map(_work, codes, chunksize=8), 1):
                    take(out)
                    if i % 500 == 0:
                        log(f"[{i}/{len(codes)}] · {time.time() - t0:.0f}초")
        if codes:
            conn.commit()
        else:
            conn.rollback()
    finally:
        conn.close()
    if not rows:
        rep = {"meta": {"date": None, "rows": 0}, "checks": {"pred": CHECKS_PRED, "ai": CHECKS_AI}, "pred": [], "ai": [], "text": ["훑을 종목이 없습니다."]}
    else:
        date = max(r["date"] for r in rows)
        rep = score([r for r in rows if r["date"] == date], names, date)
    rep["meta"].update(generated=datetime.now().isoformat(timespec="seconds"), elapsed_sec=round(time.time() - t0), scanned=len(codes), hist_rows=n_hist, hist_start=HIST_START)
    if not codes:                                              # 훑을 종목이 없으면(빈 DB) 있던 결과를 덮어쓰지 않는다
        log("훑을 종목이 없어 저장하지 않습니다")
        return rep
    (path or SIGNALS_PATH).write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
    try:
        calibrate(log)
    except Exception as e:
        log(f"보정표 실패: {e}")
    log(f"저장 {path or SIGNALS_PATH} · 이력 {n_hist:,}행 · {time.time() - t0:.0f}초")
    return rep


def load():
    try:
        return json.loads(SIGNALS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _outcomes(codes, date, conn):
    """기준일 다음날 시가 매수 → 5일·20일 뒤 종가 수익(연구와 같은 정의). 아직 안 지난 날은 None."""
    if not codes:
        return {}
    end = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=60)).strftime("%Y-%m-%d")
    out = {}
    for i in range(0, len(codes), 400):
        part = codes[i:i + 400]
        q = f"SELECT code,date,open,close FROM prices WHERE code IN ({','.join('?' * len(part))}) AND date > ? AND date <= ? ORDER BY code,date"
        cur = None
        for r in conn.execute(q, (*part, date, end)):
            if r["code"] != cur:
                cur = r["code"]
                out[cur] = []
            out[cur].append((r["open"], r["close"]))
    res = {}
    for code, bars in out.items():
        entry = bars[0][0] if bars and bars[0][0] else None
        r5 = bars[5][1] / entry - 1 if entry and len(bars) > 5 else None
        r20 = bars[20][1] / entry - 1 if entry and len(bars) > 20 else None
        res[code] = (r5, r20)
    return res


def dates_available():
    with db.get_conn() as c:
        r = c.execute("SELECT MIN(date) a, MAX(date) b FROM daily_checks").fetchone()
    return (r["a"], r["b"]) if r and r["a"] else (None, None)


def for_date(date: str):
    """기준일(그날이 거래일이 아니면 그 전 마지막 거래일)의 매수 신호 종목 — 오늘 화면과 같은 점검·추천 + 지난 날이면 이후 수익."""
    with db.get_conn() as c:
        d = (c.execute("SELECT MAX(date) d FROM daily_checks WHERE date <= ?", (date,)).fetchone() or {"d": None})["d"]
        if not d:
            return None
        recs = [dict(r) for r in c.execute("SELECT * FROM daily_checks WHERE date=?", (d,))]
        names = {r["code"]: r["name"] for r in c.execute("SELECT code,name FROM symbols")}
        prev = (c.execute("SELECT MAX(date) d FROM daily_checks WHERE date < ?", (d,)).fetchone() or {"d": None})["d"]
        nxt = (c.execute("SELECT MIN(date) d FROM daily_checks WHERE date > ?", (d,)).fetchone() or {"d": None})["d"]
        latest = (c.execute("SELECT MAX(date) d FROM daily_checks").fetchone() or {"d": None})["d"]
        oc = _outcomes([r["code"] for r in recs], d, c) if d < (latest or d) else {}
    for r in recs:
        r["r5"], r["r20"] = oc.get(r["code"], (None, None))
    thr10, thr3, thr2 = _quantiles([r["prob"] for r in recs])
    rep = _assemble(recs, d, thr10, thr3, thr2, _market_dd(d), _market_dd(d, "mkt_r20"), names, predict._tech_info(predict.plan()))
    rep["meta"].update(asked=date, prev=prev, next=nxt, latest=latest, past=bool(oc))
    if oc:
        def stat(rows):
            r5 = [z["r5"] for z in rows if z["r5"] is not None]
            r20 = [z["r20"] for z in rows if z["r20"] is not None]
            return {"n": len(rows), "n5": len(r5), "r5": float(np.mean(r5)) if r5 else None, "up5": float(np.mean([v > 0 for v in r5])) if r5 else None,
                    "n20": len(r20), "r20": float(np.mean(r20)) if r20 else None, "up20": float(np.mean([v > 0 for v in r20])) if r20 else None}
        all_recs = [{"r5": r["r5"], "r20": r["r20"]} for r in recs]
        rep["outcome"] = {"all": stat(all_recs),
                          "pred_rec": stat([z for z in rep["pred"] if z["score"] >= RECOMMEND]), "pred": stat(rep["pred"]),
                          "ai_rec": stat([z for z in rep["ai"] if z["score"] >= RECOMMEND]), "ai": stat(rep["ai"]), "high": stat(rep["high"]), "strong": stat([z for z in rep["high"] if z["tier"] == "강"])}
    return rep


if __name__ == "__main__":
    run()
