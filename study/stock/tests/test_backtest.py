import json

import numpy as np
import pandas as pd
import pytest

import config
from core import backtest as bt
from core import why


def arrays(o, h, l, c, v=None):
    n = len(o)
    df = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v if v is not None else [1000.0] * n},
                      index=pd.date_range("2024-01-01", periods=n, freq="B"))
    return bt.Arrays(df)


def flat(n, px=100.0):
    return [px] * n


def walk(n=500, seed=0):
    rng = np.random.default_rng(seed)
    c = 100 * np.cumprod(1 + rng.normal(0.0005, 0.01, n))
    o = np.r_[c[0], c[:-1]]
    return arrays(o, np.maximum(o, c) * 1.004, np.minimum(o, c) * 0.996, c)


# ---------------------------------------------------------------- 체결 규칙
def test_target_hit_exit_price_and_cost():
    n = bt.H + 5
    o, h, l, c = flat(n), flat(n), flat(n), flat(n)
    h[3] = 111.0                                   # 3번째 봉에서 목표 110 터치
    ret, why_, held, k = bt.simulate(arrays(o, h, l, c), 1, 100.0, 95.0, 110.0)
    assert why_ == "target" and held == 3 and ret == pytest.approx(0.10 - bt.COST)


def test_stop_hit_and_same_bar_stop_first():
    n = bt.H + 5
    o, h, l, c = flat(n), flat(n), flat(n), flat(n)
    l[2], h[2] = 94.0, 112.0                       # 같은 봉에서 손절(95)·목표(110) 모두 닿음 → 손절 우선
    ret, why_, held, k = bt.simulate(arrays(o, h, l, c), 1, 100.0, 95.0, 110.0)
    assert why_ == "stop" and ret == pytest.approx(-0.05 - bt.COST)


def test_gap_down_exits_at_open_not_stop():
    n = bt.H + 5
    o, h, l, c = flat(n), flat(n), flat(n), flat(n)
    o[2], h[2], l[2], c[2] = 90.0, 91.0, 89.0, 90.0   # 손절 95 아래로 갭 하락 → 시가 90에 청산
    ret, why_, held, k = bt.simulate(arrays(o, h, l, c), 1, 100.0, 95.0, None)
    assert why_ == "stop" and ret == pytest.approx(-0.10 - bt.COST)


def test_gap_up_exits_at_open_above_target():
    n = bt.H + 5
    o, h, l, c = flat(n), flat(n), flat(n), flat(n)
    o[2], h[2], l[2], c[2] = 120.0, 121.0, 119.0, 120.0
    ret, why_, _, _ = bt.simulate(arrays(o, h, l, c), 1, 100.0, 95.0, 110.0)
    assert why_ == "target" and ret == pytest.approx(0.20 - bt.COST)


def test_time_exit_at_horizon_close():
    n = bt.H + 5
    c = flat(n)
    c[bt.H] = 103.0                                # e=1 → 마지막 봉은 e+H-1 = H
    a = arrays(flat(n), [101.0] * n, [99.0] * n, c)
    ret, why_, held, _ = bt.simulate(a, 1, 100.0, 90.0, None)
    assert why_ == "time" and held == bt.H and ret == pytest.approx(0.03 - bt.COST)


def test_whipsaw_detects_recovery_after_stop():
    n = 40
    c = flat(n)
    c[12] = 101.0
    a = arrays(flat(n), flat(n), flat(n), c)
    assert bt._whipsaw(a, 10, 100.0) is True
    assert bt._whipsaw(a, 20, 100.0) is False


# ---------------------------------------------------------------- 신호 한 건의 측정
def test_metrics_skips_incomplete_horizon_and_builds_variants():
    a = walk(500)
    assert bt._metrics(a, "x", 500 - 10, None, False) is None          # H봉을 못 채우는 최근 신호
    m = bt._metrics(a, "x", 200, None, False)
    keys = {k for k, *_ in m["sims"]}
    assert {"atr2|3R", "atr3|none", "e1|3R", "e2|none"} <= keys
    assert len(m["r20"]) == 3 and len(m["fixed"]) == len(bt.HORIZONS)
    risk = {k: r for k, _, _, r, _, _ in m["sims"]}
    assert risk["atr3|none"] > risk["atr2|none"]                       # 3×ATR 손절이 더 넓다


def test_delayed_entry_uses_later_open():
    n = bt.H + 60
    o = np.full(n, 100.0)
    o[32] = 104.0                                  # 신호 i=30 → 0일 진입 e0=31, 1일 늦춤 e=32 의 시가 104
    a = arrays(o, o * 1.005, o * 0.995, o.copy())
    m = bt._metrics(a, "x", 30, None, False)
    assert m["r20"][1] == pytest.approx(a.c[32 + 20] / 104.0 - 1 - bt.COST)
    assert m["r20"][0] == pytest.approx(a.c[31 + 20] / 100.0 - 1 - bt.COST)


def test_pullback_limit_fills_only_when_price_dips():
    n = bt.H + 60
    o = np.full(n, 100.0)
    lo = o * 0.995
    lo[33] = 96.0                                  # 진입(e0=31) 시가 100의 −3% = 97 → 33번째 봉에서 체결
    filled, ret, d0 = bt._metrics(arrays(o, o * 1.005, lo, o), "x", 30, None, False)["pb"]
    assert filled is True and ret == pytest.approx(100.0 / 97.0 - 1 - bt.COST + 0.0, abs=0.05)
    assert bt._metrics(arrays(o, o * 1.005, o * 0.995, o), "x", 30, None, False)["pb"][0] is False


def test_sim_vec_counts_wins_not_boolean_or():
    """numpy bool 을 더하면 OR 이 되어 승수가 1에 고정되던 버그의 회귀 테스트."""
    acc = {}
    for ret in (0.05, 0.04, -0.03, 0.02):
        bt._addv(acc, "k", bt._sim_vec(np.float64(ret), "time", 0.05, 10, False))
    s = bt._sim_stats(acc["k"])
    assert s["n"] == 4 and s["win"] == pytest.approx(0.75) and s["mean"] == pytest.approx((0.05 + 0.04 - 0.03 + 0.02) / 4)
    assert s["pf"] == pytest.approx(0.11 / 0.03)


def test_sim_stats_target_stop_shares_and_whipsaw():
    acc = {}
    for ret, why_, whip in ((0.10, "target", False), (-0.05, "stop", True), (-0.05, "stop", False), (0.01, "time", False)):
        bt._addv(acc, "k", bt._sim_vec(ret, why_, 0.05, 5, whip))
    s = bt._sim_stats(acc["k"])
    assert (s["target"], s["stop"], s["time"]) == (0.25, 0.5, 0.25) and s["whipsaw"] == 0.5
    assert s["avg_loss_stop"] == pytest.approx(-0.05) and s["avg_gain_target"] == pytest.approx(0.10)


def test_features_use_only_past_and_have_expected_columns():
    a = walk(600)
    assert a.F.shape == (600, len(bt.FEATURES))
    full = a.F[300].copy()
    # 같은 앞부분 301봉만으로 새로 계산해도(= 미래 값이 없어도) 300번째 특징이 같아야 한다
    n = 300
    df = pd.DataFrame({"open": a.o[:n + 1], "high": a.h[:n + 1], "low": a.l[:n + 1], "close": a.c[:n + 1],
                       "volume": [1000.0] * (n + 1)}, index=pd.date_range("2024-01-01", periods=n + 1, freq="B"))
    cut = bt.Arrays(df).F[300]
    ok = ~np.isnan(full)
    assert np.allclose(full[ok], cut[ok], atol=1e-5)


# ---------------------------------------------------------------- 집계·화면
def synth_acc(n_sig=150, edge=0.02):
    rng = np.random.default_rng(1)
    acc = bt.Acc()
    k = 0
    for label, drift in (("정배열", edge), (bt.BASE_LABEL, 0.0)):
        for _ in range(n_sig if label != bt.BASE_LABEL else 300):
            r = float(rng.normal(drift, 0.05))
            date = f"2024-{k % 12 + 1:02d}-{k % 27 + 1:02d}"
            k += 1
            m = {"gap": 0.002, "mae5": -0.02, "mae10": -0.04, "fixed": [r, r, r, r], "r20": [r, r, r],
                 "pb": (True, r, r),
                 "sims": [("atr2|3R", r, "time", 0.05, 20, False), ("atr2|none", r, "stop" if r < 0 else "time", 0.05, 20, r < -0.02)]}
            bt._accumulate(acc, [label], "이평선" if label != bt.BASE_LABEL else "기준", m, date)
        acc.nstock[label] = 1
    market = {f"2024-{m:02d}-{d:02d}": [0.0, 0.0, 0.0] for m in range(1, 13) for d in range(1, 28)}
    mk = pd.DataFrame.from_dict(market, orient="index")
    return acc, mk


def test_finalize_excess_and_blocks():
    acc, mk = synth_acc()
    rep = bt.finalize(acc, mk)
    d = rep["labels"]["정배열"]
    assert d["n_signals"] == 150 and d["excess"]["t_day"] > 2 and d["excess"]["mean"] > 0.01
    assert set(d["entry_study"]) == {"0", "1", "2"} and d["pb"]["fill_rate"] == 1.0
    assert "atr2|3R" in d["sim"] and rep["baseline"]["sim"]["atr2|3R"]["n"] == 300


def test_merge_adds_instead_of_overwriting():
    a1, _ = synth_acc(50)
    a2, _ = synth_acc(50)
    a1.merge(a2)
    assert a1.nsig["정배열"] == 100 and a1.sim[("정배열", "atr2|3R")][0] == 100.0


def write_report(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(bt, "REPORT_PATH", tmp_path / "bt.json")
    acc, mk = synth_acc()
    rep = bt.finalize(acc, mk)
    rep["meta"] = {"generated": "x", "stocks": 1, "signals": 150, "baseline_samples": 300, "errors": [], "date_min": "2020-01-01",
                   "date_max": "2024-01-01", "params": {"horizon_bars": 60, "cost": 0.003, "base_step": 7, "pb_dip": 0.03, "pb_wait": 5,
                                                        "risk_range": [0.005, 0.25]}}
    (tmp_path / "bt.json").write_text(json.dumps(rep), encoding="utf-8")


def test_view_renders_all_sections(tmp_path, monkeypatch):
    write_report(tmp_path, monkeypatch)
    monkeypatch.setattr(why, "WHY_PATH", tmp_path / "none.json")
    from app import create_app
    html = create_app().test_client().get("/analysis").get_data(as_text=True)
    assert "정배열" in html and "시장보다 우위" in html and "기준(아무 날 진입)" in html
    for sec in ("진입을 늦추면?", "손절을 넓히면?", "매집봉이 있으면 더 잘 될까?"):
        assert sec in html


def test_view_without_report(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(bt, "REPORT_PATH", tmp_path / "none.json")
    monkeypatch.setattr(why, "WHY_PATH", tmp_path / "none2.json")
    from app import create_app
    c = create_app().test_client()
    assert "아직 분석 결과가 없습니다" in c.get("/analysis").get_data(as_text=True)
    assert "아직 분석 결과가 없습니다" in c.get("/analysis/why").get_data(as_text=True)


# ---------------------------------------------------------------- 성공·실패 이유
def synth_rows(n=1200, seed=2):
    """특징 하나(volratio)가 성공을 만드는 가짜 신호 + 무작위 기준."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        v = float(rng.normal(2.0, 1.0))
        r20 = 0.02 * (v - 2.0) + float(rng.normal(0, 0.03))
        feats = {f: float(rng.normal()) for f in bt.FEATURES}
        feats["volratio"] = v
        feats["mkt_r60"] = float(rng.normal(0.02, 0.1))
        why_code = 1.0 if r20 < -0.02 else 0.0
        rows.append(("X", f"202{i % 5}-{i % 12 + 1:02d}-{i % 27 + 1:02d}", r20, 1.0 if r20 > 0.05 else (0.0 if why_code else np.nan),
                     r20, why_code, float(i % 8 + 1), float(i % 3 == 0), *[feats[f] for f in bt.FEATURES]))
    return rows


def test_why_finds_the_feature_that_drives_success():
    rows = synth_rows()
    base = [r[:2] + (0.0,) + r[3:] for r in synth_rows(600, seed=9)]
    dates = sorted({r[1] for r in rows})
    mk = pd.DataFrame({0: [0.0] * len(dates)}, index=dates)
    rep = why.analyze({"가짜 기법": rows, bt.BASE_LABEL: base}, mk)
    d = rep["labels"]["가짜 기법"]
    top = d["features"][0]
    assert top["name"] == "volratio" and top["d"] > 0.5 and top["succ_mean"] > top["fail_mean"]
    q = top["q"]
    assert q[-1]["rate"] > q[0]["rate"] + 0.3                      # 값이 클수록 성공률이 높다
    fm = d["fail_modes"]
    assert abs(sum(v for k, v in fm.items() if k.startswith(("stop_", "time_"))) - 1.0) < 1e-9
    assert d["regimes"] and d["years"]


def test_why_page_renders(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(why, "WHY_PATH", tmp_path / "why.json")
    rows = synth_rows()
    dates = sorted({r[1] for r in rows})
    rep = why.analyze({"가짜 기법": rows, bt.BASE_LABEL: rows[:500]}, pd.DataFrame({0: [0.0] * len(dates)}, index=dates))
    rep["labels"]["가짜 기법"]["tech"] = "이평선"
    (tmp_path / "why.json").write_text(json.dumps(rep), encoding="utf-8")
    from app import create_app
    html = create_app().test_client().get("/analysis/why").get_data(as_text=True)
    assert "가짜 기법" in html and "거래량 배수" in html and "시장이 같이 빠짐" in html


def test_time_to_target_quantiles():
    acc = {}
    for held in (3, 5, 5, 8, 20):
        bt._addv(acc, "k", bt._sim_vec(0.1, "target", 0.05, held, False))
    for held in (2, 4):
        bt._addv(acc, "k", bt._sim_vec(-0.05, "stop", 0.05, held, False))
    s = bt._sim_stats(acc["k"])
    assert s["target"] == pytest.approx(5 / 7) and s["held_target"] == pytest.approx(41 / 5)
    assert (s["held_q25"], s["held_q50"], s["held_q75"]) == (5, 5, 8)      # 3,5,5,8,20 의 사분위(막대 기준)
    assert s["held_stop"] == pytest.approx(3.0)


def test_fixed_percent_targets_are_simulated():
    a = walk(500)
    keys = {k for k, *_ in bt._metrics(a, "x", 200, None, False)["sims"]}
    assert {"atr2|p10", "atr2|p20", "atr2|3R"} <= keys and "atr3|p10" not in keys
