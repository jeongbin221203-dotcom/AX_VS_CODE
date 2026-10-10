"""거래정지 전 패턴 → 진입 위험 점수 + 성공률(상승 확률) 더 올리기.

표본은 strategy_study 가 만든 캐시(data/strategy_rows.npz)를 그대로 쓴다(연중일 5일 간격, 다음날 시가 진입, 20거래일, 비용 0.3%).
  1. 거래정지(거래량 0 이 20일↑) 120일 안에 들어간 행 vs 아닌 행의 모습 차이, 위험 조건 표
  2. 거래정지 위험 모델(~2021 학습, 2022~ 시험): AUC·구간별 실제 확률 → 위험 등급(낮음/중간/높음), 오늘 종목 위험 목록
  3. 성공률 올리기: 보강 규칙(피함 제외 + 갭 + AI 상위 10% + 시장 52주 고점 −10% 아래) 위에 미리 정한 보강 후보를
     학습·시험 두 시기 모두 상승 확률·평균이 오를 때만 차례로 더함 → 해마다 AI 를 다시 학습한 걸어가며 검증으로 최종 확인
"""
import json
import math
import time
from datetime import datetime

import numpy as np
import pandas as pd

import config
from core import ai, avoid_study, combined_study as cs, db, strategy_study as st

RISK_PATH = config.DATA_DIR / "risk.json"
ENS_PATH = config.DATA_DIR / "ai_ens.joblib"
HALT_MODEL_PATH = config.DATA_DIR / "halt_model.joblib"
COST = ai.COST
EXTRA = ["zero_vol60", "gapdn20", "down_streak", "close_px"]
RISK_HIGH, RISK_MID = 0.03, 0.012          # 120일 안 거래정지 예측 확률
HALT_FEATS = [f for f in ai.NAMES if f not in ai.SIGNAL_NAMES] + EXTRA


def _load_rows():
    z = np.load(st.ROWS_PATH, allow_pickle=True)
    X, date, code = z["X"], z["date"], z["code"]
    T = {k: z["T_" + k] for k in st.ROW_KEYS}
    latest = list(z["latest"])
    halts = pd.DataFrame(json.loads(str(z["halts"])))
    return X, date, code, T, latest, halts


def _feat_matrix(D: pd.DataFrame):
    return D[HALT_FEATS].to_numpy(np.float32)


def _halt_patterns(D, h120, h60, ok, tr):
    """거래정지 전 모습: 120일 안 정지 vs 아님 — 특징 중앙값·표준화 차이, 위험 조건 표(학습·시험 따로)."""
    pos, neg = ok & (h120 == 1), ok & (h120 == 0)
    feats = ["ret5", "ret20", "ret60", "nh250", "dist20", "dist60", "dist224", "atrp", "vol20", "volratio", "vr5", "vr20_60", "vdry10", "upvol20", "liq", "liq_chg", "price_level",
             "zero_vol60", "gapdn20", "down_streak", "upper", "range1", "gap0", "hist_bars", "reversed", "vspike_n", "mkt_r60"]
    diff = []
    for f in feats:
        a, b = D.loc[pos, f], D.loc[neg, f]
        sd = float(D.loc[ok, f].std()) or 1.0
        diff.append({"name": f, "label": ai.LABEL.get(f, f), "pos": float(a.median()), "neg": float(b.median()), "d": float((a.mean() - b.mean()) / sd)})
    diff.sort(key=lambda x: -abs(x["d"]))
    specs = [("주가 1,000원 미만", D["close_px"] < 1000), ("주가 500원 미만", D["close_px"] < 500), ("거래대금 3~5억 (하한 근처)", D["liq"] < math.log10(5e8)),
             ("20일 −20%↓ 급락", D["ret20"] <= -0.2), ("20일 −40%↓ 급락", D["ret20"] <= -0.4), ("5일 −20%↓ 폭락", D["ret5"] <= -0.2), ("60일 −40%↓", D["ret60"] <= -0.4),
             ("52주 고점 −70%↓", D["nh250"] <= -0.7), ("224일선 −50%↓", D["dist224"] <= -0.5), ("변동성 12%↑", D["atrp"] >= 0.12), ("변동성 8%↑", D["atrp"] >= 0.08),
             ("60일 중 거래 없는 날 1일↑", D["zero_vol60"] >= 1), ("60일 중 거래 없는 날 3일↑", D["zero_vol60"] >= 3), ("20일 −5%↓ 갭하락 2번↑", D["gapdn20"] >= 2),
             ("연속 하락 5일↑", D["down_streak"] >= 5), ("역배열", D["reversed"] == 1), ("20일 중 거래량 2배↑ 날 5일↑", D["vspike_n"] >= 5),
             ("5일 거래량 ÷ 20일 3배↑ (물량 분출)", D["vr5"] >= 3), ("거래대금 5일÷60일 급감 (−0.5↓)", D["liq_chg"] <= -0.5), ("거래대금 5일÷60일 급증 (+0.5↑)", D["liq_chg"] >= 0.5),
             ("상장 2년 미만", D["hist_bars"] < math.log10(500)), ("시장 60일 −10%↓", D["mkt_r60"] <= -0.1)]
    base_tr, base_te = float(h120[ok & tr].mean()), float(h120[ok & ~tr].mean())
    rows = []
    for name, m in specs:
        mm = m.to_numpy() & ok
        a, b = mm & tr, mm & ~tr
        if a.sum() < 500 or b.sum() < 300:
            continue
        p_tr, p_te = float(h120[a].mean()), float(h120[b].mean())
        rows.append({"name": name, "n": int(mm.sum()), "share": float(mm.sum() / ok.sum()), "p120": float(h120[mm].mean()), "p60": float(h60[mm].mean()),
                     "p_tr": p_tr, "p_te": p_te, "lift_tr": p_tr / base_tr if base_tr else None, "lift_te": p_te / base_te if base_te else None,
                     "robust": p_tr >= base_tr * 2 and p_te >= base_te * 2})
    rows.sort(key=lambda x: -((x["lift_tr"] or 0) + (x["lift_te"] or 0)))
    return {"diff": diff[:16], "rules": rows, "base": float(h120[ok].mean()), "base_tr": base_tr, "base_te": base_te, "n_pos": int(pos.sum())}


def _halt_model(D, h120, ok, tr, te, log, t0):
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score
    F = _feat_matrix(D)
    y = (h120 == 1).astype(int)
    pos = np.flatnonzero(ok & tr & (y == 1))
    neg = st._sub(ok & tr & (y == 0), 400000, seed=5)
    idx = np.r_[pos, neg]
    mdl = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.06, max_leaf_nodes=31, min_samples_leaf=100, l2_regularization=2.0, random_state=0).fit(F[idx], y[idx])
    p = np.full(len(y), np.nan)
    p[ok] = st._predict(mdl, F[ok])
    # 표본 비율 보정: 음성 하위 추출 비율만큼 odds 를 줄여 실제 확률로
    ratio = (ok & tr & (y == 0)).sum() / max(len(neg), 1)
    p = p / (p + (1 - p) * ratio)
    m = ok & te
    auc = float(roc_auc_score(y[m], p[m]))
    auc_tr = float(roc_auc_score(y[ok & tr], p[ok & tr]))
    q = pd.qcut(pd.Series(p[m]).rank(method="first"), 10, labels=False).to_numpy()
    deciles = [{"bin": int(k + 1), "p_mean": float(p[m][q == k].mean()), "actual": float(y[m][q == k].mean()), "n": int((q == k).sum())} for k in range(10)]
    tiers = []
    for name, mm in (("낮음", p < RISK_MID), ("중간", (p >= RISK_MID) & (p < RISK_HIGH)), ("높음", p >= RISK_HIGH)):
        for lab, mask in (("tr", ok & tr), ("te", m)):
            x = mm & mask
            row = next((t for t in tiers if t["name"] == name), None)
            if row is None:
                row = {"name": name}
                tiers.append(row)
            row[lab] = {"n": int(x.sum()), "share": float(x.sum() / max(mask.sum(), 1)), "actual": float(y[x].mean()) if x.sum() else None}
    log(f"[2] 거래정지 모델 AUC 시험 {auc:.3f} (학습 {auc_tr:.3f}) · {time.time() - t0:.0f}초")
    # 중요 특징(순열 중요도, 시험 표본 일부)
    imp = []
    try:
        from sklearn.inspection import permutation_importance
        sub = st._sub(m, 60000, seed=7)
        r = permutation_importance(mdl, F[sub], y[sub], scoring="roc_auc", n_repeats=3, random_state=0, n_jobs=1)
        order = np.argsort(-r.importances_mean)[:12]
        imp = [{"name": HALT_FEATS[i], "label": ai.LABEL.get(HALT_FEATS[i], {"zero_vol60": "60일 중 거래 없는 날", "gapdn20": "20일 중 −5% 갭하락 횟수", "down_streak": "연속 하락일", "close_px": "주가"}.get(HALT_FEATS[i], HALT_FEATS[i])),
                "gain": float(r.importances_mean[i])} for i in order]
    except Exception:
        pass
    try:
        import joblib
        joblib.dump(mdl, HALT_MODEL_PATH)
    except Exception:
        pass
    return mdl, p, {"auc": auc, "auc_tr": auc_tr, "deciles": deciles, "tiers": tiers, "importance": imp, "thresholds": {"high": RISK_HIGH, "mid": RISK_MID}, "ratio": float(ratio)}


def _ensemble(X, T, tr, log, t0, seed=2):
    models = {}
    for key, (hold, thr, _) in ai.HORIZONS.items():
        if key not in ("short", "mid", "midlong"):
            continue
        rr = T[f"r{hold}"].astype(float)
        v = tr & ~np.isnan(rr)
        idx = st._sub(v, st.AI_SUB, seed=seed)
        models[key] = st._fit_fast(X[idx], (rr[idx] >= thr).astype(int), seed=seed)
    log(f"[3] 기간 앙상블 학습 · {time.time() - t0:.0f}초")
    return models


def _ens_score(models, X, m):
    ranks = []
    for mdl in models.values():
        p = np.full(len(X), np.nan)
        p[m] = st._predict(mdl, X[m])
        ranks.append(pd.Series(p[m]).rank(pct=True).to_numpy())
    out = np.full(len(X), np.nan)
    out[m] = np.mean(ranks, axis=0)
    return out


def _improve(D, T, X, date, keep, gap_ok, P, p_ens, p_halt, cands, chosen, r, ex, tr, te, mk_under):
    """보강 후보를 미리 정해 두고, 학습·시험 모두 상승 확률(+1%p↑)과 평균이 오르면 더한다(탐욕적, 한 번에 하나)."""
    ai_top = P >= np.nanquantile(P[te], 0.90)
    ai_top5 = P >= np.nanquantile(P[te], 0.95)
    ens_top = p_ens >= np.nanquantile(p_ens[te], 0.90)
    ens_top5 = p_ens >= np.nanquantile(p_ens[te], 0.95)
    sig_any = np.zeros(len(date), bool)
    for n in chosen:
        sig_any |= cands[n]
    halt_low = p_halt < RISK_MID
    halt_not_high = p_halt < RISK_HIGH
    extra_avoid = ((D["dist224"] <= -0.5) | (D["ret20"] <= -0.4) | (D["nh250"] <= -0.7)).to_numpy()
    base = keep & gap_ok & ai_top & mk_under
    options = [
        ("거래정지 위험 '높음' 제외", halt_not_high), ("거래정지 위험 '낮음'만", halt_low),
        ("피함 3개 추가 (224일선 −50%↓·20일 −40%↓·52주 −70%↓)", ~extra_avoid),
        ("AI 상위 5%만", ai_top5), ("기간 앙상블 상위 10% 도 함께", ens_top), ("기간 앙상블 상위 5% 도 함께", ens_top5),
        ("채택 신호도 같이 있을 때만", sig_any), ("변동성 5%↑만 (ATR÷종가)", (D["atrp"] >= 0.05).to_numpy()),
        ("52주 고점 −10~−30% 구간 제외", ~((D["nh250"] < -0.10) & (D["nh250"] >= -0.30)).to_numpy()),
        ("거래대금 10억↑만", (D["liq"] >= 9).to_numpy()), ("20일선 위만", (D["dist20"] > 0).to_numpy()), ("60일선 위만", (D["dist60"] > 0).to_numpy()),
        ("시장 20일 수익률 0 미만도", (D["mkt_r20"] < 0).to_numpy()), ("20일 중 거래량 2배↑ 날 2일 이하", (D["vspike_n"] <= 2).to_numpy()),
        ("당일 등락 0~+5% (급등일 제외)", ((D["ret1"] >= 0) & (D["ret1"] < 0.05)).to_numpy()),
    ]
    steps = [("출발: 피함 제외 + 갭 + AI 상위 10% + 시장 52주 고점 −10% 아래", base.copy())]
    m = base.copy()
    used, tried = [], []
    remaining = list(options)
    for _ in range(5):
        cur_tr, cur_te = st._stats(r[m & tr]), st._stats(r[m & te])
        best, best_gain = None, 0.0
        for name, mask in remaining:
            mm = m & mask
            s_tr, s_te = st._stats(r[mm & tr]), st._stats(r[mm & te])
            row = {"name": name, "n_te": int((mm & te).sum()), "p_up_tr": s_tr["p_up"] if s_tr else None, "p_up_te": s_te["p_up"] if s_te else None,
                   "mean_tr": s_tr["mean"] if s_tr else None, "mean_te": s_te["mean"] if s_te else None, "stage": len(used) + 1}
            okk = s_tr and s_te and s_te["n"] >= 300 and s_tr["n"] >= 300 and s_tr["p_up"] >= cur_tr["p_up"] + 0.01 and s_te["p_up"] >= cur_te["p_up"] + 0.01 \
                and s_tr["mean"] > cur_tr["mean"] and s_te["mean"] > cur_te["mean"]
            row["pass"] = bool(okk)
            tried.append(row)
            if okk:
                gain = (s_te["p_up"] - cur_te["p_up"]) + (s_tr["p_up"] - cur_tr["p_up"])
                if gain > best_gain:
                    best, best_gain = (name, mask), gain
        if not best:
            break
        name, mask = best
        m = m & mask
        used.append(name)
        remaining = [o for o in remaining if o[0] != name]
        steps.append((f"+ {name}", m.copy()))
    rows = []
    for name, mm in steps:
        row = st._both(name, mm, r, ex, tr, te)
        row["per_month_te"] = float((mm & te).sum() / max(len(pd.Series(date[te]).str[:7].unique()), 1))
        row["split3_te"] = st._stats(T["split3"][mm & te].astype(float) - COST)
        row["tgt10_te"] = st._stats(T["tgt10"][mm & te].astype(float) - COST)
        rows.append(row)
    return {"steps": rows, "used": used, "tried": tried, "_mask": m, "_base": base}


def _walk_check(X, T, date, year, keep, gap_ok, mk_under, extra_mask, r, ex, log, t0, ai_q=0.9):
    """최종 규칙을 해마다 다시 학습한 AI 로 다시 확인(2012~): 그 해 전까지 학습 → 그 해 상위 10%."""
    y20 = (T["r20"] >= ai.TARGET)
    valid = ~np.isnan(r)
    rows = []
    oos = np.zeros(len(date), bool)
    oos_base = np.zeros(len(date), bool)
    for Y in sorted(y for y in set(year) if y >= 2012):
        past, cur = (year < Y) & valid, (year == Y) & valid
        if past.sum() < 50000 or cur.sum() < 500:
            continue
        idx = st._sub(past, st.AI_SUB, seed=Y)
        mdl = st._fit_fast(X[idx], y20[idx].astype(int), seed=Y)
        p = st._predict(mdl, X[cur])
        top = np.zeros(len(date), bool)
        top[np.flatnonzero(cur)[p >= np.nanquantile(p, 0.9)]] = True
        top5 = np.zeros(len(date), bool)
        top5[np.flatnonzero(cur)[p >= np.nanquantile(p, ai_q)]] = True
        b = cur & keep & gap_ok & top & mk_under
        f = b & extra_mask & top5
        oos |= f
        oos_base |= b
        rows.append({"year": int(Y), "base": st._stats(r[b], ex[b]), "final": st._stats(r[f], ex[f])})
        log(f"[4] {Y}: 시장 필터 규칙 {rows[-1]['base']['p_up'] * 100 if rows[-1]['base'] else float('nan'):.0f}% → 최종 {rows[-1]['final']['p_up'] * 100 if rows[-1]['final'] else float('nan'):.0f}% · {time.time() - t0:.0f}초")
    return {"rows": rows, "total": {"base": st._stats(r[oos_base], ex[oos_base]), "final": st._stats(r[oos], ex[oos])},
            "years_better": int(sum(1 for x in rows if x["base"] and x["final"] and x["final"]["p_up"] > x["base"]["p_up"])), "years": int(sum(1 for x in rows if x["base"] and x["final"]))}


def _today(latest, names, halt_mdl, ens_models, P_model, chosen, D_template_cols, mkt_under_now, improve_used, top=40):
    L = [r for r in latest if r["ok"] and np.isfinite(r["X"][ai.NAMES.index("ret60")]) and r["X"][ai.NAMES.index("liq")] >= math.log10(ai.TRADABLE_VALUE)]
    if not L:
        return {"rows": [], "risk_rows": [], "n": 0}
    XL = np.vstack([r["X"] for r in L])
    D = pd.DataFrame(XL, columns=ai.NAMES)
    for k in EXTRA:
        D[k] = [r[k] for r in L]
    D["gap1"] = np.nan
    ph = halt_mdl.predict_proba(_feat_matrix(D))[:, 1]
    ratio = D_template_cols
    ph = ph / (ph + (1 - ph) * ratio)
    rules = {n: f for n, f in avoid_study.RULES}
    avoid = np.zeros(len(L), bool)
    for n in cs.AVOID_NAMES:
        avoid |= rules[n](D).fillna(False).to_numpy()
    P = P_model.predict_proba(XL)[:, 1] if P_model is not None else np.full(len(L), np.nan)
    pe = _ens_score(ens_models, XL, np.ones(len(L), bool)) if ens_models else np.full(len(L), np.nan)
    cands = {n: m for n, m, _ in cs.candidates(D)}
    date = max(r["date"] for r in L)
    rows, risk_rows = [], []
    thr_ai = np.nanquantile(P, 0.9) if np.isfinite(P).any() else np.nan
    for i, r in enumerate(L):
        if r["date"] != date:
            continue
        tier = "높음" if ph[i] >= RISK_HIGH else "중간" if ph[i] >= RISK_MID else "낮음"
        g = lambda k: float(D[k].iloc[i])  # noqa: E731
        base = {"code": r["code"], "name": names.get(r["code"], r["code"]), "close": r["close"], "halt_p": float(ph[i]), "tier": tier, "prob": None if np.isnan(P[i]) else float(P[i]),
                "ens": None if np.isnan(pe[i]) else float(pe[i]), "ret20": g("ret20"), "nh250": g("nh250"), "atrp": g("atrp"), "liq": 10 ** g("liq"), "zero_vol60": g("zero_vol60"),
                "dist224": g("dist224"), "avoid": bool(avoid[i]), "signals": [n for n in chosen if cands[n][i]]}
        if ph[i] >= RISK_MID:
            risk_rows.append(base)
        if not avoid[i] and np.isfinite(P[i]) and P[i] >= thr_ai:
            rows.append(base)
    risk_rows.sort(key=lambda z: -z["halt_p"])
    rows.sort(key=lambda z: -(z["prob"] or 0))
    return {"date": date, "rows": rows[:top], "risk_rows": risk_rows[:top], "n_risk_high": sum(1 for z in risk_rows if z["tier"] == "높음"), "n_risk_mid": sum(1 for z in risk_rows if z["tier"] == "중간"),
            "n": len([r for r in L if r["date"] == date]), "market_under": mkt_under_now, "n_candidates": len(rows), "used": improve_used}


def run(log=print, limit: int = 0) -> dict:
    t0 = time.time()
    if not st.ROWS_PATH.exists():
        raise SystemExit("먼저 python manage.py strategy-train 으로 표본 캐시를 만드세요.")
    X, date, code, T, latest, halts = _load_rows()
    if limit:
        keep_codes = set(sorted(set(code))[::max(len(set(code)) // limit, 1)][:limit])
        sel = np.array([c in keep_codes for c in code])
        X, date, code = X[sel], date[sel], code[sel]
        T = {k: v[sel] for k, v in T.items()}
    with db.get_conn() as c:
        names = {r["code"]: r["name"] for r in c.execute("SELECT code,name FROM symbols")}
    D = pd.DataFrame(X, columns=ai.NAMES)
    for k in EXTRA + ["gap1"]:
        D[k] = T[k]
    year = pd.Series(date).str[:4].astype(int).to_numpy()
    tr_, va_, te = ai._split_masks(date)
    tr = tr_ | va_
    r20 = T["r20"].astype(float)
    ok = ~np.isnan(r20)
    r = r20 - COST
    ex = (pd.Series(r20) - pd.Series(r20).groupby(pd.Series(date)).transform("mean")).to_numpy()
    h120, h60 = T["halt120"].astype(float), T["halt60"].astype(float)
    okh = ok & ~np.isnan(h120)
    log(f"표본 {len(date):,}행 · 거래정지 120일 안 {int(np.nansum(h120)):,}행 · {time.time() - t0:.0f}초")
    rep = {"meta": {"generated": datetime.now().isoformat(timespec="seconds"), "rows": int(len(date)), "stocks": int(len(set(code))), "cost": COST, "halt_min": st.HALT_MIN,
                    "split": {"train_end": ai.VALID_END, "date_min": str(date.min()), "date_max": str(date.max())}, "risk_high": RISK_HIGH, "risk_mid": RISK_MID}}
    rep["patterns"] = _halt_patterns(D, h120, h60, okh, tr)
    halt_mdl, p_halt, rep["model"] = _halt_model(D, h120, okh, tr, te, log, t0)
    # 규칙 재현
    rules = {n: f for n, f in avoid_study.RULES}
    avoid = np.zeros(len(date), bool)
    for n in cs.AVOID_NAMES:
        avoid |= rules[n](D).to_numpy()
    keep = ok & ~avoid
    gap_ok = T["gap1"] <= cs.GAP_MAX
    cands = {n: m for n, m, _ in cs.candidates(D)}
    try:
        chosen = [n for n in json.loads(cs.COMBINED_PATH.read_text(encoding="utf-8"))["chosen"] if n in cands]
    except (OSError, ValueError, KeyError):
        chosen = ["52주 신고가", "정배열 신호"]
    model = cs._load_model()
    P = st._predict(model, X) if model is not None else np.full(len(date), np.nan)
    mk_under = (D["mkt_dd250"] < -0.10).to_numpy()
    ens = _ensemble(X, T, tr, log, t0)
    p_ens = _ens_score(ens, X, ok)
    # 위험 등급별 규칙 성과(AI 규칙 + 시장 필터)
    base = keep & gap_ok & (P >= np.nanquantile(P[te], 0.9)) & mk_under
    rep["risk_vs_return"] = [st._both(name, base & m, r, ex, tr, te, {"share": float((base & m).sum() / max(base.sum(), 1)), "halt": float(np.nanmean(h120[base & m]))})
                             for name, m in (("위험 낮음", p_halt < RISK_MID), ("위험 중간", (p_halt >= RISK_MID) & (p_halt < RISK_HIGH)), ("위험 높음", p_halt >= RISK_HIGH)) if (base & m).sum() >= 100]
    rep["improve"] = _improve(D, T, X, date, keep, gap_ok, P, p_ens, p_halt, cands, chosen, r, ex, tr, te, mk_under)
    log(f"[3] 보강 후보 적용: {rep['improve']['used']} · {time.time() - t0:.0f}초")
    final_mask, base_mask = rep["improve"].pop("_mask"), rep["improve"].pop("_base")
    # 최종 규칙의 '추가 조건'만 떼어(AI·시장 필터 제외) 걸어가며 검증에 넘김
    extra = np.ones(len(date), bool)
    not_walked = [n for n in rep["improve"]["used"] if n.startswith("기간 앙상블")]      # 해마다 다시 학습하지 않은 조건은 걸어가며 검증에서 뺌
    for name, mask in [("거래정지 위험 '높음' 제외", p_halt < RISK_HIGH), ("거래정지 위험 '낮음'만", p_halt < RISK_MID),
                       ("피함 3개 추가 (224일선 −50%↓·20일 −40%↓·52주 −70%↓)", ~((D["dist224"] <= -0.5) | (D["ret20"] <= -0.4) | (D["nh250"] <= -0.7)).to_numpy()),
                       ("AI 상위 5%만", P >= np.nanquantile(P[te], 0.95)), ("기간 앙상블 상위 10% 도 함께", p_ens >= np.nanquantile(p_ens[te], 0.9)),
                       ("기간 앙상블 상위 5% 도 함께", p_ens >= np.nanquantile(p_ens[te], 0.95)), ("채택 신호도 같이 있을 때만", np.logical_or.reduce([cands[n] for n in chosen]) if chosen else extra),
                       ("변동성 5%↑만 (ATR÷종가)", (D["atrp"] >= 0.05).to_numpy()), ("52주 고점 −10~−30% 구간 제외", ~((D["nh250"] < -0.10) & (D["nh250"] >= -0.30)).to_numpy()),
                       ("거래대금 10억↑만", (D["liq"] >= 9).to_numpy()), ("20일선 위만", (D["dist20"] > 0).to_numpy()), ("60일선 위만", (D["dist60"] > 0).to_numpy()),
                       ("시장 20일 수익률 0 미만도", (D["mkt_r20"] < 0).to_numpy()), ("20일 중 거래량 2배↑ 날 2일 이하", (D["vspike_n"] <= 2).to_numpy()),
                       ("당일 등락 0~+5% (급등일 제외)", ((D["ret1"] >= 0) & (D["ret1"] < 0.05)).to_numpy())]:
        if name in rep["improve"]["used"] and name not in not_walked and name != "AI 상위 5%만":
            extra &= mask
    rep["walk"] = _walk_check(X, T, date, year, keep, gap_ok, mk_under, extra, r, ex, log, t0, ai_q=0.95 if "AI 상위 5%만" in rep["improve"]["used"] else 0.9)
    rep["walk"]["not_walked"] = not_walked
    # 오늘: 모델은 전체 기간으로 다시 학습한 것 대신 ~2021 학습 모델 그대로(검증된 것) 사용
    mkt = pd.read_pickle(ai.bt.MKT_PATH)
    now_dd = float(mkt["mkt_dd250"].dropna().iloc[-1])
    rep["today"] = _today(latest, names, halt_mdl, ens, model, chosen, rep["model"]["ratio"], {"dd250": now_dd, "under": bool(now_dd < -0.10)}, rep["improve"]["used"])
    try:
        import joblib
        joblib.dump(ens, ENS_PATH)
    except Exception:
        pass
    rep["text"] = narrate(rep)
    rep["meta"]["elapsed_sec"] = round(time.time() - t0)
    RISK_PATH.write_text(json.dumps(rep, ensure_ascii=False, default=st._js), encoding="utf-8")
    return rep


def narrate(rep):
    out = []
    pt, md = rep["patterns"], rep["model"]
    top = [x for x in pt["rules"] if x["robust"]][:5]
    out.append(f"거래정지(120일 안) 기준 확률 {pt['base'] * 100:.2f}%. 두 시기 모두 2배↑인 위험 조건: " + (", ".join(f"{x['name']} {x['p120'] * 100:.1f}%({(x['lift_tr'] + x['lift_te']) / 2:.0f}배)" for x in top) if top else "없음") + ".")
    d = md["deciles"]
    out.append(f"거래정지 위험 모델 AUC {md['auc']:.2f}(시험 기간). 위험 점수 상위 10%의 실제 거래정지 {d[-1]['actual'] * 100:.1f}% vs 하위 10% {d[0]['actual'] * 100:.2f}%. "
               f"등급: 높음(예측 {RISK_HIGH * 100:.0f}%↑) 실제 {next((t['te']['actual'] for t in md['tiers'] if t['name'] == '높음'), 0) * 100:.1f}%, 중간 {next((t['te']['actual'] for t in md['tiers'] if t['name'] == '중간'), 0) * 100:.1f}%, 낮음 {next((t['te']['actual'] for t in md['tiers'] if t['name'] == '낮음'), 0) * 100:.2f}%.")
    rv = {x["name"]: x for x in rep["risk_vs_return"]}
    if "위험 낮음" in rv and "위험 높음" in rv and rv["위험 낮음"]["te"] and rv["위험 높음"]["te"]:
        out.append(f"시장 필터 AI 규칙에서 위험 낮음은 상승 {rv['위험 낮음']['te']['p_up'] * 100:.0f}%·평균 {rv['위험 낮음']['te']['mean'] * 100:+.2f}%, 위험 높음은 상승 {rv['위험 높음']['te']['p_up'] * 100:.0f}%·평균 {rv['위험 높음']['te']['mean'] * 100:+.2f}%(시험 기간).")
    stp = rep["improve"]["steps"]
    a, b = stp[0], stp[-1]
    if a.get("te") and b.get("te"):
        out.append(f"성공률 보강(시험 기간): 상승 {a['te']['p_up'] * 100:.0f}% · 평균 {a['te']['mean'] * 100:+.2f}%(한 달 {a['per_month_te']:.0f}건) → {b['te']['p_up'] * 100:.0f}% · {b['te']['mean'] * 100:+.2f}%(한 달 {b['per_month_te']:.0f}건, 시장 대비 {b['te']['excess'] * 100:+.2f}%p). 더한 것: "
                   + (", ".join(rep["improve"]["used"]) if rep["improve"]["used"] else "없음") + ".")
        if b.get("tgt10_te"):
            out.append(f"최종 규칙에서 목표 +10% 닿으면 파는 청산을 쓰면 상승 {b['tgt10_te']['p_up'] * 100:.0f}%·평균 {b['tgt10_te']['mean'] * 100:+.2f}%, 3분할이면 상승 {b['split3_te']['p_up'] * 100:.0f}%·평균 {b['split3_te']['mean'] * 100:+.2f}%.")
    w = rep["walk"]["total"]
    if w.get("base") and w.get("final"):
        out.append(f"해마다 AI 를 다시 학습한 걸어가며 검증(2012~): 시장 필터 규칙 상승 {w['base']['p_up'] * 100:.0f}%·평균 {w['base']['mean'] * 100:+.2f}% → 보강 규칙 {w['final']['p_up'] * 100:.0f}%·{w['final']['mean'] * 100:+.2f}%({rep['walk']['years_better']}/{rep['walk']['years']}년에서 더 높음).")
    td = rep["today"]
    if td.get("date"):
        out.append(f"{td['date']} 기준 거래 가능 {td['n']:,}종목 중 거래정지 위험 높음 {td['n_risk_high']}·중간 {td['n_risk_mid']}종목. 시장 52주 고점 대비 {td['market_under']['dd250'] * 100:+.1f}% → 시장 필터 {'켜짐(진입 가능)' if td['market_under']['under'] else '꺼짐(규칙상 쉬는 구간)'}, AI 상위 후보 {td['n_candidates']}종목.")
    return out


if __name__ == "__main__":
    run()
