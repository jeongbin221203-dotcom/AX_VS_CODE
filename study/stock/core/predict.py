"""차트 비교 화면에 겹쳐 그리는 두 가지 — '예측'(단테 기법 신호) · 'AI'(상승 확률).

- 예측: core/ai.py 가 학습에 쓰는 것과 같은 단테 계열 신호(256·이평 때리기·224·밥그릇·3번 자리·공구리·역매공파·매집봉·세력선)를
  그 종목의 모든 날짜에 계산해 마커로 돌려준다. 기법마다 매매 계획(data/plan.json)의 '처음 보는 해' 성과와 현재 규칙을 붙인다.
- AI: data/ai_model.joblib 로 날짜마다 20일 +20% 확률을 계산해 선으로, 상위 10% 경계를 넘어선 날을 마커로 돌려준다.
"""
import json
import threading

import numpy as np
import pandas as pd

from core import ai, backtest as bt, combined_study as cs, indicators as ind, plan_study

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


def dante(df, tf="D", bars=750):
    X = _features(df)
    D = pd.DataFrame(X, columns=ai.NAMES)
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
        markers.append({"date": d, "side": "buy", "label": lab, "why": DANTE_WHY.get(lab, ""), "good": bool(t and t["good"]),
                        "stat": (f"처음 보는 해 평균 {t['mean'] * 100:+.2f}%·상승 {t['p_up'] * 100:.0f}% ({t['years']}년 플러스)" if t and t["mean"] is not None else "")})
    last = D.iloc[-1]
    recent = []
    for j in DANTE_IDX:
        lab = ai.SIGNAL_LABELS[j]
        ago = last[f"sig{j:02d}"]
        if np.isfinite(ago) and 0 <= ago <= 20:
            t = info.get(lab)
            recent.append({"label": lab, "ago": int(ago), "good": bool(t and t["good"]), "rule": t["rule"] if t else "", "mean": t["mean"] if t else None, "p_up": t["p_up"] if t else None})
    recent.sort(key=lambda r: r["ago"])
    techs = {ai.SIGNAL_LABELS[j]: info[ai.SIGNAL_LABELS[j]] for j in DANTE_IDX if ai.SIGNAL_LABELS[j] in info}
    if recent:
        parts = []
        for r in recent:
            s = f"{r['label']} {r['ago']}일 전" if r["ago"] else f"{r['label']} 오늘"
            if r["mean"] is not None:
                s += f"({'✔' if r['good'] else '✖'} {r['mean'] * 100:+.1f}%·{r['p_up'] * 100:.0f}%)"
            parts.append(s)
        summary = "최근 20일 단테 신호: " + ", ".join(parts) + ". ✔ = 처음 보는 해에서 규칙이 통한 기법."
        best = next((r for r in recent if r["good"]), None)
        if best and best["rule"]:
            summary += f" 규칙({best['label']}): {best['rule']}."
    else:
        summary = "최근 20일 안에 단테 기법 신호가 없습니다."
    return {"name": "예측", "kind": "dante", "markers": markers, "recent": recent, "techs": techs, "summary": summary,
            "note": "단테 계열 14개 신호(256·이평 때리기·224·밥그릇·3번 자리·공구리·역매공파·매집봉·세력선). 괄호는 매매 계획의 '처음 보는 해' 성과(규칙대로, 비용 포함)."}


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
