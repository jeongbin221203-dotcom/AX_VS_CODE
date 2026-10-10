import json

import numpy as np
import pandas as pd
import pytest

import config
from core import ai


def frame(n=700, seed=0, vol_spike_at=None):
    rng = np.random.default_rng(seed)
    c = 100 * np.cumprod(1 + rng.normal(0.0004, 0.015, n))
    o = np.r_[c[0], c[:-1]]
    v = rng.integers(8000, 12000, n).astype(float)
    if vol_spike_at is not None:
        v[vol_spike_at] = 80000
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.005, "low": np.minimum(o, c) * 0.995, "close": c, "volume": v},
                        index=pd.date_range("2020-01-01", periods=n, freq="B"))


def test_feature_matrix_shape_and_names_unique():
    X = ai.compute_features(frame(), None)
    assert X.shape == (700, len(ai.NAMES)) and len(set(ai.NAMES)) == len(ai.NAMES)
    assert set(ai.GROUP) == set(ai.NAMES) == set(ai.LABEL)
    own = [i for i, n in enumerate(ai.NAMES) if not n.startswith("mkt_") and n != "rs20"]      # 시장 지수가 없으면 시장 관련 값만 비어 있음
    assert np.isfinite(X[600][own]).all()                                   # 충분한 이력이 쌓인 날은 나머지 값이 다 있음


def test_volume_features_react_to_spike():
    df = frame(vol_spike_at=500)
    X = ai.compute_features(df, None)
    i = ai.NAMES.index("volratio")
    assert X[500, i] > 5 and X[499, i] < 2
    assert X[500, ai.NAMES.index("vspike_n")] >= 1
    assert X[505, ai.NAMES.index("vmax10")] > 5                             # 급증이 최근 10일 최대 거래량에 반영


def test_features_do_not_use_the_future():
    df = frame(700, seed=3)
    full = ai.compute_features(df, None)[500]
    cut = ai.compute_features(df.iloc[:501], None)[500]
    ok = ~np.isnan(full)
    assert np.allclose(full[ok], cut[ok], atol=1e-4, equal_nan=True)


def test_build_rows_targets_from_next_open(tmp_path, monkeypatch):
    from core import collector, db
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(ai, "TRADABLE_VALUE", 1.0)             # 가짜 종목은 거래대금이 작으니 거래 가능 기준을 없앤다
    db.init_db()
    df = frame(900, seed=5)
    collector.save_prices("T1", df)
    out = ai.build_rows("T1")
    assert out is not None and len(out["r20"]) == len(out["date"]) == len(out["X"])
    assert out["latest"]["code"] == "T1" and out["latest"]["X"].shape == (len(ai.NAMES),)
    assert {"r5", "r60", "r120", "r250", "mx60", "fail60"} <= set(out)
    d0 = out["date"][10]
    t = df.index.get_loc(pd.Timestamp(d0))
    entry = df["open"].iloc[t + 1]
    assert out["r20"][10] == pytest.approx(df["close"].iloc[t + 1 + ai.HOLD] / entry - 1)
    assert out["touch"][10] == pytest.approx(df["high"].iloc[t + 1:t + 1 + ai.HOLD].max() / entry - 1)
    assert all(pd.Timestamp(d).dayofyear % ai.STEP_MOD == 0 for d in out["date"])


def test_topk_precision_and_metrics_on_informative_score():
    rng = np.random.default_rng(0)
    p = rng.random(20000)
    y = (rng.random(20000) < p * 0.3).astype(int)             # 점수가 높을수록 정답 확률이 높음
    m = ai._metrics(y, p)
    assert m["auc"] > 0.6 and m["top5"]["lift"] > 1.5 and m["top1"]["precision"] >= m["top10"]["precision"]
    cal = ai._calibration(y, p)
    assert len(cal) == 10 and cal[-1]["actual"] > cal[0]["actual"]


def test_buckets_and_conditions_report_lift():
    rng = np.random.default_rng(1)
    n = 6000
    d = pd.DataFrame({f: rng.normal(size=n) for f in ai.NAMES})
    d["volratio"] = rng.gamma(2.0, 1.0, n)
    y = (rng.random(n) < np.where(d["volratio"] > 3, 0.4, 0.05)).astype(int)
    t = (y | (rng.random(n) < 0.1)).astype(int)
    b = ai._buckets(d, y, t, ["volratio"])["volratio"]
    rates = [r["rate"] for r in b]
    assert rates[-1] > rates[0] * 3
    conds = {c["name"]: c for c in ai._conditions(d, y, t)}
    assert conds["거래량 3배↑ 급증"]["lift"] > 2


def test_topn_compares_methods_against_market():
    rng = np.random.default_rng(2)
    rows = []
    for dt in pd.date_range("2022-01-03", periods=400, freq="D"):
        for k in range(150):
            vr = rng.gamma(2, 1)
            rows.append((dt.strftime("%Y-%m-%d"), 0.01 * vr + rng.normal(0, 0.05), vr, rng.normal(), rng.normal(), rng.normal()))
    df = pd.DataFrame(rows, columns=["date", "r20", "volratio", "ret20", "nh250", "vr5"])
    df["atrp"] = np.random.default_rng(3).random(len(df))
    out = ai._topn(df, df["volratio"].to_numpy())
    assert out["periods"] >= 10 and out["methods"]["AI 점수"]["excess"] > out["methods"]["무작위"]["excess"]


def test_narrate_uses_report_numbers():
    rep = {"meta": {"base_tradable": 0.05, "touch_tradable": 0.12},
           "accuracy": {"test": {"auc": 0.66, "base": 0.05, "top5": {"precision": 0.15, "lift": 3.0}, "top1": {"precision": 0.25, "lift": 5.0}}},
           "topn": {"n_pick": 20, "periods": 12, "methods": {"AI 점수": {"mean": 0.01, "excess": 0.02, "win_vs_market": 0.6}}},
           "group_importance": {"거래량": 0.02, "시장": 0.0}, "importance": [{"label": "변동성"}] * 5,
           "buckets": {"volratio": [{"lo": 2.0, "hi": 3.0, "rate": 0.09, "lift": 1.8}]}}
    text = " ".join(ai.narrate(rep))
    assert "0.66" in text and "3.0배" in text and "거래량" in text and "5.0%" in text


def test_ai_page(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(ai, "AI_PATH", tmp_path / "ai.json")
    from app import create_app
    c = create_app().test_client()
    assert "아직 AI 분석 결과가 없습니다" in c.get("/ai").get_data(as_text=True)
    acc = {"n": 100, "base": 0.05, "auc": 0.65, "ap": 0.1, "top1": {"precision": 0.2, "lift": 4.0}, "top5": {"precision": 0.12, "lift": 2.4},
           "top10": {"precision": 0.09, "lift": 1.8}}
    rep = {"meta": {"generated": "x", "rows": 1000, "stocks": 10, "tradable_rows": 800, "target": 0.2, "hold": 20, "tradable_value": 3e8,
                    "split": {"train_end": "2018-12-31", "valid_end": "2021-12-31", "date_min": "2000-01-01", "date_max": "2026-09-01"},
                    "base_all": 0.05, "base_tradable": 0.05, "touch_tradable": 0.1, "iters": 50, "features": 43},
           "accuracy": {"test": acc, "test_all": acc, "valid": acc, "calibration": [{"bin": i, "pred": 0.01 * i, "actual": 0.01 * i, "n": 10} for i in range(1, 11)]},
           "yearly": [{"year": "2023", "n": 5000, "base": 0.05, "auc": 0.6, "top5": 0.1, "lift5": 2.0}],
           "topn": {"periods": 10, "n_pick": 20, "market": 0.01, "methods": {"AI 점수": {"mean": 0.02, "excess": 0.01, "win_vs_market": 0.6, "hit20": None}}},
           "importance": [{"name": "volratio", "label": "거래량", "group": "거래량", "imp": 0.01, "sd": 0.001}],
           "group_importance": {"거래량": 0.01}, "importance_base_ap": 0.1,
           "profiles": [{"name": "volratio", "label": "거래량", "group": "거래량", "d": 0.5, "win_med": 2.0, "all_med": 1.0}],
           "buckets": {"volratio": [{"lo": 0.0, "hi": 2.0, "n": 500, "rate": 0.04, "touch": 0.08, "lift": 0.8},
                                    {"lo": 2.0, "hi": None, "n": 300, "rate": 0.1, "touch": 0.2, "lift": 2.0}]},
           "conditions": [{"name": "거래량 3배↑ 급증", "n": 500, "rate": 0.1, "touch": 0.2, "lift": 2.0}],
           "today": [{"code": "005930", "name": "삼성전자", "date": "2026-10-07", "close": 272000.0, "prob": 0.12, "volratio": 1.5,
                      "ret20": 0.05, "nh250": -0.1, "vr5": 1.2}], "text": ["요약 문장"]}
    (tmp_path / "ai.json").write_text(json.dumps(rep), encoding="utf-8")
    html = c.get("/ai").get_data(as_text=True)
    assert "요약 문장" in html and "삼성전자" in html and "0.650" in html and "거래량은 얼마나 영향이 있나" in html


def test_signal_features_since_and_counts():
    df = frame(700, seed=4)
    sf = ai.signal_features(df)
    assert sf.shape == (700, len(ai.SIGNAL_LABELS) + 2)
    assert sf[:, :len(ai.SIGNAL_LABELS)].min() >= 0 and sf[:, :len(ai.SIGNAL_LABELS)].max() <= 60
    recent = (sf[:, :len(ai.SIGNAL_LABELS)] <= 10).sum(axis=1)
    assert np.array_equal(recent, sf[:, -1])                              # 최근 10봉 신호 종류 수 = since<=10 개수


def test_since_helper():
    ev = np.array([False, True, False, False, True, False])
    assert list(ai._since(ev, cap=60)) == [60, 0, 1, 2, 0, 1]
    assert list(ai._since(np.zeros(3, bool), cap=7)) == [7, 7, 7]


def test_explain_rows_attributes_probability_to_the_driver():
    from sklearn.ensemble import HistGradientBoostingClassifier
    rng = np.random.default_rng(0)
    X = rng.normal(size=(6000, 5)).astype(np.float32)
    y = (X[:, 2] > 0.8).astype(int)                         # 세 번째 특징이 정답을 결정
    m = HistGradientBoostingClassifier(max_iter=50, random_state=0).fit(X, y)
    med = np.median(X, axis=0)
    p0, contribs = ai.explain_rows(m, X[X[:, 2] > 1.5][:3], med, [0, 1, 2, 3, 4])[0]
    assert p0 > 0.8 and contribs and contribs[0][0] == 2 and contribs[0][1] > 0.5


def test_horizon_topn_uses_non_overlapping_windows():
    rng = np.random.default_rng(1)
    rows = []
    for dt in pd.date_range("2022-01-03", periods=600, freq="D"):
        for _ in range(130):
            vr = rng.random()
            rows.append((dt.strftime("%Y-%m-%d"), 0.3 * vr + rng.normal(0, 0.05), vr, rng.normal(), rng.random()))
    df = pd.DataFrame(rows, columns=["date", "r", "p", "nh250", "atrp"])
    out = ai._topn_h(df["date"], df["r"].to_numpy(), df["p"].to_numpy(), {"nh250": df["nh250"].to_numpy(), "atrp": df["atrp"].to_numpy()}, 60, 0.4)
    assert out["step_days"] == 88 and 5 <= out["periods"] <= 8
    assert out["methods"]["AI 점수"]["excess"] > out["methods"]["무작위"]["excess"] + 0.05


def pattern_frame(n=9000, seed=0):
    """하락 후 반등 상태 표본: hl10(저점 유지)이 크면 성공하도록 만든 가짜 데이터."""
    rng = np.random.default_rng(seed)
    d = pd.DataFrame({f: rng.normal(size=n) for f in ai.NAMES})
    d["dd120"] = rng.uniform(-0.6, -0.26, n)
    d["low_age"] = rng.integers(5, 40, n).astype(float)
    d["rebound60"] = rng.uniform(0.08, 0.5, n)
    d["dist20"] = rng.uniform(0.0, 0.2, n)
    d["hl10"] = rng.uniform(-0.1, 0.1, n)
    d["aligned"] = 0.0
    p = np.where(d["hl10"] >= 0, 0.5, 0.1)
    ok = rng.random(n) < p
    d["mx60"] = np.where(ok, 0.35, 0.05)
    d["fail60"] = np.where(ok, 0.0, 1.0)
    d["r60"] = np.where(ok, 0.3, -0.1)
    d["r120"] = d["r60"]
    d["date"] = [f"{2015 + i % 11}-{i % 12 + 1:02d}-{i % 27 + 1:02d}" for i in range(n)]
    d["code"] = "X"
    return d


def test_patterns_study_finds_the_driving_condition():
    from core import patterns_study as ps
    out = ps.analyze(pattern_frame(), ai.LABEL, log=lambda m: None)
    r = out["rebound"]
    assert r["n"] > 5000 and 0.25 < r["succ"] < 0.35
    named = {x["name"]: x for x in r["named"]}
    keep = named["저점을 지키는 반등 (최근 10일 저점이 60일 저점 이상)"]
    assert keep["succ"] > 0.45 and keep["lift"] > 1.4
    assert r["buckets"][0]["name"] == "hl10"                       # 성공률 차이가 가장 큰 특징
    top = r["tree"][0]
    assert "≥" not in top["rule"] and top["rate_train"] > 0.4 and top["rate_test"] is not None


def test_current_matches_ranks_by_pattern_score():
    from core import patterns_study as ps
    d = pattern_frame(300)
    d["close"] = 1000.0
    d["code"] = [f"C{i}" for i in range(len(d))]
    rows = ps.current_matches(d, {"C1": "가짜"}, "rebound", ai.LABEL, top=5)
    assert 0 < len(rows) <= 5 and all("name" in r for r in rows)


def test_patterns_page(tmp_path, monkeypatch):
    import json as _json
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(ai, "AI_PATH", tmp_path / "ai.json")
    from app import create_app
    from core import patterns_study as ps
    c = create_app().test_client()
    assert "아직 패턴 분석 결과가 없습니다" in c.get("/patterns").get_data(as_text=True)
    pats = ps.analyze(pattern_frame(), ai.LABEL, log=lambda m: None)
    pats["rebound"]["today"] = []
    rep = {"meta": {"rows": 9000, "stocks": 10, "split": {"date_min": "2015-01-01", "date_max": "2026-01-01"}}, "patterns": pats}
    (tmp_path / "ai.json").write_text(_json.dumps(rep), encoding="utf-8")
    html = c.get("/patterns").get_data(as_text=True)
    assert "하락하다 다시 상승" in html and "저점을 지키는 반등" in html and "자동으로 찾은 규칙" in html


def entry_frame():
    n = 90
    o = np.full(n, 100.0)
    c = np.full(n, 100.0)
    o[36], o[41], o[33] = 104.0, 108.0, 103.5       # 신호일 t=30, 진입일 e=31, e+5=36, e+10=41
    c[32] = 103.0                                  # 신호일 종가 100 의 +2% 이상 → 32일에 확인, 다음날(33) 시가 103.5 진입
    c[51] = 110.0                                  # 청산 종가(e+20=51)
    hi = np.maximum(o, c) * 1.002
    lo = np.minimum(o, c) * 0.998
    return pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c, "volume": 1000.0}, index=pd.date_range("2024-01-01", periods=n, freq="B"))


def test_entry_variants_math():
    df = entry_frame()
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    t = np.array([30])
    e = t + 1
    r = ai._entry_variants(df, o, h, l, c, t, e)
    assert r["x5"][0] == pytest.approx(110 / 104 - 1) and r["x10"][0] == pytest.approx(110 / 108 - 1)
    assert r["split3"][0] == pytest.approx((110 / 100 + 110 / 104 + 110 / 108) / 3 - 1)
    assert r["conf_ret"][0] == pytest.approx(110 / 103.5 - 1)             # 확인 뒤 다음날 시가 진입
    assert np.isnan(r["pb_ret"][0])                                        # 시가의 −3%까지 안 내려가 체결 안 됨
    assert r["stop_hit"][0] == 0 and r["stop_ret"][0] == pytest.approx(0.10)
    assert r["mae0"][0] > -0.01
    exp = ((100 * 0.998 / 100) + (100 * 0.998 / 104) + (100 * 0.998 / 108)) / 3 - 1     # 5·10일 뒤 높은 시가로 산 분할분이 손해
    assert r["mae_split"][0] == pytest.approx(exp, abs=1e-6)


def test_entry_variants_stop_pullback_and_split_drawdown():
    df = entry_frame()
    df.loc[df.index[34], "low"] = 90.0             # 34번째 날 급락: 손절·눌림 지정가 모두 닿음
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    t = np.array([30])
    r = ai._entry_variants(df, o, h, l, c, t, t + 1)
    assert r["stop_hit"][0] == 1 and -0.05 < r["stop_ret"][0] < -0.001
    assert r["pb_ret"][0] == pytest.approx(110 / 97 - 1)                   # 지정가 97 에 체결
    assert r["mae0"][0] == pytest.approx(90 / 100 - 1)
    assert r["mae_split"][0] > r["mae0"][0]                                # 분할은 같은 급락에서 덜 아프다(일부는 아직 못 산 상태)
