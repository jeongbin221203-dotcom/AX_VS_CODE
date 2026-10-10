"""진입 시점 연구 — 오를지 내릴지 모를 때 어떻게 들어갈 것인가.

1) 방향은 얼마나 예측되나: 20거래일 뒤 상승(+) / 큰 상승(+10%↑) / 큰 하락(−10%↓)을 각각 학습해 시험 기간(2022~)에서 확인.
2) 시장 상황·종목 상태별로 상승 확률이 얼마나 다른가(과거 통계).
3) 진입 '방식'별 비교: 한 번에 전액 / 3분할(0·5·10일) / 5일 늦춤 / +2% 확인 후 진입 / −3% 눌림 지정가 / 2×ATR 손절 병행.
   → 방향을 맞힐 수 없을 때는 방식이 위험(최악의 경우)을 얼마나 줄이는지가 핵심이다.
4) 지금 상승 가능성이 상대적으로 높은 종목과 이유.
모든 정보는 신호일 종가까지의 값, 청산은 20거래일 뒤 종가(손절 병행만 예외). 비용 0.3%는 전략마다 한 번씩 뺀다.
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
from core import ai, db
from core import backtest as bt

ENTRY_PATH = config.DATA_DIR / "entry.json"
COST = ai.COST
BIG = 0.10


def _work(code):
    try:
        return ai.build_rows(code, with_signals=False)
    except Exception:
        return None


def _stats(r):
    r = np.asarray(r, float)
    r = r[~np.isnan(r)]
    if len(r) < 30:
        return None
    return {"n": int(len(r)), "mean": float(r.mean()), "median": float(np.median(r)), "std": float(r.std()),
            "p_up": float((r > 0).mean()), "p_big_up": float((r >= BIG).mean()), "p_big_loss": float((r <= -BIG).mean()),
            "q05": float(np.quantile(r, 0.05)), "q95": float(np.quantile(r, 0.95))}


def _deciles(r, p, extra=None, bins=10):
    q = pd.qcut(pd.Series(p).rank(method="first"), bins, labels=False)
    out = []
    for k in range(bins):
        m = (q == k).to_numpy()
        s = _stats(r[m])
        if s:
            s["bin"] = k + 1
            s["p_mean"] = float(p[m].mean())
            out.append(s)
    return out


def _methods(D: dict, m):
    """진입 방식별 순수익(비용 차감) 분포. D: 배열 사전, m: 대상 행 마스크."""
    base = D["r20"][m]
    methods = [
        ("한 번에 전액 (다음날 시가)", D["r20"][m] - COST, D["mae0"][m]),
        ("3분할 (다음날·5일 뒤·10일 뒤 1/3씩)", D["split3"][m] - COST, D["mae_split"][m]),
        ("5일 늦춰서 전액", D["x5"][m] - COST, None),
        ("10일 늦춰서 전액", D["x10"][m] - COST, None),
        ("확인 후 진입 (5일 안에 신호일 종가 +2% 넘으면 다음날 시가)", D["conf_ret"][m] - COST, None),
        ("눌림 지정가 (시가 −3%, 5일 대기)", D["pb_ret"][m] - COST, None),
        ("전액 + 2×ATR 손절", D["stop_ret"][m] - COST, None),
    ]
    rows = []
    n_all = int(m.sum())
    for name, r, mae in methods:
        s = _stats(r)
        if not s:
            continue
        s["name"] = name
        s["coverage"] = s["n"] / max(n_all, 1)
        s["per_signal"] = s["coverage"] * s["mean"]                     # 안 산 신호는 0으로(현금)
        s["mae"] = float(np.nanmean(mae)) if mae is not None and np.isfinite(mae).any() else None
        # 같은 행에서 '한 번에 전액'과 비교(체결된 행만)
        ok = ~np.isnan(r)
        s["vs_all_in"] = float(np.nanmean(r[ok] - (base[ok] - COST))) if ok.any() else None
        rows.append(s)
    return rows


def _market_study(mkt: pd.DataFrame):
    """시장(전 종목 equal-weight 지수) 자체: 낙폭·최근 수익률 구간별로 이후 20거래일 상승 확률. 겹치지 않게 20일 간격 표본."""
    ret = mkt["mkt_ret"].fillna(0.0)
    level = (1 + ret).cumprod()
    fwd = level.shift(-20) / level - 1
    d = pd.DataFrame({"fwd": fwd, "dd": mkt["mkt_dd250"], "r60": mkt["mkt_r60"], "r20": mkt["mkt_r20"], "vol": mkt["mkt_vol20"]}).dropna()
    d = d.iloc[::20]
    out = {"n": int(len(d)), "up": float((d["fwd"] > 0).mean()), "mean": float(d["fwd"].mean()), "bins": {}}
    specs = {
        "52주 고점 대비 낙폭": ("dd", [-1, -0.25, -0.15, -0.05, 0.01], ["−25% 이하", "−25~−15%", "−15~−5%", "−5% 이내"]),
        "직전 60일 수익률": ("r60", [-1, -0.10, 0.0, 0.10, 2], ["−10% 이하", "−10~0%", "0~+10%", "+10% 이상"]),
    }
    for title, (col, edges, labels) in specs.items():
        b = pd.cut(d[col], edges, labels=labels)
        rows = []
        for lab in labels:
            x = d.loc[b == lab, "fwd"]
            if len(x) >= 8:
                rows.append({"label": lab, "n": int(len(x)), "p_up": float((x > 0).mean()), "mean": float(x.mean()), "q05": float(x.quantile(0.05))})
        out["bins"][title] = rows
    last = mkt.dropna(subset=["mkt_dd250", "mkt_r60"]).iloc[-1]
    out["today"] = {"date": str(mkt.dropna(subset=["mkt_dd250", "mkt_r60"]).index[-1]), "dd250": float(last["mkt_dd250"]), "r60": float(last["mkt_r60"]),
                    "r20": float(last["mkt_r20"]) if not pd.isna(last["mkt_r20"]) else None}
    return out


CONDITIONS = [
    ("시장: 강한 상승 (시장 60일 +10%↑)", lambda d: d["mkt_r60"] >= 0.10),
    ("시장: 완만한 상승 (0~+10%)", lambda d: (d["mkt_r60"] >= 0) & (d["mkt_r60"] < 0.10)),
    ("시장: 약세 (−10~0%)", lambda d: (d["mkt_r60"] >= -0.10) & (d["mkt_r60"] < 0)),
    ("시장: 급락 뒤 (60일 −10% 이하)", lambda d: d["mkt_r60"] < -0.10),
    ("시장: 52주 고점에서 −15% 이상 하락", lambda d: d["mkt_dd250"] <= -0.15),
    ("종목: 정배열 + 60일선 위", lambda d: (d["aligned"] == 1) & (d["dist60"] > 0)),
    ("종목: 역배열", lambda d: d["reversed"] == 1),
    ("종목: 20일선 가까이 눌림 (20일선 위 0~3%)", lambda d: (d["dist20"] >= 0) & (d["dist20"] < 0.03)),
    ("종목: 20일선 아래 (−5% 이하로 이탈)", lambda d: d["dist20"] <= -0.05),
    ("종목: 52주 고점 −3% 이내", lambda d: d["nh250"] >= -0.03),
    ("종목: 52주 고점 대비 −50% 이하 (깊은 하락)", lambda d: d["nh250"] <= -0.50),
    ("종목: 하락 후 반등 시작 (저점 지킴 + 20일선 위)", lambda d: (d["dd120"] <= -0.25) & (d["low_age"] >= 5) & (d["low_age"] <= 40) & (d["rebound60"] >= 0.08) & (d["dist20"] > 0) & (d["hl10"] >= 0)),
    ("종목: 거래량 3배↑ 급증 + 양봉", lambda d: (d["volratio"] >= 3) & (d["body"] > 0)),
    ("종목: 거래량 마름 (10일 최소 0.4배↓)", lambda d: d["vdry10"] < 0.4),
    ("종목: 이미 급등 (20일 +30%↑)", lambda d: d["ret20"] >= 0.30),
    ("종목: 20일 −20% 이하 급락 뒤", lambda d: d["ret20"] <= -0.20),
    ("종목: 변동성 낮음 (ATR÷종가 3%↓)", lambda d: d["atrp"] <= 0.03),
    ("종목: 변동성 높음 (ATR÷종가 7%↑)", lambda d: d["atrp"] >= 0.07),
]


def _conditions(D: pd.DataFrame, excess: np.ndarray, test: np.ndarray):
    r = D["r20"].to_numpy(float) - COST
    ok = ~np.isnan(r)
    out = []
    base = _stats(r[ok])
    for name, fn in CONDITIONS:
        m = fn(D).to_numpy() & ok
        if m.sum() < 500:
            continue
        s = _stats(r[m])
        t = _stats(r[m & test])
        out.append({"name": name, "n": s["n"], "p_up": s["p_up"], "mean": s["mean"], "excess": float(np.nanmean(excess[m])), "p_big_up": s["p_big_up"],
                    "p_big_loss": s["p_big_loss"], "q05": s["q05"], "n_test": t["n"] if t else 0, "p_up_test": t["p_up"] if t else None,
                    "mean_test": t["mean"] if t else None})
    return base, out


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
    with ProcessPoolExecutor(max_workers=workers, initializer=ai._init_worker, initargs=(bt.MKT_PATH,)) as ex:
        for i, out in enumerate(ex.map(_work, codes, chunksize=8), 1):
            if out:
                latest.append(out["latest"])
                if len(out["date"]):
                    parts.append(out)
            if i % 400 == 0:
                log(f"[표본 {i}/{len(codes)}] {sum(len(p['date']) for p in parts)}행 · {time.time() - t0:.0f}초")
    X = np.concatenate([p["X"] for p in parts])
    date = np.concatenate([p["date"] for p in parts])
    code = np.concatenate([p["code"] for p in parts])
    keys = ["r20", "x5", "x10", "split3", "mae0", "mae_split", "conf_ret", "pb_ret", "stop_ret", "stop_hit"]
    T = {k: np.concatenate([p[k] for p in parts]) for k in keys}
    log(f"표본 {len(date):,}행 · {time.time() - t0:.0f}초")
    ix = {n: i for i, n in enumerate(ai.NAMES)}
    cols = [ix[n] for n in ai.NAMES if n not in ai.SIGNAL_NAMES]
    valid = ~np.isnan(T["r20"])
    tr, va, te = ai._split_masks(date)
    r_net = T["r20"] - COST

    # ---------------- 1. 방향 예측 모델
    targets = {"up": (r_net > 0), "big_up": (T["r20"] >= BIG), "big_dn": (T["r20"] <= -BIG)}
    models, mets, preds = {}, {}, {}
    for k, y in targets.items():
        model, best, met, p, mte = ai._train_eval(X[:, cols], y.astype(int), valid, tr, va, te)
        models[k], mets[k], preds[k] = (model, best), met, p
        log(f"[1] {k}: AUC {met['auc']:.3f} · 기준 {met['base'] * 100:.1f}% · 상위5% {met['top5']['precision'] * 100:.1f}% · {time.time() - t0:.0f}초")
    te_idx = np.flatnonzero(mte)
    p_up = preds["up"]
    D = {k: v[te_idx] for k, v in T.items()}
    score = p_up
    rep = {"meta": {"generated": datetime.now().isoformat(timespec="seconds"), "rows": int(len(date)), "stocks": int(len(set(code))),
                    "test_rows": int(mte.sum()), "split": {"train_end": ai.TRAIN_END, "valid_end": ai.VALID_END, "date_min": str(date.min()), "date_max": str(date.max())},
                    "cost": COST, "hold": ai.HOLD, "features": len(cols)}}
    rep["direction"] = {"base": _stats(r_net[valid & te]), "base_all": _stats(r_net[valid]),
                        "models": {k: {"auc": mets[k]["auc"], "base": mets[k]["base"], "top1": mets[k]["top1"], "top5": mets[k]["top5"], "top10": mets[k]["top10"],
                                       "calibration": ai._calibration(targets[k][mte].astype(int), preds[k])} for k in mets},
                        "deciles": _deciles(r_net[te_idx], p_up)}
    # ---------------- 시장 자체
    mkt = pd.read_pickle(bt.MKT_PATH)
    rep["market"] = _market_study(mkt)
    # ---------------- 2. 조건별 상승 확률 (전체 기간 표본)
    Dall = pd.DataFrame(X[valid], columns=ai.NAMES)
    Dall["r20"] = T["r20"][valid]
    date_v = date[valid]
    mean_by_date = pd.Series(r_net[valid]).groupby(date_v).transform("mean").to_numpy()
    excess = r_net[valid] - mean_by_date
    base_stats, conds = _conditions(Dall, excess, (date_v > ai.VALID_END))
    rep["conditions"] = {"base": base_stats, "rows": conds}
    # ---------------- 3. 진입 방식 비교
    top10 = score >= np.quantile(score, 0.9)
    mkt_r60 = X[te_idx][:, ix["mkt_r60"]]
    universes = [("시험 기간 전체 표본", np.ones(len(te_idx), bool)), ("AI 상승 확률 상위 10%", top10),
                 ("AI 상승 확률 하위 10%", score <= np.quantile(score, 0.1)),
                 ("시장 약세·급락 때 (시장 60일 수익률 0% 이하)", mkt_r60 <= 0), ("시장 상승 때 (60일 +0% 초과)", mkt_r60 > 0)]
    rep["entry_methods"] = [{"universe": name, "rows": _methods(D, m)} for name, m in universes if m.sum() > 2000]
    # ---------------- 4. 지금 상승 가능성 높은 종목
    # 시장 특징을 넣은 모델은 '지금 시장이 낙폭 구간'이라는 이유만으로 모든 종목 확률을 같이 올린다 → 종목 고유 모델로 순위를 매기고,
    # 시험 기간 예측을 실제 비율로 맞추는(등위 회귀) 보정을 해서 과신을 줄인다. 시장 효과는 따로 보여 준다.
    from sklearn.isotonic import IsotonicRegression
    cols_s = [ix[n] for n in ai.NAMES if n not in ai.SIGNAL_NAMES and not n.startswith("mkt_") and n != "rs20"]
    models_s, preds_s, mets_s = {}, {}, {}
    for k, y in targets.items():
        model_s, best_s, met_s, p_s, _ = ai._train_eval(X[:, cols_s], y.astype(int), valid, tr, va, te)
        models_s[k], preds_s[k], mets_s[k] = model_s, p_s, met_s
        log(f"[4] 종목 고유 {k}: AUC {met_s['auc']:.3f} · {time.time() - t0:.0f}초")
    iso = {k: IsotonicRegression(out_of_bounds="clip").fit(preds_s[k], targets[k][mte].astype(int)) for k in targets}
    rep["direction"]["stock_only"] = {
        "models": {k: {"auc": mets_s[k]["auc"], "base": mets_s[k]["base"], "top5": mets_s[k]["top5"], "top10": mets_s[k]["top10"]} for k in mets_s},
        "deciles": _deciles(r_net[te_idx], preds_s["up"])}
    L = [r for r in latest if r["ok"] and not np.isnan(r["X"][ix["ret60"]]) and not np.isnan(r["X"][ix["vr5"]])
         and r["X"][ix["liq"]] >= math.log10(ai.TRADABLE_VALUE)]
    picks = []
    if L:
        XL = np.vstack([r["X"] for r in L])
        Pf = {k: models[k][0].predict_proba(XL[:, cols])[:, 1] for k in models}
        Ps = {k: iso[k].predict(models_s[k].predict_proba(XL[:, cols_s])[:, 1]) for k in models_s}
        comp = Ps["up"] + Ps["big_up"] - Ps["big_dn"]
        order = np.argsort(-comp)[:20]
        imp_names, _, _ = _importance_cols(models_s["up"], X[mte][:, cols_s], targets["up"][mte].astype(int), [ai.NAMES[j] for j in cols_s])
        feat_idx = [cols_s.index(ix[n]) for n in imp_names]
        medians = np.nanmedian(X[tr][:, cols_s], axis=0)
        ex = ai.explain_rows(models_s["up"], XL[order][:, cols_s], medians, feat_idx)
        for k, (p0, contribs) in zip(order, ex):
            row = L[k]
            cc = [(cols_s[j], c) for j, c in contribs]
            picks.append({"code": row["code"], "name": names.get(row["code"], row["code"]), "date": row["date"], "close": row["close"],
                          "p_up": float(Ps["up"][k]), "p_big_up": float(Ps["big_up"][k]), "p_big_dn": float(Ps["big_dn"][k]),
                          "p_up_raw": float(models_s["up"].predict_proba(XL[k:k + 1][:, cols_s])[0, 1]), "p_up_with_market": float(Pf["up"][k]),
                          "reason": ai.reason_text(cc, row["X"], []), "ret20": ai._num(float(row["X"][ix["ret20"]])),
                          "nh250": ai._num(float(row["X"][ix["nh250"]]))})
        # 시장 효과: 시장 특징을 평소 값으로 바꿨을 때 전체 종목의 평균 확률 변화
        mk_idx = [cols.index(ix[n]) for n in ai.NAMES if (n.startswith("mkt_") or n == "rs20") and ix[n] in cols]
        med_all = np.nanmedian(X[tr][:, cols], axis=0)
        XLc = XL[:, cols].copy()
        XLn = XLc.copy()
        XLn[:, mk_idx] = med_all[mk_idx]
        lift = float(np.mean(models["up"][0].predict_proba(XLc)[:, 1] - models["up"][0].predict_proba(XLn)[:, 1]))
        rep["today_market"] = {"mkt_r60": float(np.nanmedian(XL[:, ix["mkt_r60"]])), "mkt_dd250": float(np.nanmedian(XL[:, ix["mkt_dd250"]])),
                               "market_lift_up": lift, "mean_p_up_with_market": float(Pf["up"].mean()), "mean_p_up_stock_only": float(Ps["up"].mean())}
    rep["picks"] = picks
    rep["text"] = narrate(rep)
    rep["meta"]["elapsed_sec"] = round(time.time() - t0)
    ENTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENTRY_PATH.write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
    return rep


def _importance_cols(model, Xte, yte, col_names, n_feat=12):
    """열 부분집합용 가벼운 중요도(표본 6만 행, 1회)."""
    from sklearn.metrics import average_precision_score
    rng = np.random.default_rng(2)
    idx = rng.choice(len(Xte), min(len(Xte), 60000), replace=False)
    Xs, ys = Xte[idx], yte[idx]
    base = average_precision_score(ys, model.predict_proba(Xs)[:, 1])
    drops = []
    for j in range(Xs.shape[1]):
        Xp = Xs.copy()
        Xp[:, j] = rng.permutation(Xp[:, j])
        drops.append(base - average_precision_score(ys, model.predict_proba(Xp)[:, 1]))
    order = np.argsort(drops)[::-1][:n_feat]
    return [col_names[j] for j in order], drops, base


def narrate(rep: dict) -> list[str]:
    out = []
    d = rep["direction"]
    b = d["base"]
    out.append(f"방향은 거의 동전 던지기에 가깝습니다. 20거래일 뒤 오른 비율은 {b['p_up'] * 100:.0f}%, +10% 이상 {b['p_big_up'] * 100:.0f}%, −10% 이하 {b['p_big_loss'] * 100:.0f}%였습니다(비용 차감, 시험 기간).")
    up = d["models"]["up"]
    out.append(f"AI는 '오를지'를 AUC {up['auc']:.2f}로 가려냅니다(0.5=무작위). 상승 확률 상위 10%의 실제 상승 비율 {d['deciles'][-1]['p_up'] * 100:.0f}% vs 하위 10% {d['deciles'][0]['p_up'] * 100:.0f}%, "
               f"평균 수익 {d['deciles'][-1]['mean'] * 100:+.1f}% vs {d['deciles'][0]['mean'] * 100:+.1f}%.")
    em = rep.get("entry_methods") or []
    if em:
        rows = {r["name"]: r for r in em[0]["rows"]}
        a = rows.get("한 번에 전액 (다음날 시가)")
        s = rows.get("3분할 (다음날·5일 뒤·10일 뒤 1/3씩)")
        st = rows.get("전액 + 2×ATR 손절")
        if a and s:
            out.append(f"3분할은 평균 수익이 {s['mean'] * 100:+.2f}%로 전액({a['mean'] * 100:+.2f}%)과 비슷하지만 변동폭(표준편차)은 {a['std'] * 100:.1f}% → {s['std'] * 100:.1f}%, "
                       f"보유 중 평균 최대 낙폭은 {a['mae'] * 100:.1f}% → {s['mae'] * 100:.1f}%로 줄었습니다.")
        if a and st:
            out.append(f"2×ATR 손절을 병행하면 최악 5% 구간 손실이 {a['q05'] * 100:.1f}% → {st['q05'] * 100:.1f}%로 줄지만, 평균 수익은 {a['mean'] * 100:+.2f}% → {st['mean'] * 100:+.2f}%로 바뀝니다.")
    return out


if __name__ == "__main__":
    run()
