"""기법 신호의 과거 성과 측정 — 진입 시점·진입가, 목표가, 손절가, 매집봉 효과를 실제 일봉으로 검증한다.

규칙(미래 정보 금지)
- 신호는 그날 종가 이후에 알 수 있으므로 진입은 '다음 거래일 시가'(기본). 하루·이틀 늦춘 진입과 눌림(−3%) 지정가 진입도 함께 본다.
- 손절·목표는 신호일까지의 정보로 정한다(이평선 값, 언덕·매집봉 저가, ATR 등).
- 봉 안에서 손절과 목표가 모두 닿으면 손절이 먼저라고 본다(보수적). 갭으로 시가가 손절 아래면 시가에 청산.
- 최대 보유 H봉, 왕복 비용 COST 를 뺀다. 데이터가 모자란 최근 신호는 제외한다.
- 비교 기준(baseline): 같은 종목들에서 일정 간격으로 뽑은 '아무 날' 진입 + 시장 평균(같은 날 모든 종목 평균 수익)을 뺀 초과수익.
- 메모리: 신호를 하나씩 저장하지 않고 종목마다 합계(개수·합·제곱합 …)만 모아 합친다.
한계: 현재 상장된 종목만 있어 생존 편향이 있고, 슬리피지·체결 실패·거래량 제한은 단순화했다.
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
from core import db, indicators as ind, service, techniques

warnings.filterwarnings("ignore", category=RuntimeWarning)   # 거래량 0인 날의 나눗셈 경고(근거 문장 만들 때)

H = 60
COST = 0.003
MIN_BARS = 300
RISK_MIN, RISK_MAX = 0.005, 0.25
HORIZONS = (5, 10, 20, 60)
BASE_STEP = 7
PB_DIP = 0.03            # 눌림 지정가: 다음날 시가보다 3% 아래
PB_WAIT = 5              # 지정가를 기다리는 봉 수
RECENT_FROM = "2021-10-01"
REPORT_PATH = config.DATA_DIR / "backtest.json"
BASE_LABEL = "기준(아무 날 진입)"
ACC_SUFFIX_ON, ACC_SUFFIX_OFF = " +매집봉", " (매집봉 없음)"
ACC_VARIANT_TECHS = {"256기법", "이평 때리기", "밥그릇", "공구리", "눌림목", "돌파·신고가"}   # 매집봉 유무로 나눠 볼 기법

# 기법별 손절 규칙 (단테 영상에서 확인된 것 + 합리적 기본값)
STOP_RULES = {
    "256 자리(단기)": ("ma", 20), "256 완성(단기)": ("ma", 20),
    "256 자리(중장기)": ("ma", 112), "256 완성(중장기)": ("ma", 112),
    "이평 때리기(112)": ("ma_pct", 112, 0.97), "224 돌파": ("ma_pct", 224, 0.97),
    "밥그릇 돌파": ("ma_pct", 224, 0.97), "3번 자리(눌림)": ("ma_pct", 224, 0.97),
    "공구리(언덕 돌파)": ("hill_pct", 0.98), "역매공파": ("hill_pct", 0.98),
    "매집봉 돌파": ("acc_low", 0.99),
    "눌림목 반등": ("ma_pct", 20, 0.97), "박스 돌파": ("boxhi_pct", 0.97),
    "7일선 지지": ("ma_pct", 7, 0.97), "15일선 지지": ("ma_pct", 15, 0.97), "33일선 지지": ("ma_pct", 33, 0.97),
}
# 기법별 '자연스러운' 목표 (영상에서 말한 다음 저항선/대칭 파동)
NATURAL_TARGETS = {
    "256 자리(단기)": [("ma", 60)], "256 자리(중장기)": [("ma", 224)],
    "이평 때리기(112)": [("ma", 224)], "224 돌파": [("ma", 448)],
    "공구리(언덕 돌파)": [("measured", 1.0), ("measured", 1.5)],
    "역매공파": [("measured", 1.0), ("measured", 1.5)],
}
MA_PERIODS = (5, 7, 10, 15, 20, 33, 60, 112, 224, 448)

# 성공·실패 원인 분석용: 신호 시점(종가)에 알 수 있던 특징들
FEATURES = ["dist20", "dist60", "dist224", "slope60", "slope224", "dd250", "run20", "atrp", "volratio", "liq",
            "body", "upper", "gap0", "base_age", "hist_bars", "acc_recent", "mkt_r20", "mkt_r60", "mkt_dd250", "mkt_vol20"]
ROW_CAP = 25                                   # 종목·기법마다 저장할 신호 수(무작위 추출)
MKT_PATH = config.DATA_DIR / "market_idx.pkl"
ROWS_PATH = config.DATA_DIR / "why_rows.pkl"
MKT_DF = None                                  # 워커 전역: 날짜별 시장 특징(equal-weight 지수)
_MKT_COLS = ["mkt_r20", "mkt_r60", "mkt_dd250", "mkt_vol20"]


# ------------------------------------------------------------------ 준비
class Arrays:
    def __init__(self, df: pd.DataFrame):
        self.o, self.h, self.l, self.c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        self.n = len(df)
        close = df["close"]
        self.ma = {p: ind.sma(close, p).to_numpy(float) for p in MA_PERIODS}
        self.atr = ind.atr(df).to_numpy(float)
        self.boxhi = df["high"].rolling(20, min_periods=20).max().shift().to_numpy(float)
        self.dates = df.index.strftime("%Y-%m-%d").to_numpy()
        self.index = {d: i for i, d in enumerate(self.dates)}
        acc, _ = techniques.acc_candles(df)
        self.acc_recent = pd.Series(acc.astype(float)).rolling(60, min_periods=1).max().to_numpy() > 0
        self.F = self._features(df)

    def _features(self, df) -> np.ndarray:
        """신호 시점의 특징 행렬 (n × len(FEATURES)). 모두 그날 종가까지의 정보만 쓴다."""
        o, h, l, c, v = self.o, self.h, self.l, self.c, df["volume"].to_numpy(float)
        n = self.n
        s = pd.Series
        with np.errstate(divide="ignore", invalid="ignore"):
            avg_v = ind.sma(df["volume"], 20).shift().to_numpy(float)
            hh250 = s(h).rolling(250, min_periods=120).max().to_numpy()
            ma60, ma224 = self.ma[60], self.ma[224]
            base_age = np.full(n, np.nan)
            if n >= 60:
                base_age[59:] = 59 - sliding_window_view(l, 60).argmin(axis=1)
            rng = np.maximum(h - l, 1e-9)
            cols = {
                "dist20": c / self.ma[20] - 1, "dist60": c / ma60 - 1, "dist224": c / ma224 - 1,
                "slope60": ma60 / s(ma60).shift(20).to_numpy() - 1, "slope224": ma224 / s(ma224).shift(40).to_numpy() - 1,
                "dd250": c / hh250 - 1, "run20": c / s(c).shift(20).to_numpy() - 1, "atrp": self.atr / c,
                "volratio": v / avg_v, "liq": np.log10(s(c * v).rolling(20, min_periods=20).mean().to_numpy() + 1),
                "body": (c - o) / o, "upper": (h - np.maximum(o, c)) / rng, "gap0": o / s(c).shift().to_numpy() - 1,
                "base_age": base_age, "hist_bars": np.arange(n, dtype=float), "acc_recent": self.acc_recent.astype(float),
            }
            if MKT_DF is not None:
                m = MKT_DF.reindex(self.dates)
                for k in _MKT_COLS:
                    cols[k] = m[k].to_numpy(float)
            else:
                for k in _MKT_COLS:
                    cols[k] = np.full(n, np.nan)
        return np.column_stack([np.asarray(cols[k], float) for k in FEATURES]).astype(np.float32)


def _stop_price(rule, a: Arrays, i, marker):
    kind = rule[0]
    ctx = (marker or {}).get("ctx") or {}
    if kind == "ma":
        return a.ma[rule[1]][i]
    if kind == "ma_pct":
        return a.ma[rule[1]][i] * rule[2]
    if kind == "hill_pct":
        return ctx["hill"] * rule[1] if "hill" in ctx else np.nan
    if kind == "acc_low":
        return ctx["acc_low"] * rule[1] if "acc_low" in ctx else np.nan
    if kind == "boxhi_pct":
        return a.boxhi[i] * rule[1]
    return a.l[i]                                  # ('low',): 신호봉 저가


def _target_price(spec, a: Arrays, i, marker):
    if spec[0] == "ma":
        return a.ma[spec[1]][i]
    ctx = (marker or {}).get("ctx") or {}          # measured: 언덕 + k × (언덕 − 직전 저점)
    return ctx["hill"] + spec[1] * (ctx["hill"] - ctx["trough"]) if "hill" in ctx else np.nan


def simulate(a: Arrays, e, entry, stop, target):
    """e: 진입 봉(시가 진입). → (수익률, 사유, 보유봉, 청산봉 인덱스). target=None 이면 손절·시간청산만."""
    end = min(e + H, a.n)
    lows, highs, opens = a.l[e:end], a.h[e:end], a.o[e:end]
    s_hit = np.flatnonzero(lows <= stop)
    t_hit = np.flatnonzero(highs >= target) if target is not None else np.array([], dtype=int)
    si = s_hit[0] if len(s_hit) else 10**9
    ti = t_hit[0] if len(t_hit) else 10**9
    if si == 10**9 and ti == 10**9:
        px, why, k = a.c[end - 1], "time", end - 1
    elif si <= ti:                                  # 같은 봉이면 손절 우선
        px, why, k = min(stop, opens[si]), "stop", e + si
    else:
        px, why, k = max(target, opens[ti]), "target", e + ti
    return px / entry - 1 - COST, why, k - e + 1, k


def _whipsaw(a: Arrays, k, entry):
    """손절 뒤 20봉 안에 종가가 진입가를 회복했는가(손절이 너무 빡빡했나)."""
    seg = a.c[k + 1:k + 21]
    return bool(len(seg) and seg.max() > entry)


# ------------------------------------------------------------------ 합계 누적기
def _addv(d, key, vec):
    cur = d.get(key)
    if cur is None:
        d[key] = list(vec)
    else:
        for j, x in enumerate(vec):
            cur[j] += x


class Acc:
    """종목 단위 합계. 여러 종목(프로세스)의 결과는 merge 로 더한다."""

    def __init__(self):
        self.sim, self.fixed, self.entry, self.pb, self.date = {}, {}, {}, {}, {}
        self.nsig, self.nstock, self.tech = {}, {}, {}

    def merge(self, o: "Acc"):
        for name in ("sim", "fixed", "entry", "pb"):
            for k, v in getattr(o, name).items():
                _addv(getattr(self, name), k, v)
        for label, days in o.date.items():
            mine = self.date.setdefault(label, {})
            for dt, v in days.items():
                _addv(mine, dt, v)
        for label, n in o.nsig.items():
            self.nsig[label] = self.nsig.get(label, 0) + n
        for label, n in o.nstock.items():
            self.nstock[label] = self.nstock.get(label, 0) + n
        self.tech.update(o.tech)


def _sim_vec(ret, why, risk, held, whip):
    ret, risk = float(ret), float(risk)
    hist = [0.0] * H                                # 목표에 닿은 거래의 '걸린 봉 수' 분포(1~H봉)
    if why == "target":
        hist[min(max(int(held), 1), H) - 1] = 1.0
    return [1.0, ret, ret * ret, float(ret > 0), ret if ret > 0 else 0.0, -ret if ret < 0 else 0.0,
            float(why == "target"), float(why == "stop"), float(why == "time"), risk, ret / risk, float(held),
            float(why == "stop" and bool(whip)), ret if why == "stop" else 0.0, ret if why == "target" else 0.0,
            float(held) if why == "target" else 0.0, float(held) if why == "stop" else 0.0, *hist]


def _metrics(a: Arrays, label, i, marker, with_tech):
    """한 신호의 진입·손절·목표 결과. 변형 진입(하루·이틀 늦춤, 눌림)을 모두 계산하려고 i+5+H 봉이 필요하다."""
    e0 = i + 1
    if i + 5 + H > a.n or np.isnan(a.atr[i]) or not (a.o[e0] > 0):
        return None
    entry0 = a.o[e0]
    m = {"gap": entry0 / a.c[i] - 1, "mae5": a.l[e0:e0 + 5].min() / entry0 - 1, "mae10": a.l[e0:e0 + 10].min() / entry0 - 1,
         "fixed": [a.c[e0 + k] / entry0 - 1 - COST if e0 + k < a.n else None for k in HORIZONS], "sims": []}
    # 진입 시점 비교: 0·1·2일 늦춘 진입의 20일 수익
    m["r20"] = [a.c[e0 + d + 20] / a.o[e0 + d] - 1 - COST for d in (0, 1, 2)]
    # 눌림 지정가: 다음날 시가의 -3%에 PB_WAIT 봉 안에 닿으면 체결(시가가 이미 아래면 시가)
    limit = entry0 * (1 - PB_DIP)
    fill = next((j for j in range(e0, e0 + PB_WAIT) if a.l[j] <= limit), None)
    if fill is not None:
        px = min(a.o[fill], limit)
        m["pb"] = (True, a.c[fill + 20] / px - 1 - COST, m["r20"][0])
        fill_px = px
    else:
        m["pb"] = (False, None, m["r20"][0])
        fill_px = None

    def run(key, e, entry, stop, targets):
        if not (stop == stop) or stop >= entry or a.o[e] <= stop:
            return None
        risk = 1 - stop / entry
        if not (RISK_MIN <= risk <= RISK_MAX):
            return None
        for tname, tp in targets(entry, risk).items():
            ret, why, held, k = simulate(a, e, entry, stop, tp)
            m["sims"].append((f"{key}|{tname}", ret, why, risk, held, _whipsaw(a, k, entry) if why == "stop" else False))
        return risk

    def std_targets(entry, risk):
        return {"none": None, "3R": entry * (1 + 3 * risk), "5R": entry * (1 + 5 * risk)}

    def pct_targets(entry, risk):                  # 위험폭과 무관한 고정 목표: +10%, +20%
        return {"p10": entry * 1.10, "p20": entry * 1.20}

    atr = a.atr[i]
    run("atr2", e0, entry0, entry0 - 2 * atr, lambda en, r: {**std_targets(en, r), **pct_targets(en, r)})
    run("atr3", e0, entry0, entry0 - 3 * atr, std_targets)
    if with_tech:
        tstop = _stop_price(STOP_RULES.get(label, ("low",)), a, i, marker)
        naturals = [_target_price(sp, a, i, marker) for sp in NATURAL_TARGETS.get(label, [])]

        def tech_targets(entry, risk):
            t = {**std_targets(entry, risk), **pct_targets(entry, risk)}
            t["2R"] = entry * (1 + 2 * risk)
            for j, tp in enumerate(naturals):
                if tp == tp and tp > entry * 1.01:
                    t[f"자연{j + 1}"] = tp
            return t
        run("tech", e0, entry0, tstop, tech_targets)
        run("techw", e0, entry0, tstop - atr, tech_targets)          # 기법 손절을 ATR 1개만큼 더 넓게
    # 진입 늦추기: 손절은 같은 2×ATR 폭으로 새 진입가 기준
    for d in (1, 2):
        ed = e0 + d
        run(f"e{d}", ed, a.o[ed], a.o[ed] - 2 * atr, lambda en, r: {"none": None, "3R": en * (1 + 3 * r)})
    if fill_px is not None:
        run("epb", fill, fill_px, fill_px - 2 * atr, lambda en, r: {"none": None, "3R": en * (1 + 3 * r)})
    return m


def _accumulate(acc: Acc, labels, tech, m, date):
    for label in labels:
        acc.tech[label] = tech
        acc.nsig[label] = acc.nsig.get(label, 0) + 1
        for key, ret, why, risk, held, whip in m["sims"]:
            _addv(acc.sim, (label, key), _sim_vec(ret, why, risk, held, whip))
        for hh, r in zip(HORIZONS, m["fixed"]):
            if r is not None:
                _addv(acc.fixed, (label, hh), [1.0, float(r), float(r * r), float(r > 0)])
        _addv(acc.entry, label, [1.0, float(m["gap"]), float(m["mae5"]), float(m["mae10"]), float(m["mae10"] <= -0.03), float(m["mae10"] <= -0.05)])
        _addv(acc.date.setdefault(label, {}), date, [1.0, *[float(x) for x in m["r20"]]])
        filled, pbret, d0 = m["pb"]
        _addv(acc.pb, label, [1.0, float(filled), float(pbret) if filled else 0.0, float(d0) if filled else 0.0,
                              0.0 if filled else float(d0), 0.0 if filled else 1.0])


def _row(a: Arrays, i, date, m):
    """분석용 한 행: (날짜, 20일 수익, 3R 결과, 기법손절 결과(사유·보유·되돌림·수익), 특징들...)."""
    sims = {k: (ret, why, held, whip) for k, ret, why, risk, held, whip in m["sims"]}
    t3 = sims.get("tech|3R") or sims.get("atr2|3R")
    tn = sims.get("tech|none") or sims.get("atr2|none")
    tgt3 = np.nan if not t3 else (1.0 if t3[1] == "target" else 0.0 if t3[1] == "stop" else np.nan)
    code_why = {"time": 0.0, "stop": 1.0, "target": 2.0}
    none = (tn[0], code_why[tn[1]], tn[2], float(bool(tn[3]))) if tn else (np.nan, np.nan, np.nan, np.nan)
    return (date, m["r20"][0], tgt3, *none, *[float(x) for x in a.F[i]])


def _sample(rows, code):
    if len(rows) <= ROW_CAP:
        return rows
    rng = np.random.default_rng(int.from_bytes(code.encode()[:6].ljust(6, b"0"), "little"))
    idx = rng.choice(len(rows), ROW_CAP, replace=False)
    return [rows[j] for j in sorted(idx)]


def analyze_code(code: str):
    """한 종목의 합계(Acc) + 시장 평균용 날짜별 수익 배열 + 성공·실패 분석용 행(기법마다 무작위 일부)."""
    df = service.load_prices(code)
    if len(df) < MIN_BARS:
        return code, Acc(), _empty_mk(), {}
    a = Arrays(df)
    acc = Acc()
    seen = set()
    rows = {}
    for name in techniques.ORDER:
        if techniques.REGISTRY[name]["status"] != "ready":
            continue
        res = techniques.apply(name, df, bars=len(df))
        for mk in res["markers"] if res else []:
            if mk["side"] != "buy":
                continue
            i = a.index.get(mk["date"])
            if i is None:
                continue
            m = _metrics(a, mk["label"], i, mk, True)
            if not m:
                continue
            labels = [mk["label"]]
            if name in ACC_VARIANT_TECHS:
                labels.append(mk["label"] + (ACC_SUFFIX_ON if a.acc_recent[i] else ACC_SUFFIX_OFF))
            _accumulate(acc, labels, name, m, mk["date"])
            seen.update(labels)
            rows.setdefault(mk["label"], []).append(_row(a, i, mk["date"], m))
    for label in seen:
        acc.nstock[label] = 1
    base_rows = []
    for i in range(max(MIN_BARS // 2, 130), a.n - H - 5, BASE_STEP):
        m = _metrics(a, BASE_LABEL, i, None, False)
        if m:
            _accumulate(acc, [BASE_LABEL], "기준", m, a.dates[i])
            base_rows.append(_row(a, i, a.dates[i], m))
    acc.nstock[BASE_LABEL] = 1
    rows[BASE_LABEL] = base_rows
    return code, acc, _daily_ret20(a), {k: _sample(v, code) for k, v in rows.items()}


def _init_worker(path):
    global MKT_DF
    try:
        MKT_DF = pd.read_pickle(path)
    except (OSError, ValueError):
        MKT_DF = None


def _daily_returns(code):
    """종목의 날짜별 등락률(수정주가). 시장 지수(모든 종목 평균)를 만드는 재료."""
    df = service.load_prices(code)
    if len(df) < 30:
        return np.array([], dtype=object), np.array([])
    r = df["close"].pct_change().to_numpy(float)[1:]
    r = np.clip(r, -0.3, 0.3)                      # 오류성 극단값 방지(상·하한가 30%)
    return df.index.strftime("%Y-%m-%d").to_numpy()[1:], r


def build_market_index(codes, workers) -> pd.DataFrame:
    """모든 종목 일간 등락률의 날짜별 평균 = equal-weight 시장 지수 → 직전 20·60일 수익, 52주 고점 대비, 20일 변동성."""
    sums, cnts = {}, {}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for dates, r in ex.map(_daily_returns, codes, chunksize=8):
            if len(dates) == 0:
                continue
            ser = pd.Series(r, index=dates)
            for dt, v in ser.items():
                sums[dt] = sums.get(dt, 0.0) + v
                cnts[dt] = cnts.get(dt, 0) + 1
    idx = pd.DataFrame({"sum": pd.Series(sums), "n": pd.Series(cnts)}).sort_index()
    idx = idx[idx["n"] >= 30]                       # 종목이 너무 적던 옛날은 지수로 쓰지 않음
    ret = idx["sum"] / idx["n"]
    level = (1 + ret).cumprod()
    out = pd.DataFrame({
        "mkt_r20": level / level.shift(20) - 1, "mkt_r60": level / level.shift(60) - 1,
        "mkt_dd250": level / level.rolling(250, min_periods=120).max() - 1, "mkt_vol20": ret.rolling(20, min_periods=20).std(),
        "mkt_ret": ret, "mkt_n": idx["n"]})
    out.to_pickle(MKT_PATH)
    return out


def _empty_mk():
    return np.array([], dtype=object), np.zeros((0, 3))


def _daily_ret20(a: Arrays):
    """그 종목의 모든 날 i: '다음날 시가(+d일 늦춤) 진입 → 20봉 뒤 종가' 수익률 (d=0,1,2). 날짜별 시장 평균을 만드는 데 쓴다."""
    n = a.n
    if n < 30:
        return _empty_mk()
    i = np.arange(0, n - 23)
    cols = [a.c[i + 21 + d] / a.o[i + 1 + d] - 1 - COST for d in (0, 1, 2)]
    ok = np.all([a.o[i + 1 + d] > 0 for d in (0, 1, 2)], axis=0)
    return a.dates[i][ok], np.column_stack([c[ok] for c in cols])


# ------------------------------------------------------------------ 집계
def _hist_quantiles(hist, qs=(0.25, 0.5, 0.75)):
    total = sum(hist)
    if total <= 0:
        return [None] * len(qs)
    out, cum, j = [], 0.0, 0
    for q in qs:
        while j < len(hist) and cum + hist[j] < q * total:
            cum += hist[j]
            j += 1
        out.append(j + 1)
    return out


def _sim_stats(v):
    n, s, s2, wins, gains, losses, nt, ns, ntm, srisk, sexp, sheld, nwhip, sret_stop, sret_tgt, sht, shs = v[:17]
    hist = v[17:17 + H]
    q = _hist_quantiles(hist)
    return {"n": int(n), "mean": s / n, "win": wins / n, "pf": (gains / losses) if losses > 0 else None,
            "target": nt / n, "stop": ns / n, "time": ntm / n, "risk": srisk / n, "exp_r": sexp / n, "held": sheld / n,
            "avg_loss_stop": (sret_stop / ns) if ns else None, "avg_gain_target": (sret_tgt / nt) if nt else None,
            "whipsaw": (nwhip / ns) if ns else None,
            "held_target": (sht / nt) if nt else None, "held_stop": (shs / ns) if ns else None,
            "held_q25": q[0], "held_q50": q[1], "held_q75": q[2]}


def _fixed_stats(v):
    n, s, s2, wins = v
    mean = s / n
    return {"n": int(n), "mean": mean, "win": wins / n, "std": math.sqrt(max(s2 / n - mean * mean, 0.0) * n / max(n - 1, 1))}


def _excess_block(days: dict, mk: pd.DataFrame, d_idx: int, since: str | None = None):
    """날짜별 (신호 20일 평균 − 그날 시장 평균). 같은 날 신호는 한 표본으로 묶어 t 값을 낸다."""
    xs, weights, nsig = [], [], 0
    for dt, v in days.items():
        if since and dt < since:
            continue
        if dt not in mk.index:
            continue
        cnt = v[0]
        xs.append(v[1 + d_idx] / cnt - mk.at[dt, d_idx])
        weights.append(cnt)
        nsig += cnt
    if not xs:
        return None
    xs, w = np.array(xs), np.array(weights, float)
    nd = len(xs)
    sd = float(xs.std(ddof=1)) if nd > 1 else 0.0
    return {"n": int(nsig), "days": nd, "mean": float((xs * w).sum() / w.sum()),
            "t_day": float(xs.mean() / (sd / math.sqrt(nd))) if sd > 0 else None}


def finalize(acc: Acc, mk: pd.DataFrame) -> dict:
    labels = {}
    for label, nsig in acc.nsig.items():
        d = {"tech": acc.tech[label], "n_signals": nsig, "n_stocks": acc.nstock.get(label, 0)}
        e = acc.entry[label]
        n = e[0]
        d["entry"] = {"n": int(n), "gap": e[1] / n, "mae5": e[2] / n, "mae10": e[3] / n, "dip3_10": e[4] / n, "dip5_10": e[5] / n}
        d["fixed"] = {str(h): _fixed_stats(acc.fixed[(label, h)]) for h in HORIZONS if (label, h) in acc.fixed}
        d["sim"] = {k[1]: _sim_stats(v) for k, v in acc.sim.items() if k[0] == label}
        days = acc.date.get(label, {})
        d["entry_study"] = {str(dl): {"mean": sum(v[1 + dl] for v in days.values()) / max(sum(v[0] for v in days.values()), 1),
                                      "excess": _excess_block(days, mk, dl), "excess_recent": _excess_block(days, mk, dl, RECENT_FROM)}
                            for dl in (0, 1, 2)}
        d["excess"] = d["entry_study"]["0"]["excess"]
        d["excess_recent"] = d["entry_study"]["0"]["excess_recent"]
        p = acc.pb[label]
        n_sig, n_fill, s_pb, s_d0_f, s_d0_u, n_un = p
        d["pb"] = {"n": int(n_sig), "fill_rate": n_fill / n_sig, "pb_mean": (s_pb / n_fill) if n_fill else None,
                   "d0_same": (s_d0_f / n_fill) if n_fill else None, "d0_unfilled": (s_d0_u / n_un) if n_un else None}
        labels[label] = d
    base = labels.pop(BASE_LABEL, None)
    return {"baseline": ({"entry": base["entry"], "fixed": base["fixed"], "sim": base["sim"], "entry_study": base["entry_study"]} if base else {}),
            "labels": labels}


def _work(code):
    try:
        return analyze_code(code)
    except Exception as e:  # 한 종목의 오류가 전체를 막지 않게
        return code, None, str(e)


def run(limit: int = 0, workers: int = 0, log=print) -> dict:
    t0 = time.time()
    with db.get_conn() as c:
        codes = [r["code"] for r in c.execute(
            "SELECT code FROM prices GROUP BY code HAVING COUNT(*) >= ? ORDER BY code", (MIN_BARS,))]
        span = c.execute("SELECT MIN(date), MAX(date) FROM prices").fetchone()
    if limit:
        codes = codes[:limit]
    workers = workers or max(1, min(6, (os.cpu_count() or 2) - 2))
    log(f"시장 지수 만드는 중({len(codes)}종목)")
    build_market_index(codes, workers)
    log(f"시장 지수 완료 {time.time() - t0:.0f}초")
    total, errors, mk_d, mk_r, why_rows = Acc(), [], [], [], {}
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=(MKT_PATH,)) as ex:
        for n, out in enumerate(ex.map(_work, codes, chunksize=4), 1):
            if out[1] is None:
                errors.append((out[0], out[2]))
                continue
            total.merge(out[1])
            mk_d.append(out[2][0])
            mk_r.append(out[2][1])
            for label, rows in out[3].items():
                why_rows.setdefault(label, []).extend((out[0],) + r for r in rows)
            if n % 100 == 0:
                log(f"[{n}/{len(codes)}] 신호 {sum(total.nsig.values())}건 · {time.time() - t0:.0f}초")
    if mk_d:
        mk = pd.DataFrame(np.concatenate(mk_r), index=np.concatenate(mk_d)).groupby(level=0).mean()
    else:
        mk = pd.DataFrame(columns=[0, 1, 2])
    try:                                           # 합계는 따로 보관 — 집계 방식을 바꿔도 다시 돌리지 않고 finalize 만 다시 하면 된다
        pd.to_pickle((total, mk), config.DATA_DIR / "backtest_acc.pkl")
    except Exception:
        pass
    rep = finalize(total, mk)
    nsig = sum(v for k, v in total.nsig.items() if k != BASE_LABEL and not k.endswith((ACC_SUFFIX_ON, ACC_SUFFIX_OFF)))
    rep["meta"] = {"generated": datetime.now().isoformat(timespec="seconds"), "stocks": len(codes), "signals": nsig,
                   "baseline_samples": total.nsig.get(BASE_LABEL, 0), "errors": errors[:20], "date_min": span[0], "date_max": span[1],
                   "params": {"horizon_bars": H, "cost": COST, "base_step": BASE_STEP, "risk_range": [RISK_MIN, RISK_MAX],
                              "pb_dip": PB_DIP, "pb_wait": PB_WAIT, "elapsed_sec": round(time.time() - t0)}}
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
    try:
        from core import why
        why.save_rows(why_rows)
        why.WHY_PATH.write_text(json.dumps(why.analyze(why_rows, mk), ensure_ascii=False), encoding="utf-8")
    except Exception as e:  # 원인 분석 실패가 본 분석 결과를 막지 않게
        log(f"원인 분석 실패: {e}")
    return rep


def load_report() -> dict | None:
    try:
        return json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
