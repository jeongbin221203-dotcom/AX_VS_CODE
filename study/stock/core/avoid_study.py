"""갭 줄이기 · 절대 들어가면 안 되는 종목 연구.

1) 갭: 신호일 종가 → 다음날 시가가 벌어지는 비용. 갭 크기별 이후 수익, 갭을 줄이는 방법(갭 상한·종가 지정가·하루 나눠 매수·종가 진입)을 비교.
2) 피해야 할 종목: 신호일 종가까지 알 수 있는 '위험 조건'마다 20거래일 뒤 결과(−10%·−20% 이하 비율, 시장 대비 수익)를 학습 기간(~2021)과
   시험 기간(2022~)에서 따로 확인해, 두 기간 모두 나쁜 조건만 '피함'으로 판정. 그 조건으로 범위를 좁히면 전체·AI 상위 종목의 결과가 얼마나 좋아지는지 확인.
3) 지금 피해야 할 종목 목록과 이유.
수익은 다음날 시가에 사서 20거래일 뒤 종가, 비용 0.3% 차감(갭 방법 비교는 방법마다 진입가가 다름).
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

AVOID_PATH = config.DATA_DIR / "avoid.json"
COST = ai.COST
LIQ3, LIQ5 = math.log10(3e8), math.log10(5e8)


def _work(code):
    try:
        return ai.build_rows(code, with_signals=False, extras=True)
    except Exception:
        return None


# (이름, 분류, 설명, 조건) — 조건은 DataFrame → 불리언 Series
RULES = [
    ("주가 1,000원 미만 (저가주)", lambda d: d["close_px"] < 1000),
    ("주가 500원 미만", lambda d: d["close_px"] < 500),
    ("변동성 높음 (ATR÷종가 8%↑)", lambda d: d["atrp"] >= 0.08),
    ("변동성 극단 (ATR÷종가 12%↑)", lambda d: d["atrp"] >= 0.12),
    ("20일 +30%↑ 급등 직후", lambda d: d["ret20"] >= 0.30),
    ("20일 +50%↑ 급등 직후", lambda d: d["ret20"] >= 0.50),
    ("5일 −20%↓ 폭락 직후", lambda d: d["ret5"] <= -0.20),
    ("20일 −40%↓ 급락", lambda d: d["ret20"] <= -0.40),
    ("최근 60일 중 거래 없는 날 3일↑", lambda d: d["zero_vol60"] >= 3),
    ("최근 20일 −5%↓ 갭하락 2번↑", lambda d: d["gapdn20"] >= 2),
    ("연속 하락 5일↑", lambda d: d["down_streak"] >= 5),
    ("거래대금 낮음 (20일 평균 3~5억)", lambda d: (d["liq"] >= LIQ3) & (d["liq"] < LIQ5)),
    ("거래량 3배↑ + 음봉 (물량 분출)", lambda d: (d["volratio"] >= 3) & (d["body"] < 0)),
    ("224일선 대비 −50%↓ (장기 약세)", lambda d: d["dist224"] <= -0.50),
    ("52주 고점 대비 −70%↓", lambda d: d["nh250"] <= -0.70),
    ("긴 윗꼬리 (고저폭 8%↑, 윗꼬리 60%↑ — 상승 실패)", lambda d: (d["upper"] >= 0.6) & (d["range1"] >= 0.08)),
    ("당일 갭 +10%↑ 급등 출발", lambda d: d["gap0"] >= 0.10),
    ("저가주 + 고변동 (1,000원↓ & ATR 8%↑)", lambda d: (d["close_px"] < 1000) & (d["atrp"] >= 0.08)),
    ("역배열 + 20일선 −10%↓ 이탈", lambda d: (d["reversed"] == 1) & (d["dist20"] <= -0.10)),
]


def _stats(r):
    r = np.asarray(r, float)
    r = r[~np.isnan(r)]
    if len(r) < 30:
        return None
    return {"n": int(len(r)), "mean": float(r.mean()), "median": float(np.median(r)), "std": float(r.std()), "p_up": float((r > 0).mean()),
            "p_big_up": float((r >= 0.10).mean()), "p_dn10": float((r <= -0.10).mean()), "p_dn20": float((r <= -0.20).mean()),
            "q05": float(np.quantile(r, 0.05))}


def _rule_table(D: pd.DataFrame, r_net, excess, test):
    ok = ~np.isnan(r_net)
    base = _stats(r_net[ok])
    base_te = _stats(r_net[ok & test])
    out = []
    for name, fn in RULES:
        m = fn(D).to_numpy() & ok
        if m.sum() < 800:
            continue
        s, rest = _stats(r_net[m]), _stats(r_net[~m & ok])
        tr_m, te_m = m & ~test, m & test
        s_tr, s_te = _stats(r_net[tr_m]), _stats(r_net[te_m])
        row = {"name": name, "n": s["n"], "share": float(m.sum() / ok.sum()), "mean": s["mean"], "excess": float(np.nanmean(excess[m])),
               "p_up": s["p_up"], "p_dn10": s["p_dn10"], "p_dn20": s["p_dn20"], "q05": s["q05"], "p_dn20_rest": rest["p_dn20"],
               "lift_dn20": s["p_dn20"] / rest["p_dn20"] if rest["p_dn20"] else None,
               "excess_tr": float(np.nanmean(excess[tr_m])) if tr_m.sum() >= 200 else None,
               "excess_te": float(np.nanmean(excess[te_m])) if te_m.sum() >= 200 else None,
               "p_dn20_tr": s_tr["p_dn20"] if s_tr else None, "p_dn20_te": s_te["p_dn20"] if s_te else None,
               "n_te": int(te_m.sum())}
        # 판정: 두 기간 모두 시장보다 나쁘고(−0.5%p↓) 큰 손실(−20%↓) 확률이 전체의 1.2배↑ → 피함 / 한 기간만 나쁘면 주의
        both_bad = (row["excess_tr"] is not None and row["excess_te"] is not None and row["excess_tr"] <= -0.005 and row["excess_te"] <= -0.005)
        risky = (row["lift_dn20"] or 0) >= 1.2
        row["verdict"] = "피함" if both_bad and risky else ("주의" if (row["excess"] <= -0.005 or risky) else "관계 약함")
        out.append(row)
    out.sort(key=lambda r: ({"피함": 0, "주의": 1, "관계 약함": 2}[r["verdict"]], -(r["lift_dn20"] or 0)))
    return base, base_te, out


def _gap_study(D: dict, m_te):
    """갭 분포, 갭 크기별 이후 수익, 갭 줄이는 방법 비교."""
    g = D["gap1"][m_te]
    r_open = D["r20"][m_te] - COST
    r_close = D["r20c"][m_te] - COST
    ok = ~np.isnan(g) & ~np.isnan(r_open)
    dist = {"n": int(ok.sum()), "mean": float(np.nanmean(g[ok])), "median": float(np.nanmedian(g[ok])), "p90": float(np.nanquantile(g[ok], 0.9)),
            "p99": float(np.nanquantile(g[ok], 0.99)), "share_1": float((g[ok] >= 0.01).mean()), "share_3": float((g[ok] >= 0.03).mean()),
            "share_5": float((g[ok] >= 0.05).mean()), "share_down1": float((g[ok] <= -0.01).mean())}
    edges = [-1, -0.03, -0.01, 0.0, 0.01, 0.03, 0.05, 1]
    labels = ["−3% 이하 (갭하락)", "−3~−1%", "−1~0%", "0~+1%", "+1~+3%", "+3~+5%", "+5% 이상 (큰 갭상승)"]
    b = pd.cut(pd.Series(g), edges, labels=labels)
    buckets = []
    for lab in labels:
        m = (b == lab).to_numpy() & ok
        if m.sum() >= 300:
            buckets.append({"label": lab, "n": int(m.sum()), "gap": float(np.nanmean(g[m])), "mean_open": float(np.nanmean(r_open[m])),
                            "mean_close": float(np.nanmean(r_close[m])), "p_up_open": float((r_open[m] > 0).mean()), "p_dn10": float((r_open[m] <= -0.10).mean())})
    def meth(name, r, mask=None, entry_gap=None):
        mk = ok.copy() if mask is None else (ok & mask)
        rr = r[mk & ~np.isnan(r)]
        if len(rr) < 100:
            return None
        n_all = int(ok.sum())
        cov = len(rr) / n_all
        return {"name": name, "coverage": cov, "mean": float(rr.mean()), "per_signal": float(cov * rr.mean()), "std": float(rr.std()), "p_up": float((rr > 0).mean()),
                "p_dn10": float((rr <= -0.10).mean()), "q05": float(np.quantile(rr, 0.05)),
                "entry_gap": entry_gap}
    lc = D["lc_ret"][m_te] - COST
    vw = D["vwap_ret"][m_te] - COST
    methods = [meth("다음날 시가에 전액 (기준)", r_open, entry_gap=float(np.nanmean(g[ok]))),
               meth("갭 +1% 이하일 때만 진입", r_open, g <= 0.01),
               meth("갭 +2% 이하일 때만 진입", r_open, g <= 0.02),
               meth("갭 +3% 이하일 때만 진입", r_open, g <= 0.03),
               meth("신호일 종가에 지정가 (다음날 그 값까지 내려오면 체결)", lc, entry_gap=0.0),
               meth("다음날 하루 나눠 매수 (평균가 ≈ 시고저종 평균)", vw),
               meth("신호일 종가에 진입 (마감 직전 매수가 가능할 때)", r_close, entry_gap=0.0)]
    return {"dist": dist, "buckets": buckets, "methods": [m for m in methods if m]}


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
    keys = ["r20", "gap1", "r20c", "lc_ret", "vwap_ret", "zero_vol60", "gapdn20", "down_streak", "close_px", "stop_ret"]
    T = {k: np.concatenate([p[k] for p in parts]) for k in keys}
    log(f"표본 {len(date):,}행 · {time.time() - t0:.0f}초")
    ix = {n: i for i, n in enumerate(ai.NAMES)}
    valid = ~np.isnan(T["r20"])
    tr, va, te = ai._split_masks(date)
    r_net = T["r20"] - COST
    D = pd.DataFrame(X, columns=ai.NAMES)
    for k in ("zero_vol60", "gapdn20", "down_streak", "close_px"):
        D[k] = T[k]
    mean_by_date = pd.Series(r_net).groupby(date).transform("mean").to_numpy()
    excess = r_net - mean_by_date
    test_mask = date > ai.VALID_END
    base, base_te, rules = _rule_table(D, r_net, excess, test_mask)
    rep = {"meta": {"generated": datetime.now().isoformat(timespec="seconds"), "rows": int(len(date)), "stocks": int(len(codes)),
                    "split": {"train_end": ai.TRAIN_END, "valid_end": ai.VALID_END, "date_min": str(date.min()), "date_max": str(date.max())}, "cost": COST}}
    rep["rules"] = {"base": base, "base_test": base_te, "rows": rules}
    log(f"[규칙] {len(rules)}개 평가 · 피함 {sum(r['verdict'] == '피함' for r in rules)}개 · {time.time() - t0:.0f}초")

    # ---- 피함 규칙의 합집합으로 범위를 좁히면
    avoid = [(n, fn) for n, fn in RULES if any(r["name"] == n and r["verdict"] == "피함" for r in rules)]
    flag = np.zeros(len(D), bool)
    for _, fn in avoid:
        flag |= fn(D).to_numpy()
    keep = ~flag
    narrowing = {"rules": [n for n, _ in avoid], "excluded_share": float(flag[valid].mean()), "excluded_share_test": float(flag[valid & test_mask].mean())}
    for label, m in (("전체 기간", valid), ("시험 기간(2022~)", valid & test_mask)):
        narrowing[label] = {"before": _stats(r_net[m]), "excluded": _stats(r_net[m & flag]), "after": _stats(r_net[m & keep])}
    # ---- 모델: 종목 고유 '오를 확률'(AI 상위 10%) + '크게 잃을 확률'(위험 점수)
    cols = [ix[n] for n in ai.NAMES if n not in ai.SIGNAL_NAMES and not n.startswith("mkt_") and n != "rs20"]
    y_up = (r_net > 0).astype(int)
    y_crash = (T["r20"] <= -0.20).astype(int)
    m_up, _, met_up, p_up, mte = ai._train_eval(X[:, cols], y_up, valid, tr, va, te)
    m_cr, _, met_cr, p_cr, _ = ai._train_eval(X[:, cols], y_crash, valid, tr, va, te)
    log(f"[모델] 오를 확률 AUC {met_up['auc']:.3f} · 폭락(−20%↓) AUC {met_cr['auc']:.3f} · {time.time() - t0:.0f}초")
    te_idx = np.flatnonzero(mte)
    top = p_up >= np.quantile(p_up, 0.9)
    keep_te = keep[te_idx]
    rn = r_net[te_idx]
    cr_dec = pd.qcut(pd.Series(p_cr).rank(method="first"), 10, labels=False)
    narrowing["ai_top10"] = {"before": _stats(rn[top]), "after": _stats(rn[top & keep_te]), "removed_share": float(1 - (top & keep_te).sum() / max(top.sum(), 1))}
    narrowing["crash_model"] = {"auc": met_cr["auc"], "base": met_cr["base"], "top5": met_cr["top5"], "top10": met_cr["top10"],
                                "deciles": [{"bin": int(k) + 1, "p_crash": float(y_crash[te_idx][(cr_dec == k).to_numpy()].mean()),
                                             "mean": float(rn[(cr_dec == k).to_numpy()].mean())} for k in range(10)]}
    safe = p_cr <= np.quantile(p_cr, 0.7)                                  # 위험 점수 하위 70%만 남기면
    narrowing["after_crash_filter"] = {"before": _stats(rn), "after": _stats(rn[safe]), "ai_top10_after": _stats(rn[top & safe & keep_te])}
    rep["narrowing"] = narrowing
    # ---- 갭
    rep["gap"] = _gap_study(T, mte)
    # 신호 종류별 평균 갭(기법 백테스트 결과가 있으면)
    rep["gap"]["by_signal"] = _gap_by_signal()
    # ---- 지금 피해야 할 종목
    L = [r for r in latest if r["ok"] and not np.isnan(r["X"][ix["ret60"]]) and not np.isnan(r["X"][ix["vr5"]])
         and r["X"][ix["liq"]] >= math.log10(ai.TRADABLE_VALUE)]
    today = {"n_universe": len(L), "flagged": 0, "rows": []}
    if L:
        XL = np.vstack([r["X"] for r in L])
        DL = pd.DataFrame(XL, columns=ai.NAMES)
        for k in ("zero_vol60", "gapdn20", "down_streak", "close_px"):
            DL[k] = [r.get(k, np.nan) for r in L]
        hit = {n: fn(DL).to_numpy() for n, fn in avoid}
        danger = m_cr.predict_proba(XL[:, cols])[:, 1]
        cnt = np.sum([h for h in hit.values()], axis=0) if hit else np.zeros(len(L))
        flagged = np.flatnonzero(cnt > 0)
        today["flagged"] = int(len(flagged))
        order = flagged[np.lexsort((-danger[flagged], -cnt[flagged]))][:40]
        for k in order:
            today["rows"].append({"code": L[k]["code"], "name": names.get(L[k]["code"], L[k]["code"]), "date": L[k]["date"], "close": L[k]["close"],
                                  "n_rules": int(cnt[k]), "danger": float(danger[k]), "reasons": [n for n, h in hit.items() if h[k]]})
        top_danger = np.argsort(-danger)[:15]
        today["top_danger"] = [{"code": L[k]["code"], "name": names.get(L[k]["code"], L[k]["code"]), "close": L[k]["close"], "danger": float(danger[k]),
                                "reasons": [n for n, h in hit.items() if h[k]]} for k in top_danger]
        today["base_danger"] = float(met_cr["base"])
    rep["today"] = today
    rep["text"] = narrate(rep)
    rep["meta"]["elapsed_sec"] = round(time.time() - t0)
    AVOID_PATH.parent.mkdir(parents=True, exist_ok=True)
    AVOID_PATH.write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
    return rep


def _gap_by_signal():
    try:
        r = json.loads(bt.REPORT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = [{"label": k, "n": v["n_signals"], "gap": v["entry"]["gap"], "tech": v["tech"]} for k, v in r["labels"].items()
            if not k.endswith((bt.ACC_SUFFIX_ON, bt.ACC_SUFFIX_OFF)) and v["n_signals"] >= 1000]
    rows.sort(key=lambda x: -x["gap"])
    return rows


def narrate(rep: dict) -> list[str]:
    out = []
    g = rep["gap"]["dist"]
    out.append(f"신호 다음날 시가는 신호일 종가보다 평균 {g['mean'] * 100:+.2f}% (중앙 {g['median'] * 100:+.2f}%), 10%는 {g['p90'] * 100:+.1f}% 이상 벌어집니다. +3% 이상 갭상승은 {g['share_3'] * 100:.0f}%.")
    ms = {m["name"]: m for m in rep["gap"]["methods"]}
    base = ms.get("다음날 시가에 전액 (기준)")
    for name in ("갭 +2% 이하일 때만 진입", "신호일 종가에 지정가 (다음날 그 값까지 내려오면 체결)", "다음날 하루 나눠 매수 (평균가 ≈ 시고저종 평균)"):
        m = ms.get(name)
        if m and base:
            out.append(f"{name}: 체결 {m['coverage'] * 100:.0f}%, 평균 {m['mean'] * 100:+.2f}% (기준 {base['mean'] * 100:+.2f}%), 신호당 {m['per_signal'] * 100:+.2f}% (기준 {base['per_signal'] * 100:+.2f}%).")
    n = rep["narrowing"]
    out.append(f"'피함' 규칙 {len(n['rules'])}개로 거래 가능 종목의 {n['excluded_share'] * 100:.0f}%를 제외하면 시험 기간 평균 {n['시험 기간(2022~)']['before']['mean'] * 100:+.2f}% → "
               f"{n['시험 기간(2022~)']['after']['mean'] * 100:+.2f}%, −20% 이하 비율 {n['시험 기간(2022~)']['before']['p_dn20'] * 100:.0f}% → {n['시험 기간(2022~)']['after']['p_dn20'] * 100:.0f}%.")
    return out


if __name__ == "__main__":
    run()
