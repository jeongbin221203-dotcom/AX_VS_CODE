"""그룹별 지표 묶음 탐색(2026-10-18): 그룹마다 지표 묶음(볼린저·RSI·MACD…)을 앞으로 하나씩 더하며(forward selection)
검증(2022~23)으로 고르고, 고른 묶음을 시험(2024~26)에서 확인한다. 20거래일 보유, 상위 K, 추가 비용 0.3%p.
실행: STOCK_SCHEDULER=0 python tools/research/p1.py [그룹...]  → %TEMP%/stk/P1_<그룹>.pkl"""
import os, sys, time, joblib, numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor as HR

T = os.environ["TEMP"] + "/stk/"
FAM = {
    "볼린저": ["bb_pctb", "bb_width", "bb_width_pct"],
    "RSI": ["rsi14"],
    "MACD": ["macd_n", "macd_hist_n", "macd_up"],
    "스토캐스틱": ["stoch_k", "stoch_d"],
    "ADX·CCI·MFI": ["adx", "di_diff", "cci20", "mfi14"],
    "일목·켈트너·돈치안": ["ichi_cloud", "ichi_tk", "keltner", "donch20", "donch55"],
    "거래량": ["volratio", "vr5", "vr20_60", "vmax10", "vdry10", "upvol20", "obv20", "liq", "liq_chg", "vspike_n", "acc_recent", "cmf20", "vol_z60", "vol_trend", "turn"],
    "이동평균": ["dist5", "dist20", "dist60", "dist112", "dist224", "slope20", "slope60", "aligned", "reversed", "cross20", "cross60", "cross112", "above200"],
    "모멘텀": ["ret1", "ret5", "ret10", "ret20", "ret60", "ret120", "ret250", "mom12_1", "mom6_1", "rebound60", "up_streak", "dn_streak"],
    "변동성": ["atrp", "vol20", "vol60", "range1", "range20", "range_pct", "vol20_60", "skew60", "mdd60", "updown60", "downvol60"],
    "캔들·갭": ["gap0", "body", "upper", "lower", "gap_pct", "gap_n20"],
    "고저점": ["pos60", "nh250", "dd120", "low_age", "hl10", "hh20_gap", "from_high250", "from_low250", "high_age"],
    "시장대비": ["beta60", "rs20", "rs60", "kosdaq"],
}
GROUPS = ["대형주", "중형주", "소형주", "잡주"]
KCFG = {"대형주": 5, "중형주": 10, "소형주": 7, "잡주": 5}
MAXSTEP = 6

D = pd.read_pickle(T + "/D6g.pkl")
D["date"] = D["date"].astype(str)
allc = sorted({c for v in FAM.values() for c in v})
assert all(c in D.columns for c in allc), [c for c in allc if c not in D.columns]
rk = [c for c in allc if D[c].nunique() > 10]
R = D.groupby("date")[rk].rank(pct=True).astype("float32")
R.columns = [c + "_r" for c in rk]
D = pd.concat([D, R], axis=1)
rkset = set(rk)


def cols_of(fams):
    out = []
    for f in fams:
        for c in FAM[f]:
            out.append(c)
            if c in rkset:
                out.append(c + "_r")
    return out


def curve(g, dates):
    g = g.reindex(dates).fillna(0.0).to_numpy(); nsl = 6
    sl = [g[k::nsl] for k in range(nsl)]; L = min(len(s) for s in sl)
    if L < 3:
        return (np.nan,) * 3
    step = np.mean([s[:L] for s in sl], axis=0); eq = np.cumprod(1 + step); per = nsl * 5; yrs = L * per / 365
    cagr = eq[-1] ** (1 / yrs) - 1; vol = step.std() * np.sqrt(365 / per)
    sh = (step.mean() * 365 / per) / vol if vol > 0 else np.nan
    return cagr * 100, sh, (eq / np.maximum.accumulate(eq) - 1).min() * 100


def fit_predict(G, cols, years):
    """연도별 학습(그 전 해 11월까지) → 그 해 예측 점수. 반환 {year: Series}"""
    out = {}
    for Y in years:
        tr = G[(G.yr < Y) & (G.date < f"{Y - 1}-12-01")].dropna(subset=["y20"])
        te = G[G.yr == Y]
        m = HR(max_iter=150, learning_rate=0.07, max_depth=5, min_samples_leaf=150, l2_regularization=2.0, random_state=0)
        m.fit(tr[cols].astype("float32"), tr.y20)
        out[Y] = pd.Series(m.predict(te[cols].astype("float32")), index=te.index)
    return out


def topk_series(G, pred, K, dates):
    d = G.loc[pred.index, ["date", "r20", "liqv"]].assign(p=pred)
    d = d[(d.liqv >= 3e8)].dropna(subset=["r20"]).sort_values(["date", "p"], ascending=[True, False]).groupby("date").head(K)
    return (d.r20 - 0.003).groupby(d.date).mean()


for g in (sys.argv[1:] or GROUPS):
    t0 = time.time()
    G = D[D.grp == g].copy()
    G["y20"] = G.groupby("date")["r20"].rank(pct=True).astype("float32")
    K = KCFG[g]
    dates_all = sorted(G.loc[G.yr >= 2022, "date"].unique())
    va = [d for d in dates_all if d[:4] <= "2023"]; te = [d for d in dates_all if d[:4] >= "2024"]
    Vs = sorted(set(va))

    def evalset(fams):
        cols = cols_of(fams)
        pv = fit_predict(G, cols, (2022, 2023))
        gv = topk_series(G, pd.concat(pv.values()), K, va)
        return curve(gv, va)

    log = []
    # 전체 지표(기준선)
    cv_all = evalset(list(FAM))
    print(f"[{g}] 전체 지표 검증 CAGR {cv_all[0]:+.1f}% 샤프 {cv_all[1]:.2f} ({time.time() - t0:.0f}초)", flush=True)
    chosen, best = [], -1e9
    for step in range(MAXSTEP):
        cand = []
        for f in FAM:
            if f in chosen:
                continue
            cv = evalset(chosen + [f])
            cand.append((cv[1] if cv[1] == cv[1] else -9, cv, f))
            print(f"  step{step + 1} +{f}: 검증 CAGR {cv[0]:+.1f}% 샤프 {cv[1]:.2f}", flush=True)
        cand.sort(key=lambda x: -x[0])
        sc, cv, f = cand[0]
        log.append((list(chosen) + [f], cv))
        print(f"[{g}] step{step + 1} 선택 +{f} → {chosen + [f]} 검증 샤프 {sc:.2f} CAGR {cv[0]:+.1f}% ({time.time() - t0:.0f}초)", flush=True)
        if sc <= best + 0.05:
            break
        chosen.append(f); best = sc
        joblib.dump({"log": log, "all": cv_all}, T + f"P1_{g}.pkl")
    # 시험: 앞 단계별 묶음과 전체 지표
    res = []
    for fams, cvv in log + [(list(FAM), cv_all)]:
        cols = cols_of(fams)
        py = fit_predict(G, cols, (2022, 2023, 2024, 2025, 2026))
        gt = topk_series(G, pd.concat([py[y] for y in (2024, 2025, 2026)]), K, te)
        ctt = curve(gt, te)
        res.append((fams, cvv, ctt))
        print(f"[{g}] 시험 {fams if len(fams) < 13 else '전체'}: 검증 {cvv[0]:+.1f}%/{cvv[1]:.2f} → 시험 {ctt[0]:+.1f}% 샤프 {ctt[1]:.2f} 낙폭 {ctt[2]:.1f}%", flush=True)
    joblib.dump({"log": log, "all": cv_all, "res": res, "chosen": chosen}, T + f"P1_{g}.pkl")
    print(f"[{g}] 완료 {time.time() - t0:.0f}초", flush=True)
