"""하락 위험 구간 — '20거래일 안에 −10% 이하로 끝날 확률'(다음날 시가 진입 기준, 비용 0.3% 포함).

추세(224일선 아래·60일선 하락 등)만으로는 더 떨어진다는 근거가 없었고(전체 평균과 같은 수준), 변동성 중심의 모델만 구분력이 있었다
(2022~ 시험 AUC 0.65, 위험 상위 10% 의 −10%↓ 44% vs 하위 10% 8.7%). 그래서 방향 예측이 아니라 '크게 흔들릴 위험' 표시다.
학습 ≤2021, 시험 2022~. 신호일 종가까지 아는 특징만 쓴다(ai.compute_features).
"""
import json
import os
import random
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime

import numpy as np
import pandas as pd

import config
from core import ai, db

MODEL_PATH = config.DATA_DIR / "drop_model.joblib"
REPORT_PATH = config.DATA_DIR / "drop_risk.json"
DROP = -0.10
TRAIN_END = "2021-12-31"
# 시장 지수 특징(rs20·mkt_*)은 뺀다 — 학습 표본을 만들 때 시장 지수를 안 불러 값이 없고, 화면에서는 있어 둘이 어긋난다
COLS = [c for c in ai.NAMES if not c.startswith(("sig", "mkt_")) and c not in ("dante_n10", "sig_n10", "hist_bars", "rs20")]
HI_Q, MID_Q = 0.90, 0.75
# 확실한 저점(바닥) 신호 = 바닥 기법 신호가 난 날 하락 위험 확률이 이 값 미만. 2022~ 시험(전 종목): 바닥 신호 23,650건 → 7,885건(33%)만 남고
# 20일 시장 대비 −0.60%p → +0.49%p(2022~23년 +0.48 · 2024~26년 +0.50), 20일 뒤 −10%↓ 26.1% → 13.1%, −20%↓ 7.7% → 1.6%. 값을 낮출수록 더 적게·더 확실하게(0.15 → 22% 남음).
CERTAIN_P = 0.18          # 시험 기간 위험 확률의 분위 — 상위 10% = 높음, 10~25% = 주의
_cache = {"mtime": None, "obj": None}


def _work(code):
    try:
        r = ai.build_rows(code, with_signals=True, extras=False)
        if r is None or len(r["date"]) == 0:
            return None
        D = pd.DataFrame(r["X"], columns=ai.NAMES)
        D["date"] = r["date"]
        D["r20"] = r["r20"]
        return D
    except Exception:
        return None


def build_rows(workers=6, log=print):
    with db.get_conn() as c:
        codes = [r[0] for r in c.execute("SELECT code FROM symbols")]
    with ProcessPoolExecutor(workers) as ex:
        parts = [p for p in ex.map(_work, codes, chunksize=20) if p is not None]
    D = pd.concat(parts, ignore_index=True).dropna(subset=["r20"])
    D["y"] = ((D["r20"] - ai.COST) <= DROP).astype(int)
    log(f"표본 {len(D):,}행")
    return D


def _matrix(X):
    return np.asarray(X, float)[:, [ai.NAMES.index(c) for c in COLS]]


TARGETS = {"dn10": ("−10%↓", lambda n: n <= -0.10), "dn20": ("−20%↓", lambda n: n <= -0.20),
           "up10": ("+10%↑", lambda n: n >= 0.10), "up20": ("+20%↑", lambda n: n >= 0.20)}
QUANTILES = (0.10, 0.50, 0.90)


def _fit_clf(tr, y):
    from sklearn.ensemble import HistGradientBoostingClassifier as H
    return H(max_iter=200, learning_rate=0.08, max_depth=5, random_state=0).fit(tr[COLS].astype("float64"), y)


def train(D=None, log=print):
    """위험(−10%↓)·−20%↓·+10%↑·+20%↑ 확률 모델 4개 + 20일 수익률 분위(10·50·90%) 모델 3개.
    학습 ≤2021 · 시험 2022~. 시장 지수 특징은 뺀다 — 넣으면 시험 AUC 가 0.654→0.637 로 오히려 떨어졌다(시기 구분표 역할을 해 과적합).
    모델을 키우거나 해마다 다시 학습해도 AUC 는 0.654→0.655 로 그대로(걸어가며 검증 2013~2026 해마다 0.54~0.70) — 정보량의 한계."""
    import joblib
    from sklearn.ensemble import HistGradientBoostingRegressor as R
    from sklearn.metrics import roc_auc_score
    t0 = time.time()
    if D is None:
        D = build_rows(log=log)
    net = (D["r20"] - ai.COST).to_numpy()
    te = (D["date"] >= "2022-01-01").to_numpy()
    keep = [c for c in COLS if D[c].replace([np.inf, -np.inf], np.nan).notna().sum() > 1000]
    assert keep == COLS, "특징 열 중 값이 없는 열이 있습니다"
    tr_idx = np.flatnonzero(~te)
    rng = np.random.default_rng(0)
    tr_idx = rng.choice(tr_idx, size=min(len(tr_idx), 500_000), replace=False)
    tr, T, Tnet = D.iloc[tr_idx], D[te], net[te]
    models, targets = {}, {}
    for key, (label, fn) in TARGETS.items():
        m = _fit_clf(tr, fn(net[tr_idx]).astype(int))
        p = m.predict_proba(T[COLS].astype("float64"))[:, 1]
        y = fn(Tnet).astype(int)
        qs = np.quantile(p, np.linspace(0, 1, 11))
        bins = np.clip(np.searchsorted(qs[1:-1], p, side="right"), 0, 9)
        cal = [{"pred": float(p[bins == b].mean()), "actual": float(y[bins == b].mean())} for b in range(10)]
        targets[key] = {"label": label, "base": float(y.mean()), "auc": float(roc_auc_score(y, p)), "top10": cal[9]["actual"], "bottom10": cal[0]["actual"],
                        "calib_err": float(np.mean([abs(c["pred"] - c["actual"]) for c in cal])), "calibration": cal}
        models[key] = m
        log(f"{key} AUC {targets[key]['auc']:.3f} 기준 {targets[key]['base']:.3f}")
    qmods, qcov = {}, {}
    for q in QUANTILES:
        m = R(loss="quantile", quantile=q, max_iter=150, learning_rate=0.08, max_depth=5, random_state=0).fit(tr[COLS].astype("float64"), net[tr_idx])
        qmods[q] = m
        qcov[str(q)] = float((Tnet <= m.predict(T[COLS].astype("float64"))).mean())     # 시험 기간에 실제로 그 분위 아래였던 비율(기대: q)
    log("분위 적중(기대 0.1·0.5·0.9): " + json.dumps(qcov))
    p10 = models["dn10"].predict_proba(T[COLS].astype("float64"))[:, 1]
    cut_hi, cut_mid = float(np.quantile(p10, HI_Q)), float(np.quantile(p10, MID_Q))
    lvl = np.where(p10 >= cut_hi, 2, np.where(p10 >= cut_mid, 1, 0))
    rows = []
    for name, sel in (("전체", np.ones(len(T), bool)), ("낮음", lvl == 0), ("주의", lvl == 1), ("높음", lvl == 2)):
        s_ = T[sel]
        yy = Tnet[sel]
        rows.append({"level": name, "n": int(sel.sum()), "dn10": float((yy <= -0.10).mean()), "dn20": float((yy <= -0.20).mean()), "mean": float(yy.mean())})
    rep = {"generated": datetime.now().isoformat(timespec="seconds"), "rows": int(len(D)), "train_end": TRAIN_END, "auc_test": targets["dn10"]["auc"],
           "cut_hi": cut_hi, "cut_mid": cut_mid, "levels": rows, "targets": targets, "quantile_coverage": qcov, "elapsed_sec": round(time.time() - t0)}
    # 화면에 쓰는 모델은 가장 최근 표본(라벨이 확정된 마지막 날까지)을 모두 넣어 다시 학습한다 — 위 시험 수치는 ≤2021 학습 모델의 것(걸어가며 검증에서 해마다 다시 학습해도 같은 수준)
    all_idx = rng.choice(len(D), size=min(len(D), 600_000), replace=False)
    full = D.iloc[all_idx]
    live = {k: _fit_clf(full, fn(net[all_idx]).astype(int)) for k, (_, fn) in TARGETS.items()}
    live_q = {q: R(loss="quantile", quantile=q, max_iter=150, learning_rate=0.08, max_depth=5, random_state=0).fit(full[COLS].astype("float64"), net[all_idx]) for q in QUANTILES}
    rep["live_through"] = str(D["date"].max())
    rep["live_rows"] = int(len(all_idx))
    REPORT_PATH.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    joblib.dump({"model": live["dn10"], "models": live, "quantiles": live_q, "cut_hi": cut_hi, "cut_mid": cut_mid, "through": rep["live_through"]}, MODEL_PATH)
    REPORT_PATH.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    return rep


def load():
    try:
        mt = MODEL_PATH.stat().st_mtime
    except OSError:
        return None
    if _cache["mtime"] != mt:
        import joblib
        _cache.update(mtime=mt, obj=joblib.load(MODEL_PATH))
    return _cache["obj"]


def report():
    try:
        return json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def probs(X):
    """특징 행렬(날짜 × ai.NAMES) → 날짜별 위험 확률(값이 모자라면 NaN)."""
    obj = load()
    if obj is None:
        raise RuntimeError("위험 모델이 없습니다 (python manage.py risk-train)")
    M = _matrix(X)
    ok = np.isfinite(M[:, COLS.index("ret60")])
    P = np.full(len(M), np.nan)
    if ok.any():
        P[ok] = obj["model"].predict_proba(M[ok].astype("float64"))[:, 1]
    return P, obj["cut_hi"], obj["cut_mid"]


def forecast(X):
    """오늘(마지막 행) 20일 전망: 네 가지 도달 확률 + 20일 수익률 분위. 모델이 없으면 None."""
    obj = load()
    if obj is None or "models" not in obj:
        return None
    M = _matrix(X)[-1:]
    if not np.isfinite(M[0, COLS.index("ret60")]):
        return None
    M = M.astype("float64")
    rep = report() or {}
    out = {"prob": {}, "range": {}}
    for key, m in obj["models"].items():
        t = (rep.get("targets") or {}).get(key, {})
        out["prob"][key] = {"label": TARGETS[key][0], "p": float(m.predict_proba(M)[0, 1]), "base": t.get("base"), "auc": t.get("auc")}
    qs = sorted(obj["quantiles"])
    out["range"] = {f"q{int(q * 100)}": float(obj["quantiles"][q].predict(M)[0]) for q in qs}
    return out


def retrain_if_due(days=30, log=print):
    """마지막 학습 후 days 일이 지났으면 새 데이터로 다시 학습(장 마감 갱신 뒤 스케줄러가 부른다)."""
    import time as _t
    try:
        age = (_t.time() - MODEL_PATH.stat().st_mtime) / 86400
    except OSError:
        age = 1e9
    if age < days:
        return None
    return train(log=log)
