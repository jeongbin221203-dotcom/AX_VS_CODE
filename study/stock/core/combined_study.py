"""지금까지의 분석 결과를 한 규칙으로 묶어 전체 데이터에 적용 — 종합 전략 검증.

순서(각 단계는 앞 단계 위에 쌓임):
  0. 거래 가능 종목(20일 평균 거래대금 3억↑)의 아무 날 = 기준
  1. '피함' 7개 규칙 제외(피할 종목 연구에서 두 시기 모두 나빴던 것)
  2. 진입 신호: 기법 정확도·AI·거래량 급증 연구에서 앞·뒤 시기 모두 평균보다 좋았던 신호(신호일 포함 최근 3봉 안)
     — 후보를 미리 정해 두고, 학습 기간(~2018)과 시험 기간(2022~)이 모두 시장보다 좋은 것만 최종 규칙에 넣는다.
  3. 갭 +3% 이하일 때만 진입(갭 연구)
  4. 3분할(0·5·10일) / 2×ATR 손절(진입 시점 연구)
진입은 신호일 다음날 시가, 청산은 20거래일 뒤 종가(비용 0.3% 차감). 60·120일 수익도 같이 본다.
표본은 ai.build_rows 와 같은 날짜 추출(연중일 % 5)이라 전 종목·전 기간을 고르게 본다.
"""
import json
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime

import numpy as np
import pandas as pd

import config
from core import ai, avoid_study, db
from core import backtest as bt

COMBINED_PATH = config.DATA_DIR / "combined.json"
COST = ai.COST
SIG_WIN = 2                     # 신호일 포함 최근 3봉 안(0·1·2봉 전)
GAP_MAX = 0.03
AI_TOP = 0.10                   # AI 점수 상위 10%
AVOID_NAMES = ["당일 갭 +10%↑ 급등 출발", "20일 +50%↑ 급등 직후", "변동성 높음 (ATR÷종가 8%↑)", "20일 +30%↑ 급등 직후",
               "최근 60일 중 거래 없는 날 3일↑", "긴 윗꼬리 (고저폭 8%↑, 윗꼬리 60%↑ — 상승 실패)", "거래량 3배↑ + 음봉 (물량 분출)"]
KEYS = ["r5", "r20", "r60", "r120", "touch", "split3", "mae0", "mae_split", "stop_ret", "stop_hit", "gap1", "zero_vol60", "gapdn20", "down_streak", "close_px"]


def _work(code):
    try:
        return ai.build_rows(code, with_signals=True, extras=True)
    except Exception:
        return None


def _sig(D, label):
    return (D[f"sig{ai.SIGNAL_LABELS.index(label):02d}"] <= SIG_WIN).to_numpy()


# 진입 신호 후보 — 앞선 연구에서 좋게 나온 것들을 '미리' 정해 둔다(여기서 새로 고르지 않음)
def candidates(D: pd.DataFrame):
    vol_up = ((D["volratio"] >= 3) & (D["body"] > 0)).to_numpy()
    out = [
        ("눌림목 반등", _sig(D, "눌림목 반등"), "거래량 급증 연구: 급증과 겹쳤을 때 20일 안 +20% 30%(평균 24%)"),
        ("52주 신고가", _sig(D, "52주 신고가"), "거래량 급증 연구 30% · AI 기간별 비교에서도 양호"),
        ("매집봉 돌파", _sig(D, "매집봉 돌파"), "단테 기법 · 급증 연구 29%"),
        ("장대양봉+거래량", _sig(D, "장대양봉+거래량"), "급증 연구 28%(표본 가장 많음)"),
        ("정배열 신호", _sig(D, "정배열"), "급증 연구 27%"),
        ("거래량 3배↑ 양봉 + 신고가·매집봉·정배열·눌림목 중 하나", vol_up & (_sig(D, "52주 신고가") | _sig(D, "매집봉 돌파") | _sig(D, "정배열") | _sig(D, "눌림목 반등")),
         "급증 연구: 음봉 급증은 피함, 양봉 급증은 신호와 겹칠 때만"),
        ("52주 신고가 + 최근 60봉 매집봉", _sig(D, "52주 신고가") & (D["acc_recent"] == 1).to_numpy(), "급증 연구 두 조건 겹침 39%(두 시기 모두)"),
        ("정배열 + 눌림목 반등", (D["aligned"] == 1).to_numpy() & _sig(D, "눌림목 반등"), "급증 연구 두 조건 겹침 35%"),
        ("역매공파 + 52주 신고가", _sig(D, "역매공파") & _sig(D, "52주 신고가"), "급증 연구 두 조건 겹침 43%(표본 적음)"),
        ("이평 때리기(112) + 매집봉 돌파", _sig(D, "이평 때리기(112)") & _sig(D, "매집봉 돌파"), "급증 연구 두 조건 겹침 34%"),
        ("하락 후 반등 시작 (저점 지킴 + 20일선 위)", ((D["dd120"] <= -0.25) & (D["low_age"] >= 5) & (D["low_age"] <= 40) & (D["rebound60"] >= 0.08) & (D["dist20"] > 0) & (D["hl10"] >= 0)).to_numpy(),
         "패턴 연구(하락하다 다시 상승)"),
        ("정배열 + 20일선 눌림 (0~3%)", ((D["aligned"] == 1) & (D["dist20"] >= 0) & (D["dist20"] < 0.03)).to_numpy(), "진입 시점 연구 조건"),
    ]
    return out


def _stats(r, excess=None):
    r = np.asarray(r, float)
    ok = ~np.isnan(r)
    r = r[ok]
    if len(r) < 30:
        return None
    s = {"n": int(len(r)), "mean": float(r.mean()), "median": float(np.median(r)), "std": float(r.std()), "p_up": float((r > 0).mean()),
         "p_big_up": float((r >= 0.10).mean()), "p_dn10": float((r <= -0.10).mean()), "p_dn20": float((r <= -0.20).mean()), "q05": float(np.quantile(r, 0.05))}
    if excess is not None:
        e = np.asarray(excess, float)[ok]
        s["excess"] = float(np.nanmean(e))
    return s


def _tstat_by_date(excess, date, m):
    """같은 날 신호가 몰리므로 날짜별 평균으로 t 통계(날짜 수 기준)."""
    e = pd.Series(excess[m]).groupby(pd.Series(date[m])).mean().dropna()
    if len(e) < 10:
        return None, int(len(e))
    return float(e.mean() / (e.std(ddof=1) / math.sqrt(len(e)))) if e.std(ddof=1) > 0 else None, int(len(e))


def _stage(name, m, T, ex, date, tr, te, ret_key="r20", mae_key="mae0", note=""):
    r = T[ret_key] - COST
    row = {"name": name, "note": note, "ret_key": ret_key}
    for lab, mm in (("all", m), ("tr", m & tr), ("te", m & te)):
        s = _stats(r[mm], ex[mm])
        if s and mae_key in T:
            s["mae"] = float(np.nanmean(T[mae_key][mm]))
        if s:
            s["r60"] = float(np.nanmean(T["r60"][mm])) - COST
            s["r120"] = float(np.nanmean(T["r120"][mm])) - COST
            s["touch20"] = float(np.nanmean(T["touch"][mm] >= 0.20))
            s["t"], s["dates"] = _tstat_by_date(ex, date, mm & ~np.isnan(r))
        row[lab] = s
    n_te = int((m & te).sum())
    months = max(len(pd.Series(date[te]).str[:7].unique()), 1)
    row["per_month_te"] = n_te / months
    row["share"] = float(m.mean())
    return row


def _yearly(m, T, ex, date, key="r20"):
    r = T[key] - COST
    y = pd.Series(date).str[:4].to_numpy()
    out = []
    for yr in sorted(set(y[m])):
        mm = m & (y == yr) & ~np.isnan(r)
        if mm.sum() < 20:
            continue
        out.append({"year": yr, "n": int(mm.sum()), "mean": float(r[mm].mean()), "excess": float(np.nanmean(ex[mm])), "p_up": float((r[mm] > 0).mean()),
                    "market": float(r[mm].mean() - np.nanmean(ex[mm])), "p_dn20": float((r[mm] <= -0.20).mean())})
    return out


def _monthly(m, T, date, key="split3"):
    r = T[key] - COST
    mon = pd.Series(date).str[:7].to_numpy()
    df = pd.DataFrame({"mon": mon[m], "r": r[m]}).dropna()
    g = df.groupby("mon")["r"].agg(["mean", "count"]).reset_index()
    if not len(g):
        return None
    cum = float(np.prod(1 + g["mean"].to_numpy()) - 1)
    return {"months": int(len(g)), "p_pos": float((g["mean"] > 0).mean()), "mean": float(g["mean"].mean()), "worst": float(g["mean"].min()),
            "best": float(g["mean"].max()), "cum": cum, "last": [{"mon": a, "mean": float(b), "n": int(c)} for a, b, c in g.tail(24).itertuples(index=False)]}


def _regime(m, T, ex, D, tr, te):
    specs = [("시장 60일 +10%↑ 강세", D["mkt_r60"] >= 0.10), ("0~+10%", (D["mkt_r60"] >= 0) & (D["mkt_r60"] < 0.10)),
             ("−10~0%", (D["mkt_r60"] >= -0.10) & (D["mkt_r60"] < 0)), ("60일 −10%↓ 급락 뒤", D["mkt_r60"] < -0.10)]
    r = T["r20"] - COST
    out = []
    for name, mm in specs:
        mm = m & mm.to_numpy()
        if mm.sum() < 100:
            continue
        s = _stats(r[mm], ex[mm])
        s["name"] = name
        s["share"] = float(mm.sum() / max(m.sum(), 1))
        s["excess_te"] = float(np.nanmean(ex[mm & te])) if (mm & te).sum() >= 50 else None
        out.append(s)
    return out


def _load_model():
    try:
        import joblib
        mdl = joblib.load(ai.MODEL_PATH)
        if getattr(mdl, "n_features_in_", None) == len(ai.NAMES):
            return mdl
    except Exception:
        pass
    return None


def run(workers: int = 0, log=print, limit: int = 0) -> dict:
    t0 = time.time()
    with db.get_conn() as c:
        codes = [r["code"] for r in c.execute("SELECT code FROM prices GROUP BY code HAVING COUNT(*) >= ? ORDER BY code", (ai.MIN_BARS,))]
        names = {r["code"]: r["name"] for r in c.execute("SELECT code,name FROM symbols")}
    if limit:
        codes = codes[::max(len(codes) // limit, 1)][:limit]
    workers = workers or max(1, min(6, (os.cpu_count() or 2) - 2))
    if not bt.MKT_PATH.exists():
        bt.build_market_index(codes, workers)
    parts, latest = [], []
    with ProcessPoolExecutor(max_workers=workers, initializer=ai._init_worker, initargs=(bt.MKT_PATH,)) as ex_:
        for i, out in enumerate(ex_.map(_work, codes, chunksize=8), 1):
            if out:
                latest.append(out["latest"])
                if len(out["date"]):
                    parts.append(out)
            if i % 400 == 0:
                log(f"[표본 {i}/{len(codes)}] {sum(len(p['date']) for p in parts):,}행 · {time.time() - t0:.0f}초")
    X = np.concatenate([p["X"] for p in parts])
    date = np.concatenate([p["date"] for p in parts])
    code = np.concatenate([p["code"] for p in parts])
    T = {k: np.concatenate([p[k] for p in parts]) for k in KEYS}
    del parts
    D = pd.DataFrame(X, columns=ai.NAMES)
    for k in ("zero_vol60", "gapdn20", "down_streak", "close_px", "gap1"):
        D[k] = T[k]
    log(f"표본 {len(date):,}행 · {time.time() - t0:.0f}초")
    tr, va, te = ai._split_masks(date)
    tr = tr | va                                              # 학습 기간 = ~2021, 시험 = 2022~
    r20 = T["r20"]
    ok = ~np.isnan(r20)
    ex = (pd.Series(r20) - pd.Series(r20).groupby(pd.Series(date)).transform("mean")).to_numpy()   # 같은 날 모든 표본 평균 대비(시장 조정)

    # ---- AI 점수(중기 모델: ~2021 학습, 2022~ 는 처음 보는 기간)
    model = _load_model()
    ai_top = np.zeros(len(date), bool)
    if model is not None:
        P = np.full(len(date), np.nan)
        step = 200000
        for s in range(0, len(date), step):
            P[s:s + step] = model.predict_proba(X[s:s + step])[:, 1]
        thr = np.nanquantile(P[te], 1 - AI_TOP) if te.sum() else np.nanquantile(P, 1 - AI_TOP)
        ai_top = P >= thr
        log(f"AI 모델 적용 · 상위 {AI_TOP * 100:.0f}% 기준 점수 {thr:.3f} · {time.time() - t0:.0f}초")

    rep = {"meta": {"generated": datetime.now().isoformat(timespec="seconds"), "rows": int(len(date)), "stocks": int(len(set(code))), "cost": COST, "hold": ai.HOLD,
                    "split": {"train_end": ai.VALID_END, "date_min": str(date.min()), "date_max": str(date.max())}, "gap_max": GAP_MAX, "sig_win": SIG_WIN + 1,
                    "ai": model is not None, "ai_top": AI_TOP}}

    # ---- 1단계: 피함 규칙
    rules = {n: f for n, f in avoid_study.RULES}
    avoid = np.zeros(len(date), bool)
    avoid_rows = []
    for n in AVOID_NAMES:
        m = rules[n](D).to_numpy() & ok
        avoid |= m
        avoid_rows.append({"name": n, "n": int(m.sum()), "share": float(m.mean()), "excess_te": float(np.nanmean(ex[m & te])) if (m & te).sum() >= 100 else None})
    keep = ok & ~avoid
    rep["avoid"] = {"rows": avoid_rows, "share": float(avoid[ok].mean())}

    # ---- 2단계: 진입 신호 후보 — 피함 제외 뒤에서 평가, 두 시기 모두 시장보다 좋아야 채택
    cands = candidates(D)
    cand_rows, chosen = [], []
    for name, m, why in cands:
        m = m & keep
        row = _stage(name, m, T, ex, date, tr, te)
        row["why"] = why
        a, b = row.get("tr"), row.get("te")
        row["pass"] = bool(a and b and a["n"] >= 300 and b["n"] >= 200 and a["excess"] > 0 and b["excess"] > 0)
        cand_rows.append(row)
        if row["pass"]:
            chosen.append((name, m))
    if model is not None:
        m = ai_top & keep
        row = _stage(f"AI 점수 상위 {AI_TOP * 100:.0f}%", m, T, ex, date, tr, te, note="~2021 은 학습에 쓴 기간(참고만), 2022~ 가 실제 성적")
        row["why"] = "AI 분석: 상위 5% 적중 2.2배"
        row["pass"] = bool(row.get("te") and row["te"]["n"] >= 200 and row["te"]["excess"] > 0)
        row["ai"] = True
        cand_rows.append(row)
    rep["candidates"] = cand_rows
    rep["chosen"] = [n for n, _ in chosen]
    sig_any = np.zeros(len(date), bool)
    for _, m in chosen:
        sig_any |= m
    sig_all = np.zeros(len(date), bool)
    for _, m, _ in cands:
        sig_all |= (m & keep)

    # ---- 단계별(쌓기) 표
    gap_ok = (T["gap1"] <= GAP_MAX)
    stages = [
        _stage("0. 기준 — 거래 가능 종목 아무 날 (전액, 다음날 시가)", ok, T, ex, date, tr, te),
        _stage("1. 피함 7개 규칙 제외", keep, T, ex, date, tr, te),
        _stage("2a. (참고) 후보 신호 전부 중 하나라도", sig_all, T, ex, date, tr, te),
        _stage("2. 채택 신호 중 하나라도 (두 시기 모두 시장보다 좋았던 것)", sig_any, T, ex, date, tr, te),
        _stage(f"3. 갭 +{GAP_MAX * 100:.0f}% 이하일 때만 진입", sig_any & gap_ok, T, ex, date, tr, te),
        _stage("4. 3분할 진입 (다음날·5일 뒤·10일 뒤 1/3씩)", sig_any & gap_ok, T, ex, date, tr, te, ret_key="split3", mae_key="mae_split",
               note="수익은 분할 평균가 기준 · 시장 대비는 전액 기준 날짜 평균을 뺀 값"),
        _stage("4'. 전액 + 2×ATR 손절 (3분할 대신)", sig_any & gap_ok, T, ex, date, tr, te, ret_key="stop_ret"),
    ]
    if model is not None:
        stages.append(_stage(f"5. 3·4단계 + AI 점수 상위 {AI_TOP * 100:.0f}% (3분할)", sig_any & gap_ok & ai_top, T, ex, date, tr, te, ret_key="split3", mae_key="mae_split",
                             note="~2021 은 AI 학습 기간이라 2022~ 만 믿을 것"))
        stages.append(_stage(f"5'. 피함 제외 + 갭 + AI 상위 {AI_TOP * 100:.0f}% (신호 없이, 3분할)", keep & gap_ok & ai_top, T, ex, date, tr, te, ret_key="split3", mae_key="mae_split"))
    rep["stages"] = stages
    final = sig_any & gap_ok
    rep["final"] = {"n": int(final.sum()), "n_te": int((final & te).sum()), "yearly": _yearly(final, T, ex, date, "split3"), "yearly_full": _yearly(final, T, ex, date, "r20"),
                    "monthly_te": _monthly(final & te, T, date, "split3"), "regime": _regime(final, T, ex, D, tr, te),
                    "by_signal": [_stage(n, m & gap_ok, T, ex, date, tr, te, ret_key="split3", mae_key="mae_split") for n, m in chosen]}
    # 신호 겹침 수(채택 신호 중 몇 개가 동시에)
    cnt = np.zeros(len(date), int)
    for _, m in chosen:
        cnt += (m & gap_ok).astype(int)
    rep["final"]["overlap"] = [_stage(f"채택 신호 {k}개 겹침" if k < 3 else "채택 신호 3개 이상 겹침", (cnt == k) if k < 3 else (cnt >= 3), T, ex, date, tr, te, ret_key="split3", mae_key="mae_split")
                               for k in (1, 2, 3) if ((cnt == k) if k < 3 else (cnt >= 3)).sum() >= 100]

    # ---- 오늘 후보: 최신 날 기준 피함 제외 + 채택 신호
    rep["today"] = _today(latest, names, model, rep["chosen"])
    rep["text"] = narrate(rep)
    rep["meta"]["elapsed_sec"] = round(time.time() - t0)
    COMBINED_PATH.parent.mkdir(parents=True, exist_ok=True)
    COMBINED_PATH.write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
    return rep


def _today(latest, names, model, chosen, top=40):
    L = [r for r in latest if r["ok"] and np.isfinite(r["X"][ai.NAMES.index("ret60")]) and r["X"][ai.NAMES.index("liq")] >= math.log10(ai.TRADABLE_VALUE)]
    if not L:
        return {"date": None, "rows": [], "n_tradable": 0, "n_keep": 0}
    XL = np.vstack([r["X"] for r in L])
    D = pd.DataFrame(XL, columns=ai.NAMES)
    for k in ("zero_vol60", "gapdn20", "down_streak", "close_px"):
        D[k] = [r[k] for r in L]
    D["gap1"] = np.nan
    rules = {n: f for n, f in avoid_study.RULES}
    avoid = np.zeros(len(L), bool)
    hits = [[] for _ in L]
    for n in AVOID_NAMES:
        m = rules[n](D).fillna(False).to_numpy()
        avoid |= m
        for i in np.flatnonzero(m):
            hits[i].append(n)
    cands = {n: m for n, m, _ in candidates(D)}
    P = model.predict_proba(XL)[:, 1] if model is not None else np.full(len(L), np.nan)
    rows = []
    for i, r in enumerate(L):
        sigs = [n for n in chosen if cands[n][i]]
        if avoid[i] or not sigs:
            continue
        x = r["X"]
        g = lambda k: float(x[ai.NAMES.index(k)])  # noqa: E731
        rows.append({"code": r["code"], "name": names.get(r["code"], r["code"]), "date": r["date"], "close": r["close"], "signals": sigs, "n_sig": len(sigs),
                     "prob": None if np.isnan(P[i]) else float(P[i]), "volratio": g("volratio"), "ret20": g("ret20"), "nh250": g("nh250"), "atrp": g("atrp"),
                     "dist20": g("dist20"), "mkt_r60": g("mkt_r60")})
    rows.sort(key=lambda z: (-z["n_sig"], -(z["prob"] or 0)))
    date = max(r["date"] for r in L)
    rows = [z for z in rows if z["date"] == date]
    return {"date": date, "rows": rows[:top], "n_rows": len(rows), "n_tradable": len(L), "n_keep": int((~avoid).sum())}


def narrate(rep: dict) -> list[str]:
    out = []
    st = rep["stages"]
    base, fin = st[0], next((s for s in st if s["name"].startswith("4.")), None)
    te = base.get("te")
    if te:
        out.append(f"기준(거래 가능 종목 아무 날, 시험 기간 2022~): 20일 평균 {te['mean'] * 100:+.2f}%, 상승 {te['p_up'] * 100:.0f}%, −20%↓ {te['p_dn20'] * 100:.0f}%.")
    s1 = st[1].get("te")
    if s1:
        out.append(f"피함 7개 규칙으로 {rep['avoid']['share'] * 100:.0f}%를 빼면 시험 기간 평균 {s1['mean'] * 100:+.2f}%, −20%↓ {s1['p_dn20'] * 100:.0f}%.")
    out.append(f"진입 신호 후보 {len(rep['candidates'])}개 중 두 시기 모두 시장보다 좋았던 것은 {len(rep['chosen'])}개: " + (", ".join(rep["chosen"]) if rep["chosen"] else "없음") + ".")
    s2 = next((s for s in st if s["name"].startswith("2.")), None)
    if s2 and s2.get("te"):
        t = s2["te"]
        out.append(f"채택 신호(시험 기간 {t['n']:,}건, 한 달 평균 {s2['per_month_te']:.0f}건): 평균 {t['mean'] * 100:+.2f}%, 시장 대비 {t['excess'] * 100:+.2f}%p, 상승 {t['p_up'] * 100:.0f}%, +10%↑ {t['p_big_up'] * 100:.0f}%, −20%↓ {t['p_dn20'] * 100:.0f}%.")
    s3 = next((s for s in st if s["name"].startswith("3.")), None)
    if s3 and s3.get("te") and s2 and s2.get("te"):
        out.append(f"갭 +{rep['meta']['gap_max'] * 100:.0f}% 이하만 사면 {s2['te']['n']:,}건 중 {s3['te']['n']:,}건 체결, 평균 {s3['te']['mean'] * 100:+.2f}%(시장 대비 {s3['te']['excess'] * 100:+.2f}%p).")
    if fin and fin.get("te"):
        t = fin["te"]
        out.append(f"3분할까지 적용한 최종 규칙(시험 기간): 평균 {t['mean'] * 100:+.2f}%, 변동폭 {t['std'] * 100:.1f}%, 보유 중 평균 최대 낙폭 {t['mae'] * 100:+.1f}%, 최악 5% {t['q05'] * 100:+.1f}%, 60일 {t['r60'] * 100:+.1f}% · 120일 {t['r120'] * 100:+.1f}%.")
        mo = rep["final"].get("monthly_te")
        if mo:
            out.append(f"시험 기간 월별로 보면 {mo['months']}개월 중 {mo['p_pos'] * 100:.0f}%가 플러스, 월평균 {mo['mean'] * 100:+.2f}%, 최악 달 {mo['worst'] * 100:+.1f}%, 매달 전액 재투자 가정 누적 {mo['cum'] * 100:+.0f}%.")
    s5 = next((s for s in st if s["name"].startswith("5.")), None)
    if s5 and s5.get("te"):
        t = s5["te"]
        out.append(f"여기에 AI 상위 {rep['meta']['ai_top'] * 100:.0f}%를 더하면(시험 기간 {t['n']:,}건) 평균 {t['mean'] * 100:+.2f}%, 시장 대비 {t['excess'] * 100:+.2f}%p, 상승 {t['p_up'] * 100:.0f}%.")
    td = rep.get("today") or {}
    if td.get("date"):
        out.append(f"{td['date']} 기준 거래 가능 {td['n_tradable']:,}종목 중 피함 제외 {td['n_keep']:,}종목, 채택 신호가 있는 종목 {td.get('n_rows', 0)}개(다음날 시가 갭 +{rep['meta']['gap_max'] * 100:.0f}% 넘으면 안 삼).")
    return out


if __name__ == "__main__":
    run()
