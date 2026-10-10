"""급등주 분석 — 20거래일 안에 +50%(고가 기준) 오른 구간의 시작일을 찾아, 오르기 전 공통점·왜 올랐나(가격·거래량으로 추정)·오른 뒤를 본다.

  급등 구간 = 종가 기준 이후 20거래일 고가가 +50% 이상이 되는 첫 날(앞 20일 안에 그런 날이 없던 것)부터 정점까지. 시작일 = 그 창 안에서 종가가 가장 낮은 날(실제 출발점).
  거래 가능 종목(20일 평균 거래대금 3억↑), 상장 250일↑.
  1. 시작일 모습: 60일 전부터의 가격·거래량 경로(평균), 시작 전 조건별 비율을 아무 날과 비교(배수)
  2. 왜 올랐나(가격·거래량 맥락으로 분류 — 뉴스·공시 데이터는 없음): 첫날 상한가/큰 갭(재료 추정), 바닥 반등, 신고가 돌파, 급락 뒤 V자, 횡보 응축 뒤, 거래량 선행, 시장 동반
  3. 오른 뒤: 정점까지 며칠, 정점 뒤 60일 되돌림, 시작일 종가 대비 60·120일 뒤
  4. 표본 캐시(strategy_rows.npz)로 기법 신호·특징 배수(급등 전 행 vs 아무 날)
"""
import json
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

import config
from core import ai, db, strategy_study as st
from core import backtest as bt

BOOM_PATH = config.DATA_DIR / "boom.json"
GAIN = 0.50
WIN = 20
PRE = 60
POST = 120
LIQ_MIN = math.log10(ai.TRADABLE_VALUE)
BASE_STEP = 37


def _episodes(df):
    o, h, l, c, v = (df[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
    n = len(c)
    if n < 250 + WIN + POST + 5:
        return None
    fh = sliding_window_view(h[1:], WIN)                                   # fh[t] = 고가 t+1..t+20
    g = np.full(n, np.nan)
    g[:len(fh)] = fh.max(axis=1) / c[:len(fh)] - 1
    boom = g >= GAIN
    prior = pd.Series(boom.astype(float)).rolling(WIN, min_periods=1).sum().shift().fillna(0).to_numpy()
    val = pd.Series(c * v).rolling(20, min_periods=20).mean().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        liq = np.log10(val + 1)
    t_all = np.arange(n)
    first = t_all[boom & (prior == 0) & (liq >= LIQ_MIN) & (t_all >= 250) & (t_all + WIN + POST < n)]
    # 실제 출발점 = 그 창(첫날~정점 전날) 안에서 종가가 가장 낮은 날(그 뒤로 정점까지 오름). 첫날 기준은 20일 앞까지 잡혀 너무 이르다.
    starts = []
    for t0 in first:
        p = t0 + 1 + int(np.argmax(h[t0 + 1:t0 + WIN + 1]))
        starts.append(t0 + int(np.argmin(c[t0:p])))
    starts = np.unique(np.array(starts, int))
    base = t_all[(liq >= LIQ_MIN) & (t_all >= 250) & (t_all + WIN + POST < n) & (t_all % BASE_STEP == 0)]
    return o, h, l, c, v, starts, base


def _profile(o, h, l, c, v, t):
    """시작일 t(배열) 기준 경로와 특징. 경로는 t−PRE..t+WIN 의 종가(시작일 종가=1)와 거래량 배수(직전 20일 평균 대비)."""
    n = len(c)
    a20 = pd.Series(v).rolling(20, min_periods=20).mean().shift().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        vr = v / a20
    ret1 = np.r_[np.nan, c[1:] / c[:-1] - 1]
    hh250 = pd.Series(h).rolling(250, min_periods=120).max().to_numpy()
    ll60 = pd.Series(l).rolling(60, min_periods=60).min().to_numpy()
    hh20 = pd.Series(h).rolling(20, min_periods=20).max().to_numpy()
    ll20 = pd.Series(l).rolling(20, min_periods=20).min().to_numpy()
    ma = {p: pd.Series(c).rolling(p, min_periods=p).mean().to_numpy() for p in (5, 20, 60, 112, 224)}
    avg5 = pd.Series(v).rolling(5, min_periods=5).mean().to_numpy()
    avg60 = pd.Series(v).rolling(60, min_periods=60).mean().to_numpy()
    vmin10 = pd.Series(v).rolling(10, min_periods=10).min().to_numpy()
    spike = pd.Series((vr >= 3).astype(float)).rolling(20, min_periods=1).sum().to_numpy()
    tr = pd.Series(np.maximum(h - l, np.maximum(np.abs(h - np.r_[c[0], c[:-1]]), np.abs(l - np.r_[c[0], c[:-1]])))).rolling(14).mean().to_numpy()
    P = {}
    Wc = sliding_window_view(c, PRE + WIN + 1)[t - PRE]                 # t−60..t+20
    Wv = sliding_window_view(np.nan_to_num(vr, nan=1.0), PRE + WIN + 1)[t - PRE]
    P["path_c"] = Wc / c[t][:, None]
    P["path_v"] = np.clip(Wv, 0, 20)
    Wh = sliding_window_view(h, WIN + POST + 1)[t]                      # t..t+140
    Wl = sliding_window_view(l, WIN + POST + 1)[t]
    Wc2 = sliding_window_view(c, WIN + POST + 1)[t]
    # 정점 = 시작 뒤 상승이 끝나는 날까지(고점 대비 −20% 되돌림이 처음 나오거나 60일)의 최고가. +50% 는 20일 안에 닿지만 상승 자체는 더 갈 수 있다.
    H60, L60 = Wh[:, 1:61], Wl[:, 1:61]
    cm = np.maximum.accumulate(H60, axis=1)
    broke = L60 <= cm * 0.80
    end = np.where(broke.any(axis=1), broke.argmax(axis=1) + 1, 60)       # 상승이 끝난 날(포함)
    end = np.maximum(end, WIN)                                             # +50% 는 20일 안에 닿으므로 최소 20일은 본다(첫날 상한가 뒤 흔들림이 '끝'으로 잡히지 않게)
    peak = np.array([int(np.argmax(H60[i, :end[i]])) + 1 for i in range(len(t))])
    rows = np.arange(len(t))
    P["gain"] = Wh[rows, peak] / c[t] - 1
    P["peak_day"] = peak.astype(float)
    P["move_end"] = end.astype(float)
    P["gain20"] = Wh[:, 1:WIN + 1].max(axis=1) / c[t] - 1
    P["gain60"] = Wh[:, 1:61].max(axis=1) / c[t] - 1
    P["r20"] = Wc2[:, WIN] / c[t] - 1
    P["r60"] = Wc2[:, 60] / c[t] - 1
    P["r120"] = Wc2[:, 120] / c[t] - 1
    after = np.full(len(t), np.nan)
    for i, p in enumerate(peak):                                        # 정점 뒤 60일 최저 종가(되돌림)
        after[i] = Wl[i, p:p + 61].min() / Wh[i, p] - 1
    P["retrace60"] = after
    P["first_ret"] = c[t + 1] / c[t] - 1
    P["first_gap"] = o[t + 1] / c[t] - 1
    P["first_vr"] = np.nan_to_num(vr[t + 1], nan=1.0)
    up_days = (np.diff(Wc2[:, :WIN + 1], axis=1) > 0).sum(axis=1)
    P["up_days"] = up_days.astype(float)
    big_days = (np.diff(Wc2[:, :WIN + 1], axis=1) / Wc2[:, :WIN] >= 0.10).sum(axis=1)
    P["big_days"] = big_days.astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        F = {"ret5": c[t] / c[t - 5] - 1, "ret20": c[t] / c[t - 20] - 1, "ret60": c[t] / c[t - 60] - 1, "nh250": c[t] / hh250[t] - 1, "low60": c[t] / ll60[t] - 1,
             "range20": (hh20[t] - ll20[t]) / c[t], "dist5": c[t] / ma[5][t] - 1, "dist20": c[t] / ma[20][t] - 1, "dist60": c[t] / ma[60][t] - 1, "dist224": c[t] / ma[224][t] - 1,
             "aligned": ((ma[5][t] > ma[20][t]) & (ma[20][t] > ma[60][t]) & (ma[60][t] > ma[112][t])).astype(float),
             "reversed": ((ma[5][t] < ma[20][t]) & (ma[20][t] < ma[60][t]) & (ma[60][t] < ma[112][t])).astype(float),
             "volratio": vr[t], "vr5": avg5[t] / a20[t], "vr20_60": a20[t] / avg60[t], "vdry10": vmin10[t] / a20[t], "spike20": spike[t],
             "vmax5": sliding_window_view(np.nan_to_num(vr, nan=1.0), 5)[t - 4].max(axis=1), "atrp": tr[t] / c[t], "price": c[t], "liq": np.log10(val_of(c, v)[t] + 1),
             "ret1": ret1[t], "hist": t.astype(float)}
    P.update(F)
    return P


PRE10_KEYS = ["nh250", "low60", "dist224", "dist20", "aligned", "reversed", "ret20", "ret60", "range20", "atrp", "volratio", "vr5", "vmax5", "vr20_60", "vdry10", "spike20", "price", "liq", "hist"]


def val_of(c, v):
    return pd.Series(c * v).rolling(20, min_periods=20).mean().to_numpy()


def _work(code):
    try:
        df = ai.service.load_prices(code)
        ep = _episodes(df)
        if ep is None:
            return None
        o, h, l, c, v, starts, base = ep
        dates = df.index.strftime("%Y-%m-%d").to_numpy()
        out = {"code": code}
        for name, tt in (("ev", starts), ("bs", base)):
            if len(tt) == 0:
                out[name] = None
                continue
            P = _profile(o, h, l, c, v, tt)
            P10 = _profile(o, h, l, c, v, tt - 10)
            for k in PRE10_KEYS:
                P["pre10_" + k] = P10[k]
            P["date"] = dates[tt]
            P["code"] = np.full(len(tt), code)
            if ai.MKT_DF is not None:
                m = ai.MKT_DF.reindex(P["date"])
                for k in ("mkt_r20", "mkt_r60", "mkt_dd250"):
                    P[k] = m[k].to_numpy(float)
            else:
                for k in ("mkt_r20", "mkt_r60", "mkt_dd250"):
                    P[k] = np.full(len(tt), np.nan)
            out[name] = P
        return out
    except Exception:
        return None


def _cat(parts, key):
    arrs = [p[key] for p in parts if p.get(key) is not None]
    if not arrs:
        return None
    return {k: np.concatenate([a[k] for a in arrs]) for k in arrs[0]}


def _share(m):
    m = np.asarray(m, float)
    return float(np.nanmean(m)) if len(m) else None


def _conditions(E):
    """시작일(종가)까지의 정보로 정한 조건 — 급등 전 비율 vs 아무 날 비율."""
    return [
        ("위치", "52주 고점 −5% 이내 (신고가 근처)", E["nh250"] >= -0.05), ("위치", "52주 고점 −5~−30%", (E["nh250"] < -0.05) & (E["nh250"] >= -0.30)),
        ("위치", "52주 고점 −30~−50%", (E["nh250"] < -0.30) & (E["nh250"] >= -0.50)), ("위치", "52주 고점 −50% 이하 (바닥권)", E["nh250"] < -0.50),
        ("위치", "60일 저점 +5% 이내", E["low60"] <= 0.05), ("위치", "224일선 위", E["dist224"] > 0), ("위치", "224일선 −30% 아래", E["dist224"] <= -0.30),
        ("추세", "정배열 (5>20>60>112)", E["aligned"] == 1), ("추세", "역배열 (5<20<60<112)", E["reversed"] == 1), ("추세", "20일선 위", E["dist20"] > 0),
        ("추세", "직전 20일 +20% 이상 이미 상승", E["ret20"] >= 0.20), ("추세", "직전 20일 −20% 이하 급락", E["ret20"] <= -0.20), ("추세", "직전 20일 ±5% 횡보", E["ret20"].__abs__() < 0.05),
        ("추세", "직전 60일 −30% 이하", E["ret60"] <= -0.30),
        ("응축", "20일 가격 폭 10% 미만 (응축)", E["range20"] < 0.10), ("응축", "20일 가격 폭 30% 이상 (요동)", E["range20"] >= 0.30),
        ("응축", "변동성 낮음 (ATR÷종가 3% 미만)", E["atrp"] < 0.03), ("응축", "변동성 높음 (ATR÷종가 6% 이상)", E["atrp"] >= 0.06),
        ("거래량", "시작일 거래량 3배↑", E["volratio"] >= 3), ("거래량", "직전 5일 평균 거래량 1.5배↑ (거래량 선행)", E["vr5"] >= 1.5), ("거래량", "직전 5일 안 3배↑ 급증 있음", E["vmax5"] >= 3),
        ("거래량", "20일 평균 ÷ 60일 평균 1.5배↑ (거래량 늘어나는 중)", E["vr20_60"] >= 1.5), ("거래량", "직전 10일 최소 거래량 0.3배↓ (거래량 마름)", E["vdry10"] < 0.3),
        ("거래량", "직전 20일 안 3배↑ 급증 2번↑", E["spike20"] >= 2), ("거래량", "직전 20일 안 3배↑ 급증 없음", E["spike20"] == 0),
        ("종목", "주가 1,000원 미만", E["price"] < 1000), ("종목", "주가 1,000~5,000원", (E["price"] >= 1000) & (E["price"] < 5000)), ("종목", "주가 20,000원 이상", E["price"] >= 20000),
        ("종목", "거래대금 3~10억 (소형)", E["liq"] < 9), ("종목", "거래대금 50억↑", E["liq"] >= math.log10(5e9)), ("종목", "상장 2년 미만", E["hist"] < 500),
        ("시장", "시장 60일 −10% 이하 (급락 뒤)", E["mkt_r60"] <= -0.10), ("시장", "시장 60일 +10% 이상 (강세)", E["mkt_r60"] >= 0.10), ("시장", "시장 52주 고점 −20% 아래", E["mkt_dd250"] <= -0.20),
    ]


def _why(E):
    """왜 올랐나 — 가격·거래량 맥락으로 추정 분류(겹침 허용, 우선순위로 하나씩 배정)."""
    first_big = (E["first_ret"] >= 0.20) | (E["first_gap"] >= 0.10)
    cats = [
        ("첫날 상한가·큰 갭 (재료·뉴스 추정)", first_big),
        ("거래량 선행 (직전 5일 1.5배↑ 또는 5일 안 3배↑ 급증)", (E["vr5"] >= 1.5) | (E["vmax5"] >= 3)),
        ("신고가 돌파형 (52주 고점 −10% 이내)", E["nh250"] >= -0.10),
        ("급락 뒤 V자 반등 (직전 20일 −20%↓)", E["ret20"] <= -0.20),
        ("바닥권 반등 (52주 고점 −50%↓)", E["nh250"] < -0.50),
        ("횡보 응축 뒤 (20일 폭 12% 미만)", E["range20"] < 0.12),
        ("시장 동반 (시장 20일 +5%↑)", E["mkt_r20"] >= 0.05),
        ("추세 지속 (정배열·20일선 위)", (E["aligned"] == 1) & (E["dist20"] > 0)),
    ]
    n = len(E["gain"])
    assigned = np.full(n, -1)
    for i, (_, m) in enumerate(cats):
        m = np.asarray(m, bool) & (assigned < 0)
        assigned[m] = i
    out = []
    for i, (name, m) in enumerate(cats):
        anym = np.asarray(m, bool)
        onlym = assigned == i
        out.append({"name": name, "share_any": float(anym.mean()), "share_first": float(onlym.mean()), "n": int(onlym.sum()),
                    "gain": float(np.nanmedian(E["gain"][onlym])) if onlym.any() else None, "peak_day": float(np.nanmedian(E["peak_day"][onlym])) if onlym.any() else None,
                    "r60": float(np.nanmean(E["r60"][onlym])) if onlym.any() else None, "r120": float(np.nanmean(E["r120"][onlym])) if onlym.any() else None,
                    "retrace": float(np.nanmedian(E["retrace60"][onlym])) if onlym.any() else None,
                    "enterable": float(np.nanmean(E["first_ret"][onlym] < 0.10)) if onlym.any() else None})
    rest = assigned < 0
    if rest.sum() > 0:
        out.append({"name": "그 밖 (뚜렷한 맥락 없음)", "share_any": float(rest.mean()), "share_first": float(rest.mean()), "n": int(rest.sum()), "gain": float(np.nanmedian(E["gain"][rest])),
                    "peak_day": float(np.nanmedian(E["peak_day"][rest])), "r60": float(np.nanmean(E["r60"][rest])), "r120": float(np.nanmean(E["r120"][rest])),
                    "retrace": float(np.nanmedian(E["retrace60"][rest])), "enterable": float(np.nanmean(E["first_ret"][rest] < 0.10))})
    return out


def _paths(E, B):
    days = list(range(-PRE, WIN + 1))
    pc, pv = np.nanmedian(E["path_c"], axis=0), np.nanmedian(E["path_v"], axis=0)
    pvm = np.nanmean(np.minimum(E["path_v"], 10), axis=0)
    bc, bv = np.nanmedian(B["path_c"], axis=0), np.nanmedian(B["path_v"], axis=0)
    share_v2 = (E["path_v"] >= 2).mean(axis=0)
    bshare_v2 = (B["path_v"] >= 2).mean(axis=0)
    return [{"d": d, "c": float(pc[i]), "v": float(pv[i]), "vmean": float(pvm[i]), "v2": float(share_v2[i]), "bc": float(bc[i]), "bv": float(bv[i]), "bv2": float(bshare_v2[i])} for i, d in enumerate(days)]


def _cache_lifts(codes_set):
    """표본 캐시(기법 신호 포함)에서 20일 안 +50%(고가) 행 vs 전체: 신호·특징 비율 배수."""
    if not st.ROWS_PATH.exists():
        return None
    z = np.load(st.ROWS_PATH, allow_pickle=True)
    X, date = z["X"], z["date"]
    touch, r5 = z["T_touch"].astype(float), z["T_r5"].astype(float)
    D = pd.DataFrame(X, columns=ai.NAMES)
    ok = ~np.isnan(touch)
    boom = ok & (touch >= GAIN)
    pre = boom & (D["ret5"].to_numpy() < 0.05)                           # 아직 안 오른 상태의 급등 직전
    sig_rows = []
    for j, lab in enumerate(ai.SIGNAL_LABELS):
        m = (D[f"sig{j:02d}"] <= 3).to_numpy()
        a, b, c_ = m[ok].mean(), m[boom].mean(), m[pre].mean()
        if a > 0:
            sig_rows.append({"name": lab, "dante": j < ai.N_DANTE, "base": float(a), "boom": float(b), "pre": float(c_), "lift": float(b / a), "lift_pre": float(c_ / a)})
    sig_rows.sort(key=lambda x: -x["lift_pre"])
    cnt = np.column_stack([(D[f"sig{j:02d}"] <= 3).to_numpy() for j in range(len(ai.SIGNAL_LABELS))]).sum(axis=1)
    stack = [{"name": f"신호 {k}개 겹침" if k < 4 else "신호 4개 이상", "base": float(((cnt == k) if k < 4 else (cnt >= 4))[ok].mean()),
              "p_boom": float(touch[ok & ((cnt == k) if k < 4 else (cnt >= 4))].__ge__(GAIN).mean())} for k in range(5)]
    feats = ["atrp", "vol20", "range20", "nh250", "dd120", "rebound60", "low_age", "dist20", "dist60", "dist224", "ret5", "ret20", "ret60", "volratio", "vr5", "vr20_60", "vdry10",
             "vspike_n", "upvol20", "obv20", "liq", "liq_chg", "price_level", "hist_bars", "mkt_r20", "mkt_r60", "mkt_dd250", "cross20", "cross60", "acc_recent", "upper", "body"]
    diff = []
    for f in feats:
        a, b = D.loc[pre, f], D.loc[ok & ~boom, f]
        sd = float(D.loc[ok, f].std()) or 1.0
        diff.append({"name": f, "label": ai.LABEL.get(f, f), "pre": float(a.median()), "rest": float(b.median()), "d": float((a.mean() - b.mean()) / sd)})
    diff.sort(key=lambda x: -abs(x["d"]))
    return {"n_boom": int(boom.sum()), "n_pre": int(pre.sum()), "p_boom": float(boom[ok].mean()), "signals": sig_rows, "stack": stack, "diff": diff[:16]}


def run(workers: int = 0, log=print, limit: int = 0) -> dict:
    t0 = time.time()
    with db.get_conn() as c:
        codes = [r["code"] for r in c.execute("SELECT code FROM prices GROUP BY code HAVING COUNT(*) >= 400 ORDER BY code")]
        names = {r["code"]: r["name"] for r in c.execute("SELECT code,name FROM symbols")}
    if limit:
        codes = codes[::max(len(codes) // limit, 1)][:limit]
    workers = workers or max(1, min(6, (os.cpu_count() or 2) - 2))
    if not bt.MKT_PATH.exists():
        bt.build_market_index(codes, workers)
    parts = []
    with ProcessPoolExecutor(max_workers=workers, initializer=ai._init_worker, initargs=(bt.MKT_PATH,)) as ex:
        for i, out in enumerate(ex.map(_work, codes, chunksize=8), 1):
            if out:
                parts.append(out)
            if i % 500 == 0:
                log(f"[종목 {i}/{len(codes)}] 급등 {sum(len(p['ev']['date']) for p in parts if p.get('ev'))}건 · {time.time() - t0:.0f}초")
    E, B = _cat(parts, "ev"), _cat(parts, "bs")
    del parts
    log(f"급등 시작 {len(E['date']):,}건 · 기준 {len(B['date']):,}건 · {time.time() - t0:.0f}초")
    rep = {"meta": {"generated": datetime.now().isoformat(timespec="seconds"), "gain": GAIN, "win": WIN, "events": int(len(E["date"])), "baseline": int(len(B["date"])),
                    "stocks": int(len(set(E["code"]))), "date_min": str(E["date"].min()), "date_max": str(E["date"].max()), "liq_min": ai.TRADABLE_VALUE}}
    year = pd.Series(E["date"]).str[:4].astype(int).to_numpy()
    byear = pd.Series(B["date"]).str[:4].astype(int).to_numpy()
    rep["by_year"] = [{"year": int(y), "n": int((year == y).sum()), "per_stock": float((year == y).sum() / max(len(set(E["code"][year == y])), 1)),
                       "base_rate": float((year == y).sum() / max((byear == y).sum() * BASE_STEP, 1))} for y in sorted(set(year)) if y >= 2000]
    # 1. 시작일 모습
    rows = []
    condE, condB = _conditions(E), _conditions(B)
    for (grp, name, me), (_, _, mb) in zip(condE, condB):
        a, b = _share(np.asarray(mb, float)), _share(np.asarray(me, float))
        if a is None or b is None or np.asarray(me, bool).sum() < 30:
            continue
        me_ = np.asarray(me, bool)
        rows.append({"group": grp, "name": name, "base": a, "boom": b, "lift": b / a if a > 0 else None, "n": int(me_.sum()),
                     "gain": float(np.nanmedian(E["gain"][me_])), "enterable": float(np.nanmean(E["first_ret"][me_] < 0.10)), "r120": float(np.nanmean(E["r120"][me_]))})
    rep["conditions"] = rows
    E10 = dict(E)
    E10.update({k: E["pre10_" + k] for k in PRE10_KEYS if "pre10_" + k in E})
    E10["mkt_r60"], E10["mkt_dd250"] = E["mkt_r60"], E["mkt_dd250"]
    pre10 = {name: _share(np.asarray(m, float)) for _, name, m in _conditions(E10)}
    for r in rows:
        r["pre10"] = pre10.get(r["name"])
        r["lift10"] = (r["pre10"] / r["base"]) if r["pre10"] is not None and r["base"] else None
    rep["paths"] = _paths(E, B)
    # 통계: 시작 첫날·정점·되돌림·이후
    g = E["gain"]
    rep["stats"] = {"gain_med": float(np.nanmedian(g)), "gain_q75": float(np.nanquantile(g, 0.75)), "gain100": float((E["gain60"] >= 1.0).mean()), "peak_med": float(np.nanmedian(E["peak_day"])),
                    "peak_hist": [{"label": lab, "share": float(((E["peak_day"] >= a) & (E["peak_day"] <= b)).mean())} for a, b, lab in ((1, 5, "1~5일"), (6, 10, "6~10일"), (11, 20, "11~20일"), (21, 40, "21~40일"), (41, 60, "41~60일"))],
                    "gain20_med": float(np.nanmedian(E["gain20"])), "move_end_med": float(np.nanmedian(E["move_end"])),
                    "first_ret_med": float(np.nanmedian(E["first_ret"])), "first_up10": float((E["first_ret"] >= 0.10).mean()), "first_up20": float((E["first_ret"] >= 0.20).mean()),
                    "first_gap5": float((E["first_gap"] >= 0.05).mean()), "first_vr3": float((E["first_vr"] >= 3).mean()), "first_down": float((E["first_ret"] < 0).mean()),
                    "up_days_med": float(np.nanmedian(E["up_days"])), "big_days_med": float(np.nanmedian(E["big_days"])), "big_days_share2": float((E["big_days"] >= 2).mean()),
                    "retrace_med": float(np.nanmedian(E["retrace60"])), "retrace50": float((E["retrace60"] <= -0.5).mean()), "retrace30": float((E["retrace60"] <= -0.3).mean()),
                    "r20": float(np.nanmean(E["r20"])), "r60": float(np.nanmean(E["r60"])), "r120": float(np.nanmean(E["r120"])), "r120_med": float(np.nanmedian(E["r120"])),
                    "r120_pos": float((E["r120"] > 0).mean()), "r120_keep_half": float((E["r120"] >= g / 2).mean()),
                    "base_r120": float(np.nanmean(B["r120"])), "base_first_up10": float((B["first_ret"] >= 0.10).mean())}
    rep["why"] = _why(E)
    # 급등 뒤 뒤따라 사면(정점 이후·+50% 확인 뒤)
    conf = E["gain"] >= GAIN
    rep["chase"] = {"after_peak_r60": float(np.nanmedian(E["retrace60"][conf])), "share_new_high_after": float(np.nanmean((E["gain60"] > E["gain"] * 1.1)[conf]))}
    rep["cache"] = _cache_lifts(set(codes))
    log(f"신호 배수·경로 완료 · {time.time() - t0:.0f}초")
    # 최근 급등(참고)·최근 '급등 전 조건' 많이 갖춘 종목은 예측이 아니므로 넣지 않음 — 최근 1년 급등 사례 상위
    recent = year >= int(E["date"].max()[:4]) - 1
    idx = np.flatnonzero(recent)
    idx = idx[np.argsort(-E["gain"][idx])][:30]
    rep["recent"] = [{"code": E["code"][i], "name": names.get(E["code"][i], E["code"][i]), "date": E["date"][i], "gain": float(E["gain"][i]), "peak_day": float(E["peak_day"][i]),
                      "first_ret": float(E["first_ret"][i]), "nh250": float(E["nh250"][i]), "ret20": float(E["ret20"][i]), "vr5": float(E["vr5"][i]), "range20": float(E["range20"][i]),
                      "r120": float(E["r120"][i]), "retrace": float(E["retrace60"][i])} for i in idx]
    rep["text"] = narrate(rep)
    rep["meta"]["elapsed_sec"] = round(time.time() - t0)
    BOOM_PATH.write_text(json.dumps(rep, ensure_ascii=False, default=st._js), encoding="utf-8")
    return rep


def narrate(rep):
    s, m = rep["stats"], rep["meta"]
    out = [f"20거래일 안에 +{m['gain'] * 100:.0f}%(고가) 오른 급등 {m['events']:,}건({m['stocks']:,}종목). 시작(저점) 뒤 상승이 끝날 때까지(고점 −20% 되돌림 전) 정점 상승폭 중앙 {s['gain_med'] * 100:+.0f}%(상위 25% {s['gain_q75'] * 100:+.0f}%), 정점까지 중앙 {s['peak_med']:.0f}일, 60일 안 +100% 도달 {s['gain100'] * 100:.0f}%."]
    out.append(f"첫날에 이미 +10% 이상 오른 경우 {s['first_up10'] * 100:.0f}%(+20%↑ {s['first_up20'] * 100:.0f}%, 아무 날 {s['base_first_up10'] * 100:.0f}%), 첫날 거래량 3배↑ {s['first_vr3'] * 100:.0f}% — 이런 급등은 첫날부터 터져 시작일 종가에 사기 어렵고, 나머지 {100 - s['first_up10'] * 100:.0f}%는 첫날이 +10% 미만이라 따라 들어갈 틈이 있었습니다.")
    top = sorted([r for r in rep["conditions"] if r["lift"] and r["n"] >= 100], key=lambda x: -x["lift"])[:5]
    low = sorted([r for r in rep["conditions"] if r["lift"] and r["n"] >= 100], key=lambda x: x["lift"])[:3]
    out.append("오르기 전 공통점(시작일 기준, 아무 날 대비 배수): " + ", ".join(f"{r['name']} {r['lift']:.1f}배({r['boom'] * 100:.0f}%)" for r in top) + ". 드문 것: " + ", ".join(f"{r['name']} {r['lift']:.1f}배" for r in low) + ".")
    top10 = sorted([r for r in rep["conditions"] if r.get("lift10") and r["n"] >= 100], key=lambda x: -x["lift10"])[:4]
    out.append("시작일이 저점이라 '하락 뒤'가 과장될 수 있어 10일 전 모습으로도 보면: " + ", ".join(f"{r['name']} {r['lift10']:.1f}배({r['pre10'] * 100:.0f}%)" for r in top10) + ".")
    p = {x["d"]: x for x in rep["paths"]}
    out.append(f"거래량 경로(직전 20일 평균 대비 중앙값): 급등 20일 전 {p[-20]['v']:.2f}배 → 5일 전 {p[-5]['v']:.2f}배 → 1일 전 {p[-1]['v']:.2f}배 → 시작일 {p[0]['v']:.2f}배 → 다음날 {p[1]['v']:.2f}배(아무 날은 {p[-1]['bv']:.2f}배 수준). 2배↑ 거래량이 나온 날 비율은 5일 전 {p[-5]['v2'] * 100:.0f}% → 1일 전 {p[-1]['v2'] * 100:.0f}%(아무 날 {p[-1]['bv2'] * 100:.0f}%).")
    w = sorted(rep["why"], key=lambda x: -x["share_first"])[:4]
    out.append("왜 올랐나(가격·거래량 맥락 추정, 우선순위 배정): " + ", ".join(f"{x['name']} {x['share_first'] * 100:.0f}%" for x in w) + ".")
    out.append(f"오른 뒤: 정점에서 60일 안 되돌림 중앙 {s['retrace_med'] * 100:+.0f}%(−50%↓ {s['retrace50'] * 100:.0f}%), 시작일 종가 대비 120일 뒤 평균 {s['r120'] * 100:+.0f}%·중앙 {s['r120_med'] * 100:+.0f}%, 상승폭의 절반 이상을 지킨 비율 {s['r120_keep_half'] * 100:.0f}%.")
    c = rep.get("cache")
    if c and c["signals"]:
        tops = c["signals"][:4]
        out.append(f"기법 신호(표본 캐시, 아직 안 오른 급등 직전 행 {c['n_pre']:,}개): 급등 직전에 많이 나온 신호 " + ", ".join(f"{x['name']} {x['lift_pre']:.1f}배" for x in tops) + ".")
    return out


if __name__ == "__main__":
    run()
