import json

import numpy as np
import pandas as pd
import pytest

import config
from core import ai, surge_study as sg


def series(n=400, seed=0):
    rng = np.random.default_rng(seed)
    c = 100 * np.cumprod(1 + rng.normal(0.0, 0.01, n))
    o = np.r_[c[0], c[:-1]]
    return o.copy(), np.maximum(o, c) * 1.002, np.minimum(o, c) * 0.998, c.copy()


def test_paths_first_hit_day_drawdown_and_dn10():
    o, h, l, c = series()
    t = 200
    e = np.array([t + 1])
    entry = o[t + 1]
    h[t + 5] = entry * 1.25                         # 진입일을 1일째로 셀 때 5일째에 +25% 터치
    l[t + 3] = entry * 0.93                         # 그 전에 −7% 흔들림(−10%는 아님)
    p = sg._paths(o, h, l, c, e)
    assert p["d10"][0] == 5 and p["d20"][0] == 5 and np.isnan(p["d30"][0])
    assert p["dd20"][0] == pytest.approx(0.93 - 1, abs=0.01) and np.isnan(p["dn10"][0]) or p["dn10"][0] > 5
    assert p["g5"][0] == pytest.approx(0.25, abs=0.01) and p["g120"][0] >= p["g5"][0]
    assert p["peak"][0] == 5
    l[t + 2] = entry * 0.89                         # −10% 를 먼저 맞음
    p2 = sg._paths(o, h, l, c, e)
    assert p2["dn10"][0] == 2 and p2["d20"][0] == 5


def test_summary_probabilities_and_days():
    n = 1000
    D = pd.DataFrame({"d20": np.r_[np.full(300, 4.0), np.full(700, np.nan)], "d10": np.r_[np.full(500, 3.0), np.full(500, np.nan)],
                      "d30": np.nan, "d50": np.nan, "d100": np.nan, "dn10": np.nan, "dd20": np.r_[np.full(300, -0.04), np.full(700, np.nan)],
                      "r20": 0.01, "r60": 0.02, "r120": 0.03, "peak": 10.0})
    s = sg._summary(D)
    assert s["hit"]["20"]["5"] == pytest.approx(0.3) and s["hit"]["20"]["20"] == pytest.approx(0.3) and s["hit"]["10"]["5"] == pytest.approx(0.5)
    assert s["days"]["20"]["median"] == 4 and s["days"]["20"]["share"] == pytest.approx(0.3)
    assert s["up20_before_dn10"] == pytest.approx(0.3) and s["dd_before_20"] == pytest.approx(-0.04)


def test_breakdown_and_time_hist_shapes():
    rng = np.random.default_rng(1)
    n = 4000
    D = pd.DataFrame({"ret1": rng.normal(0.03, 0.06, n), "vdry10": rng.uniform(0.1, 1.2, n), "nh250": rng.uniform(-0.8, 0, n), "dist224": rng.normal(0, 0.3, n),
                      "ret20": rng.normal(0, 0.2, n), "volratio": rng.uniform(3, 15, n), "price_level": rng.uniform(2.5, 5, n), "mkt_r60": rng.normal(0, 0.1, n),
                      "gap": rng.normal(0.01, 0.04, n), "date": [f"{2010 + i % 16}-01-01" for i in range(n)], "r20": rng.normal(0, 0.1, n), "r60": rng.normal(0, 0.2, n)})
    for k in (10, 20, 30, 50, 100):
        D[f"d{k}"] = np.where(rng.random(n) < 0.3, rng.integers(1, 100, n), np.nan)
    D["dn10"] = np.where(rng.random(n) < 0.4, rng.integers(1, 100, n), np.nan)
    b = sg._breakdown(D)
    assert {g["group"] for g in b} >= {"급증한 날의 모습", "주가 위치 (52주 고점 대비)", "시기"}
    assert all(r["n"] >= 300 for g in b for r in g["rows"])
    th = sg._time_hist(D)
    assert [t["x"] for t in th] == [20, 50] and sum(x["share_all"] for x in th[0]["bins"]) == pytest.approx(th[0]["hit_share"], abs=0.01)
    cu = sg._curve(D, 20)
    assert cu[0]["p"] <= cu[-1]["p"]                                           # 누적이라 줄지 않음


def test_work_finds_first_surge_and_forward_outcome(tmp_path, monkeypatch):
    from core import collector, db
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(ai, "TRADABLE_VALUE", 1.0)
    monkeypatch.setattr(sg, "LIQ_MIN", 0.0)
    db.init_db()
    n = 700
    rng = np.random.default_rng(3)
    c = 100 * np.cumprod(1 + rng.normal(0.0, 0.005, n))
    o = np.r_[c[0], c[:-1]]
    v = np.full(n, 10000.0)
    v[400] = 80000.0                                # 첫 급증(8배)
    v[405] = 90000.0                                # 5일 뒤 또 급증 — 20일 안이라 이벤트로 안 침
    v[450] = 90000.0                                # 50일째 재폭등(5배↑)
    c[401:] = c[401:] * 1.3                         # 급증 다음 날부터 +30% 점프
    o[401:] = np.r_[c[400], c[401:-1]]
    df = pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.003, "low": np.minimum(o, c) * 0.997, "close": c, "volume": v},
                      index=pd.date_range("2020-01-01", periods=n, freq="B"))
    collector.save_prices("T1", df)
    out = sg._work("T1")
    ev = out["ev"]
    assert ev is not None and 400 in {df.index.get_loc(pd.Timestamp(d)) for d in ev["date"]} and 405 not in {df.index.get_loc(pd.Timestamp(d)) for d in ev["date"]}
    row = list(ev["date"]).index(df.index[400].strftime("%Y-%m-%d"))
    assert ev["volratio"][row] >= 7 and ev["d20"][row] == 1                # 다음날 시가부터 +20% 구간(첫날 고가가 닿음)
    assert ev["rs_n"][row] == 1 and ev["rs_first"][row] == 50 and ev["rs10_n"][row] == 0     # 405 는 앞 폭등과 5일 안이라 새 폭등 아님
    assert abs(ev["gap"][row]) < 0.05                                      # 점프는 다음날 장중에 일어나 시가 갭은 작음


def test_surge_page(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(sg, "SURGE_PATH", tmp_path / "surge.json")
    from app import create_app
    c = create_app().test_client()
    assert "아직 분석 결과가 없습니다" in c.get("/surge").get_data(as_text=True)
    hit = {str(x): {str(n): 0.2 for n in sg.HORIZ} for x in (10, 20, 30, 50, 100)}
    days = {str(x): {"share": 0.3, "median": 12.0, "q25": 4.0, "q75": 30.0} for x in (10, 20, 30, 50, 100)}
    summ = {"n": 1000, "hit": hit, "days": days, "r20": 0.01, "r60": 0.02, "r120": 0.03, "median_r20": 0.0, "peak_median": 20.0, "up20_before_dn10": 0.2,
            "dn10_first": 0.5, "dd_before_20": -0.05}
    rep = {"meta": {"generated": "x", "stocks": 5, "events": 1000, "baseline": 5000, "min_ratio": 3.0, "look": 120, "liq_min": 3e8, "date_min": "2000-01-01", "date_max": "2026-01-01"},
           "levels": [{"name": "거래량 3배 이상 (전체)", **summ}], "baseline": summ,
           "time_hist": [{"x": 20, "hit_share": 0.3, "bins": [{"label": "1~3일", "share_hits": 0.2, "share_all": 0.06}]}],
           "curve": {"event": [{"day": 5, "p": 0.1}], "baseline": [{"day": 5, "p": 0.1}], "event50": [{"day": 5, "p": 0.05}], "baseline50": [{"day": 5, "p": 0.05}]},
           "breakdown": [{"group": "급증한 날의 모습", "rows": [{"name": "양봉 +3~+10%", "n": 500, "p20_in20": 0.3, "p30_in60": 0.2, "p50_in120": 0.1, "days20": 8.0, "dn10_first": 0.5, "r20": 0.01, "r60": 0.0}]}],
           "profile": [{"name": "volratio", "big": 6.0, "rest": 5.0}],
           "recent": [{"code": "005930", "name": "삼성전자", "date": "2026-10-07", "close": 272000.0, "volratio": 4.0, "ret1": 0.05, "nh250": -0.1, "vdry10": 0.3, "group": "양봉 +3%↑ · 중간(−10~−50%) · 거래량 건조 후",
                      "n": 1000, "p20_in20": 0.3, "p50_in120": 0.1, "days20": 8.0, "dn10_first": 0.5, "r20": 0.01}],
           "text": ["요약 문장"]}
    (tmp_path / "surge.json").write_text(json.dumps(rep), encoding="utf-8")
    html = c.get("/surge").get_data(as_text=True)
    for s in ("요약 문장", "삼성전자", "어떤 급증이 큰 상승으로 이어졌나", "급증 후 몇 일째에 닿았나"):
        assert s in html


def _fake_events(n=9000, seed=5):
    rng = np.random.default_rng(seed)
    E = pd.DataFrame({"d20": np.where(rng.random(n) < 0.5, rng.integers(1, 121, n), np.nan), "d30": np.nan, "d50": np.where(rng.random(n) < 0.2, rng.integers(1, 121, n), np.nan),
                      "dn10": np.where(rng.random(n) < 0.4, rng.integers(1, 100, n), np.nan), "g120": rng.uniform(0, 1.5, n), "peak": rng.integers(1, 121, n).astype(float),
                      "dd20": rng.uniform(-0.2, 0, n), "r20": rng.normal(0, 0.1, n), "r60": rng.normal(0, 0.2, n), "r120": rng.normal(0, 0.3, n),
                      "rs_n": rng.integers(0, 4, n).astype(float), "rs10_n": rng.integers(0, 2, n).astype(float), "rs_peak": (rng.random(n) < 0.3).astype(float),
                      "rs_trig20": (rng.random(n) < 0.3).astype(float), "rs_before20": rng.integers(0, 3, n).astype(float),
                      "volratio": rng.uniform(3, 12, n), "ret1": rng.normal(0.02, 0.05, n), "nh250": rng.uniform(-0.8, 0, n),
                      "aligned": (rng.random(n) < 0.2).astype(float), "reversed": (rng.random(n) < 0.3).astype(float), "ma_cross_n": rng.integers(0, 4, n).astype(float),
                      "ma_above_n": rng.integers(0, 6, n).astype(float), "ma_tight": rng.uniform(0, 0.2, n), "cross20": rng.integers(0, 61, n).astype(float),
                      "cross60": rng.integers(0, 61, n).astype(float), "cross112": rng.integers(0, 61, n).astype(float), "slope60": rng.normal(0, 0.05, n),
                      "acc_recent": (rng.random(n) < 0.3).astype(float), "date": [f"{2005 + i % 20}-06-01" for i in range(n)],
                      "code": [f"{i % 50:06d}" for i in range(n)]})
    E["rs_first"] = np.where(E["rs_n"] > 0, rng.integers(1, 121, n), np.nan)
    for j in range(len(ai.SIGNAL_LABELS)):
        E[f"sig{j:02d}"] = rng.integers(0, 61, n).astype(float)
    return E


def test_resurge_windows_and_trigger():
    n = 400
    fs = np.zeros(n, bool)
    fs[30] = fs[80] = True
    t = np.array([10])
    d20 = np.array([22.0])                          # 22일째 = 바 32 에 +20% 닿음 → 바 30 재폭등은 닿기 직전 5일 안
    peak = np.array([71.0])                         # 정점 71일째 = 바 81 → 바 80 은 정점 직전
    r = sg._resurge(fs, fs, t, d20, peak)
    assert r["rs_n"][0] == 2 and r["rs_first"][0] == 20 and r["rs_before20"][0] == 1 and r["rs_trig20"][0] == 1 and r["rs_peak"][0] == 1
    r2 = sg._resurge(fs, fs, t, np.array([np.nan]), peak)
    assert np.isnan(r2["rs_before20"][0]) and np.isnan(r2["rs_trig20"][0])


def test_speed_size_signals_examples_shapes():
    E = _fake_events()
    B = E.sample(800, random_state=1)
    sp = sg._speed(E, B)
    assert [x["name"] for x in sp][0].startswith("5일 안") and sp[-1].get("baseline") and abs(sum(x["share"] for x in sp[:-1]) - 1) < 1e-9
    assert all(len(x["rs_bins"]) == len(sg.RS_BINS) for x in sp) and "rs_trig" in sp[0] and "rs_trig" not in sp[-2]
    sz = sg._size(E)
    assert abs(sum(x["share"] for x in sz) - 1) < 1e-9
    ex = sg._examples(E, {}, top=5)
    assert len(ex["fast"]) <= 5 and all(r["gain"] <= sg.EX_CAP for r in ex["slow"])
    s = sg._signals(E)
    assert s["singles"] and all(r["n"] >= 300 for r in s["singles"]) and s["pair_count"] > 0
    assert all(x["n"] >= 300 for x in s["pairs_best"]) and [x["p20_in20"] for x in s["pairs_best"]] == sorted((x["p20_in20"] for x in s["pairs_best"]), reverse=True)
