"""AI(머신러닝) 분석 — '20거래일 뒤 +20% 이상 오른 종목'의 특징·예측 정확성·거래량의 영향.

목표(정답): 신호일 다음날 시가에 사서 20봉 뒤 종가가 진입가보다 20% 이상 높음(y20). 보조로 20봉 안에 고가가 +20%에 한 번이라도 닿음(touch).
모델: HistGradientBoosting(그래디언트 부스팅 트리). 특징 약 45개는 모두 '그날 종가까지' 알 수 있는 값(가격·이평 위치, 수익률, 변동성, 거래량, 시장 상황).
검증: 시간 순서로 나눔 — 학습 ≤2018, 검증 2019~2021(반복 횟수 고르기), 시험 2022~(처음 보는 기간). 정확성은 시험 기간 기준.
쓸모 확인: 시험 기간에 28일 간격으로 AI 점수 상위 N개를 사서 20거래일 보유했을 때 평균 수익을 시장·단순 규칙(거래량 급증·모멘텀 등)과 비교.
※ 과거 통계일 뿐이며 상장폐지 종목이 빠져 있다(생존 편향). 투자 권유가 아니다.
"""
import json
import math
import os
import time
import warnings
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

import config
from core import backtest as bt
from core import db, indicators as ind, service, techniques

warnings.filterwarnings("ignore")

AI_PATH = config.DATA_DIR / "ai.json"
MODEL_PATH = config.DATA_DIR / "ai_model.joblib"
MIN_BARS = 300
STEP_MOD = 5                    # 날짜 기준 표본 추출: 달력 연중일(dayofyear) % 5 == 0 인 날만(모든 종목이 같은 날짜를 가짐)
TARGET = 0.20
HOLD = 20
TRADABLE_VALUE = 3e8            # 최근 20일 평균 거래대금 3억 원 이상을 '실제로 살 수 있는' 종목으로
TRAIN_END, VALID_END = "2018-12-31", "2021-12-31"
TOPN_STEP_DAYS = 28
COST = bt.COST

# (이름, 한글, 묶음)
FEATURES = [
    ("dist5", "5일선 대비 위치", "추세·이평"), ("dist20", "20일선 대비 위치", "추세·이평"), ("dist60", "60일선 대비 위치", "추세·이평"),
    ("dist112", "112일선 대비 위치", "추세·이평"), ("dist224", "224일선 대비 위치", "추세·이평"),
    ("slope20", "20일선 기울기(10봉)", "추세·이평"), ("slope60", "60일선 기울기(20봉)", "추세·이평"),
    ("pos60", "60일 범위 내 위치(0=저점,1=고점)", "추세·이평"), ("nh250", "52주 고점 대비", "추세·이평"),
    ("aligned", "정배열(5>20>60>112)", "추세·이평"), ("reversed", "역배열(5<20<60<112)", "추세·이평"),
    ("ret1", "당일 등락률", "모멘텀"), ("ret5", "5일 수익률", "모멘텀"), ("ret10", "10일 수익률", "모멘텀"),
    ("ret20", "20일 수익률", "모멘텀"), ("ret60", "60일 수익률", "모멘텀"), ("rs20", "20일 수익률 − 시장", "모멘텀"),
    ("atrp", "변동성(ATR÷종가)", "변동성·캔들"), ("vol20", "20일 등락률 표준편차", "변동성·캔들"),
    ("range1", "당일 고저 폭", "변동성·캔들"), ("gap0", "당일 시가 갭", "변동성·캔들"), ("body", "당일 몸통", "변동성·캔들"),
    ("upper", "당일 윗꼬리 비율", "변동성·캔들"), ("lower", "당일 아랫꼬리 비율", "변동성·캔들"),
    ("volratio", "당일 거래량 ÷ 20일 평균", "거래량"), ("vr5", "5일 평균 ÷ 20일 평균 거래량", "거래량"),
    ("vr20_60", "20일 평균 ÷ 60일 평균 거래량", "거래량"), ("vmax10", "최근 10일 최대 거래량 ÷ 20일 평균", "거래량"),
    ("vdry10", "최근 10일 최소 거래량 ÷ 20일 평균(마름)", "거래량"), ("upvol20", "20일 중 상승일 거래량 비중", "거래량"),
    ("obv20", "OBV 20일 변화(평균 거래량 대비)", "거래량"), ("liq", "거래대금 규모(로그)", "거래량"),
    ("liq_chg", "5일 거래대금 ÷ 60일 거래대금(로그)", "거래량"), ("vspike_n", "20일 중 거래량 2배↑ 날 수", "거래량"),
    ("mkt_r20", "시장 직전 20일 수익률", "시장"), ("mkt_r60", "시장 직전 60일 수익률", "시장"),
    ("mkt_dd250", "시장 52주 고점 대비", "시장"), ("mkt_vol20", "시장 변동성(20일)", "시장"),
    ("hist_bars", "상장 후 경과(로그)", "기타"), ("price_level", "주가 수준(로그)", "기타"), ("acc_recent", "최근 60봉 매집봉 있음", "기타"),
]
ORIG_NAMES = [f[0] for f in FEATURES]                         # 처음 43개: 가격·거래량·시장
FEATURES += [
    ("dd120", "120일 고점 대비 낙폭", "패턴"), ("low_age", "60일 저점 이후 경과 봉", "패턴"), ("rebound60", "60일 저점 대비 반등폭", "패턴"),
    ("hl10", "최근 10일 저점 ÷ 60일 저점 −1(0↑=저점 지킴)", "패턴"), ("range20", "20일 가격 폭(응축)", "패턴"),
    ("hh20_gap", "20일 고점 대비", "패턴"), ("cross20", "20일선 상향돌파 후 경과 봉", "패턴"),
    ("cross60", "60일선 상향돌파 후 경과 봉", "패턴"), ("cross112", "112일선 상향돌파 후 경과 봉", "패턴"),
]
PATTERN_NAMES = [f[0] for f in FEATURES[len(ORIG_NAMES):]]
SIGNAL_LABELS = ["256 자리(단기)", "256 완성(단기)", "256 자리(중장기)", "256 완성(중장기)", "이평 때리기(112)", "224 돌파", "밥그릇 돌파",
                 "3번 자리(눌림)", "공구리(언덕 돌파)", "역매공파", "매집봉 돌파", "7일선 지지", "15일선 지지", "33일선 지지", "정배열",
                 "20/60 골든", "눌림목 반등", "박스 돌파", "52주 신고가", "거래량↑양봉", "장대양봉+거래량", "상승 장악형", "망치형", "샛별형"]
N_DANTE = 11                                                   # 앞의 11개가 단테 기법 계열
SIGNAL_NAMES = [f"sig{i:02d}" for i in range(len(SIGNAL_LABELS))] + ["dante_n10", "sig_n10"]
FEATURES += [(f"sig{i:02d}", f"{lab} 후 경과 봉(60=없음)", "기법 신호") for i, lab in enumerate(SIGNAL_LABELS)]
FEATURES += [("dante_n10", "최근 10봉 단테 기법 신호 종류 수", "기법 신호"), ("sig_n10", "최근 10봉 전체 신호 종류 수", "기법 신호")]
NAMES = [f[0] for f in FEATURES]
LABEL = {f[0]: f[1] for f in FEATURES}
GROUP = {f[0]: f[2] for f in FEATURES}
MKT_DF = None
HORIZONS = {"short": (5, 0.10, "단기 5거래일 +10%"), "mid": (20, 0.20, "중기 20거래일 +20%"), "midlong": (60, 0.40, "중장기 60거래일 +40%"),
            "long": (120, 0.60, "장기 120거래일 +60%"), "xlong": (250, 1.00, "초장기 250거래일 +100%")}


def _init_worker(path):
    global MKT_DF
    try:
        MKT_DF = pd.read_pickle(path)
    except (OSError, ValueError):
        MKT_DF = None


# ------------------------------------------------------------------ 특징
def _since(event: np.ndarray, cap: int = 60) -> np.ndarray:
    """이벤트가 마지막으로 있었던 뒤 몇 봉 지났나(오늘=0). 없으면 cap."""
    idx = np.where(event, np.arange(len(event)), -1)
    last = np.maximum.accumulate(idx)
    return np.where(last >= 0, np.minimum(np.arange(len(event)) - last, cap), cap).astype(float)


def signal_features(df: pd.DataFrame) -> np.ndarray:
    """기법 신호가 마지막으로 나온 뒤 경과 봉(60=없음) + 최근 10봉 신호 종류 수. 신호 자체가 미래 정보를 쓰지 않음(테스트로 확인)."""
    n = len(df)
    pos = {d: i for i, d in enumerate(df.index.strftime("%Y-%m-%d"))}
    ev = {lab: np.zeros(n, bool) for lab in SIGNAL_LABELS}
    for name in techniques.ORDER:
        if techniques.REGISTRY[name]["status"] != "ready":
            continue
        res = techniques.apply(name, df, bars=n)
        for m in res["markers"] if res else []:
            if m["side"] == "buy" and m["label"] in ev and m["date"] in pos:
                ev[m["label"]][pos[m["date"]]] = True
    since = np.column_stack([_since(ev[lab]) for lab in SIGNAL_LABELS])
    recent = since <= 10
    return np.column_stack([since, recent[:, :N_DANTE].sum(axis=1), recent.sum(axis=1)])


def compute_features(df: pd.DataFrame, mkt: pd.DataFrame | None, with_signals: bool = True) -> np.ndarray:
    """종목 하나의 모든 날짜에 대한 특징 행렬(n × len(NAMES)). 그날 종가까지의 정보만 쓴다."""
    o, h, l, c, v = (df[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
    n = len(df)
    s = pd.Series
    cs, vs = s(c), s(v)
    with np.errstate(divide="ignore", invalid="ignore"):
        ma = {p: cs.rolling(p, min_periods=p).mean().to_numpy() for p in (5, 20, 60, 112, 224)}
        val = s(c * v)
        avg20 = vs.rolling(20, min_periods=20).mean()
        avg20_prev = avg20.shift().to_numpy()
        avg5, avg60 = vs.rolling(5, min_periods=5).mean(), vs.rolling(60, min_periods=60).mean()
        up = (np.r_[False, c[1:] > c[:-1]]).astype(float)
        obv = np.cumsum(np.where(np.r_[0.0, np.sign(np.diff(c))] > 0, v, np.where(np.r_[0.0, np.sign(np.diff(c))] < 0, -v, 0.0)))
        ret1 = cs.pct_change().to_numpy()
        rng = np.maximum(h - l, 1e-9)
        hh60, ll60 = s(h).rolling(60, min_periods=60).max().to_numpy(), s(l).rolling(60, min_periods=60).min().to_numpy()
        hh250 = s(h).rolling(250, min_periods=120).max().to_numpy()
        a20 = avg20.to_numpy()
        acc, _ = techniques.acc_candles(df)
        cols = {
            "dist5": c / ma[5] - 1, "dist20": c / ma[20] - 1, "dist60": c / ma[60] - 1, "dist112": c / ma[112] - 1, "dist224": c / ma[224] - 1,
            "slope20": ma[20] / s(ma[20]).shift(10).to_numpy() - 1, "slope60": ma[60] / s(ma[60]).shift(20).to_numpy() - 1,
            "pos60": (c - ll60) / np.maximum(hh60 - ll60, 1e-9), "nh250": c / hh250 - 1,
            "aligned": ((ma[5] > ma[20]) & (ma[20] > ma[60]) & (ma[60] > ma[112])).astype(float),
            "reversed": ((ma[5] < ma[20]) & (ma[20] < ma[60]) & (ma[60] < ma[112])).astype(float),
            "ret1": ret1, "ret5": c / cs.shift(5).to_numpy() - 1, "ret10": c / cs.shift(10).to_numpy() - 1,
            "ret20": c / cs.shift(20).to_numpy() - 1, "ret60": c / cs.shift(60).to_numpy() - 1,
            "atrp": ind.atr(df).to_numpy(float) / c, "vol20": s(ret1).rolling(20, min_periods=20).std().to_numpy(),
            "range1": (h - l) / c, "gap0": o / cs.shift().to_numpy() - 1, "body": (c - o) / o,
            "upper": (h - np.maximum(o, c)) / rng, "lower": (np.minimum(o, c) - l) / rng,
            "volratio": v / avg20_prev, "vr5": avg5.to_numpy() / a20, "vr20_60": a20 / avg60.to_numpy(),
            "vmax10": vs.rolling(10, min_periods=10).max().to_numpy() / a20, "vdry10": vs.rolling(10, min_periods=10).min().to_numpy() / a20,
            "upvol20": s(v * up).rolling(20, min_periods=20).sum().to_numpy() / (vs.rolling(20, min_periods=20).sum().to_numpy() + 1e-9),
            "obv20": (obv - s(obv).shift(20).to_numpy()) / (a20 * 20 + 1e-9),
            "liq": np.log10(val.rolling(20, min_periods=20).mean().to_numpy() + 1),
            "liq_chg": np.log10(val.rolling(5, min_periods=5).mean().to_numpy() + 1) - np.log10(val.rolling(60, min_periods=60).mean().to_numpy() + 1),
            "vspike_n": s((v >= 2 * avg20_prev).astype(float)).rolling(20, min_periods=20).sum().to_numpy(),
            "hist_bars": np.log10(np.arange(n) + 1.0), "price_level": np.log10(np.maximum(c, 1.0)),
            "acc_recent": (s(acc.astype(float)).rolling(60, min_periods=1).max().to_numpy() > 0).astype(float),
        }
        dates = df.index.strftime("%Y-%m-%d")
        if mkt is not None:
            m = mkt.reindex(dates)
            for k in ("mkt_r20", "mkt_r60", "mkt_dd250", "mkt_vol20"):
                cols[k] = m[k].to_numpy(float)
        else:
            for k in ("mkt_r20", "mkt_r60", "mkt_dd250", "mkt_vol20"):
                cols[k] = np.full(n, np.nan)
        cols["rs20"] = cols["ret20"] - cols["mkt_r20"]
        # 하락 후 반등·응축 패턴
        hh120 = s(h).rolling(120, min_periods=60).max().to_numpy()
        hh20, ll20 = s(h).rolling(20, min_periods=20).max().to_numpy(), s(l).rolling(20, min_periods=20).min().to_numpy()
        ll10 = s(l).rolling(10, min_periods=10).min().to_numpy()
        low_age = np.full(n, np.nan)
        if n >= 60:
            low_age[59:] = 59 - sliding_window_view(l, 60).argmin(axis=1)
        cu = lambda m: np.r_[False, (c[1:] > m[1:]) & (c[:-1] <= m[:-1])]
        cols.update({"dd120": c / hh120 - 1, "low_age": low_age, "rebound60": c / ll60 - 1, "hl10": ll10 / ll60 - 1,
                     "range20": (hh20 - ll20) / c, "hh20_gap": c / hh20 - 1,
                     "cross20": _since(cu(ma[20])), "cross60": _since(cu(ma[60])), "cross112": _since(cu(ma[112]))})
        if with_signals:
            sf = signal_features(df)
            for j, nm in enumerate(SIGNAL_NAMES):
                cols[nm] = sf[:, j]
        else:
            for nm in SIGNAL_NAMES:
                cols[nm] = np.full(n, np.nan)
    X = np.column_stack([np.asarray(cols[k], float) for k in NAMES])
    X[~np.isfinite(X)] = np.nan
    return X.astype(np.float32)


def _entry_variants(df, o, h, l, c, t, e):
    """진입 방식 비교용(20거래일 뒤 종가로 청산, 비용 전): 5·10일 늦춘 진입, 3분할(0·5·10일), 5일 안 +2% 확인 후 진입,
    −3% 눌림 지정가, 2×ATR 손절 — 그리고 보유 중 최대 하락(mae). 모두 신호일(t) 종가까지의 정보로 정하고 다음날(e)부터 체결."""
    n, H = len(c), HOLD
    m = len(t)
    nan = lambda: np.full(m, np.nan)  # noqa: E731
    res = {k: nan() for k in ("x5", "x10", "split3", "mae0", "mae_split", "conf_ret", "pb_ret", "stop_ret", "stop_hit")}
    ok = (e + H) < n
    if not ok.any() or n <= H + 2:
        return res
    ee = e[ok]
    tt = t[ok]
    exit_c = c[ee + H]
    o0, o5, o10 = o[ee], o[ee + 5], o[ee + 10]
    res["x5"][ok] = exit_c / o5 - 1
    res["x10"][ok] = exit_c / o10 - 1
    res["split3"][ok] = (exit_c / o0 + exit_c / o5 + exit_c / o10) / 3 - 1
    Wl = sliding_window_view(l, H + 1)[ee]                         # (행, 21): 진입일부터 청산일까지의 저가
    Wc = sliding_window_view(c, H + 1)[ee]
    Wo = sliding_window_view(o, H + 1)[ee]
    res["mae0"][ok] = Wl.min(axis=1) / o0 - 1
    days = np.arange(H + 1)
    v = np.zeros_like(Wl)
    for off, op in ((0, o0), (5, o5), (10, o10)):
        v += np.where(days[None, :] >= off, Wl / op[:, None], 1.0)  # 아직 안 산 분할분은 현금(1.0)
    res["mae_split"][ok] = (v / 3).min(axis=1) - 1
    # 확인 후 진입: 5일(e..e+4) 안에 종가가 신호일 종가의 +2% 이상이 되면 그 다음날 시가에 진입
    cond = Wc[:, :5] >= (c[tt] * 1.02)[:, None]
    anyc = cond.any(axis=1)
    f = cond.argmax(axis=1)
    entry_c = Wo[np.arange(len(ee)), np.minimum(f + 1, H)]
    conf = np.where(anyc, exit_c / entry_c - 1, np.nan)
    res["conf_ret"][ok] = conf
    # 눌림 지정가: 5일 안에 시가의 −3%에 닿으면 체결(시가가 이미 아래면 시가)
    limit = o0 * 0.97
    hit = Wl[:, :5] <= limit[:, None]
    anyh = hit.any(axis=1)
    j = hit.argmax(axis=1)
    fill = np.minimum(Wo[np.arange(len(ee)), j], limit)
    res["pb_ret"][ok] = np.where(anyh, exit_c / fill - 1, np.nan)
    # 2×ATR 손절(청산은 손절가 또는 갭이면 시가, 안 닿으면 20일 뒤 종가)
    stop = o0 - 2 * ind.atr(df).to_numpy(float)[tt]
    shit = Wl <= stop[:, None]
    anys = shit.any(axis=1)
    d = shit.argmax(axis=1)
    px = np.where(anys, np.minimum(stop, Wo[np.arange(len(ee)), d]), exit_c)
    valid_stop = stop > 0
    res["stop_ret"][ok] = np.where(valid_stop, px / o0 - 1, np.nan)
    res["stop_hit"][ok] = np.where(valid_stop, anys.astype(float), np.nan)
    return res


def _avoid_extras(df, o, h, l, c, t, e):
    """갭·피해야 할 종목 연구용 값: 신호일 종가→다음날 시가 갭, 종가 진입·종가 지정가·장중 평균가 진입의 20일 수익(비용 전),
    거래 없는 날 수, 갭 하락 횟수, 연속 하락일 등. 모두 신호일(t) 종가까지의 정보 + 다음날 이후 가격."""
    n, H = len(c), HOLD
    m = len(t)
    nan = lambda: np.full(m, np.nan)  # noqa: E731
    res = {k: nan() for k in ("gap1", "r20c", "lc_ret", "vwap_ret", "tone_down")}
    ok = (e + H) < n
    if ok.any():
        ee, tt = e[ok], t[ok]
        exit_c = c[ee + H]
        res["gap1"][ok] = o[ee] / c[tt] - 1
        res["r20c"][ok] = exit_c / c[tt] - 1
        fill = np.where(l[ee] <= c[tt], np.minimum(o[ee], c[tt]), np.nan)        # 신호일 종가에 지정가: 다음날 그 값까지 내려오면 체결
        res["lc_ret"][ok] = exit_c / fill - 1
        res["vwap_ret"][ok] = exit_c / ((o[ee] + h[ee] + l[ee] + c[ee]) / 4) - 1   # 다음날 하루 나눠 사면 평균가 ≈ 시고저종 평균
    v = df["volume"].to_numpy(float)
    pc = np.r_[np.nan, c[:-1]]
    gap_dn = ((o / pc - 1) <= -0.05).astype(float)
    down = np.r_[0.0, (c[1:] < c[:-1]).astype(float)]
    res["zero_vol60"] = pd.Series((v <= 0).astype(float)).rolling(60, min_periods=1).sum().to_numpy()[t]
    res["gapdn20"] = pd.Series(gap_dn).rolling(20, min_periods=1).sum().to_numpy()[t]
    res["down_streak"] = _since(down == 0, cap=20)[t]                          # 연속 하락일 수(상승·보합이면 0)
    res["listing_bars"] = t.astype(float)
    res["close_px"] = c[t]
    return res


def _avoid_latest(df):
    """가장 최근 거래일의 거래 없는 날 수·갭 하락 횟수·연속 하락일(피해야 할 종목 목록용)."""
    c = df["close"].to_numpy(float)
    v = df["volume"].to_numpy(float)
    o = df["open"].to_numpy(float)
    pc = np.r_[np.nan, c[:-1]]
    gap_dn = ((o / pc - 1) <= -0.05).astype(float)
    down = np.r_[0.0, (c[1:] < c[:-1]).astype(float)]
    return {"zero_vol60": float((v[-60:] <= 0).sum()), "gapdn20": float(gap_dn[-20:].sum()), "down_streak": float(_since(down == 0, cap=20)[-1]),
            "close_px": float(c[-1])}


def build_rows(code: str, with_signals: bool = True, extras: bool = False):
    """한 종목의 학습 표본: 특징 + 여러 기간(5·20·60·120·250거래일)의 수익률 + 60일 최대 상승·새 저점 여부. 거래 가능한 날만."""
    df = service.load_prices(code)
    if len(df) < MIN_BARS:
        return None
    X = compute_features(df, MKT_DF, with_signals)
    n = len(df)
    dates = df.index
    latest = {"code": code, "date": dates[-1].strftime("%Y-%m-%d"), "close": float(df["close"].iloc[-1]),
              "ok": bool(df["volume"].iloc[-1] > 0), "X": X[-1]}
    if extras:
        latest.update(_avoid_latest(df))
    t = np.arange(250, n - 7)
    sel = (dates[t].dayofyear.to_numpy() % STEP_MOD) == 0
    t = t[sel]
    if len(t) == 0:
        return {"latest": latest, "X": np.zeros((0, len(NAMES)), np.float32), "date": np.array([], dtype=object), "code": np.array([], dtype=object)}
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    e = t + 1
    entry = o[e]
    out = {}
    for key, (hold, _, _) in HORIZONS.items():
        idx = e + hold
        out[f"r{hold}"] = np.where(idx < n, c[np.minimum(idx, n - 1)] / entry - 1, np.nan)
    tail = lambda arr, k, fn: pd.Series(arr)  # noqa: E731
    fmax = pd.Series(h).rolling(HOLD, min_periods=HOLD).max().shift(-(HOLD - 1)).to_numpy()
    out["touch"] = fmax[e] / entry - 1                        # 20일 안 최고가(진입가 대비)
    fmax60 = pd.Series(h).rolling(60, min_periods=60).max().shift(-59).to_numpy()
    fmin60 = pd.Series(l).rolling(60, min_periods=60).min().shift(-59).to_numpy()
    out["mx60"] = fmax60[e] / entry - 1                       # 60일 안 최고가
    trough = pd.Series(l).rolling(60, min_periods=60).min().to_numpy()[t]
    out["fail60"] = np.where(np.isnan(fmin60[e]), np.nan, (fmin60[e] < trough).astype(float))   # 60일 안에 직전 60일 저점을 깼나
    out.update(_entry_variants(df, o, h, l, c, t, e))
    if extras:
        out.update(_avoid_extras(df, o, h, l, c, t, e))
        out["t"] = t.astype(float)                                # 표본 행의 봉 번호(다른 연구가 같은 행에 값을 덧붙일 때 씀)
    keep = (entry > 0) & (X[t][:, NAMES.index("liq")] >= math.log10(TRADABLE_VALUE)) & ~np.isnan(X[t][:, NAMES.index("ret60")])
    res = {"X": X[t][keep], "date": dates[t].strftime("%Y-%m-%d").to_numpy()[keep], "code": np.full(int(keep.sum()), code), "latest": latest}
    res.update({k: v[keep] for k, v in out.items()})
    return res


def _work(code):
    try:
        return build_rows(code)
    except Exception:
        return None


# ------------------------------------------------------------------ 평가 도구
def _topk_precision(y, p, frac):
    k = max(int(len(y) * frac), 1)
    idx = np.argpartition(-p, k - 1)[:k]
    return float(y[idx].mean())


def _metrics(y, p):
    from sklearn.metrics import average_precision_score, roc_auc_score
    base = float(y.mean())
    out = {"n": int(len(y)), "base": base, "auc": float(roc_auc_score(y, p)) if 0 < y.sum() < len(y) else None,
           "ap": float(average_precision_score(y, p)) if y.sum() else None}
    for name, fr in (("top1", 0.01), ("top5", 0.05), ("top10", 0.10)):
        pr = _topk_precision(y, p, fr)
        out[name] = {"precision": pr, "lift": pr / base if base else None}
    return out


def _calibration(y, p, bins=10):
    q = pd.qcut(pd.Series(p).rank(method="first"), bins, labels=False)
    g = pd.DataFrame({"p": p, "y": y, "q": q}).groupby("q")
    return [{"bin": int(k) + 1, "pred": float(v["p"].mean()), "actual": float(v["y"].mean()), "n": int(len(v))} for k, v in g]


def _cohen(a, b):
    a, b = a[~np.isnan(a)], b[~np.isnan(b)]
    if len(a) < 30 or len(b) < 30:
        return None
    sp = math.sqrt(((len(a) - 1) * a.var(ddof=1) + (len(b) - 1) * b.var(ddof=1)) / (len(a) + len(b) - 2))
    return float((a.mean() - b.mean()) / sp) if sp > 0 else None


def _num(x):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else x


# ------------------------------------------------------------------ 학습·분석
def _split_masks(date):
    d = pd.Series(date)
    return (d <= TRAIN_END).to_numpy(), ((d > TRAIN_END) & (d <= VALID_END)).to_numpy(), (d > VALID_END).to_numpy()


def _fit(Xtr, ytr, Xva, yva):
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import average_precision_score
    m = HistGradientBoostingClassifier(max_iter=400, learning_rate=0.06, max_leaf_nodes=31, min_samples_leaf=200, l2_regularization=2.0,
                                       early_stopping=False, random_state=0)
    m.fit(Xtr, ytr)
    best, best_ap = m.n_iter_, -1
    for i, pr in enumerate(m.staged_predict_proba(Xva), 1):          # 검증 기간에서 가장 좋은 반복 횟수
        if i % 10 == 0:
            ap = average_precision_score(yva, pr[:, 1])
            if ap > best_ap:
                best, best_ap = i, ap
    m2 = HistGradientBoostingClassifier(max_iter=best, learning_rate=0.06, max_leaf_nodes=31, min_samples_leaf=200, l2_regularization=2.0,
                                        early_stopping=False, random_state=0)
    m2.fit(Xtr, ytr)
    return m2, best, best_ap


def _baselines(Xte, yte):
    """AI 없이 특징 하나만으로 순위를 매겼을 때의 구분 능력 — AI 가 그보다 얼마나 나은가."""
    rows = []
    for f in ("atrp", "vol20", "range1", "volratio", "vr5", "vspike_n", "ret20", "nh250", "dist20"):
        p = np.nan_to_num(Xte[:, NAMES.index(f)].astype(float), nan=np.nanmedian(Xte[:, NAMES.index(f)]))
        m = _metrics(yte, p)
        rows.append({"name": f, "label": LABEL[f], "auc": m["auc"], "top5": m["top5"]["precision"], "lift5": m["top5"]["lift"]})
    return rows


def _topn(df: pd.DataFrame, p: np.ndarray, n_pick=20):
    """시험 기간에 TOPN_STEP_DAYS 간격으로 상위 n_pick 종목을 샀을 때 20일 평균 수익(비용 차감)과 시장(거래 가능 전체) 평균."""
    d = df.assign(p=p, net=df["r20"] - COST)
    dates = sorted(d["date"].unique())
    picks, last = [], None
    for dt in dates:
        ts = pd.Timestamp(dt)
        if last is None or (ts - last).days >= TOPN_STEP_DAYS:
            picks.append(dt)
            last = ts
    methods = {"AI 점수": "p", "거래량 급증(당일÷20일)": "volratio", "20일 모멘텀": "ret20", "52주 신고가 근접": "nh250", "거래량 5일÷20일": "vr5", "변동성 큰 순(ATR÷종가)": "atrp"}
    res = {k: [] for k in methods}
    rng = np.random.default_rng(0)
    res["무작위"] = []
    mkt = []
    for dt in picks:
        g = d[d["date"] == dt]
        if len(g) < 100:
            continue
        mkt.append(g["net"].mean())
        for name, col in methods.items():
            res[name].append(g.nlargest(n_pick, col)["net"].mean())
        res["무작위"].append(g.iloc[rng.choice(len(g), n_pick, replace=False)]["net"].mean())
    mk = np.array(mkt)
    out = {"periods": len(mk), "n_pick": n_pick, "market": float(mk.mean()) if len(mk) else None, "methods": {}}
    for name, vals in res.items():
        v = np.array(vals)
        if len(v) == 0:
            continue
        ex = v - mk
        out["methods"][name] = {"mean": float(v.mean()), "excess": float(ex.mean()), "win_vs_market": float((ex > 0).mean()),
                                "hit20": None}
    return out


def _buckets(Xdf: pd.DataFrame, y, t, feats):
    out = {}
    for f in feats:
        s = Xdf[f]
        if f == "volratio":
            edges = [0, 0.5, 1, 1.5, 2, 3, 5, 10, np.inf]
        elif f == "vspike_n":
            edges = [-1, 0, 1, 2, 3, 5, 20]
        else:
            try:
                edges = list(np.unique(np.nanquantile(s, np.linspace(0, 1, 6))))
            except Exception:
                continue
            if len(edges) < 3:
                continue
            edges[0], edges[-1] = -np.inf, np.inf
        b = pd.cut(s, edges, include_lowest=True)
        g = pd.DataFrame({"b": b, "y": y, "t": t}).groupby("b", observed=True)
        base = float(np.mean(y))
        rows = []
        for iv, v in g:
            if len(v) < 200:
                continue
            rows.append({"lo": _num(float(iv.left)), "hi": _num(float(iv.right)), "n": int(len(v)), "rate": float(v["y"].mean()),
                         "touch": float(v["t"].mean()), "lift": float(v["y"].mean() / base) if base else None})
        out[f] = rows
    return out


CONDITIONS = [
    ("거래량 3배↑ 급증", lambda d: d["volratio"] >= 3),
    ("거래량 3배↑ + 장대양봉(+5%)", lambda d: (d["volratio"] >= 3) & (d["body"] >= 0.05)),
    ("거래량 마름 후 급증 (10일 최소 0.4배↓ & 당일 2배↑)", lambda d: (d["vdry10"] < 0.4) & (d["volratio"] >= 2)),
    ("52주 고점 −3% 이내", lambda d: d["nh250"] >= -0.03),
    ("52주 고점 −3% 이내 + 거래량 2배↑", lambda d: (d["nh250"] >= -0.03) & (d["volratio"] >= 2)),
    ("224일선 아래 −10%↓ + 거래량 2배↑", lambda d: (d["dist224"] <= -0.10) & (d["volratio"] >= 2)),
    ("최근 60봉 매집봉 있음", lambda d: d["acc_recent"] == 1),
    ("정배열", lambda d: d["aligned"] == 1),
    ("역배열", lambda d: d["reversed"] == 1),
    ("20일 수익률 −20%↓ (많이 빠진 뒤)", lambda d: d["ret20"] <= -0.20),
    ("20일 수익률 +30%↑ (이미 급등)", lambda d: d["ret20"] >= 0.30),
    ("거래대금 5일÷60일 2배↑ (돈이 몰림)", lambda d: d["liq_chg"] >= math.log10(2)),
]


def _conditions(Xdf, y, t):
    base = float(np.mean(y))
    out = []
    for name, fn in CONDITIONS:
        m = fn(Xdf).to_numpy()
        if m.sum() < 200:
            continue
        out.append({"name": name, "n": int(m.sum()), "rate": float(y[m].mean()), "touch": float(t[m].mean()),
                    "lift": float(y[m].mean() / base) if base else None})
    return out


def _importance(model, Xte, yte):
    from sklearn.inspection import permutation_importance
    from sklearn.metrics import average_precision_score
    rng = np.random.default_rng(1)
    idx = rng.choice(len(Xte), min(len(Xte), 120000), replace=False)
    Xs, ys = Xte[idx], yte[idx]
    base = average_precision_score(ys, model.predict_proba(Xs)[:, 1])
    imp = []
    for j, name in enumerate(NAMES):
        drops = []
        for r in range(3):
            Xp = Xs.copy()
            Xp[:, j] = rng.permutation(Xp[:, j])
            drops.append(base - average_precision_score(ys, model.predict_proba(Xp)[:, 1]))
        imp.append({"name": name, "label": LABEL[name], "group": GROUP[name], "imp": float(np.mean(drops)), "sd": float(np.std(drops))})
    groups = {}
    for g in sorted(set(GROUP.values())):
        cols = [j for j, nme in enumerate(NAMES) if GROUP[nme] == g]
        drops = []
        for r in range(3):
            Xp = Xs.copy()
            perm = rng.permutation(len(Xp))
            Xp[:, cols] = Xp[perm][:, cols]                 # 묶음 전체를 같은 순서로 섞는다(묶음 안 관계 유지)
            drops.append(base - average_precision_score(ys, model.predict_proba(Xp)[:, 1]))
        groups[g] = float(np.mean(drops))
    imp.sort(key=lambda r: -r["imp"])
    return imp, groups, float(base)


def _profiles(Xdf, y):
    win, rest = Xdf[y == 1], Xdf[y == 0]
    out = []
    for f in NAMES:
        d = _cohen(win[f].to_numpy(float), rest[f].to_numpy(float))
        if d is None:
            continue
        out.append({"name": f, "label": LABEL[f], "group": GROUP[f], "d": d, "win_med": _num(float(win[f].median())),
                    "all_med": _num(float(Xdf[f].median()))})
    out.sort(key=lambda r: -abs(r["d"]))
    return out


def _fit_n(X, y, n_iter):
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(max_iter=max(int(n_iter), 10), learning_rate=0.06, max_leaf_nodes=31, min_samples_leaf=200,
                                          l2_regularization=2.0, early_stopping=False, random_state=0).fit(X, y)


def _train_eval(Xs, y, valid, tr, va, te):
    """한 모델 학습·평가. valid=정답이 있는 행. 반환: 모델, 반복 횟수, 시험 지표, 시험 점수, 시험 행 마스크."""
    mtr, mva, mte = tr & valid, va & valid, te & valid
    model, best, _ = _fit(Xs[mtr], y[mtr], Xs[mva], y[mva])
    p = model.predict_proba(Xs[mte])[:, 1]
    return model, best, _metrics(y[mte], p), p, mte


def _topn_h(date, ret, p, extra: dict, hold, thr, n_pick=20):
    """보유 기간(hold)만큼 겹치지 않게 날짜를 띄워 가며 AI 상위 n_pick 을 샀을 때: 평균 수익·시장 대비·적중."""
    d = pd.DataFrame({"date": date, "net": ret - COST, "hit": (ret >= thr).astype(float), "p": p, **extra})
    step = max(int(hold * 1.45) + 1, 7)
    picks, last = [], None
    for dt in sorted(d["date"].unique()):
        ts = pd.Timestamp(dt)
        if last is None or (ts - last).days >= step:
            picks.append(dt)
            last = ts
    methods = {"AI 점수": "p", "52주 신고가 근접": "nh250", "변동성 큰 순": "atrp"}
    res = {k: {"net": [], "hit": []} for k in methods}
    res["무작위"] = {"net": [], "hit": []}
    mk, mhit = [], []
    rng = np.random.default_rng(0)
    for dt in picks:
        g = d[d["date"] == dt]
        if len(g) < 100:
            continue
        mk.append(g["net"].mean())
        mhit.append(g["hit"].mean())
        for name, col in methods.items():
            top = g.nlargest(n_pick, col)
            res[name]["net"].append(top["net"].mean())
            res[name]["hit"].append(top["hit"].mean())
        rnd = g.iloc[rng.choice(len(g), n_pick, replace=False)]
        res["무작위"]["net"].append(rnd["net"].mean())
        res["무작위"]["hit"].append(rnd["hit"].mean())
    mk = np.array(mk)
    out = {"periods": int(len(mk)), "n_pick": n_pick, "step_days": step, "market": float(mk.mean()) if len(mk) else None,
           "market_hit": float(np.mean(mhit)) if mhit else None, "methods": {}}
    for name, v in res.items():
        net = np.array(v["net"])
        if len(net) == 0:
            continue
        out["methods"][name] = {"mean": float(net.mean()), "median": float(np.median(net)), "excess": float((net - mk).mean()),
                                "win_vs_market": float(((net - mk) > 0).mean()), "hit": float(np.mean(v["hit"]))}
    return out


def explain_rows(model, Xrows, medians, feat_idx, top_k=3):
    """종목별 '왜 높게 나왔나': 특징 하나를 중앙값으로 바꿨을 때 확률이 얼마나 떨어지나(= 그 특징의 기여, %p)."""
    out = []
    for x in Xrows:
        variants = [x.copy()]
        for j in feat_idx:
            v = x.copy()
            v[j] = medians[j]
            variants.append(v)
        P = model.predict_proba(np.vstack(variants))[:, 1]
        contrib = P[0] - P[1:]
        order = np.argsort(-contrib)[:top_k]
        out.append((float(P[0]), [(feat_idx[k], float(contrib[k])) for k in order if contrib[k] > 0]))
    return out


def _fmt_value(name: str, v: float) -> str:
    if name.startswith("sig") and name[3:5].isdigit():
        return "없음" if v >= 60 else ("오늘" if v == 0 else f"{int(v)}봉 전")
    if name in ("dante_n10", "sig_n10", "vspike_n"):
        return f"{int(v)}"
    return f"{v * 100:.1f}%" if name in PCT_NAMES else f"{v:.2f}"


PCT_NAMES = {"dist5", "dist20", "dist60", "dist112", "dist224", "slope20", "slope60", "nh250", "ret1", "ret5", "ret10", "ret20", "ret60", "rs20",
             "atrp", "vol20", "range1", "gap0", "body", "mkt_r20", "mkt_r60", "mkt_dd250", "mkt_vol20", "dd120", "rebound60", "hl10",
             "range20", "hh20_gap"}


def reason_text(contribs, x, active):
    parts = []
    for j, c in contribs:
        nm = NAMES[j]
        lab = LABEL[nm].replace(" 후 경과 봉(60=없음)", " 신호")
        parts.append(f"{lab} {_fmt_value(nm, x[j])} (확률 +{c * 100:.1f}%p)")
    if active:
        parts.append("최근 10봉 기법 신호: " + ", ".join(active[:4]))
    return " · ".join(parts)


def run(workers: int = 0, log=print, limit: int = 0) -> dict:
    t0 = time.time()
    with db.get_conn() as c:
        codes = [r["code"] for r in c.execute("SELECT code FROM prices GROUP BY code HAVING COUNT(*) >= ? ORDER BY code", (MIN_BARS,))]
        names = {r["code"]: r["name"] for r in c.execute("SELECT code,name FROM symbols")}
    if limit:
        codes = codes[::max(len(codes) // limit, 1)][:limit]
    workers = workers or max(1, min(6, (os.cpu_count() or 2) - 2))
    if not bt.MKT_PATH.exists():
        bt.build_market_index(codes, workers)
    parts, latest = [], []
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=(bt.MKT_PATH,)) as ex:
        for i, out in enumerate(ex.map(_work, codes, chunksize=8), 1):
            if out:
                latest.append(out["latest"])
                if len(out["date"]):
                    parts.append(out)
            if i % 200 == 0:
                log(f"[표본 {i}/{len(codes)}] {sum(len(p['date']) for p in parts)}행 · {time.time() - t0:.0f}초")
    X = np.concatenate([p["X"] for p in parts])
    date = np.concatenate([p["date"] for p in parts])
    code = np.concatenate([p["code"] for p in parts])
    T = {k: np.concatenate([p[k] for p in parts]) for k in ("r5", "r20", "r60", "r120", "r250", "touch", "mx60", "fail60")}
    log(f"표본 {len(date):,}행(거래 가능) · 특징 {len(NAMES)}개 · {time.time() - t0:.0f}초")
    tr, va, te = _split_masks(date)
    ix = {n: i for i, n in enumerate(NAMES)}
    cols_base = [ix[n] for n in ORIG_NAMES]
    cols_pat = cols_base + [ix[n] for n in PATTERN_NAMES]
    cols_all = list(range(len(NAMES)))

    # ---------------- A. 기법 신호를 특징으로 넣으면 좋아지나? (20거래일 +20%)
    y20 = (T["r20"] >= TARGET).astype(int)
    v20 = ~np.isnan(T["r20"])
    sets = [("가격·거래량·시장만", cols_base), ("+ 하락·반등 패턴 특징", cols_pat), ("+ 기법 신호(매집봉·256·공구리 등)", cols_all)]
    cmp_rows, main = [], None
    for title, cols in sets:
        model, best, met, p, mte = _train_eval(X[:, cols], y20, v20, tr, va, te)
        dfte = {"nh250": X[mte][:, ix["nh250"]], "atrp": X[mte][:, ix["atrp"]]}
        tn = _topn_h(date[mte], T["r20"][mte], p, dfte, HOLD, TARGET)
        cmp_rows.append({"name": title, "n_features": len(cols), "iters": int(best), "auc": met["auc"], "ap": met["ap"], "top1": met["top1"],
                         "top5": met["top5"], "top10": met["top10"], "topn_mean": tn["methods"]["AI 점수"]["mean"],
                         "topn_excess": tn["methods"]["AI 점수"]["excess"], "topn_win": tn["methods"]["AI 점수"]["win_vs_market"]})
        log(f"[A] {title}: AUC {met['auc']:.3f} · 상위5% {met['top5']['precision'] * 100:.1f}% · {time.time() - t0:.0f}초")
        if cols is cols_all:
            main = (model, best, met, p, mte)
    model, best_iter, met, pt, mte = main
    mt = mte
    X_te, y_te = X[mt], y20[mt]
    rep = {"meta": {"generated": datetime.now().isoformat(timespec="seconds"), "rows": int(len(date)), "stocks": int(len(set(code))),
                    "tradable_rows": int(len(date)), "target": TARGET, "hold": HOLD, "tradable_value": TRADABLE_VALUE,
                    "split": {"train_end": TRAIN_END, "valid_end": VALID_END, "date_min": str(date.min()), "date_max": str(date.max())},
                    "base_all": float(y20[v20].mean()), "base_tradable": float(y20[v20].mean()), "touch_tradable": float((T["touch"] >= TARGET)[~np.isnan(T["touch"])].mean()),
                    "iters": int(best_iter), "features": len(NAMES), "features_base": len(ORIG_NAMES)}}
    rep["signals_compare"] = cmp_rows
    rep["accuracy"] = {"test": met, "calibration": _calibration(y_te, pt),
                       "valid": _metrics(y20[va & v20], model.predict_proba(X[va & v20])[:, 1])}
    ym = pd.Series(date[mt]).str[:4].to_numpy()
    yearly = []
    for yr in sorted(set(ym)):
        mm = ym == yr
        if mm.sum() > 2000 and 0 < y_te[mm].sum():
            m = _metrics(y_te[mm], pt[mm])
            yearly.append({"year": yr, "n": m["n"], "base": m["base"], "auc": m["auc"], "top5": m["top5"]["precision"], "lift5": m["top5"]["lift"]})
    rep["yearly"] = yearly
    dfte = pd.DataFrame({"date": date[mt], "r20": T["r20"][mt], "volratio": X_te[:, ix["volratio"]], "ret20": X_te[:, ix["ret20"]],
                         "nh250": X_te[:, ix["nh250"]], "vr5": X_te[:, ix["vr5"]], "atrp": X_te[:, ix["atrp"]]})
    rep["topn"] = _topn(dfte, pt)
    rep["baselines"] = _baselines(X_te, y_te)
    imp, groups, ap = _importance(model, X_te, y_te)
    rep["importance"], rep["group_importance"], rep["importance_base_ap"] = imp, groups, ap
    Xdf = pd.DataFrame(X[v20], columns=NAMES)
    yy, tt = y20[v20], (T["touch"][v20] >= TARGET).astype(int)
    rep["profiles"] = _profiles(Xdf, yy)
    top_feats = [r["name"] for r in imp[:8]] + [f for f in ("volratio", "vr5", "vdry10", "liq", "vspike_n", "upvol20", "obv20") if f not in [r["name"] for r in imp[:8]]]
    rep["buckets"] = _buckets(Xdf, yy, tt, top_feats)
    rep["conditions"] = _conditions(Xdf, yy, tt)
    log(f"[A] 중요도·프로필 완료 · {time.time() - t0:.0f}초")

    # ---------------- B. 단기·중기·중장기·장기 비교 + 추천 종목
    L = [r for r in latest if r["ok"] and not np.isnan(r["X"][ix["ret60"]]) and not np.isnan(r["X"][ix["vr5"]])
         and r["X"][ix["liq"]] >= math.log10(TRADABLE_VALUE)]
    XL = np.vstack([r["X"] for r in L]) if L else np.zeros((0, len(NAMES)), np.float32)
    medians = np.nanmedian(X[tr], axis=0)
    market_by_date = pd.Series(1.0, index=date)
    hz = {}
    for key, (hold, thr, title) in HORIZONS.items():
        r = T[f"r{hold}"]
        valid = ~np.isnan(r)
        y = (r >= thr).astype(int)
        mdl, best, met, p, mte2 = _train_eval(X, y, valid, tr, va, te)
        dfx = {"nh250": X[mte2][:, ix["nh250"]], "atrp": X[mte2][:, ix["atrp"]]}
        tn = _topn_h(date[mte2], r[mte2], p, dfx, hold, thr)
        entry = {"title": title, "hold": hold, "thr": thr, "iters": int(best), "n_test": met["n"], "base": met["base"], "auc": met["auc"],
                 "top1": met["top1"], "top5": met["top5"], "top10": met["top10"], "topn": tn,
                 "mean_all": float(np.nanmean(r)), "median_all": float(np.nanmedian(r)), "pos_all": float((r[valid] > 0).mean())}
        # 최종 모델(검증 기간까지 학습)로 오늘 점수 + 이유
        final = _fit_n(X[(tr | va) & valid], y[(tr | va) & valid], best)
        if len(XL):
            P = final.predict_proba(XL)[:, 1]
            order = np.argsort(-P)[:10]
            imp_h, _, _ = _importance_fast(final, X[mte2], y[mte2])
            feat_idx = [ix[f] for f in imp_h[:14]]
            ex = explain_rows(final, XL[order], medians, feat_idx)
            picks = []
            for k, (p0, contribs) in zip(order, ex):
                row = L[k]
                active = [SIGNAL_LABELS[j] for j in range(len(SIGNAL_LABELS)) if row["X"][ix[SIGNAL_NAMES[j]]] <= 10]
                picks.append({"code": row["code"], "name": names.get(row["code"], row["code"]), "date": row["date"], "close": row["close"],
                              "prob": float(p0), "reason": reason_text(contribs, row["X"], active), "signals": active,
                              "ret20": _num(float(row["X"][ix["ret20"]])), "nh250": _num(float(row["X"][ix["nh250"]])),
                              "volratio": _num(float(row["X"][ix["volratio"]]))})
            entry["picks"] = picks
        hz[key] = entry
        log(f"[B] {title}: AUC {met['auc']:.3f} · AI Top20 평균 {tn['methods']['AI 점수']['mean'] * 100:+.2f}% (시장 {tn['market'] * 100:+.2f}%) · {time.time() - t0:.0f}초")
        if key == "mid":
            try:
                import joblib
                joblib.dump(final, MODEL_PATH)
            except Exception:
                pass
            rep["today"] = [{"code": pk["code"], "name": pk["name"], "date": pk["date"], "close": pk["close"], "prob": pk["prob"],
                             "volratio": pk["volratio"], "ret20": pk["ret20"], "nh250": pk["nh250"], "vr5": None} for pk in entry.get("picks", [])]
    rep["horizons"] = hz

    # ---------------- C. 기법 신호별 기간별 수익률(시장 평균 대비) — 단기·중장기·장기 비교
    D = pd.DataFrame(X, columns=NAMES)
    D["date"], D["code"] = date, code
    for k, v in T.items():
        D[k] = v
    rep["signal_horizons"] = _signal_horizons(D)
    # ---------------- D. 하락 후 반등 / 상승 패턴
    from core import patterns_study as ps
    pats = ps.analyze(D, LABEL, log=log)
    if L:
        XLd = pd.DataFrame(XL, columns=NAMES)
        XLd["code"] = [r["code"] for r in L]
        XLd["close"] = [r["close"] for r in L]
        for key in pats:
            pats[key]["today"] = ps.current_matches(XLd, names, key, LABEL)
    rep["patterns"] = pats
    rep["text"] = narrate(rep) + narrate_extra(rep)
    rep["meta"]["elapsed_sec"] = round(time.time() - t0)
    AI_PATH.parent.mkdir(parents=True, exist_ok=True)
    AI_PATH.write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
    return rep


def _importance_fast(model, Xte, yte, n_feat=14):
    """가벼운 중요도(표본 6만 행, 1회): 추천 이유에 쓸 특징 이름 상위 n_feat."""
    from sklearn.metrics import average_precision_score
    rng = np.random.default_rng(2)
    idx = rng.choice(len(Xte), min(len(Xte), 60000), replace=False)
    Xs, ys = Xte[idx], yte[idx]
    base = average_precision_score(ys, model.predict_proba(Xs)[:, 1])
    drops = []
    for j in range(len(NAMES)):
        Xp = Xs.copy()
        Xp[:, j] = rng.permutation(Xp[:, j])
        drops.append(base - average_precision_score(ys, model.predict_proba(Xp)[:, 1]))
    order = np.argsort(drops)[::-1][:n_feat]
    return [NAMES[j] for j in order], drops, base


def _signal_horizons(D: pd.DataFrame):
    """기법 신호가 '오늘' 나온 표본의 기간별 평균 수익(시장 평균=같은 날 모든 표본의 평균을 뺌)."""
    out = []
    hs = [5, 20, 60, 120, 250]
    mk = {h: D.groupby("date")[f"r{h}"].transform("mean") for h in hs}
    for i, lab in enumerate(SIGNAL_LABELS):
        m = (D[f"sig{i:02d}"] == 0).to_numpy()
        if m.sum() < 500:
            continue
        row = {"label": lab, "n": int(m.sum()), "dante": i < N_DANTE, "h": {}}
        for h in hs:
            r = D.loc[m, f"r{h}"]
            ex = r - mk[h][m]
            ok = r.notna()
            if ok.sum() >= 200:
                row["h"][str(h)] = {"mean": float(r[ok].mean() - COST), "excess": float(ex[ok].mean()), "win": float((r[ok] - COST > 0).mean()), "n": int(ok.sum())}
        out.append(row)
    base = {str(h): {"mean": float(D[f"r{h}"].mean() - COST), "win": float((D[f"r{h}"] - COST > 0).mean())} for h in hs}
    return {"signals": out, "base": base}


def narrate_extra(rep: dict) -> list[str]:
    out = []
    sc = rep.get("signals_compare") or []
    if len(sc) == 3:
        a, c = sc[0], sc[2]
        d_auc = c["auc"] - a["auc"]
        out.append(f"기법 신호·패턴 특징을 더하면 AUC {a['auc']:.3f} → {c['auc']:.3f}({d_auc:+.3f}), 상위 5% 적중 {a['top5']['precision'] * 100:.1f}% → {c['top5']['precision'] * 100:.1f}%로 "
                   + ("의미 있게 좋아졌습니다." if d_auc >= 0.01 else "거의 달라지지 않았습니다(기법 신호가 가격·거래량 정보에 이미 들어 있음)."))
    hz = rep.get("horizons") or {}
    if hz:
        parts = []
        for k, h in hz.items():
            tn = h["topn"]["methods"].get("AI 점수")
            if tn:
                parts.append(f"{h['title'].split()[0]} {tn['mean'] * 100:+.1f}%(시장 {h['topn']['market'] * 100:+.1f}%)")
        out.append("기간별 AI 상위 20개 평균 수익(비용 차감): " + ", ".join(parts) + ".")
    return out


def narrate(rep: dict) -> list[str]:
    """결과를 사람이 읽는 문장으로. 숫자는 모두 위 표에서 가져온 값."""
    m, acc = rep["meta"], rep["accuracy"]["test"]
    out = [f"{TARGET * 100:.0f}% 이상 오르는 일(20거래일 뒤 종가 기준)은 거래 가능 종목에서 평균 {m['base_tradable'] * 100:.1f}%로 드뭅니다(20일 안에 한 번이라도 닿는 비율은 {m['touch_tradable'] * 100:.1f}%)."]
    if acc["auc"]:
        out.append(f"처음 보는 기간(2022~)에서 AI의 구분 능력(AUC)은 {acc['auc']:.2f}입니다(0.5=무작위). AI 점수 상위 5%의 실제 적중률은 {acc['top5']['precision'] * 100:.1f}%로 "
                   f"기준 {acc['base'] * 100:.1f}%의 {acc['top5']['lift']:.1f}배, 상위 1%는 {acc['top1']['precision'] * 100:.1f}%({acc['top1']['lift']:.1f}배)입니다.")
    tn = rep["topn"]["methods"].get("AI 점수")
    if tn:
        out.append(f"매 {TOPN_STEP_DAYS}일마다 AI 상위 {rep['topn']['n_pick']}개를 20거래일 보유했을 때 비용 차감 평균 {tn['mean'] * 100:+.2f}%, 시장 대비 {tn['excess'] * 100:+.2f}%p"
                   f"({rep['topn']['periods']}회 중 {tn['win_vs_market'] * 100:.0f}%에서 시장보다 우수)입니다.")
    bl = {b["name"]: b for b in rep.get("baselines", [])}
    if "atrp" in bl and acc["auc"]:
        out.append(f"참고: 변동성(ATR÷종가) 하나만으로 순위를 매겨도 AUC가 {bl['atrp']['auc']:.2f}입니다 — AI의 구분 능력 상당 부분은 '변동성이 큰 종목이 크게 움직인다'는 사실에서 옵니다(방향을 맞히는 것이 아님).")
    g = rep["group_importance"]
    top_g = sorted(g.items(), key=lambda kv: -kv[1])
    out.append("예측에 가장 크게 기여한 묶음은 " + ", ".join(f"{k}" for k, _ in top_g[:3]) + " 순입니다.")
    out.append("개별 특징 상위: " + ", ".join(r["label"] for r in rep["importance"][:5]) + ".")
    vb = rep["buckets"].get("volratio")
    if vb:
        best = max(vb, key=lambda r: r["rate"])
        lo = "∞" if best["hi"] is None else best["hi"]
        out.append(f"당일 거래량이 20일 평균의 {best['lo']:.1f}~{lo}배인 구간의 적중률이 {best['rate'] * 100:.1f}%로 가장 높았습니다(기준 대비 {best['lift']:.1f}배).")
    return out


if __name__ == "__main__":
    run()
