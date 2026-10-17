"""차트 비교 화면에 겹쳐 그리는 두 가지 — '예측'(단테 기법 신호) · 'AI'(상승 확률).

- 예측: core/ai.py 가 학습에 쓰는 것과 같은 단테 계열 신호(256·이평 때리기·224·밥그릇·3번 자리·공구리·역매공파·매집봉·세력선)를
  그 종목의 모든 날짜에 계산해 마커로 돌려준다. 기법마다 매매 계획(data/plan.json)의 '처음 보는 해' 성과와 현재 규칙을 붙인다.
- AI: data/ai_model.joblib 로 날짜마다 20일 +20% 확률을 계산해 선으로, 상위 10% 경계를 넘어선 날을 마커로 돌려준다.
"""
import json
import threading

import numpy as np
import pandas as pd

from core import ai, avoid_study, drop_risk, backtest as bt, combined_study as cs, indicators as ind, plan_study

AI_THR_DEFAULT = 0.138          # 2026-10-08 거래 가능 종목 1,322개의 AI 확률 상위 10% 경계(plan.json 에 값이 없을 때)
DANTE_IDX = list(range(ai.N_DANTE)) + [11, 12, 13]   # 11개 단테 기법 + 세력선(7·15·33일선 지지)
DANTE_WHY = {
    "256 자리(단기)": "60>20>5 역배열 뒤 5일선이 20일선을 골든크로스, 60일선은 아직 위 — 20일선을 손절 기준으로",
    "256 완성(단기)": "256 자리 뒤 종가가 60일선을 뚫음(추세 전환)",
    "256 자리(중장기)": "224>112 역배열 뒤 단기선이 112일선 위로 — 6개월·1년선 기준",
    "256 완성(중장기)": "종가가 224일선(1년선)을 뚫음",
    "이평 때리기(112)": "큰 하락으로 벌어진 장기 이평(224가 112보다 10%↑) 뒤 종가가 112일선에 안착",
    "224 돌파": "종가가 224일선을 돌파",
    "밥그릇 돌파": "224일선 아래 4개월 이상 머문 뒤 224일선 ±10% 안으로 진입",
    "3번 자리(눌림)": "224일선 돌파 뒤 눌러 주는 흔들기 자리",
    "공구리(언덕 돌파)": "하락 중 직전 반등 고점(언덕)을 종가로 돌파(바닥 탈피)",
    "역매공파": "역배열 + 매집봉 + 공구리가 모두 있는 날",
    "매집봉 돌파": "매집봉(거래량 2배 장대양봉·윗꼬리) 고가를 종가로 돌파",
    "7일선 지지": "정배열에서 저가가 7일선에 닿고 양봉으로 위에서 마감(세력선)",
    "15일선 지지": "정배열에서 저가가 15일선에 닿고 양봉으로 위에서 마감(세력선)",
    "33일선 지지": "정배열에서 저가가 33일선에 닿고 양봉으로 위에서 마감(세력선)",
}
_plan_cache = {"mtime": None, "rep": None}
_mkt_cache = {"mtime": None, "df": None}


def plan():
    p = plan_study.PLAN_PATH
    try:
        mt = p.stat().st_mtime
    except OSError:
        return None
    if _plan_cache["mtime"] != mt:
        try:
            _plan_cache.update(mtime=mt, rep=json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None
    return _plan_cache["rep"]


def _mkt():
    try:
        mt = bt.MKT_PATH.stat().st_mtime
    except OSError:
        return None
    if _mkt_cache["mtime"] != mt:
        try:
            _mkt_cache.update(mtime=mt, df=pd.read_pickle(bt.MKT_PATH))
        except (OSError, ValueError):
            return None
    return _mkt_cache["df"]


def ai_threshold() -> float:
    """AI 상위 10% 경계: 전 종목 훑기(signals.json, 매일) → 매매 계획(plan.json) → 기본값 순."""
    try:
        from core import signal_scan
        thr = (signal_scan.load() or {}).get("meta", {}).get("thr10")
        if thr:
            return float(thr)
    except Exception:
        pass
    rep = plan()
    thr = ((rep or {}).get("today") or {}).get("ai_thr")
    return float(thr) if thr else AI_THR_DEFAULT


_feat_cache: dict = {}
_feat_lock = threading.Lock()


def _features(df):
    """특징 행렬(날짜 × 76). 같은 종목·같은 데이터면 다시 계산하지 않는다(예측·AI 가 같은 행렬을 씀, 2~3초).
    비교 화면은 두 요청을 동시에 보내므로 잠금으로 한 번만 계산한다."""
    with _feat_lock:
        mkt = _mkt()                                      # 먼저 읽어야 캐시 키의 시장 지수 시각이 고정된다
        key = (len(df), str(df.index[-1]), float(df["close"].iloc[-1]), float(df["volume"].iloc[-1]), _mkt_cache["mtime"])
        hit = _feat_cache.get(key)
        if hit is not None:
            return hit
        if mkt is not None:
            dates = pd.Index(df.index.strftime("%Y-%m-%d"))
            mkt = mkt.reindex(mkt.index.union(dates)).ffill(limit=5)   # 시장 지수가 하루 늦게 갱신돼도 마지막 값으로
        X = ai.compute_features(df, mkt, True)
        if len(_feat_cache) >= 16:
            _feat_cache.pop(next(iter(_feat_cache)))
        _feat_cache[key] = X
        return X


def _tech_info(rep):
    """매매 계획의 기법별 '처음 보는 해' 성과·현재 규칙."""
    out = {}
    for t in (rep or {}).get("techs", []):
        e = t["oos"]["exit"]
        good = bool(e and e["mean"] > 0 and t["oos"]["years_pos"] >= t["years"] * 0.6)
        r = t["rule_now"]
        out[t["label"]] = {"mean": e["mean"] if e else None, "p_up": e["p_up"] if e else None, "good": good,
                           "years": f"{t['oos']['years_pos']}/{t['years']}", "rule": f"{r['cond']} → {r['entry']} → {r['exit']}", "ai": bool(t.get("ai"))}
    return out


def _bar_index(df, tf):
    return None if tf == "D" else ind.resample(df, tf).index


def _to_bar(dates, bar_index):
    """일봉 날짜 → 그 날짜가 들어가는 주봉·월봉의 날짜(봉 끝 날짜)."""
    if bar_index is None:
        return list(dates)
    pos = np.minimum(bar_index.searchsorted(pd.to_datetime(dates)), len(bar_index) - 1)
    return list(bar_index[pos].strftime("%Y-%m-%d"))


def _daily_bars(tf, bars):
    return bars * {"D": 1, "W": 5, "M": 21}[tf]


def _strong(df, D):
    """신호 날짜별 '강' 여부 = 피함 7개 규칙에 안 걸림 + 60일선 상승 중 + 종가가 224일선 위.
    2026-10-11 전 종목(2,656개·표본 140만 행) 점검: 14개 기법 신호의 시장 대비 20일 수익 평균이 학습 −1.02%p·시험(2022~) −0.95%p 였는데,
    이 조건을 만족하는 신호만 보면 학습 −0.27%p·시험 +0.15%p. 수익을 보장하는 조건이 아니라 약한 신호를 걸러 내는 용도. 신호일 종가까지 아는 값만 쓴다."""
    X = D.copy()
    X["zero_vol60"] = (df["volume"].to_numpy(float) <= 0).astype(float)
    X["zero_vol60"] = X["zero_vol60"].rolling(60, min_periods=1).sum()
    rules = dict(avoid_study.RULES)
    avoid = np.zeros(len(X), bool)
    for name in cs.AVOID_NAMES:
        avoid |= rules[name](X).fillna(False).to_numpy()
    return (~avoid) & (X["slope60"].to_numpy() > 0) & (X["dist224"].to_numpy() > 0)


def dante(df, tf="D", bars=750):
    X = _features(df)
    D = pd.DataFrame(X, columns=ai.NAMES)
    strong = _strong(df, D)
    try:
        risk_p = drop_risk.probs(X)[0]                      # 날짜별 하락 위험 확률 — '확실한 저점' 판정에 쓴다
    except RuntimeError:
        risk_p = None                                       # 위험 모델이 없으면 구분하지 않음(전부 표시)
    bottom_labels = set(ai.SIGNAL_LABELS[:11])              # 바닥·전환 기법 11개(256·이평 때리기·224·밥그릇·3번 자리·공구리·역매공파·매집봉)
    n = len(df)
    start = max(n - _daily_bars(tf, bars), 0)
    info = _tech_info(plan())
    bar_index = _bar_index(df, tf)
    dates = df.index.strftime("%Y-%m-%d")
    raw = []
    for j in DANTE_IDX:
        lab = ai.SIGNAL_LABELS[j]
        col = D[f"sig{j:02d}"].to_numpy()
        for i in np.flatnonzero(col[start:] == 0) + start:
            raw.append((i, lab))
    raw.sort()
    bar_dates = _to_bar([dates[i] for i, _ in raw], bar_index)   # 한 번에 변환(마커마다 하면 주봉에서 1초↑)
    markers, seen = [], set()
    for (i, lab), d in zip(raw, bar_dates):
        if (d, lab) in seen:
            continue
        seen.add((d, lab))
        t = info.get(lab)
        markers.append({"date": d, "side": "buy", "label": lab, "why": DANTE_WHY.get(lab, ""), "good": bool(t and t["good"]), "strong": bool(strong[i]),
                        **({"bottom": True, "certain": bool(np.isfinite(risk_p[i]) and risk_p[i] < drop_risk.CERTAIN_P)} if (risk_p is not None and lab in bottom_labels) else {}),
                        "stat": (f"처음 보는 해 평균 {t['mean'] * 100:+.2f}%·상승 {t['p_up'] * 100:.0f}% ({t['years']}년 플러스)" if t and t["mean"] is not None else "")})
    last = D.iloc[-1]
    recent = []
    for j in DANTE_IDX:
        lab = ai.SIGNAL_LABELS[j]
        ago = last[f"sig{j:02d}"]
        if np.isfinite(ago) and 0 <= ago <= 20:
            t = info.get(lab)
            recent.append({"label": lab, "ago": int(ago), "good": bool(t and t["good"]), "strong": bool(strong[len(strong) - 1 - int(ago)]),
                           "certain": bool(risk_p is not None and lab in bottom_labels and np.isfinite(risk_p[len(risk_p) - 1 - int(ago)]) and risk_p[len(risk_p) - 1 - int(ago)] < drop_risk.CERTAIN_P), "rule": t["rule"] if t else "", "mean": t["mean"] if t else None, "p_up": t["p_up"] if t else None})
    recent.sort(key=lambda r: r["ago"])
    techs = {ai.SIGNAL_LABELS[j]: info[ai.SIGNAL_LABELS[j]] for j in DANTE_IDX if ai.SIGNAL_LABELS[j] in info}
    if recent:
        parts = []
        for r in recent:
            s = f"{r['label']} {r['ago']}일 전" if r["ago"] else f"{r['label']} 오늘"
            if r["mean"] is not None:
                s += f"({'✔' if r['good'] else '✖'} {r['mean'] * 100:+.1f}%·{r['p_up'] * 100:.0f}%)"
            s += " 강" if r["strong"] else " 약"
            parts.append(s)
        summary = "최근 20일 단테 신호: " + ", ".join(parts) + ". ✔ = 처음 보는 해에서 규칙이 통한 기법. 강 = 급등·고변동 등 피함 규칙에 안 걸리고 60일선 상승·224일선 위."
        best = next((r for r in recent if r["good"]), None)
        if best and best["rule"]:
            summary += f" 규칙({best['label']}): {best['rule']}."
    else:
        summary = "최근 20일 안에 단테 기법 신호가 없습니다."
    return {"name": "예측", "kind": "dante", "markers": markers, "recent": recent, "techs": techs, "summary": summary,
            "note": "단테 계열 14개 신호(256·이평 때리기·224·밥그릇·3번 자리·공구리·역매공파·매집봉·세력선). 초록 ▲ = 확실한 저점 신호(바닥 기법 + 신호일 하락 위험 18% 미만, 기본으로는 이것만 표시), 빨간 화살표 = 강(피함 규칙 없음·60일선 상승·224일선 위), 금색 ■★n = 같은 날 강 신호 3개 이상(눈에 띄게 한 표시일 뿐 겹칠수록 성과가 좋아지진 않았음), 회색 = 약(과거 점검에서 걸러낼수록 시장 대비 수익이 나아짐). 괄호는 매매 계획의 '처음 보는 해' 성과(규칙대로, 비용 포함)."}


_model_cache = {"mtime": None, "model": None}


def _model():
    """AI 모델은 한 번만 읽어 둔다(요청마다 joblib 로 읽으면 느림). 파일이 바뀌면 다시 읽는다."""
    try:
        mt = ai.MODEL_PATH.stat().st_mtime
    except OSError:
        return None
    if _model_cache["mtime"] != mt:
        _model_cache.update(mtime=mt, model=cs._load_model())
    return _model_cache["model"]


def ai_pred(df, tf="D", bars=750):
    model = _model()
    if model is None:
        raise RuntimeError("AI 모델이 없습니다 (python manage.py ai-train)")
    X = _features(df)
    valid = np.isfinite(X[:, ai.NAMES.index("ret60")])
    P = np.full(len(df), np.nan)
    if valid.any():
        P[valid] = model.predict_proba(X[valid])[:, 1]
    thr = ai_threshold()
    n = len(df)
    start = max(n - _daily_bars(tf, bars), 0)
    dates = df.index.strftime("%Y-%m-%d")
    bar_index = _bar_index(df, tf)
    bars_of = _to_bar(list(dates[start:]), bar_index)
    prob, seen = [], {}
    for k, i in enumerate(range(start, n)):
        if np.isfinite(P[i]):
            seen[bars_of[k]] = float(P[i])                 # 주봉·월봉은 그 봉의 마지막 날 값
    prob = [{"time": d, "value": round(v, 4)} for d, v in seen.items()]
    markers = []
    above = P >= thr
    for i in range(start, n):
        if above[i] and (i == 0 or not above[i - 1]):     # 상위 10% 경계를 넘어서는 날
            d = _to_bar([dates[i]], bar_index)[0]
            if markers and markers[-1]["date"] == d:
                continue
            markers.append({"date": d, "side": "buy", "label": "AI 상위 10%", "why": f"20일 +20% 확률 {P[i] * 100:.1f}% (경계 {thr * 100:.1f}%)"})
    info = _tech_info(plan())
    t = next((v for v in info.values() if v["ai"]), None)
    p_now = float(P[-1]) if np.isfinite(P[-1]) else None
    if p_now is None:
        summary = "AI 확률을 계산할 데이터가 부족합니다(250봉 이상 필요)."
    else:
        summary = f"오늘 AI 확률 {p_now * 100:.1f}% — 상위 10% 경계 {thr * 100:.1f}% {'위 ✔' if p_now >= thr else '아래'}."
        if t and t["mean"] is not None:
            summary += f" AI 상위 10%는 처음 보는 해 평균 {t['mean'] * 100:+.2f}%·상승 {t['p_up'] * 100:.0f}%({t['years']}년 플러스), 규칙: {t['rule']}."
    return {"name": "AI", "kind": "ai", "prob": prob, "thr": thr, "markers": markers, "latest": {"prob": p_now, "top": bool(p_now is not None and p_now >= thr)},
            "rule": t, "summary": summary,
            "note": "선 = 날짜마다 그날까지의 정보로 계산한 '20일 안 +20%' 확률(2021년까지 학습한 모델). 점선 = 상위 10% 경계, 마커 = 경계를 넘어선 날."}


def _risk_track(df, P, cut_hi, cut_mid):
    """이 종목에서 지난 예측이 맞았나 — 2022년 이후(모델이 학습에 안 쓴 기간), 신호일 다음날 시가 진입 20거래일 뒤 종가 수익(비용 포함)."""
    o, c = df["open"].to_numpy(float), df["close"].to_numpy(float)
    n = len(df)
    net = np.full(n, np.nan)
    ok = np.arange(n) + 21 < n
    i = np.flatnonzero(ok)
    net[i] = c[i + 21] / o[i + 1] - 1 - ai.COST
    m = (df.index >= "2022-01-01") & np.isfinite(P) & np.isfinite(net)
    if m.sum() < 60:
        return None
    out = {}
    for name, sel in (("전체", m), ("높음", m & (P >= cut_hi)), ("주의", m & (P >= cut_mid) & (P < cut_hi)), ("낮음", m & (P < cut_mid))):
        r = net[sel]
        out[name] = {"n": int(len(r)), "dn10": float((r <= -0.10).mean()) if len(r) else None, "mean": float(r.mean()) if len(r) else None}
    return out


def risk(df, tf="D", bars=750):
    """하락 위험 구간 — 날짜별 '20일 안 −10%↓ 확률'(선)과 높음·주의 구간(배경 띠). 주봉·월봉은 그 봉 안의 최댓값."""
    X = _features(df)
    P, cut_hi, cut_mid = drop_risk.probs(X)
    n = len(df)
    start = max(n - _daily_bars(tf, bars), 0)
    dates = df.index.strftime("%Y-%m-%d")
    bar_index = _bar_index(df, tf)
    bars_of = _to_bar(list(dates[start:]), bar_index)
    best = {}
    for k, i in enumerate(range(start, n)):
        if np.isfinite(P[i]):
            d = bars_of[k]
            best[d] = max(best.get(d, 0.0), float(P[i]))
    prob = [{"time": d, "value": round(v, 4)} for d, v in best.items()]
    zones = [{"time": d, "level": 2 if v >= cut_hi else 1} for d, v in best.items() if v >= cut_mid]
    # 표시 날짜: 위험 확률이 높음 경계를 처음 넘어선 날 / 급등 뒤 과열(60일 +80%↑ 또는 224일선 대비 +100%↑)이 처음 된 날 — 직전 10봉에 같은 상태가 없었던 날만
    ret60_a, dist224_a = X[:, ai.NAMES.index("ret60")], X[:, ai.NAMES.index("dist224")]
    state = {"risk": np.nan_to_num(P, nan=0.0) >= cut_hi, "heat": (np.nan_to_num(ret60_a, nan=0.0) >= 0.8) | (np.nan_to_num(dist224_a, nan=0.0) >= 1.0)}
    events, seen_ev = [], set()
    for kind, st in state.items():
        before = pd.Series(st).shift(1).rolling(10, min_periods=1).max().fillna(0).to_numpy() > 0
        idx = [i for i in np.flatnonzero(st & ~before) if i >= start]
        for i, d in zip(idx, _to_bar([dates[i] for i in idx], bar_index)):
            if (kind, d) not in seen_ev:
                seen_ev.add((kind, d))
                events.append({"time": d, "kind": kind})
    p_now = float(P[-1]) if np.isfinite(P[-1]) else None
    rep = drop_risk.report() or {}
    lv = {r["level"]: r for r in rep.get("levels", [])}

    def stat(name):
        r = lv.get(name)
        return f"{name}: 20일 안 −10%↓ {r['dn10'] * 100:.0f}%·−20%↓ {r['dn20'] * 100:.0f}%" if r else ""
    if p_now is None:
        summary = "위험 확률을 계산할 데이터가 부족합니다(250봉 이상 필요)."
        level = None
    else:
        level = "높음" if p_now >= cut_hi else "주의" if p_now >= cut_mid else "낮음"
        summary = f"오늘 하락 위험 {level} — 20일 안 −10% 이하 확률 {p_now * 100:.0f}% (평균 {lv.get('전체', {}).get('dn10', 0) * 100:.0f}%)."
    track = _risk_track(df, P, cut_hi, cut_mid)
    last = X[-1]
    ret60, dist224 = float(last[ai.NAMES.index("ret60")]), float(last[ai.NAMES.index("dist224")])
    # 급등 뒤 과열(60일 +80%↑ 또는 224일선 대비 +100%↑): 시험 두 기간 모두 20일 안 −20%↓ 22~29%·−30%↓ 8~14%(전체 7%·2%)였다
    overheat = None
    if np.isfinite(ret60) and np.isfinite(dist224) and (ret60 >= 0.8 or dist224 >= 1.0):
        overheat = {"ret60": ret60, "dist224": dist224}
    fc = drop_risk.forecast(X)
    return {"name": "하락 위험", "kind": "risk", "prob": prob, "zones": zones, "forecast": fc, "track": track, "overheat": overheat, "events": events, "cut_hi": cut_hi, "cut_mid": cut_mid,
            "latest": {"prob": p_now, "level": level}, "summary": summary,
            "note": "빨간 띠 = 위험 높음(시험 기간 상위 10%), 연한 띠 = 주의(상위 10~25%), 아래 선 = 확률. '곧 떨어진다'는 방향 예측이 아니라 크게 흔들릴 위험이 큰 구간(변동성 중심)입니다. "
                    + " · ".join(x for x in (stat("높음"), stat("낮음")) if x) + f" (2022~ 시험, AUC {rep.get('auc_test', 0):.2f})"
                    + (f" 화면의 모델은 {rep['live_through']}까지 결과가 확정된 데이터로 학습했고 한 달마다 새 데이터로 다시 학습합니다." if rep.get("live_through") else "")}
