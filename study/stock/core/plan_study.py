"""매매 계획 — 모든 기법(신호)마다 ① 최적 매수 시점 ② 미래를 모른다는 가정(걸어가며 검증) ③ 매도 시점·손절 위치.

규칙은 해마다 '그 해 전까지의 데이터'로만 고르고 그 해에 적용한다(미래를 모르는 상황 재현). 고르는 순서:
  (1) 추가 조건(없음/갭 +3% 이하/피함 제외/시장 52주 고점 −10% 아래/당일 음봉/5일 조정 뒤/AI 상위 10%(그 해 전까지 학습)/조합) → 신호당 기대(체결 비율 × 평균)가 가장 큰 것
  (2) 진입 방법(다음날 시가/5일 뒤/10일 뒤/3분할/확인 후/눌림 지정가) → 신호당 기대
  (3) 청산·손절(20일 종가/목표 +10·+20/트레일링 10%/20일선 이탈/목표 +20·손절 −10/손절 −7·−10·−15/2×ATR 손절/10·40일) → 평균 수익이 가장 큰 것(상승 확률 같이 보고)
그 뒤 처음 보는 해들(2012~)의 성과를 모아 기법별로 비교한다. 손절 위치는 20일 안 최대 하락(MAE) 분포로 '이긴 거래가 어디까지 흔들렸나'를 본다.
표본은 strategy_study 캐시(연중일 5일 간격, 거래 가능 종목) 중 '그날 신호가 난 행'. 비용 0.3%.
"""
import json
import math
import time
from datetime import datetime

import numpy as np
import pandas as pd

import config
from core import ai, avoid_study, combined_study as cs, db, strategy_study as st

PLAN_PATH = config.DATA_DIR / "plan.json"
COST = ai.COST
WF_START = 2012
ENTRIES = [("다음날 시가 전액", "r20"), ("5일 뒤 시가", "x5"), ("10일 뒤 시가", "x10"), ("3분할 (0·5·10일)", "split3"), ("확인 후 (5일 안 +2%)", "conf_ret"), ("눌림 지정가 (시가 −3%, 5일)", "pb_ret")]
EXITS = [("20일 뒤 종가", "r20"), ("10일 뒤 종가", "r10"), ("40일 뒤 종가", "r40"), ("목표 +10%", "tgt10"), ("목표 +20%", "tgt20"), ("트레일링 10%", "trail10"), ("20일선 이탈", "ma20x"),
         ("목표 +20% / 손절 −10%", "ts20_10"), ("손절 −7%", "stop7"), ("손절 −10%", "stop10"), ("손절 −15%", "stop15"), ("2×ATR 손절", "stop_ret")]
MIN_N = 300


def _stats(v, n_all=None):
    v = np.asarray(v, float)
    ok = ~np.isnan(v)
    r = v[ok]
    if len(r) < 30:
        return None
    s = {"n": int(len(r)), "mean": float(r.mean()), "p_up": float((r > 0).mean()), "p_big_up": float((r >= 0.10).mean()), "p_dn10": float((r <= -0.10).mean()), "q05": float(np.quantile(r, 0.05)), "median": float(np.median(r))}
    s["coverage"] = float(len(r) / n_all) if n_all else 1.0
    s["per_signal"] = s["coverage"] * s["mean"]
    return s


def _conditions(D, T, avoid, ai_top):
    gap_ok = T["gap1"] <= cs.GAP_MAX
    mk = (D["mkt_dd250"] < -0.10).to_numpy()
    down = (D["ret1"] <= 0).to_numpy()
    pull = (D["ret5"] < 0).to_numpy()
    keep = ~avoid
    return [("조건 없음", np.ones(len(avoid), bool)), ("갭 +3% 이하", gap_ok), ("피함 제외", keep), ("시장 52주 고점 −10% 아래", mk), ("당일 음봉", down), ("5일 조정 뒤", pull), ("AI 상위 10%", ai_top),
            ("피함 제외 + 갭", keep & gap_ok), ("피함 제외 + 갭 + 시장 필터", keep & gap_ok & mk), ("피함 제외 + 갭 + AI 상위 10%", keep & gap_ok & ai_top), ("피함 제외 + 갭 + 시장 필터 + AI 상위 10%", keep & gap_ok & mk & ai_top)]


def _pick(T, m, conds, past):
    """과거(past)에서만 규칙 고르기. 반환: (조건 이름·마스크, 진입 이름·키, 청산 이름·키, 과거 성과)"""
    base_n = int((m & past).sum())
    if base_n < MIN_N:
        return None
    best_c, best_v = None, -9
    for name, cm in conds:
        mm = m & past & cm
        if mm.sum() < MIN_N:
            continue
        s = _stats(T["r20"][mm] - COST, mm.sum())
        if s and s["per_signal"] > best_v:
            best_c, best_v = (name, cm), s["per_signal"]
    if best_c is None:
        return None
    mm = m & past & best_c[1]
    best_e, best_v = None, -9
    for name, k in ENTRIES:
        s = _stats(T[k][mm] - COST, mm.sum())
        if s and s["per_signal"] > best_v:
            best_e, best_v = (name, k), s["per_signal"]
    best_x, best_v = None, -9
    xs = {}
    for name, k in EXITS:
        s = _stats(T[k][mm] - COST, mm.sum())
        if s:
            xs[name] = s
            if s["mean"] > best_v:
                best_x, best_v = (name, k), s["mean"]
    return {"cond": best_c, "entry": best_e, "exit": best_x, "past": {"n": int(mm.sum()), "entry": _stats(T[best_e[1]][mm] - COST, mm.sum()), "exit": xs.get(best_x[0])}}


def _mae_table(T, m):
    """20일 안 최대 하락(MAE)별: 그 깊이까지 떨어진 거래 중 결국 20일 뒤 플러스였던 비율(손절했으면 놓쳤을 비율)과 전체 비율."""
    mae = T["mae0"][m].astype(float)
    r = T["r20"][m].astype(float) - COST
    ok = ~np.isnan(mae) & ~np.isnan(r)
    mae, r = mae[ok], r[ok]
    rows = []
    for x in (0.03, 0.05, 0.07, 0.10, 0.15, 0.20):
        hit = mae <= -x
        if hit.sum() < 30:
            continue
        rows.append({"level": x, "share": float(hit.mean()), "win_after": float((r[hit] > 0).mean()), "mean_after": float(r[hit].mean()), "win_share": float(hit[r > 0].mean()), "lose_share": float(hit[r <= 0].mean())})
    return {"rows": rows, "mae_win_med": float(np.median(mae[r > 0])) if (r > 0).any() else None, "mae_lose_med": float(np.median(mae[r <= 0])) if (r <= 0).any() else None, "n": int(ok.sum())}


def _days_table(T, m):
    out = {}
    for lab, k, dk in (("목표 +10%", "tgt10", "tgt10_d"), ("목표 +20%", "tgt20", "tgt20_d"), ("트레일링 10%", "trail10", "trail10_d"), ("20일선 이탈", "ma20x", "ma20x_d")):
        d = T[dk][m].astype(float)
        v = T[k][m].astype(float)
        ok = ~np.isnan(d)
        if ok.sum() < 30:
            continue
        cap = {"tgt10_d": 20, "tgt20_d": 20, "trail10_d": 60, "ma20x_d": 60}[dk]
        hit = d[ok] < cap
        out[lab] = {"hit": float(hit.mean()), "days_med": float(np.median(d[ok][hit])) if hit.any() else None, "mean": float(np.nanmean(v[ok]) - COST), "p_up": float(np.nanmean(v[ok] - COST > 0))}
    return out


def run(log=print, limit: int = 0) -> dict:
    t0 = time.time()
    z = np.load(st.ROWS_PATH, allow_pickle=True)
    X, date, code = z["X"], z["date"], z["code"]
    T = {k: z["T_" + k].astype(float) for k in st.ROW_KEYS}
    latest = list(z["latest"])
    with db.get_conn() as c:
        names = {r["code"]: r["name"] for r in c.execute("SELECT code,name FROM symbols")}
    D = pd.DataFrame(X, columns=ai.NAMES)
    for k in ("zero_vol60", "gapdn20", "down_streak", "close_px", "gap1"):
        D[k] = T[k]
    year = pd.Series(date).str[:4].astype(int).to_numpy()
    ok = ~np.isnan(T["r20"])
    rules = {n: f for n, f in avoid_study.RULES}
    avoid = np.zeros(len(date), bool)
    for n in cs.AVOID_NAMES:
        avoid |= rules[n](D).to_numpy()
    # AI 상위 10%: 해마다 그 해 전까지로 학습(미래 정보 없음)
    y20 = (T["r20"] >= ai.TARGET)
    ai_top = np.zeros(len(date), bool)
    years = sorted(y for y in set(year) if y >= WF_START)
    for Y in years:
        past, cur = (year < Y) & ok, (year == Y) & ok
        if past.sum() < 50000 or cur.sum() < 500:
            continue
        idx = st._sub(past, st.AI_SUB, seed=Y)
        mdl = st._fit_fast(X[idx], y20[idx].astype(int), seed=Y)
        p = st._predict(mdl, X[cur])
        ai_top[np.flatnonzero(cur)[p >= np.nanquantile(p, 0.9)]] = True
        log(f"[AI {Y}] · {time.time() - t0:.0f}초")
    conds = _conditions(D, T, avoid, ai_top)
    rep = {"meta": {"generated": datetime.now().isoformat(timespec="seconds"), "rows": int(ok.sum()), "wf_start": WF_START, "cost": COST, "min_n": MIN_N}}
    techs = []
    for j, lab in enumerate(ai.SIGNAL_LABELS):
        m = ok & (D[f"sig{j:02d}"].to_numpy() == 0)
        if m.sum() < 2000:
            continue
        oos_entry = np.full(len(date), np.nan)
        oos_exit = np.full(len(date), np.nan)
        oos_naive = np.full(len(date), np.nan)
        oos_mask = np.zeros(len(date), bool)
        hist = []
        for Y in years:
            past, cur = year < Y, year == Y
            pk = _pick(T, m, conds, past)
            if pk is None:
                continue
            mm = m & cur & pk["cond"][1]
            oos_entry[mm] = T[pk["entry"][1]][mm] - COST
            oos_exit[mm] = T[pk["exit"][1]][mm] - COST
            oos_naive[m & cur] = T["r20"][m & cur] - COST
            oos_mask |= mm
            hist.append({"year": int(Y), "cond": pk["cond"][0], "entry": pk["entry"][0], "exit": pk["exit"][0], "n": int(mm.sum()),
                         "entry_stat": _stats(oos_entry[mm], mm.sum()), "exit_stat": _stats(oos_exit[mm], mm.sum()), "naive": _stats(T["r20"][m & cur] - COST)})
        if not hist:
            continue
        last = hist[-1]
        freq = lambda key: pd.Series([h[key] for h in hist]).value_counts().to_dict()  # noqa: E731
        all_rows = m & (year >= WF_START)
        techs.append({"label": lab, "dante": j < ai.N_DANTE, "n": int(m.sum()), "years": len(hist),
                      "oos": {"entry": _stats(oos_entry[oos_mask], oos_mask.sum()), "exit": _stats(oos_exit[oos_mask], oos_mask.sum()), "naive": _stats(oos_naive[all_rows]),
                              "share": float(oos_mask.sum() / max(all_rows.sum(), 1)), "years_pos": int(sum(1 for h in hist if h["exit_stat"] and h["exit_stat"]["mean"] > 0))},
                      "rule_now": {"cond": last["cond"], "entry": last["entry"], "exit": last["exit"]}, "freq": {"cond": freq("cond"), "entry": freq("entry"), "exit": freq("exit")},
                      "hist": hist, "mae": _mae_table(T, m & (year >= WF_START)), "days": _days_table(T, m & (year >= WF_START))})
        log(f"[{lab}] 처음 보는 해 {len(hist)}년 · 규칙대로 {techs[-1]['oos']['exit']['mean'] * 100 if techs[-1]['oos']['exit'] else float('nan'):+.2f}%·{techs[-1]['oos']['exit']['p_up'] * 100 if techs[-1]['oos']['exit'] else float('nan'):.0f}% (단순 {techs[-1]['oos']['naive']['mean'] * 100:+.2f}%) · {time.time() - t0:.0f}초")
    # AI 상위 10% 자체도 하나의 '기법'으로
    m = ok & ai_top
    oos_e, oos_x, oos_mask, hist = np.full(len(date), np.nan), np.full(len(date), np.nan), np.zeros(len(date), bool), []
    conds_ai = [c_ for c_ in conds if "AI" not in c_[0]]
    for Y in years:
        past, cur = year < Y, year == Y
        pk = _pick(T, m, conds_ai, past)
        if pk is None:
            continue
        mm = m & cur & pk["cond"][1]
        oos_e[mm] = T[pk["entry"][1]][mm] - COST
        oos_x[mm] = T[pk["exit"][1]][mm] - COST
        oos_mask |= mm
        hist.append({"year": int(Y), "cond": pk["cond"][0], "entry": pk["entry"][0], "exit": pk["exit"][0], "n": int(mm.sum()), "entry_stat": _stats(oos_e[mm], mm.sum()), "exit_stat": _stats(oos_x[mm], mm.sum()), "naive": _stats(T["r20"][m & cur] - COST)})
    if hist:
        last = hist[-1]
        all_rows = m & (year >= WF_START)
        techs.append({"label": "AI 상위 10% (해마다 재학습)", "dante": False, "ai": True, "n": int(m.sum()), "years": len(hist),
                      "oos": {"entry": _stats(oos_e[oos_mask], oos_mask.sum()), "exit": _stats(oos_x[oos_mask], oos_mask.sum()), "naive": _stats(T["r20"][all_rows] - COST), "share": float(oos_mask.sum() / max(all_rows.sum(), 1)),
                              "years_pos": int(sum(1 for h in hist if h["exit_stat"] and h["exit_stat"]["mean"] > 0))},
                      "rule_now": {"cond": last["cond"], "entry": last["entry"], "exit": last["exit"]}, "freq": {}, "hist": hist, "mae": _mae_table(T, all_rows), "days": _days_table(T, all_rows)})
    techs.sort(key=lambda t: -(t["oos"]["exit"]["mean"] if t["oos"]["exit"] else -9))
    rep["techs"] = techs
    rep["base"] = {"naive": _stats(T["r20"][ok & (year >= WF_START)] - COST), "mae": _mae_table(T, ok & (year >= WF_START))}
    # 오늘: 신호 난 종목에 그 기법의 현재 규칙(마지막 해 전까지로 고른 것) 붙이기 — AI 는 전체 기간 모델(~2021 학습본) 점수
    rep["today"] = _today(latest, names, techs, rules)
    rep["text"] = narrate(rep)
    rep["meta"]["elapsed_sec"] = round(time.time() - t0)
    PLAN_PATH.write_text(json.dumps(rep, ensure_ascii=False, default=st._js), encoding="utf-8")
    return rep


def _today(latest, names, techs, rules, top=60):
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
    model = cs._load_model()
    P = model.predict_proba(XL)[:, 1] if model is not None else np.full(len(L), np.nan)
    thr = np.nanquantile(P, 0.9) if np.isfinite(P).any() else np.nan
    try:
        import pandas as _pd
        mk_dd = float(_pd.read_pickle(ai.bt.MKT_PATH)["mkt_dd250"].dropna().iloc[-1])
    except Exception:
        mk_dd = float(np.nanmedian(D["mkt_dd250"]))
    tinfo = {t["label"]: t for t in techs if not t.get("ai")}
    good = {lab for lab, t in tinfo.items() if t["oos"]["exit"] and t["oos"]["exit"]["mean"] > 0 and t["oos"]["years_pos"] >= t["years"] * 0.6}
    date = max(r["date"] for r in L)
    rows = []
    for i, r in enumerate(L):
        if r["date"] != date:
            continue
        sigs = [ai.SIGNAL_LABELS[j] for j in range(len(ai.SIGNAL_LABELS)) if r["X"][ai.NAMES.index(f"sig{j:02d}")] == 0 and ai.SIGNAL_LABELS[j] in tinfo]
        ai_t = bool(np.isfinite(P[i]) and P[i] >= thr)
        if not sigs and not ai_t:
            continue
        best = max(sigs, key=lambda s_: tinfo[s_]["oos"]["exit"]["mean"] if tinfo[s_]["oos"]["exit"] else -9, default=None)
        t = tinfo.get(best) if best else next((x for x in techs if x.get("ai")), None)
        rule = t["rule_now"] if t else {"cond": "–", "entry": "–", "exit": "–"}
        cond = rule["cond"]
        met = (("피함" not in cond) or not avoid[i]) and (("시장" not in cond) or mk_dd < -0.10) and (("AI" not in cond) or ai_t) and (("음봉" not in cond) or r["X"][ai.NAMES.index("ret1")] <= 0) and (("조정" not in cond) or r["X"][ai.NAMES.index("ret5")] < 0)
        rows.append({"code": r["code"], "name": names.get(r["code"], r["code"]), "close": r["close"], "signals": sigs, "ai_top": ai_t, "best": best or ("AI 상위 10%" if ai_t else None), "good": bool(best in good) if best else ai_t,
                     "cond": cond, "entry": rule["entry"], "exit": rule["exit"], "cond_met": bool(met), "avoid": bool(avoid[i]), "prob": None if np.isnan(P[i]) else float(P[i]),
                     "oos_mean": t["oos"]["exit"]["mean"] if t and t["oos"]["exit"] else None, "oos_up": t["oos"]["exit"]["p_up"] if t and t["oos"]["exit"] else None})
    rows.sort(key=lambda z: (z["avoid"], not z["cond_met"], not z["good"], -(z["oos_mean"] or -9)))
    return {"date": date, "n": len(rows), "mkt_dd250": mk_dd, "rows": rows[:top], "ai_thr": None if np.isnan(thr) else float(thr)}   # ai_thr: 차트 비교 'AI' 겹치기의 상위 10% 경계


def narrate(rep):
    out = []
    b = rep["base"]["naive"]
    out.append(f"기준(거래 가능 종목 아무 날, {rep['meta']['wf_start']}~): 다음날 시가 매수·20일 보유 평균 {b['mean'] * 100:+.2f}%·상승 {b['p_up'] * 100:.0f}%. 모든 규칙은 그 해 전까지의 데이터로만 고르고 그 해에 적용했습니다(미래를 모르는 가정).")
    good = [t for t in rep["techs"] if t["oos"]["exit"] and t["oos"]["exit"]["mean"] > 0 and t["oos"]["years_pos"] >= t["years"] * 0.6]
    out.append("규칙대로 했을 때 처음 보는 해에서 평균 플러스·60% 이상의 해에서 플러스였던 기법: " + (", ".join(f"{t['label']} {t['oos']['exit']['mean'] * 100:+.2f}%·상승 {t['oos']['exit']['p_up'] * 100:.0f}%({t['oos']['years_pos']}/{t['years']}년)" for t in good) if good else "없음") + ".")
    bad = [t for t in rep["techs"] if t not in good and not t.get("ai")]
    out.append("규칙을 붙여도 처음 보는 해에서 시장을 못 이긴 기법: " + ", ".join(t["label"] for t in bad[:12]) + ("…" if len(bad) > 12 else "") + ".")
    cnt_c, cnt_e, cnt_x = {}, {}, {}
    for t in rep["techs"]:
        for h in t["hist"]:
            cnt_c[h["cond"]] = cnt_c.get(h["cond"], 0) + 1
            cnt_e[h["entry"]] = cnt_e.get(h["entry"], 0) + 1
            cnt_x[h["exit"]] = cnt_x.get(h["exit"], 0) + 1
    top = lambda d, k=3: ", ".join(f"{a} {n}회" for a, n in sorted(d.items(), key=lambda x: -x[1])[:k])  # noqa: E731
    out.append(f"해마다 과거 데이터가 고른 규칙(기법×연도 합계): 조건 — {top(cnt_c)} / 진입 — {top(cnt_e)} / 청산·손절 — {top(cnt_x)}.")
    m = rep["base"]["mae"]
    r10 = next((x for x in m["rows"] if abs(x["level"] - 0.10) < 1e-9), None)
    r5 = next((x for x in m["rows"] if abs(x["level"] - 0.05) < 1e-9), None)
    if r10 and r5:
        out.append(f"손절 위치(아무 날 기준): 20일 안 −5%를 맞은 거래는 {r5['share'] * 100:.0f}%이고 그 중 {r5['win_after'] * 100:.0f}%는 결국 플러스로 끝남(−5% 손절이면 이긴 거래의 {r5['win_share'] * 100:.0f}%를 자름). −10%는 {r10['share'] * 100:.0f}%·그 뒤 플러스 {r10['win_after'] * 100:.0f}%(이긴 거래의 {r10['win_share'] * 100:.0f}% 자름). 이긴 거래의 최대 하락 중앙 {m['mae_win_med'] * 100:+.1f}%, 진 거래 {m['mae_lose_med'] * 100:+.1f}%.")
    td = rep["today"]
    if td.get("date"):
        out.append(f"{td['date']} 신호가 난 종목 {td['n']}개에 그 기법의 현재 규칙(조건·진입·청산)을 붙였습니다.")
    return out


if __name__ == "__main__":
    run()
