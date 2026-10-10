import json

import numpy as np
import pandas as pd

import config
from core import ai, risk_study as rs, strategy_study as st


def _D(n=4000, seed=4):
    rng = np.random.default_rng(seed)
    D = pd.DataFrame(rng.normal(0, 0.2, (n, len(ai.NAMES))).astype(np.float32), columns=ai.NAMES)
    D["liq"] = rng.uniform(8.6, 10.5, n)
    D["hist_bars"] = rng.uniform(2.5, 4, n)
    D["reversed"] = (rng.random(n) < 0.3).astype(float)
    D["vspike_n"] = rng.integers(0, 8, n).astype(float)
    for k in rs.EXTRA:
        D[k] = rng.integers(0, 5, n).astype(float)
    D["close_px"] = rng.uniform(300, 30000, n)
    return D


def test_halt_patterns_rules_and_robust_flag():
    D = _D()
    n = len(D)
    rng = np.random.default_rng(1)
    h120 = (rng.random(n) < 0.05).astype(float)
    h120[(D["dist224"] <= -0.5).to_numpy()] = 1.0                         # 224일선 −50%↓ 는 반드시 정지
    h60 = h120 * (rng.random(n) < 0.5)
    ok = np.ones(n, bool)
    tr = np.arange(n) < n // 2
    out = rs._halt_patterns(D, h120, h60, ok, tr)
    names = {r["name"]: r for r in out["rules"]}
    assert out["base"] > 0 and len(out["diff"]) > 0
    if "224일선 −50%↓" in names:
        assert names["224일선 −50%↓"]["robust"] and names["224일선 −50%↓"]["p120"] == 1.0


def test_halt_model_and_tiers():
    D = _D(6000)
    n = len(D)
    rng = np.random.default_rng(2)
    risk = 1 / (1 + np.exp(-(-4 + 3 * (D["dist224"].to_numpy() < -0.2) + 2 * (D["zero_vol60"].to_numpy() >= 3))))
    h120 = (rng.random(n) < risk).astype(float)
    ok = np.ones(n, bool)
    tr = np.arange(n) < 4000
    te = ~tr
    mdl, p, rep = rs._halt_model(D, h120, ok, tr, te, lambda m: None, 0.0)
    assert rep["auc"] > 0.6 and len(rep["deciles"]) == 10 and {t["name"] for t in rep["tiers"]} == {"낮음", "중간", "높음"}
    assert np.nanmin(p) >= 0 and np.nanmax(p) <= 1 and rep["deciles"][-1]["actual"] >= rep["deciles"][0]["actual"]


def test_ens_score_rank_average():
    class M:
        def __init__(self, col):
            self.col = col

        def predict_proba(self, X):
            p = 1 / (1 + np.exp(-X[:, self.col]))
            return np.c_[1 - p, p]
    X = np.random.default_rng(0).normal(0, 1, (500, 3)).astype(np.float32)
    s = rs._ens_score({"a": M(0), "b": M(1)}, X, np.ones(500, bool))
    assert s.shape == (500,) and 0 < np.nanmin(s) and np.nanmax(s) <= 1.0
    # 두 열 모두 높은 행이 순위 평균도 높다
    hi = np.argmax(X[:, 0] + X[:, 1])
    assert s[hi] > np.nanmedian(s)


def test_risk_page(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(rs, "RISK_PATH", tmp_path / "r.json")
    from app import create_app
    c = create_app().test_client()
    assert "아직 결과가 없습니다" in c.get("/risk").get_data(as_text=True)
    s = {"n": 500, "mean": 0.02, "p_up": 0.6, "p_big_up": 0.2, "p_dn10": 0.1, "p_dn20": 0.05, "q05": -0.2, "std": 0.1, "median": 0.0, "excess": 0.01}
    both = lambda name, **k: {"name": name, "n": 500, "all": s, "tr": s, "te": s, "robust": True, **k}  # noqa: E731
    row = {"code": "005930", "name": "삼성전자", "close": 100000.0, "halt_p": 0.04, "tier": "높음", "prob": 0.2, "ens": 0.9, "ret20": 0.1, "nh250": -0.1, "atrp": 0.05, "liq": 5e9,
           "zero_vol60": 0.0, "dist224": 0.1, "avoid": False, "signals": ["52주 신고가"]}
    rep = {"meta": {"generated": "x", "rows": 1000, "stocks": 10, "cost": 0.003, "halt_min": 20, "split": {"train_end": "2021-12-31", "date_min": "2000-01-01", "date_max": "2026-10-10"}, "risk_high": 0.03, "risk_mid": 0.012},
           "patterns": {"diff": [{"name": "dist224", "label": "224일선", "pos": -0.3, "neg": 0.0, "d": -0.5}], "rules": [{"name": "224일선 −50%↓", "n": 100, "share": 0.01, "p120": 0.05, "p60": 0.03, "p_tr": 0.05, "p_te": 0.05, "lift_tr": 7.0, "lift_te": 6.0, "robust": True}],
                        "base": 0.007, "base_tr": 0.007, "base_te": 0.008, "n_pos": 100},
           "model": {"auc": 0.8, "auc_tr": 0.85, "deciles": [{"bin": i, "p_mean": 0.01, "actual": 0.01, "n": 100} for i in range(1, 11)],
                     "tiers": [{"name": n, "tr": {"n": 10, "share": 0.1, "actual": 0.01}, "te": {"n": 10, "share": 0.1, "actual": 0.01}} for n in ("낮음", "중간", "높음")],
                     "importance": [{"name": "dist224", "label": "224일선", "gain": 0.1}], "thresholds": {"high": 0.03, "mid": 0.012}, "ratio": 3.0},
           "risk_vs_return": [both("위험 낮음", share=0.8, halt=0.005)],
           "improve": {"steps": [{**both("출발"), "per_month_te": 90.0, "split3_te": s, "tgt10_te": s}], "used": ["AI 상위 5%만"], "tried": [{"name": "AI 상위 5%만", "n_te": 300, "p_up_tr": 0.7, "p_up_te": 0.66, "mean_tr": 0.1, "mean_te": 0.07, "stage": 1, "pass": True}]},
           "walk": {"rows": [{"year": 2024, "base": s, "final": s}], "total": {"base": s, "final": s}, "years_better": 1, "years": 1, "not_walked": []},
           "today": {"date": "2026-10-08", "rows": [row], "risk_rows": [row], "n_risk_high": 1, "n_risk_mid": 0, "n": 1000, "market_under": {"dd250": -0.2, "under": True}, "n_candidates": 1, "used": ["AI 상위 5%만"]},
           "text": ["요약 문장"]}
    (tmp_path / "r.json").write_text(json.dumps(rep), encoding="utf-8")
    html = c.get("/risk").get_data(as_text=True)
    for k in ("요약 문장", "삼성전자", "거래정지 전 패턴", "거래정지 위험 모델", "성공률 올리기", "걸어가며 검증"):
        assert k in html
