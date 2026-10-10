"""기법 신호가 '성공한 이유'와 '실패한 이유' 찾기.

성공 = 신호 다음날 시가에 사서 20봉 뒤 종가 수익이 같은 날 시장 평균(전 종목 평균)보다 높았던 신호.
신호 시점(종가)에 알 수 있던 특징 20개를 성공/실패 그룹에서 비교하고(표준화 평균 차이 d, 분위별 성공률),
기법 손절로 손실이 난 경우를 원인별로 나눈다(시장 동반 하락·진입 직후 이탈·손절 뒤 회복·서서히 하락).
※ 인과가 아니라 '같이 나타난 특징'이다. 표본이 크지만 같은 날 신호가 몰려 있어 해석은 보수적으로.
"""
import math

import numpy as np
import pandas as pd

import config
from core import backtest as bt

WHY_PATH = config.DATA_DIR / "why.json"
BASE = bt.BASE_LABEL
COLS = ["code", "date", "r20", "tgt3", "n_ret", "n_why", "n_held", "n_whip"] + bt.FEATURES
FEATURE_LABELS = {
    "dist20": "20일선 대비 위치", "dist60": "60일선 대비 위치", "dist224": "224일선 대비 위치",
    "slope60": "60일선 기울기(20봉)", "slope224": "224일선 기울기(40봉)", "dd250": "52주 고점 대비 낙폭",
    "run20": "직전 20일 상승률", "atrp": "변동성(ATR÷종가)", "volratio": "거래량 배수(20일 평균 대비)",
    "liq": "거래대금 규모(로그)", "body": "신호봉 몸통", "upper": "신호봉 윗꼬리 비율", "gap0": "신호일 시가 갭",
    "base_age": "60일 저점 이후 경과 봉", "hist_bars": "상장 후 경과 봉", "acc_recent": "최근 60봉 매집봉 있음",
    "mkt_r20": "시장 직전 20일 수익률", "mkt_r60": "시장 직전 60일 수익률", "mkt_dd250": "시장 52주 고점 대비",
    "mkt_vol20": "시장 변동성(20일)",
}
PCT_FEATURES = {"dist20", "dist60", "dist224", "slope60", "slope224", "dd250", "run20", "atrp", "body", "gap0",
                "mkt_r20", "mkt_r60", "mkt_dd250", "mkt_vol20"}
MIN_ROWS = 300


def to_frame(rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=COLS)


def save_rows(rows_by_label: dict):
    frames = []
    for label, rows in rows_by_label.items():
        if rows:
            f = to_frame(rows)
            f.insert(0, "label", label)
            frames.append(f)
    if frames:
        pd.concat(frames).to_pickle(bt.ROWS_PATH)


def _nan(x):
    return None if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else x


def _cohen_d(a: np.ndarray, b: np.ndarray):
    a, b = a[~np.isnan(a)], b[~np.isnan(b)]
    if len(a) < 30 or len(b) < 30:
        return None
    sp = math.sqrt(((len(a) - 1) * a.var(ddof=1) + (len(b) - 1) * b.var(ddof=1)) / (len(a) + len(b) - 2))
    return float((a.mean() - b.mean()) / sp) if sp > 0 else None


def _quintiles(x: pd.Series, succ: pd.Series):
    ok = x.notna()
    if ok.sum() < 100 or x[ok].nunique() < 5:
        return None
    q = pd.qcut(x[ok], 5, labels=False, duplicates="drop")
    g = succ[ok].groupby(q)
    return [{"rate": float(r), "n": int(n), "lo": float(x[ok][q == k].min()), "hi": float(x[ok][q == k].max())}
            for k, (r, n) in enumerate(zip(g.mean(), g.size()))]


def _feature_table(df: pd.DataFrame, base: pd.DataFrame | None):
    succ = df["succ"]
    out = []
    for f in bt.FEATURES:
        d = _cohen_d(df.loc[succ, f].to_numpy(float), df.loc[~succ, f].to_numpy(float))
        if d is None:
            continue
        out.append({"name": f, "label": FEATURE_LABELS[f], "pct": f in PCT_FEATURES, "d": d,
                    "succ_mean": _nan(float(df.loc[succ, f].mean())), "fail_mean": _nan(float(df.loc[~succ, f].mean())),
                    "all_mean": _nan(float(df[f].mean())), "base_mean": _nan(float(base[f].mean())) if base is not None else None,
                    "q": _quintiles(df[f], succ)})
    out.sort(key=lambda r: -abs(r["d"]))
    return out


def _fail_modes(df: pd.DataFrame, mkfwd: pd.Series):
    """기법 손절만 걸고 보유했을 때(tech|none)의 결과를 원인별로."""
    d = df[df["n_why"].notna()].copy()
    if len(d) < MIN_ROWS:
        return None
    mdown = d["date"].map(mkfwd) <= -0.05
    stop = d["n_why"] == 1
    fast = d["n_held"] <= 3
    whip = d["n_whip"] == 1
    t = d["n_why"] == 0
    cats = {
        "stop_market": stop & mdown, "stop_fast": stop & ~mdown & fast,
        "stop_whip": stop & ~mdown & ~fast & whip, "stop_slow": stop & ~mdown & ~fast & ~whip,
        "time_loss": t & (d["n_ret"] < 0), "time_win": t & (d["n_ret"] >= 0),
    }
    n = len(d)
    out = {k: float(v.sum() / n) for k, v in cats.items()}
    out["n"] = n
    out["avg_loss_stop"] = _nan(float(d.loc[stop, "n_ret"].mean())) if stop.any() else None
    out["avg_hold_stop"] = _nan(float(d.loc[stop, "n_held"].mean())) if stop.any() else None
    return out


REGIMES = [("시장 하락장 (60일 −10% 이하)", -9, -0.10), ("약세 (−10~0%)", -0.10, 0.0), ("완만한 상승 (0~10%)", 0.0, 0.10), ("강한 상승 (+10% 이상)", 0.10, 9)]


def _regimes(df: pd.DataFrame):
    out = []
    for name, lo, hi in REGIMES:
        m = (df["mkt_r60"] > lo) & (df["mkt_r60"] <= hi) if lo > -9 else (df["mkt_r60"] <= hi)
        if hi == 9:
            m = df["mkt_r60"] > lo
        sub = df[m]
        if len(sub) >= 50:
            out.append({"name": name, "n": int(len(sub)), "rate": float(sub["succ"].mean()), "x20": float(sub["x20"].mean())})
    return out


def _years(df: pd.DataFrame):
    y = df["date"].str[:4]
    g = df.groupby(y)
    return [{"year": k, "n": int(len(v)), "rate": float(v["succ"].mean()), "x20": float(v["x20"].mean())}
            for k, v in g if len(v) >= 50]


def analyze(rows_by_label: dict, mk: pd.DataFrame) -> dict:
    mkfwd = mk[0] if len(mk) else pd.Series(dtype=float)
    base_rows = rows_by_label.get(BASE) or []
    base = to_frame(base_rows) if base_rows else None
    if base is not None:
        base["x20"] = base["r20"] - base["date"].map(mkfwd)
        base["succ"] = base["x20"] > 0
    out = {"labels": {}, "feature_labels": FEATURE_LABELS,
           "baseline": ({"n": int(len(base)), "rate": float(base["succ"].mean()), "x20": float(base["x20"].mean())} if base is not None else None)}
    for label, rows in rows_by_label.items():
        if label == BASE or len(rows) < MIN_ROWS:
            continue
        df = to_frame(rows)
        df["x20"] = df["r20"] - df["date"].map(mkfwd)
        df = df[df["x20"].notna()].copy()
        if len(df) < MIN_ROWS:
            continue
        df["succ"] = df["x20"] > 0
        tg = df["tgt3"].dropna()
        out["labels"][label] = {
            "n": int(len(df)), "rate": float(df["succ"].mean()), "x20": float(df["x20"].mean()),
            "tgt3_rate": float(tg.mean()) if len(tg) else None, "tgt3_n": int(len(tg)),
            "features": _feature_table(df, base), "fail_modes": _fail_modes(df, mkfwd),
            "regimes": _regimes(df), "years": _years(df),
        }
    return out


def load() -> dict | None:
    import json
    try:
        return json.loads(WHY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
