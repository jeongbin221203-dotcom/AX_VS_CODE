"""거래량 급등 뒤 큰 상승이 얼마 만에 나왔나 — 이벤트 스터디.

이벤트: 거래량이 직전 20일 평균의 3배 이상(3~5배 / 5~10배 / 10배 이상)으로 터진 날, 거래 가능 종목(20일 평균 거래대금 3억 원↑),
직전 20일 안에 3배 급증이 없었던 '첫 급증'만. 진입은 급증 다음날 시가.
측정: 이후 120거래일 동안 고가가 진입가 대비 +10·20·30·50·100%에 처음 닿은 날(급증 후 며칠), N일 안에 닿을 확률, 닿기 전 최대 하락,
−10%를 먼저 맞았는지, 정점까지 걸린 날. 기준은 같은 종목들에서 10봉 간격으로 뽑은 '아무 날'.
조건별(양봉·음봉, 위치, 직전 거래량 마름, 이미 급등했는지, 가격대, 시장, 갭, 시기)로 나눠 어떤 급증이 큰 상승으로 이어졌는지 본다.
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
from core import ai, db
from core import backtest as bt

SURGE_PATH = config.DATA_DIR / "surge.json"
LOOK = 120                                      # 이후 관찰 거래일
THRESH = (0.10, 0.20, 0.30, 0.50, 1.00)
HORIZ = (5, 10, 20, 60, 120)
MIN_RATIO = 3.0
RS_RATIO = 5.0                                  # '재폭등' = 거래량이 20일 평균의 5배 이상(3배는 전체 날의 약 7%라 거의 항상 나온다)
RS_BIG = 10.0
BASE_STEP = 10
LIQ_MIN = math.log10(ai.TRADABLE_VALUE)
EXTRA = list(ai.SIGNAL_NAMES) + ["aligned", "reversed", "cross20", "cross60", "cross112", "slope60", "acc_recent"]
FEAT = ["volratio", "ret1", "body", "nh250", "dist224", "dist20", "vdry10", "ret20", "atrp", "price_level", "mkt_r60", "upper", "liq"]


def _paths(o, h, l, c, e):
    """진입 인덱스 e(배열) → 이후 LOOK 거래일의 결과. (e+LOOK < n 인 행만 넘긴다)"""
    W = LOOK
    Wh = sliding_window_view(h, W)[e]            # (행, W): 진입일(1일째)부터 W일의 고가
    Wl = sliding_window_view(l, W)[e]
    Wc = sliding_window_view(c, W)[e]
    entry = o[e]
    out = {}
    for n in HORIZ:
        out[f"g{n}"] = Wh[:, :n].max(axis=1) / entry - 1                       # N일 안 최대 상승
        out[f"r{n}"] = Wc[:, n - 1] / entry - 1                                # N일째 종가 수익
    cummin = np.minimum.accumulate(Wl, axis=1)
    for x in THRESH:
        hit = Wh >= entry[:, None] * (1 + x)
        anyh = hit.any(axis=1)
        idx = hit.argmax(axis=1)
        out[f"d{int(x * 100)}"] = np.where(anyh, idx + 1, np.nan)               # 처음 닿은 날(급증 다음날이 1일째)
        if abs(x - 0.20) < 1e-9:
            out["dd20"] = np.where(anyh, cummin[np.arange(len(e)), idx] / entry - 1, np.nan)   # 닿기 전 최대 하락
    dn = Wl <= entry[:, None] * 0.90
    out["dn10"] = np.where(dn.any(axis=1), dn.argmax(axis=1) + 1, np.nan)       # −10%를 처음 맞은 날
    out["peak"] = Wh.argmax(axis=1) + 1                                          # 120일 안 고가 정점까지 걸린 날
    out["gap"] = entry / np.r_[c[0], c[:-1]][e - 1] - 1                          # 신호일 종가 대비 다음날 시가
    return out


def _resurge(fs, fs10, t, d20, peak):
    """급증 뒤 120일 동안 거래량 폭등(5배↑, 10배↑)이 또 있었나. 새 폭등 = 직전 5일 안에 같은 크기 폭등이 없던 날. 일수는 급증 다음날이 1일째."""
    W = sliding_window_view(fs, LOOK)[t + 1]
    ar = np.arange(LOOK)[None, :]
    anyr = W.any(axis=1)
    has = np.isfinite(d20)
    dd = np.where(has, d20, 0).astype(int)
    pk = peak.astype(int)
    W10 = sliding_window_view(fs10, LOOK)[t + 1]
    first = np.where(anyr, W.argmax(axis=1) + 1, np.nan)
    return {"rs_n": W.sum(axis=1).astype(float), "rs_first": first,
            "rs_before20": np.where(has, (W & (ar < (dd - 1)[:, None])).sum(axis=1), np.nan),
            "rs_trig20": np.where(has, (W & (ar >= (dd - 6)[:, None]) & (ar <= (dd - 1)[:, None])).any(axis=1).astype(float), np.nan),
            "rs_peak": (W & (ar >= (pk - 6)[:, None]) & (ar <= (pk - 1)[:, None])).any(axis=1).astype(float),
            "rs10_n": W10.sum(axis=1).astype(float)}


def _ma_extra(c, t):
    """급증일 종가가 이평선(5·20·60·112·224)을 한꺼번에 몇 개 올라섰나, 위에 있는 이평선 수, 이평선 밀집도."""
    cs = pd.Series(c)
    mas = [cs.rolling(p, min_periods=p).mean().to_numpy() for p in (5, 20, 60, 112, 224)]
    prev_c = np.r_[np.nan, c[:-1]]
    with np.errstate(invalid="ignore"):
        cross = sum(((c > m) & (prev_c <= np.r_[np.nan, m[:-1]])).astype(float) for m in mas)
        above = sum((c > m).astype(float) for m in mas)
        tight = (np.maximum.reduce(mas[1:4]) - np.minimum.reduce(mas[1:4])) / c
    return {"ma_cross_n": cross[t], "ma_above_n": above[t], "ma_tight": tight[t]}


def _work(code):
    try:
        df = ai.service.load_prices(code)
        if len(df) < 400:
            return None
        X = ai.compute_features(df, ai.MKT_DF, with_signals=True)
        ix = {n: i for i, n in enumerate(ai.NAMES)}
        o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        n = len(df)
        vr = X[:, ix["volratio"]]
        liq = X[:, ix["liq"]]
        flag = (vr >= MIN_RATIO) & np.isfinite(vr)
        prior = pd.Series(flag.astype(float)).rolling(20, min_periods=1).sum().shift().fillna(0).to_numpy()
        base_ok = (liq >= LIQ_MIN) & (np.arange(n) >= 250)
        dates = df.index.strftime("%Y-%m-%d").to_numpy()
        t_all = np.arange(n)
        ev_t = t_all[flag & (prior == 0) & base_ok & (t_all + 1 + LOOK < n)]
        recent_t = t_all[flag & base_ok & (t_all >= n - 6)]
        bs_t = t_all[base_ok & (t_all % BASE_STEP == 0) & (t_all + 1 + LOOK < n)]
        def _starts(fl):
            pv = pd.Series(fl.astype(float)).rolling(5, min_periods=1).sum().shift().fillna(0).to_numpy()
            return fl & (pv == 0)
        fs = _starts((vr >= RS_RATIO) & np.isfinite(vr))
        fs10 = _starts((vr >= RS_BIG) & np.isfinite(vr))
        res = {"code": code}
        for name, tt in (("ev", ev_t), ("bs", bs_t)):
            if len(tt) == 0:
                res[name] = None
                continue
            p = _paths(o, h, l, c, tt + 1)
            p["date"] = dates[tt]
            for f in FEAT + ([k for k in EXTRA if k not in FEAT] if name == "ev" else []):
                p[f] = X[tt, ix[f]].astype(float)
            p.update(_resurge(fs, fs10, tt, p["d20"], p["peak"]))
            if name == "ev":
                p.update(_ma_extra(c, tt))
            p["code"] = np.full(len(tt), code)
            res[name] = p
        rec = []
        for t in recent_t:
            rec.append({"code": code, "date": dates[t], "close": float(c[t]), **{f: float(X[t, ix[f]]) for f in FEAT}})
        res["recent"] = rec
        return res
    except Exception:
        return None


def _cat(parts, key):
    arrs = [p[key] for p in parts if p.get(key) is not None]
    if not arrs:
        return pd.DataFrame()
    cols = arrs[0].keys()
    return pd.DataFrame({k: np.concatenate([a[k] for a in arrs]) for k in cols})


def _summary(D: pd.DataFrame):
    if len(D) < 50:
        return None
    s = {"n": int(len(D)), "hit": {}, "days": {}, "r20": float(D["r20"].mean()), "r60": float(D["r60"].mean()),
         "r120": float(D["r120"].mean()), "median_r20": float(D["r20"].median()), "peak_median": float(D["peak"].median())}
    for x in THRESH:
        k = f"{int(x * 100)}"
        s["hit"][k] = {str(n): float((D[f"d{k}"] <= n).mean()) for n in HORIZ}
        d = D[f"d{k}"].dropna()
        s["days"][k] = {"share": float(len(d) / len(D)), "median": float(d.median()) if len(d) else None,
                        "q25": float(d.quantile(0.25)) if len(d) else None, "q75": float(d.quantile(0.75)) if len(d) else None}
    h20 = D["d20"]
    dn = D["dn10"]
    s["up20_before_dn10"] = float((h20.notna() & (dn.isna() | (h20 < dn))).mean())
    s["dn10_first"] = float((dn.notna() & (h20.isna() | (dn <= h20))).mean())
    s["dd_before_20"] = float(D["dd20"].median()) if D["dd20"].notna().any() else None
    return s


def _breakdown(D: pd.DataFrame):
    """조건별(3배↑ 급증 전체 기준): n, 20일 안 +20%, 60일 안 +30%, 120일 안 +50%, +20%까지 중앙 며칠, −10% 먼저 맞은 비율, 20·60일 평균 수익."""
    specs = {
        "급증한 날의 모습": [("양봉 +10% 이상 (장대 급등)", D["ret1"] >= 0.10), ("양봉 +3~+10%", (D["ret1"] >= 0.03) & (D["ret1"] < 0.10)),
                           ("보합권 (−3~+3%)", (D["ret1"] > -0.03) & (D["ret1"] < 0.03)), ("음봉 −3% 이하 (물량 분출)", D["ret1"] <= -0.03)],
        "급증 직전 거래량": [("직전 10일 매우 건조 (최소 0.3배 미만)", D["vdry10"] < 0.3), ("건조 (0.3~0.6배)", (D["vdry10"] >= 0.3) & (D["vdry10"] < 0.6)),
                           ("평소 수준 (0.6배 이상)", D["vdry10"] >= 0.6)],
        "주가 위치 (52주 고점 대비)": [("고점권 (−10% 이내)", D["nh250"] >= -0.10), ("−10~−30%", (D["nh250"] < -0.10) & (D["nh250"] >= -0.30)),
                                  ("−30~−50%", (D["nh250"] < -0.30) & (D["nh250"] >= -0.50)), ("바닥권 (−50% 이하)", D["nh250"] < -0.50)],
        "224일선 대비": [("224일선 +20% 이상 위", D["dist224"] >= 0.20), ("224일선 ±20% 이내", (D["dist224"] > -0.20) & (D["dist224"] < 0.20)),
                       ("224일선 −20% 이하 아래", D["dist224"] <= -0.20)],
        "급증 전 20일 등락": [("이미 +30% 이상 급등", D["ret20"] >= 0.30), ("0~+30%", (D["ret20"] >= 0) & (D["ret20"] < 0.30)),
                          ("−20~0%", (D["ret20"] > -0.20) & (D["ret20"] < 0)), ("−20% 이하 급락", D["ret20"] <= -0.20)],
        "거래량 배수": [("3~5배", (D["volratio"] >= 3) & (D["volratio"] < 5)), ("5~10배", (D["volratio"] >= 5) & (D["volratio"] < 10)),
                    ("10배 이상", D["volratio"] >= 10)],
        "주가 수준": [("1,000원 미만", D["price_level"] < 3), ("1,000~5,000원", (D["price_level"] >= 3) & (D["price_level"] < math.log10(5000))),
                   ("5,000~20,000원", (D["price_level"] >= math.log10(5000)) & (D["price_level"] < math.log10(20000))),
                   ("20,000원 이상", D["price_level"] >= math.log10(20000))],
        "시장 (60일 수익률)": [("시장 급락 뒤 (−10% 이하)", D["mkt_r60"] <= -0.10), ("약세 (−10~0%)", (D["mkt_r60"] > -0.10) & (D["mkt_r60"] <= 0)),
                          ("완만한 상승 (0~+10%)", (D["mkt_r60"] > 0) & (D["mkt_r60"] < 0.10)), ("강한 상승 (+10% 이상)", D["mkt_r60"] >= 0.10)],
        "다음날 갭 (시가 − 급증일 종가)": [("갭하락", D["gap"] < 0), ("0~+3%", (D["gap"] >= 0) & (D["gap"] < 0.03)),
                                   ("+3~+7%", (D["gap"] >= 0.03) & (D["gap"] < 0.07)), ("+7% 이상 (큰 갭상승)", D["gap"] >= 0.07)],
        "시기": [("2018년까지", D["date"] <= "2018-12-31"), ("2019~2021", (D["date"] > "2018-12-31") & (D["date"] <= "2021-12-31")),
               ("2022년 이후", D["date"] > "2021-12-31")],
    }
    out = []
    for group, items in specs.items():
        rows = []
        for name, m in items:
            m = np.asarray(m)
            if m.sum() < 300:
                continue
            d = D[m]
            d20 = d["d20"]
            dn = d["dn10"]
            rows.append({"name": name, "n": int(m.sum()), "p20_in20": float((d["d20"] <= 20).mean()), "p30_in60": float((d["d30"] <= 60).mean()),
                         "p50_in120": float((d["d50"] <= 120).mean()), "days20": float(d20.dropna().median()) if d20.notna().any() else None,
                         "dn10_first": float((dn.notna() & (d20.isna() | (dn <= d20))).mean()), "r20": float(d["r20"].mean()), "r60": float(d["r60"].mean())})
        out.append({"group": group, "rows": rows})
    return out


def _time_hist(D: pd.DataFrame):
    bins = [(1, 3), (4, 5), (6, 10), (11, 20), (21, 40), (41, 60), (61, 120)]
    out = []
    for x in (20, 50):
        d = D[f"d{x}"].dropna()
        out.append({"x": x, "hit_share": float(len(d) / len(D)), "bins": [{"label": f"{a}~{b}일", "share_hits": float(((d >= a) & (d <= b)).mean()) if len(d) else 0.0,
                                                                      "share_all": float(((D[f"d{x}"] >= a) & (D[f"d{x}"] <= b)).mean())} for a, b in bins]})
    return out


def _curve(D: pd.DataFrame, x=20):
    days = [1, 2, 3, 5, 7, 10, 15, 20, 30, 40, 60, 90, 120]
    return [{"day": n, "p": float((D[f"d{x}"] <= n).mean())} for n in days]


def _q(x, q):
    x = x.dropna()
    return float(x.quantile(q)) if len(x) else None


RS_BINS = ((1, 10), (11, 20), (21, 40), (41, 80), (81, 120))


def _speed(E: pd.DataFrame, B: pd.DataFrame | None = None):
    """+20%에 닿는 데 걸린 기간별(가장 빠름 ~ 가장 느림 · 못 닿음)로 상승폭과 재급증(거래량 폭등이 또 있었나)을 비교."""
    d = E["d20"]
    groups = [("5일 안 (가장 빠름)", d <= 5), ("6~20일", (d >= 6) & (d <= 20)), ("21~60일", (d >= 21) & (d <= 60)),
              ("61~120일 (가장 느림)", d >= 61), ("120일 안 +20% 못 닿음", d.isna())]
    out = []
    for name, m in groups:
        m = np.asarray(m)
        if m.sum() < 100:
            continue
        g = E[m]
        hit = name != groups[-1][0]
        row = {"name": name, "n": int(m.sum()), "share": float(m.mean()),
               "gain_med": float(g["g120"].median()), "gain_q25": _q(g["g120"], 0.25), "gain_q75": _q(g["g120"], 0.75),
               "p30": float((g["g120"] >= 0.3).mean()), "p50": float((g["g120"] >= 0.5).mean()), "p100": float((g["g120"] >= 1.0).mean()),
               "peak_med": float(g["peak"].median()), "dn10_first": float((g["dn10"].notna() & (g["d20"].isna() | (g["dn10"] <= g["d20"]))).mean()),
               "rs_any": float((g["rs_n"] > 0).mean()), "rs_n": float(g["rs_n"].mean()), "rs_first": float(g["rs_first"].median()) if g["rs_first"].notna().any() else None,
               "rs_peak": float(g["rs_peak"].mean()), "r120": float(g["r120"].mean()), "rs10_any": float((g["rs10_n"] > 0).mean()),
               "rs_bins": [float(((g["rs_first"] >= a) & (g["rs_first"] <= b)).mean()) for a, b in RS_BINS]}
        if hit:
            row["rs_before"] = float((g["rs_before20"] > 0).mean())
            row["rs_trig"] = float(g["rs_trig20"].mean())
            row["rs_trig_exp"] = float(min(g["rs_n"].mean() * 6 / LOOK, 1.0))          # 재폭등이 아무 때나 났다면 닿기 직전 6일 안에 있을 비율(우연 기대치)
            row["dd"] = float(g["dd20"].median()) if g["dd20"].notna().any() else None
        out.append(row)
    if B is not None and len(B):
        out.append({"name": "기준: 아무 날 (비교용)", "n": int(len(B)), "baseline": True, "rs_any": float((B["rs_n"] > 0).mean()), "rs_n": float(B["rs_n"].mean()),
                    "rs_first": float(B["rs_first"].median()) if B["rs_first"].notna().any() else None, "rs10_any": float((B["rs10_n"] > 0).mean()),
                    "rs_bins": [float(((B["rs_first"] >= a) & (B["rs_first"] <= b)).mean()) for a, b in RS_BINS]})
    return out


def _size(E: pd.DataFrame):
    """120일 안 최고 상승폭별: 얼마나 걸렸나, 거래량 폭등이 또 있었나."""
    g = E["g120"]
    groups = [("+100% 이상", g >= 1.0), ("+50~100%", (g >= 0.5) & (g < 1.0)), ("+30~50%", (g >= 0.3) & (g < 0.5)),
              ("+20~30%", (g >= 0.2) & (g < 0.3)), ("+20% 미만", g < 0.2)]
    out = []
    for name, m in groups:
        m = np.asarray(m)
        if m.sum() < 100:
            continue
        x = E[m]
        out.append({"name": name, "n": int(m.sum()), "share": float(m.mean()),
                    "d20_med": float(x["d20"].median()) if x["d20"].notna().any() else None,
                    "d20_q25": _q(x["d20"], 0.25), "d20_q75": _q(x["d20"], 0.75),
                    "d50_med": float(x["d50"].median()) if x["d50"].notna().any() else None,
                    "peak_med": float(x["peak"].median()), "peak_q25": _q(x["peak"], 0.25), "peak_q75": _q(x["peak"], 0.75),
                    "rs_any": float((x["rs_n"] > 0).mean()), "rs_n": float(x["rs_n"].mean()), "rs_peak": float(x["rs_peak"].mean()),
                    "ratio_med": float(x["volratio"].median()), "ret1_med": float(x["ret1"].median())})
    return out


EX_CAP = 10.0       # 예시에서 +1,000% 넘는 값은 액면분할·데이터 오류 가능성이 있어 뺀다


def _examples(E: pd.DataFrame, names, top=12):
    def rows(m, order):
        x = E[np.asarray(m) & (E["g120"] <= EX_CAP).to_numpy()].sort_values(order[0], ascending=order[1]).head(top)
        return [{"name": names.get(r.code, r.code), "code": r.code, "date": r.date, "volratio": float(r.volratio), "d20": None if np.isnan(r.d20) else float(r.d20),
                 "d50": None if np.isnan(r.d50) else float(r.d50), "gain": float(r.g120), "peak": float(r.peak), "rs_n": float(r.rs_n),
                 "rs_first": None if np.isnan(r.rs_first) else float(r.rs_first), "ret1": float(r.ret1), "nh250": float(r.nh250)} for r in x.itertuples()]
    fast = E["d50"] <= 5
    slow = E["d20"] >= 90
    return {"fast": rows(fast, ("g120", False)), "slow": rows(slow, ("g120", False)), "fast_n": int(fast.sum()), "slow_n": int(slow.sum())}


def _stat_row(name, d, base20=None):
    d20, dn = d["d20"], d["dn10"]
    r = {"name": name, "n": int(len(d)), "p20_in20": float((d20 <= 20).mean()), "p30_in60": float((d["d30"] <= 60).mean()),
         "p50_in120": float((d["d50"] <= 120).mean()), "days20": float(d20.dropna().median()) if d20.notna().any() else None,
         "dn10_first": float((dn.notna() & (d20.isna() | (dn <= d20))).mean()), "r20": float(d["r20"].mean()), "r60": float(d["r60"].mean()),
         "rs_any": float((d["rs_n"] > 0).mean())}
    return r


def _conditions(E: pd.DataFrame):
    """급증일(+앞 3봉) 기법 신호·이평선 상황 조건들(이름, 불리언 배열)."""
    conds = []
    for j, lab in enumerate(ai.SIGNAL_LABELS):
        conds.append((f"{lab}", (E[f"sig{j:02d}"] <= 3).to_numpy(), "기법 신호 (급증일 포함 최근 3봉 안)"))
    conds += [("정배열 (5>20>60>112)", (E["aligned"] == 1).to_numpy(), "이평선 상태"), ("역배열 (5<20<60<112)", (E["reversed"] == 1).to_numpy(), "이평선 상태"),
              ("급증일 이평선 2개 이상 한꺼번에 상향 돌파", (E["ma_cross_n"] >= 2).to_numpy(), "이평선 상태"),
              ("급증일 이평선 1개 상향 돌파", (E["ma_cross_n"] == 1).to_numpy(), "이평선 상태"),
              ("5·20·60·112·224 모두 위", (E["ma_above_n"] == 5).to_numpy(), "이평선 상태"), ("5·20·60·112·224 모두 아래", (E["ma_above_n"] == 0).to_numpy(), "이평선 상태"),
              ("20·60·112일선 밀집 (폭 5% 이내)", (E["ma_tight"] < 0.05).to_numpy(), "이평선 상태"),
              ("20일선 상향 돌파 3봉 안", (E["cross20"] <= 3).to_numpy(), "이평선 상태"), ("60일선 상향 돌파 3봉 안", (E["cross60"] <= 3).to_numpy(), "이평선 상태"),
              ("112일선 상향 돌파 3봉 안", (E["cross112"] <= 3).to_numpy(), "이평선 상태"),
              ("60일선 기울기 상승", (E["slope60"] > 0.02).to_numpy(), "이평선 상태"), ("최근 60봉 안 매집봉", (E["acc_recent"] == 1).to_numpy(), "이평선 상태")]
    return conds


def _signals(E: pd.DataFrame):
    conds = _conditions(E)
    allp = _stat_row("전체", E)
    rows = []
    for name, m, grp in conds:
        if m.sum() < 300:
            continue
        r = _stat_row(name, E[m])
        r.update({"group": grp, "share": float(m.mean()), "lift20": r["p20_in20"] - allp["p20_in20"], "lift50": r["p50_in120"] - allp["p50_in120"]})
        rows.append(r)
    # 겹친 신호 개수
    S = np.column_stack([(E[f"sig{j:02d}"] <= 3).to_numpy() for j in range(len(ai.SIGNAL_LABELS))])
    cnt, dcnt = S.sum(axis=1), S[:, :ai.N_DANTE].sum(axis=1)
    stack = []
    for label, arr, bins in (("전체 기법 신호", cnt, [(0, 0, "0개"), (1, 1, "1개"), (2, 2, "2개"), (3, 3, "3개"), (4, 99, "4개 이상")]),
                             ("단테 기법 신호(앞 11개)", dcnt, [(0, 0, "0개"), (1, 1, "1개"), (2, 99, "2개 이상")])):
        for a, b, nm in bins:
            m = (arr >= a) & (arr <= b)
            if m.sum() >= 300:
                r = _stat_row(f"{label} {nm} 겹침", E[m])
                r["share"] = float(m.mean())
                stack.append(r)
    # 두 조건 겹침 — 시기별로 둘 다 좋았던 것만 '안정적' 표시
    M = np.column_stack([m for _, m, _ in conds])
    names = [n for n, _, _ in conds]
    h20 = (E["d20"] <= 20).to_numpy()
    h50 = (E["d50"] <= 120).to_numpy()
    early = (E["date"] <= "2018-12-31").to_numpy()
    base_e, base_l = h20[early].mean(), h20[~early].mean()
    pairs = []
    for i in range(M.shape[1]):
        if M[:, i].sum() < 300:
            continue
        for j in range(i + 1, M.shape[1]):
            if conds[i][2] == conds[j][2] == "이평선 상태" and ("정배열" in names[i] or "역배열" in names[i]) and ("정배열" in names[j] or "역배열" in names[j]):
                continue
            m = M[:, i] & M[:, j]
            n = int(m.sum())
            if n < 300:
                continue
            me, ml = m & early, m & ~early
            pairs.append({"name": f"{names[i]} + {names[j]}", "n": n, "p20_in20": float(h20[m].mean()), "p50_in120": float(h50[m].mean()),
                          "days20": float(E["d20"][m].median()) if E["d20"][m].notna().any() else None,
                          "dn10_first": float((E["dn10"][m].notna() & (E["d20"][m].isna() | (E["dn10"][m] <= E["d20"][m]))).mean()),
                          "r20": float(E["r20"][m].mean()), "n_early": int(me.sum()), "n_late": int(ml.sum()),
                          "p20_early": float(h20[me].mean()) if me.sum() >= 100 else None, "p20_late": float(h20[ml].mean()) if ml.sum() >= 100 else None,
                          "stable": bool(me.sum() >= 100 and ml.sum() >= 100 and h20[me].mean() > base_e + 0.03 and h20[ml].mean() > base_l + 0.03)})
    pairs.sort(key=lambda x: -x["p20_in20"])
    best = pairs[:12]
    stable = [x for x in pairs if x["stable"]][:12]
    worst = pairs[::-1][:8]
    return {"singles": sorted(rows, key=lambda x: -x["lift20"]), "stack": stack, "pairs_best": best, "pairs_stable": stable, "pairs_worst": worst,
            "pair_count": len(pairs), "base_early": float(base_e), "base_late": float(base_l)}


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
    parts, recent = [], []
    with ProcessPoolExecutor(max_workers=workers, initializer=ai._init_worker, initargs=(bt.MKT_PATH,)) as ex:
        for i, out in enumerate(ex.map(_work, codes, chunksize=8), 1):
            if out:
                parts.append(out)
                recent += out["recent"]
            if i % 400 == 0:
                log(f"[종목 {i}/{len(codes)}] 급증 {sum(len(p['ev']['date']) for p in parts if p.get('ev'))}건 · {time.time() - t0:.0f}초")
    E = _cat(parts, "ev")
    B = _cat(parts, "bs")
    log(f"급증 이벤트 {len(E):,}건 · 기준 표본 {len(B):,}건 · {time.time() - t0:.0f}초")
    E = E.replace([np.inf, -np.inf], np.nan)
    rep = {"meta": {"generated": datetime.now().isoformat(timespec="seconds"), "stocks": len(codes), "events": int(len(E)), "baseline": int(len(B)),
                    "min_ratio": MIN_RATIO, "look": LOOK, "liq_min": ai.TRADABLE_VALUE, "date_min": str(E["date"].min()), "date_max": str(E["date"].max())}}
    levels = [("거래량 3배 이상 (전체)", E["volratio"] >= 3), ("3~5배", (E["volratio"] >= 3) & (E["volratio"] < 5)),
              ("5~10배", (E["volratio"] >= 5) & (E["volratio"] < 10)), ("10배 이상", E["volratio"] >= 10)]
    rep["levels"] = [{"name": n, **_summary(E[m])} for n, m in levels if m.sum() >= 50]
    rep["baseline"] = _summary(B)
    rep["time_hist"] = _time_hist(E)
    rep["curve"] = {"event": _curve(E, 20), "baseline": _curve(B, 20), "event50": _curve(E, 50), "baseline50": _curve(B, 50)}
    rep["breakdown"] = _breakdown(E)
    # 큰 상승으로 이어진 급증의 공통점: +50%(120일 안) 도달 vs 미도달 특징 평균
    big = E["d50"].notna()
    rep["profile"] = [{"name": f, "big": float(E.loc[big, f].median()), "rest": float(E.loc[~big, f].median())} for f in FEAT if f in E]
    # 최근 급증 종목과 비슷한 과거 급증의 결과
    rep["recent"] = _recent(recent, E, names)
    rep["speed"] = _speed(E, B)
    rep["size"] = _size(E)
    rep["examples"] = _examples(E, names)
    rep["signals"] = _signals(E)
    rep["text"] = narrate(rep)
    rep["meta"]["elapsed_sec"] = round(time.time() - t0)
    SURGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SURGE_PATH.write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
    return rep


def _bucket3(r):
    candle = 0 if r["ret1"] >= 0.03 else 1 if r["ret1"] > -0.03 else 2
    pos = 0 if r["nh250"] >= -0.10 else 1 if r["nh250"] >= -0.50 else 2
    quiet = 0 if r["vdry10"] < 0.6 else 1
    return candle, pos, quiet


def _recent(recent, E: pd.DataFrame, names, top=25):
    """최근 6거래일 안 3배↑ 급증 종목 + 같은 (급증일 모습·위치·직전 거래량) 그룹의 과거 결과."""
    if not recent:
        return []
    cand = E["ret1"].to_numpy()
    cat = np.where(cand >= 0.03, 0, np.where(cand > -0.03, 1, 2))
    pos = np.where(E["nh250"] >= -0.10, 0, np.where(E["nh250"] >= -0.50, 1, 2))
    quiet = np.where(E["vdry10"] < 0.6, 0, 1)
    key = pd.Series(cat * 100 + pos * 10 + quiet)
    stats = {}
    for k, idx in key.groupby(key).groups.items():
        d = E.iloc[list(idx)]
        if len(d) >= 300:
            stats[k] = {"n": int(len(d)), "p20_in20": float((d["d20"] <= 20).mean()), "p50_in120": float((d["d50"] <= 120).mean()),
                        "days20": float(d["d20"].dropna().median()) if d["d20"].notna().any() else None,
                        "dn10_first": float((d["dn10"].notna() & (d["d20"].isna() | (d["dn10"] <= d["d20"]))).mean()), "r20": float(d["r20"].mean())}
    labels = {0: "양봉 +3%↑", 1: "보합권", 2: "음봉"}
    plabels = {0: "고점권", 1: "중간(−10~−50%)", 2: "바닥권(−50%↓)"}
    out = []
    for r in recent:
        if not all(np.isfinite([r["ret1"], r["nh250"], r["vdry10"], r["volratio"]])):
            continue
        c, p, q = _bucket3(r)
        st = stats.get(c * 100 + p * 10 + q)
        if not st:
            continue
        out.append({"code": r["code"], "name": names.get(r["code"], r["code"]), "date": r["date"], "close": r["close"], "volratio": r["volratio"], "ret1": r["ret1"],
                    "nh250": r["nh250"], "vdry10": r["vdry10"], "group": f"{labels[c]} · {plabels[p]} · {'거래량 건조 후' if q == 0 else '평소 거래량 후'}", **st})
    out.sort(key=lambda x: (-x["p20_in20"], -x["volratio"]))
    return out[:top]


def narrate(rep: dict) -> list[str]:
    out = []
    lv = {x["name"]: x for x in rep["levels"]}
    a, b = lv.get("거래량 3배 이상 (전체)"), rep["baseline"]
    if a and b:
        out.append(f"거래량 3배↑ 급증 {a['n']:,}건 중 20거래일 안에 +20%에 닿은 비율은 {a['hit']['20']['20'] * 100:.0f}%로, 아무 날({b['hit']['20']['20'] * 100:.0f}%)의 {a['hit']['20']['20'] / max(b['hit']['20']['20'], 1e-9):.1f}배입니다.")
        d = a["days"]["20"]
        if d["median"]:
            out.append(f"+20%에 닿은 경우만 보면 급증 다음날부터 중앙 {d['median']:.0f}일(빠른 쪽 25% {d['q25']:.0f}일 · 느린 쪽 25% {d['q75']:.0f}일) 만에 닿았습니다.")
        out.append(f"하지만 +20%에 닿기 전에 −10%를 먼저 맞은 비율이 {a['dn10_first'] * 100:.0f}%({'아무 날 ' + format(b['dn10_first'] * 100, '.0f') + '%'})이고, 20일 평균 수익은 {a['r20'] * 100:+.1f}%({b['r20'] * 100:+.1f}%)입니다.")
    sp = {x["name"]: x for x in rep.get("speed", [])}
    fast, slow, miss = sp.get("5일 안 (가장 빠름)"), sp.get("61~120일 (가장 느림)"), sp.get("120일 안 +20% 못 닿음")
    if fast and slow:
        out.append(f"+20%에 5일 안에 닿은 급증({fast['share'] * 100:.0f}%)은 120일 안 최고 상승폭 중앙값 {fast['gain_med'] * 100:+.0f}%(+100%↑ {fast['p100'] * 100:.0f}%), "
                   f"61~120일에 늦게 닿은 급증({slow['share'] * 100:.0f}%)은 {slow['gain_med'] * 100:+.0f}%(+100%↑ {slow['p100'] * 100:.0f}%)입니다.")
        out.append(f"거래량 5배↑ 폭등이 또 나온 비율은 빠른 쪽 {fast['rs_any'] * 100:.0f}%, 느린 쪽 {slow['rs_any'] * 100:.0f}%" + (f", 못 닿은 쪽 {miss['rs_any'] * 100:.0f}%" if miss else "") + (f", 아무 날 {sp['기준: 아무 날 (비교용)']['rs_any'] * 100:.0f}%" if "기준: 아무 날 (비교용)" in sp else "")
                   + f". 느린 쪽은 +20%에 닿기 직전 5일 안에 재급증이 있었던 비율이 {slow.get('rs_trig', 0) * 100:.0f}%(빠른 쪽 {fast.get('rs_trig', 0) * 100:.0f}%)입니다.")
    sg = rep.get("signals")
    if sg and sg["singles"]:
        top = sg["singles"][0]
        out.append(f"겹치는 신호 중 20일 안 +20% 확률을 가장 올린 단독 조건은 '{top['name']}'({top['p20_in20'] * 100:.0f}%, 표본 {top['n']:,}건)입니다.")
        if sg["pairs_stable"]:
            b = sg["pairs_stable"][0]
            out.append(f"두 조건 겹침 중 앞·뒤 시기 모두 평균보다 높았던 최상위는 '{b['name']}'(+20% {b['p20_in20'] * 100:.0f}%, 표본 {b['n']:,}건)입니다.")
        else:
            out.append("두 조건 겹침 가운데 앞·뒤 시기 모두 평균보다 확실히 높았던 조합은 없었습니다.")
    return out


if __name__ == "__main__":
    run()
