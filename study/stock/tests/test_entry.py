import json

import numpy as np
import pandas as pd
import pytest

import config
from core import ai, entry_study as es


def synth(n=6000, seed=0):
    rng = np.random.default_rng(seed)
    r20 = rng.normal(0.0, 0.12, n)
    d = {"r20": r20, "x5": r20 + rng.normal(0, 0.02, n), "x10": r20 + rng.normal(0, 0.03, n)}
    d["split3"] = 0.75 * r20 + rng.normal(0, 0.01, n)          # 분할은 진입가를 평균해 변동이 줄어든 수익
    d["mae0"] = -np.abs(rng.normal(0.08, 0.04, n))
    d["mae_split"] = d["mae0"] * 0.8
    conf = r20 + 0.01
    conf[rng.random(n) < 0.6] = np.nan                       # 확인 안 된 신호
    d["conf_ret"] = conf
    pb = r20 - 0.005
    pb[rng.random(n) < 0.5] = np.nan
    d["pb_ret"] = pb
    d["stop_ret"] = np.maximum(r20, -0.08)                   # 손절로 손실 제한
    return d


def test_stats_quantiles_and_probabilities():
    s = es._stats(np.array([-0.2, -0.1, 0.0, 0.05, 0.1, 0.3] * 20))
    assert s["n"] == 120 and s["p_up"] == pytest.approx(0.5) and s["p_big_up"] == pytest.approx(2 / 6)
    assert s["p_big_loss"] == pytest.approx(2 / 6) and s["q05"] <= -0.1
    assert es._stats(np.array([0.1] * 5)) is None                              # 표본이 너무 작으면 None


def test_deciles_are_ordered_by_score():
    rng = np.random.default_rng(1)
    p = rng.random(5000)
    r = rng.normal(0.05 * (p - 0.5), 0.1)
    dec = es._deciles(r, p)
    assert len(dec) == 10 and dec[-1]["mean"] > dec[0]["mean"] and dec[-1]["p_mean"] > dec[0]["p_mean"]


def test_methods_cash_for_unfilled_and_split_reduces_risk():
    D = synth()
    rows = {r["name"]: r for r in es._methods(D, np.ones(6000, bool))}
    allin = rows["한 번에 전액 (다음날 시가)"]
    split = rows["3분할 (다음날·5일 뒤·10일 뒤 1/3씩)"]
    conf = rows["확인 후 진입 (5일 안에 신호일 종가 +2% 넘으면 다음날 시가)"]
    assert allin["coverage"] == 1.0 and 0.35 < conf["coverage"] < 0.45
    assert conf["per_signal"] == pytest.approx(conf["coverage"] * conf["mean"])     # 안 산 신호는 0
    assert split["std"] < allin["std"] and split["mae"] > allin["mae"]               # 낙폭이 덜 깊음(덜 음수)
    assert rows["전액 + 2×ATR 손절"]["q05"] > allin["q05"]                             # 최악 5% 손실이 줄어듦


def test_market_study_bins_and_today():
    idx = pd.date_range("2000-01-03", periods=3000, freq="B").strftime("%Y-%m-%d")
    rng = np.random.default_rng(2)
    ret = pd.Series(rng.normal(0.0004, 0.01, 3000), index=idx)
    level = (1 + ret).cumprod()
    mkt = pd.DataFrame({"mkt_ret": ret, "mkt_dd250": level / level.rolling(250, min_periods=120).max() - 1,
                        "mkt_r20": level / level.shift(20) - 1, "mkt_r60": level / level.shift(60) - 1, "mkt_vol20": ret.rolling(20).std()})
    out = es._market_study(mkt)
    assert out["n"] > 100 and set(out["bins"]) == {"52주 고점 대비 낙폭", "직전 60일 수익률"}
    assert out["today"]["date"] == idx[-1] and -1 < out["today"]["dd250"] <= 0.0001


def test_conditions_report_excess_and_test_period():
    rng = np.random.default_rng(3)
    n = 20000
    D = pd.DataFrame({f: rng.normal(size=n) for f in ai.NAMES})
    D["mkt_r60"] = rng.normal(0, 0.1, n)
    D["r20"] = np.where(D["mkt_r60"] > 0.1, 0.03, 0.0) + rng.normal(0, 0.1, n)
    excess = D["r20"].to_numpy() - D["r20"].mean()
    test = rng.random(n) < 0.3
    base, rows = es._conditions(D, excess, test)
    row = {r["name"]: r for r in rows}["시장: 강한 상승 (시장 60일 +10%↑)"]
    assert row["mean"] > base["mean"] + 0.015 and row["n_test"] > 0 and row["p_up"] > base["p_up"]


def test_entry_page(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(es, "ENTRY_PATH", tmp_path / "entry.json")
    from app import create_app
    c = create_app().test_client()
    assert "아직 분석 결과가 없습니다" in c.get("/entry").get_data(as_text=True)
    st = es._stats(np.random.default_rng(0).normal(0.0, 0.1, 500))
    method_rows = [{**st, "name": "한 번에 전액 (다음날 시가)", "coverage": 1.0, "per_signal": st["mean"], "mae": -0.1, "vs_all_in": 0.0}]
    model = {"auc": 0.6, "base": 0.4, "top1": {"precision": 0.5, "lift": 1.2}, "top5": {"precision": 0.5, "lift": 1.2}, "top10": {"precision": 0.5, "lift": 1.2}, "calibration": []}
    dec = [{**st, "bin": i, "p_mean": 0.3 + 0.03 * i} for i in range(1, 11)]
    rep = {"meta": {"generated": "x", "rows": 100, "stocks": 3, "cost": 0.003, "hold": 20, "features": 50,
                    "split": {"train_end": "2018-12-31", "valid_end": "2021-12-31", "date_min": "2000-01-01", "date_max": "2026-01-01"}},
           "direction": {"base": st, "base_all": st, "models": {"up": model, "big_up": model, "big_dn": model}, "deciles": dec},
           "market": {"n": 100, "up": 0.6, "mean": 0.01, "bins": {"52주 고점 대비 낙폭": [{"label": "−25% 이하", "n": 20, "p_up": 0.7, "mean": 0.03, "q05": -0.1}]},
                      "today": {"date": "2026-10-07", "dd250": -0.05, "r60": 0.02, "r20": 0.01}},
           "conditions": {"base": st, "rows": [{"name": "시장: 강한 상승 (시장 60일 +10%↑)", "n": 600, "p_up": 0.5, "mean": 0.01, "excess": 0.01,
                                                "p_big_up": 0.2, "p_big_loss": 0.2, "q05": -0.2, "n_test": 100, "p_up_test": 0.5, "mean_test": 0.0}]},
           "entry_methods": [{"universe": "시험 기간 전체 표본", "rows": method_rows}],
           "picks": [{"code": "005930", "name": "삼성전자", "date": "2026-10-07", "close": 272000.0, "p_up": 0.6, "p_big_up": 0.2, "p_big_dn": 0.1,
                      "reason": "변동성 3.0% (확률 +2.0%p)", "ret20": 0.02, "nh250": -0.1}],
           "today_market": {"mkt_r60": 0.01, "mkt_dd250": -0.04}, "text": ["요약 문장"]}
    (tmp_path / "entry.json").write_text(json.dumps(rep), encoding="utf-8")
    html = c.get("/entry").get_data(as_text=True)
    for s in ("요약 문장", "삼성전자", "어떻게 들어갈까", "시장 전체가 오를지", "이런 상황이면 상승 확률은"):
        assert s in html
