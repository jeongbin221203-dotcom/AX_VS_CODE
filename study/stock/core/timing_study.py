"""여러 기법을 적용해 '언제 사는 게 맞았나' — 기법(신호)마다 진입 시점·방법별 성과를 비교하고, 오늘 신호가 난 종목에 가장 나았던 매수 시점을 붙인다.

표본은 strategy_study 캐시(연중일 5일 간격, 거래 가능 종목)의 행 중 '그날 신호가 난' 행. 20거래일 보유(비용 0.3%).
진입 방법: 다음날 시가 전액 / 5일·10일 뒤 시가 / 3분할(0·5·10일) / 확인 후(5일 안 신호일 종가 +2%) / 눌림 지정가(시가 −3%, 5일) / 전액+2×ATR 손절
청산 방법(다음날 시가 진입): 목표 +10% · +20% / 트레일링 10% / 20일선 이탈 / 목표 +20%·손절 −10%
추가 조건: 갭 +3% 이하 / 시장 52주 고점 −10% 아래 / AI 상위 10% / 피함 제외
학습(~2021)·시험(2022~)을 따로 보고, 두 시기 모두 좋은 것만 '권장'으로 표시한다.
"""
import json
import math
import time
from datetime import datetime

import numpy as np
import pandas as pd

import config
from core import ai, avoid_study, combined_study as cs, db, strategy_study as st

TIMING_PATH = config.DATA_DIR / "timing.json"
COST = ai.COST
ENTRIES = [("다음날 시가 전액", "r20"), ("5일 뒤 시가", "x5"), ("10일 뒤 시가", "x10"), ("3분할 (0·5·10일)", "split3"), ("확인 후 (5일 안 +2% 넘으면)", "conf_ret"),
           ("눌림 지정가 (시가 −3%, 5일 대기)", "pb_ret"), ("전액 + 2×ATR 손절", "stop_ret")]
EXITS = [("목표 +10% 닿으면 매도", "tgt10"), ("목표 +20% 닿으면 매도", "tgt20"), ("트레일링 10%", "trail10"), ("20일선 이탈 매도", "ma20x"), ("목표 +20% / 손절 −10%", "ts20_10")]


def _stats(r, n_all):
    r = np.asarray(r, float)
    ok = ~np.isnan(r)
    rr = r[ok]
    if len(rr) < 30:
        return None
    cov = len(rr) / max(n_all, 1)
    return {"n": int(len(rr)), "coverage": float(cov), "mean": float(rr.mean()), "per_signal": float(cov * rr.mean()), "p_up": float((rr > 0).mean()), "p_big_up": float((rr >= 0.10).mean()),
            "p_dn10": float((rr <= -0.10).mean()), "q05": float(np.quantile(rr, 0.05)), "std": float(rr.std())}


def _method_rows(T, m, tr, te, ex):
    rows = []
    n_tr, n_te = int((m & tr).sum()), int((m & te).sum())
    for name, k in ENTRIES + EXITS:
        v = T[k].astype(float) - COST
        s_tr, s_te = _stats(v[m & tr], n_tr), _stats(v[m & te], n_te)
        if not (s_tr and s_te):
            continue
        s_te["excess"] = float(np.nanmean(ex[m & te][~np.isnan(v[m & te])])) if k == "r20" else None
        rows.append({"name": name, "key": k, "kind": "진입" if any(k == kk for _, kk in ENTRIES) else "청산", "tr": s_tr, "te": s_te})
    base = next((r for r in rows if r["key"] == "r20"), None)
    for r in rows:
        b = base
        r["better"] = bool(b and r["key"] != "r20" and r["tr"]["p_up"] > b["tr"]["p_up"] and r["te"]["p_up"] > b["te"]["p_up"] and r["tr"]["per_signal"] >= b["tr"]["per_signal"] and r["te"]["per_signal"] >= b["te"]["per_signal"])
    return rows


def _cond_rows(T, D, m, tr, te, ex, gap_ok, mk_under, ai_top, keep):
    specs = [("조건 없음", np.ones(len(m), bool)), ("갭 +3% 이하만", gap_ok), ("피함 제외", keep), ("시장 52주 고점 −10% 아래일 때만", mk_under), ("AI 상위 10%와 겹칠 때만", ai_top),
             ("피함 제외 + 갭 + 시장 필터", keep & gap_ok & mk_under), ("피함 제외 + 갭 + 시장 필터 + AI 상위 10%", keep & gap_ok & mk_under & ai_top)]
    r = T["r20"].astype(float) - COST
    out = []
    base_tr, base_te = _stats(r[m & tr], 1), _stats(r[m & te], 1)
    for name, c in specs:
        mm = m & c
        s_tr, s_te = _stats(r[mm & tr], 1), _stats(r[mm & te], 1)
        if not (s_tr and s_te):
            continue
        s_te["excess"] = float(np.nanmean(ex[mm & te]))
        s_tr["excess"] = float(np.nanmean(ex[mm & tr]))
        out.append({"name": name, "n_te": s_te["n"], "share_te": float(s_te["n"] / max(base_te["n"], 1)) if base_te else None, "tr": s_tr, "te": s_te,
                    "better": bool(base_tr and base_te and name != "조건 없음" and s_tr["p_up"] > base_tr["p_up"] + 0.01 and s_te["p_up"] > base_te["p_up"] + 0.01 and s_tr["mean"] > base_tr["mean"] and s_te["mean"] > base_te["mean"])})
    return out


def run(log=print, limit: int = 0) -> dict:
    t0 = time.time()
    if not st.ROWS_PATH.exists():
        raise SystemExit("먼저 python manage.py strategy-train 으로 표본 캐시를 만드세요.")
    z = np.load(st.ROWS_PATH, allow_pickle=True)
    X, date, code = z["X"], z["date"], z["code"]
    T = {k: z["T_" + k] for k in st.ROW_KEYS}
    latest = list(z["latest"])
    with db.get_conn() as c:
        names = {r["code"]: r["name"] for r in c.execute("SELECT code,name FROM symbols")}
    D = pd.DataFrame(X, columns=ai.NAMES)
    for k in ("zero_vol60", "gapdn20", "down_streak", "close_px", "gap1"):
        D[k] = T[k]
    tr_, va_, te = ai._split_masks(date)
    tr = tr_ | va_
    r20 = T["r20"].astype(float)
    ok = ~np.isnan(r20)
    ex = (pd.Series(r20) - pd.Series(r20).groupby(pd.Series(date)).transform("mean")).to_numpy()
    rules = {n: f for n, f in avoid_study.RULES}
    avoid = np.zeros(len(date), bool)
    for n in cs.AVOID_NAMES:
        avoid |= rules[n](D).to_numpy()
    keep = ok & ~avoid
    gap_ok = T["gap1"] <= cs.GAP_MAX
    mk_under = (D["mkt_dd250"] < -0.10).to_numpy()
    model = cs._load_model()
    P = st._predict(model, X) if model is not None else np.full(len(date), np.nan)
    ai_top = P >= np.nanquantile(P[te], 0.9) if model is not None else np.zeros(len(date), bool)
    log(f"표본 {len(date):,}행 · {time.time() - t0:.0f}초")
    rep = {"meta": {"generated": datetime.now().isoformat(timespec="seconds"), "rows": int(len(date)), "split": {"train_end": ai.VALID_END}, "cost": COST, "ai": model is not None, "gap_max": cs.GAP_MAX}}
    techs = []
    sig_masks = {}
    for j, lab in enumerate(ai.SIGNAL_LABELS):
        m = ok & (D[f"sig{j:02d}"].to_numpy() == 0)                 # 그날 신호
        if (m & tr).sum() < 300 or (m & te).sum() < 200:
            continue
        sig_masks[lab] = m
        methods = _method_rows(T, m, tr, te, ex)
        conds = _cond_rows(T, D, m, tr, te, ex, gap_ok, mk_under, ai_top, keep)
        base = next((x for x in methods if x["key"] == "r20"), None)
        best_entry = max([x for x in methods if x["kind"] == "진입" and x["better"]], key=lambda x: x["te"]["p_up"], default=None)
        best_exit = max([x for x in methods if x["kind"] == "청산" and x["better"]], key=lambda x: x["te"]["p_up"], default=None)
        best_cond = max([x for x in conds if x["better"]], key=lambda x: x["te"]["p_up"], default=None)
        techs.append({"label": lab, "dante": j < ai.N_DANTE, "n_tr": int((m & tr).sum()), "n_te": int((m & te).sum()), "share": float(m.mean()), "base": base, "methods": methods, "conds": conds,
                      "best_entry": best_entry["name"] if best_entry else "다음날 시가 전액", "best_exit": best_exit["name"] if best_exit else "20일 뒤 종가",
                      "best_cond": best_cond["name"] if best_cond else "조건 없음",
                      "best_p_up": (best_cond or best_entry or base)["te"]["p_up"] if (best_cond or best_entry or base) else None,
                      "robust": bool(base and base["tr"]["mean"] > 0 and base["te"]["mean"] > 0 and base["te"].get("excess", 0) > 0)})
    if model is not None:
        m = ok & ai_top
        methods = _method_rows(T, m, tr, te, ex)
        conds = _cond_rows(T, D, m, tr, te, ex, gap_ok, mk_under, np.ones(len(m), bool), keep)
        base = next((x for x in methods if x["key"] == "r20"), None)
        best_entry = max([x for x in methods if x["kind"] == "진입" and x["better"]], key=lambda x: x["te"]["p_up"], default=None)
        best_exit = max([x for x in methods if x["kind"] == "청산" and x["better"]], key=lambda x: x["te"]["p_up"], default=None)
        best_cond = max([x for x in conds if x["better"]], key=lambda x: x["te"]["p_up"], default=None)
        techs.append({"label": "AI 상위 10% (기법 아님, 비교)", "dante": False, "n_tr": int((m & tr).sum()), "n_te": int((m & te).sum()), "share": float(m.mean()), "base": base, "methods": methods, "conds": conds,
                      "best_entry": best_entry["name"] if best_entry else "다음날 시가 전액", "best_exit": best_exit["name"] if best_exit else "20일 뒤 종가", "best_cond": best_cond["name"] if best_cond else "조건 없음",
                      "best_p_up": (best_cond or best_entry or base)["te"]["p_up"], "robust": True, "ai": True})
    techs.sort(key=lambda x: -(x["base"]["te"]["excess"] if x["base"] and x["base"]["te"].get("excess") is not None else -1))
    rep["techs"] = techs
    rep["base_all"] = {"tr": _stats(r20[ok & tr] - COST, 1), "te": _stats(r20[ok & te] - COST, 1)}
    log(f"기법 {len(techs)}개 · {time.time() - t0:.0f}초")
    # 오늘: 최신 날 신호가 난 종목 + 권장 매수 시점
    rep["today"] = _today(latest, names, techs, model, rules)
    rep["text"] = narrate(rep)
    rep["meta"]["elapsed_sec"] = round(time.time() - t0)
    TIMING_PATH.write_text(json.dumps(rep, ensure_ascii=False, default=st._js), encoding="utf-8")
    return rep


def _today(latest, names, techs, model, rules, top=60):
    L = [r for r in latest if r["ok"] and np.isfinite(r["X"][ai.NAMES.index("ret60")]) and r["X"][ai.NAMES.index("liq")] >= math.log10(ai.TRADABLE_VALUE)]
    if not L:
        return {"date": None, "rows": []}
    XL = np.vstack([r["X"] for r in L])
    D = pd.DataFrame(XL, columns=ai.NAMES)
    for k in ("zero_vol60", "gapdn20", "down_streak", "close_px"):
        D[k] = [r[k] for r in L]
    D["gap1"] = np.nan
    avoid = np.zeros(len(L), bool)
    for n in cs.AVOID_NAMES:
        avoid |= rules[n](D).fillna(False).to_numpy()
    P = model.predict_proba(XL)[:, 1] if model is not None else np.full(len(L), np.nan)
    thr = np.nanquantile(P, 0.9) if np.isfinite(P).any() else np.nan
    halt_p = np.full(len(L), np.nan)
    try:
        import joblib
        from core import risk_study
        hm = joblib.load(risk_study.HALT_MODEL_PATH)
        halt_p = hm.predict_proba(risk_study._feat_matrix(D))[:, 1]
        ratio = json.loads(risk_study.RISK_PATH.read_text(encoding="utf-8"))["model"]["ratio"]      # 학습 때 음성 하위 추출 비율 보정
        halt_p = halt_p / (halt_p + (1 - halt_p) * ratio)
    except Exception:
        pass
    tinfo = {t["label"]: t for t in techs if not t.get("ai")}
    date = max(r["date"] for r in L)
    try:
        mk_dd = float(pd.read_pickle(ai.bt.MKT_PATH)["mkt_dd250"].dropna().iloc[-1])
    except Exception:
        mk_dd = float(np.nanmedian(D["mkt_dd250"]))
    rows = []
    for i, r in enumerate(L):
        if r["date"] != date:
            continue
        sigs = [ai.SIGNAL_LABELS[j] for j in range(len(ai.SIGNAL_LABELS)) if r["X"][ai.NAMES.index(f"sig{j:02d}")] == 0 and ai.SIGNAL_LABELS[j] in tinfo]
        if not sigs and not (np.isfinite(P[i]) and P[i] >= thr):
            continue
        best = max(sigs, key=lambda s_: tinfo[s_]["best_p_up"] or 0, default=None)
        t = tinfo.get(best) if best else None
        cond = t["best_cond"] if t else "조건 없음"
        cond_met = (("피함" not in cond) or not avoid[i]) and (("시장 필터" not in cond) or mk_dd < -0.10) and (("AI" not in cond) or (np.isfinite(P[i]) and P[i] >= thr))
        rows.append({"code": r["code"], "name": names.get(r["code"], r["code"]), "close": r["close"], "signals": sigs, "best_signal": best, "cond_met": bool(cond_met), "entry": t["best_entry"] if t else "다음날 시가 전액",
                     "exit": t["best_exit"] if t else "20일 뒤 종가", "cond": t["best_cond"] if t else "조건 없음", "p_up": t["best_p_up"] if t else None, "robust": bool(t and t["robust"]),
                     "prob": None if np.isnan(P[i]) else float(P[i]), "ai_top": bool(np.isfinite(P[i]) and P[i] >= thr), "halt_p": None if np.isnan(halt_p[i]) else float(halt_p[i]), "avoid": bool(avoid[i]),
                     "ret20": float(r["X"][ai.NAMES.index("ret20")]), "nh250": float(r["X"][ai.NAMES.index("nh250")]), "atrp": float(r["X"][ai.NAMES.index("atrp")])})
    rows.sort(key=lambda z: (z["avoid"], not z["cond_met"], not (z["robust"] or z["ai_top"]), -(z["p_up"] or 0), -(z["prob"] or 0)))
    return {"date": date, "rows": rows[:top], "n": len(rows), "mkt_dd250": mk_dd, "mkt_under": bool(mk_dd < -0.10)}


def narrate(rep):
    out = []
    b = rep["base_all"]["te"]
    out.append(f"기준(거래 가능 종목 아무 날, 시험 기간 2022~): 다음날 시가 전액 20일 평균 {b['mean'] * 100:+.2f}%, 상승 {b['p_up'] * 100:.0f}%.")
    good = [t for t in rep["techs"] if t["robust"] and not t.get("ai")]
    out.append("시험 기간에 시장보다 좋았던 기법(다음날 시가 전액 기준): " + (", ".join(f"{t['label']} {t['base']['te']['mean'] * 100:+.2f}%·상승 {t['base']['te']['p_up'] * 100:.0f}%" for t in good[:6]) if good else "없음") + ".")
    bad = [t for t in rep["techs"] if not t["robust"] and not t.get("ai")]
    out.append("시장보다 못했거나 한 시기만 좋았던 기법: " + ", ".join(t["label"] for t in bad[:10]) + ("…" if len(bad) > 10 else "") + ".")
    ent = {}
    for t in rep["techs"]:
        ent[t["best_entry"]] = ent.get(t["best_entry"], 0) + 1
    out.append("기법별로 두 시기 모두 '다음날 시가 전액'보다 상승 확률이 높았던 진입 방법: " + ", ".join(f"{k} {v}개 기법" for k, v in sorted(ent.items(), key=lambda x: -x[1])) + ".")
    cond = {}
    for t in rep["techs"]:
        cond[t["best_cond"]] = cond.get(t["best_cond"], 0) + 1
    out.append("추가 조건 중 가장 많이 권장된 것: " + ", ".join(f"{k} {v}개" for k, v in sorted(cond.items(), key=lambda x: -x[1])[:4]) + ".")
    top = sorted([t for t in rep["techs"] if t["best_p_up"]], key=lambda t: -t["best_p_up"])[:5]
    out.append("조건·진입을 맞췄을 때 상승 확률 상위: " + ", ".join(f"{t['label']} {t['best_p_up'] * 100:.0f}%({t['best_cond']} · {t['best_entry']})" for t in top) + ".")
    td = rep["today"]
    if td.get("date"):
        out.append(f"{td['date']} 신호가 난 종목 {td['n']}개(시장 52주 고점 대비 {td['mkt_dd250'] * 100:+.1f}% → 시장 필터 {'켜짐' if td['mkt_under'] else '꺼짐'}). 종목마다 가장 나았던 매수 시점·청산·조건을 붙였습니다.")
    return out


if __name__ == "__main__":
    run()
