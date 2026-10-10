import json

import numpy as np
import pandas as pd

import config
from core import ai, strategy_study as ss


def _df(n=400, seed=0):
    rng = np.random.default_rng(seed)
    c = 100 * np.cumprod(1 + rng.normal(0.0, 0.01, n))
    o = np.r_[c[0], c[:-1]]
    v = np.full(n, 1000.0)
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.002, "low": np.minimum(o, c) * 0.998, "close": c, "volume": v}, index=pd.date_range("2020-01-01", periods=n, freq="B"))


def test_halt_starts_marks_first_zero_day_of_long_runs():
    v = np.ones(50)
    v = np.ones(120)
    v[10:25] = 0                      # 15일 — 짧아서 아님
    v[30:57] = 0                      # 27일 — 거래정지
    v[80:100] = 0                     # 정확히 20일
    s = ss._halt_starts(v)
    assert list(np.flatnonzero(s)) == [30, 80]
    ep = ss._halt_episodes(_df().assign(volume=np.r_[np.ones(100), np.zeros(25), np.ones(275)]), "T")
    assert len(ep) == 1 and ep[0]["length"] == 25 and ep[0]["date"] == (pd.Timestamp("2020-01-01") + pd.offsets.BDay(100)).strftime("%Y-%m-%d") and not ep[0]["ongoing"]


def test_exit_variants_target_stop_and_trailing():
    df = _df()
    o, h, l, c = (df[k].to_numpy(float).copy() for k in ("open", "high", "low", "close"))
    t = np.array([200])
    e = 201
    entry = o[e]
    h[e + 3] = entry * 1.12                                   # 4일째 +12% 터치 → 목표 +10% 체결
    l[e + 6] = entry * 0.88                                   # 7일째 −12% → 손절 −10% 체결(목표 뒤라 tgt 에 영향 없음)
    df2 = df.copy()
    df2["high"], df2["low"] = h, l
    r = ss._exit_variants(df2, t)
    assert r["tgt10"][0] == np.float64(0.10) or abs(r["tgt10"][0] - 0.10) < 1e-9
    assert r["tgt10_d"][0] == 4 and abs(r["stop10"][0] + 0.10) < 1e-9 and r["tgt20_d"][0] == 20
    assert abs(r["r10"][0] - (c[e + 9] / entry - 1)) < 1e-9 and r["halt120"][0] == 0 and r["surge60"][0] == 0
    # 트레일링: 종가가 고점 대비 10% 빠지는 날 청산
    assert 1 <= r["trail10_d"][0] <= 60 and np.isfinite(r["trail10"][0])


def test_stats_both_and_robust_rule():
    rng = np.random.default_rng(2)
    n = 2000
    r = rng.normal(0.01, 0.1, n)
    ex = r - r.mean()
    tr = np.arange(n) < 1000
    te = ~tr
    m = np.ones(n, bool)
    row = ss._both("x", m, r, ex, tr, te, {"share": 1.0})
    assert row["n"] == n and row["tr"]["n"] == 1000 and row["te"]["n"] == 1000 and "robust" in row and row["share"] == 1.0
    ex2 = ex.copy()
    ex2[tr] = -0.01
    assert ss._both("y", m, r, ex2, tr, te)["robust"] is False


def test_walk_forward_and_fit_fast_small():
    rng = np.random.default_rng(3)
    n = 3000
    X = rng.normal(0, 1, (n, 5)).astype(np.float32)
    y = (X[:, 0] + rng.normal(0, 0.5, n) > 0.8).astype(int)
    mdl = ss._fit_fast(X, y, n_iter=20)
    p = ss._predict(mdl, X, step=1000)
    assert p.shape == (n,) and np.corrcoef(p, X[:, 0])[0, 1] > 0.5
    assert len(ss._sub(np.ones(n, bool), 100)) == 100


def test_strategy_page(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(ss, "STRATEGY_PATH", tmp_path / "s.json")
    from app import create_app
    c = create_app().test_client()
    assert "아직 분석 결과가 없습니다" in c.get("/strategy").get_data(as_text=True)
    st = {"n": 500, "mean": 0.01, "p_up": 0.5, "p_big_up": 0.2, "p_dn10": 0.1, "p_dn20": 0.05, "q05": -0.2, "std": 0.1, "median": 0.0, "excess": 0.005, "days": 20.0, "per_day": 0.01}
    both = lambda name, **k: {"name": name, "n": 500, "all": st, "tr": st, "te": st, "robust": True, **k}  # noqa: E731
    rep = {"meta": {"generated": "x", "rows": 1000, "stocks": 10, "cost": 0.003, "split": {"train_end": "2021-12-31", "date_min": "2000-01-01", "date_max": "2026-10-10"},
                    "chosen": ["52주 신고가"], "ai": True, "halt_min": 5, "gap_max": 0.03, "ai_top": 0.1, "cached": False},
           "halt": {"n": 100, "long": 10, "ongoing": 1, "by_year": [{"year": 2024, "n": 5, "long": 1}], "resume": {"n": 90, "mean": -0.05, "median": -0.01, "p_dn20": 0.1, "p_dn50": 0.02, "q05": -0.5, "p_up": 0.4},
                    "resume20": {"n": 80, "mean": -0.06, "median": -0.02, "p_dn20": 0.12}, "resume_long": None, "before": [{"name": "주가 1,000원 미만", "share": 0.3, "n": 30}],
                    "prob": [{"name": "거래 가능 종목 아무 날", "n": 1000, "p120": 0.01, "p60": 0.005, "p120_te": 0.01}], "rules": [{"name": "변동성 높음", "n": 500, "p120": 0.03, "lift": 3.0}], "base120": 0.01},
           "market": {"sig": [both("시장 60일선 위", share=0.5)], "ai": [both("시장 60일선 위", share=0.5)], "base": [both("시장 60일선 위", share=0.5)],
                      "filters": [{"name": "시장 60일선 위", "n_te": 300, "p_up_tr": 0.5, "p_up_te": 0.52, "mean_tr": 0.01, "mean_te": 0.02, "d_up_tr": 0.02, "d_up_te": 0.03, "share_te": 0.6, "robust": True}],
                      "base_": None, "base": {"tr": st, "te": st, "label": "AI 규칙"}},
           "exits": {"ai": [{"name": "20일 뒤 종가 (기준)", "tr": st, "te": st}], "sig": [{"name": "20일 뒤 종가 (기준)", "tr": st, "te": st}]},
           "traits": {"ai": [{"group": "주가", "rows": [both("5,000원 미만", share=0.2)]}], "sig": [{"group": "주가", "rows": [both("5,000원 미만", share=0.2)]}]},
           "quiet": {"base": {"surge": 0.5, "up20": 0.3, "r60": 0.01, "r20": 0.0}, "base_up20_te": 0.3, "base_up20_tr": 0.3,
                     "rows": [both("가격 제자리", share=0.1, surge60=0.6, up20_60=0.35, up20_60_tr=0.34, up20_60_te=0.36, r60=0.02, r60_te=0.02)]},
           "season": {"ai": [{"group": "요일", "rows": [both("월")]}], "sig": [{"group": "요일", "rows": [both("월")]}], "base": [{"group": "요일", "rows": [both("월")]}]},
           "fail": {"n_lose_tr": 100, "n_tr": 1000, "base_lose_tr": 0.1, "base_lose_te": 0.1, "diff": [{"name": "atrp", "label": "변동성", "lose": 0.05, "rest": 0.04, "d": 0.3}],
                    "cands": [{"name": "거래량 5배↑", "n_tr": 300, "n_te": 150, "share_te": 0.1, "p_lose_tr": 0.15, "p_lose_te": 0.14, "lift_tr": 1.5, "lift_te": 1.4, "mean_tr": -0.01, "mean_te": -0.01,
                               "excess_tr": -0.01, "excess_te": -0.01, "robust": True, "p_up_rest_te": 0.5}]},
           "walk": {"rows": [{"year": 2024, "n_picked": 3, "sig": st, "ai": st, "base": st}], "total": {"sig": st, "ai": st, "base": st, "sig_years_pos": 1, "sig_years": 1, "ai_years_pos": 1, "ai_years": 1},
                    "chosen_hist": [{"year": 2024, "picked": ["52주 신고가"]}], "start": 2010},
           "ai_check": {"calibration": [{"bin": i, "p_mean": 0.1, "hit": 0.1, "mean": 0.01, "p_up": 0.5, "excess": 0.0} for i in range(1, 11)],
                        "models": [{"name": "기존 모델 · 상위 10%", "model": "기존 모델", "filter": "상위 10%", **st}]},
           "improve": {"steps": [{**both("출발"), "per_month_te": 50.0, "split3_te": st}, {**both("+ 시장 필터"), "per_month_te": 30.0, "split3_te": st}], "used": ["시장 60일선 위"],
                       "exits": [{"name": "20일 뒤 종가 (기준)", "tr": st, "te": st, "robust": False}, {"name": "목표 +10% 닿으면 매도", "tr": st, "te": st, "robust": True}], "final_mask_n": 300},
           "portfolio": {"보강 규칙 · 같은 비중": {"final": 1.5, "cagr": 0.1, "mdd": -0.2, "trades": 100, "curve": [{"d": "2024-01-01", "v": 1.0}], "months_pos": 0.6}, "market": {"mean20": 0.0, "curve_final": 1.1}},
           "text": ["요약 문장"]}
    (tmp_path / "s.json").write_text(json.dumps(rep), encoding="utf-8")
    html = c.get("/strategy").get_data(as_text=True)
    for s in ("요약 문장", "보강한 최종 규칙", "걸어가며 검증", "거래정지 종목", "조용한 매집", "캘리브레이션", "새 피함 후보", "포트폴리오 시뮬레이션"):
        assert s in html
