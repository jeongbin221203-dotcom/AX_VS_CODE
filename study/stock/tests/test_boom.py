import json

import numpy as np
import pandas as pd

import config
from core import boom_study as bs


def _df(n=800, seed=0):
    rng = np.random.default_rng(seed)
    c = 100 * np.cumprod(1 + rng.normal(0.0, 0.004, n))
    o = np.r_[c[0], c[:-1]]
    v = np.full(n, 10000.0)
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.002, "low": np.minimum(o, c) * 0.998, "close": c, "volume": v}, index=pd.date_range("2018-01-01", periods=n, freq="B"))


def test_episode_start_is_the_low_before_the_rise(monkeypatch):
    monkeypatch.setattr(bs, "LIQ_MIN", 0.0)
    df = _df()
    c = df["close"].to_numpy().copy()
    # 400~404 완만히 내려가다 405 부터 10일 동안 +80% 급등
    c[400:405] = c[399] * np.array([0.99, 0.98, 0.97, 0.96, 0.95])
    for i in range(405, 415):
        c[i] = c[i - 1] * 1.06
    c[415:] = c[414]
    df["close"] = c
    df["open"] = np.r_[c[0], c[:-1]]
    df["high"] = np.maximum(df["open"], df["close"]) * 1.002
    df["low"] = np.minimum(df["open"], df["close"]) * 0.998
    o, h, l, cc, v, starts, base = bs._episodes(df)
    assert list(starts) == [404]                                    # 첫 +50% 도달 가능일(395 근처)이 아니라 실제 저점 404
    P = bs._profile(o, h, l, cc, v, starts)
    assert P["gain"][0] > 0.75 and 9 <= P["peak_day"][0] <= 11 and abs(P["first_ret"][0] - 0.06) < 0.01
    assert P["path_c"].shape == (1, bs.PRE + bs.WIN + 1) and abs(P["path_c"][0, bs.PRE] - 1.0) < 1e-9
    assert P["retrace60"][0] <= 0 and P["up_days"][0] >= 10


def test_why_assigns_each_event_once():
    n = 300
    rng = np.random.default_rng(1)
    E = {k: rng.normal(0, 0.2, n) for k in ("first_ret", "first_gap", "vr5", "vmax5", "nh250", "ret20", "range20", "mkt_r20", "dist20", "gain", "peak_day", "r60", "r120", "retrace60")}
    E["aligned"] = (rng.random(n) < 0.3).astype(float)
    E["vr5"] = rng.uniform(0.5, 3, n)
    E["vmax5"] = rng.uniform(0.5, 5, n)
    E["range20"] = rng.uniform(0.05, 0.4, n)
    out = bs._why(E)
    assert abs(sum(x["share_first"] for x in out) - 1.0) < 1e-6 and all(0 <= x["share_any"] <= 1 for x in out)


def test_boom_page(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(bs, "BOOM_PATH", tmp_path / "b.json")
    from app import create_app
    c = create_app().test_client()
    assert "아직 분석 결과가 없습니다" in c.get("/boom").get_data(as_text=True)
    days = list(range(-bs.PRE, bs.WIN + 1))
    rep = {"meta": {"generated": "x", "gain": 0.5, "win": 20, "events": 100, "baseline": 1000, "stocks": 50, "date_min": "2000-01-01", "date_max": "2026-10-10", "liq_min": 3e8},
           "by_year": [{"year": 2025, "n": 10, "per_stock": 1.2, "base_rate": 0.01}],
           "conditions": [{"group": g, "name": f"{g} 조건", "base": 0.1, "boom": 0.3, "lift": 3.0, "n": 50, "gain": 0.6, "enterable": 0.8, "r120": 0.2, "pre10": 0.2, "lift10": 2.0} for g in ["위치", "추세", "응축", "거래량", "종목", "시장"]],
           "paths": [{"d": d, "c": 1.0, "v": 0.8, "vmean": 1.0, "v2": 0.1, "bc": 1.0, "bv": 0.7, "bv2": 0.1} for d in days],
           "stats": {"gain_med": 0.6, "gain_q75": 0.8, "gain100": 0.2, "peak_med": 8.0, "peak_hist": [{"label": "1~5일", "share": 0.1}], "gain20_med": 0.55, "move_end_med": 12.0, "first_ret_med": 0.03, "first_up10": 0.2, "first_up20": 0.05,
                     "first_gap5": 0.1, "first_vr3": 0.3, "first_down": 0.2, "up_days_med": 12.0, "big_days_med": 2.0, "big_days_share2": 0.5, "retrace_med": -0.3, "retrace50": 0.1, "retrace30": 0.5,
                     "r20": 0.3, "r60": 0.2, "r120": 0.1, "r120_med": 0.05, "r120_pos": 0.55, "r120_keep_half": 0.4, "base_r120": 0.02, "base_first_up10": 0.02},
           "why": [{"name": "첫날 상한가·큰 갭 (재료·뉴스 추정)", "share_any": 0.3, "share_first": 0.3, "n": 30, "gain": 0.7, "peak_day": 5.0, "r60": 0.1, "r120": 0.0, "retrace": -0.4, "enterable": 0.1}],
           "chase": {"after_peak_r60": -0.3, "share_new_high_after": 0.2},
           "cache": {"n_boom": 500, "n_pre": 300, "p_boom": 0.02, "signals": [{"name": "매집봉 돌파", "dante": True, "base": 0.03, "boom": 0.04, "pre": 0.05, "lift": 1.3, "lift_pre": 1.6}],
                     "stack": [{"name": "신호 0개 겹침", "base": 0.5, "p_boom": 0.02}], "diff": [{"name": "atrp", "label": "변동성", "pre": 0.05, "rest": 0.04, "d": 0.3}]},
           "recent": [{"code": "005930", "name": "삼성전자", "date": "2026-05-02", "gain": 0.9, "peak_day": 7.0, "first_ret": 0.05, "nh250": -0.3, "ret20": -0.1, "vr5": 1.5, "range20": 0.1, "r120": 0.5, "retrace": -0.2}],
           "text": ["요약 문장"]}
    (tmp_path / "b.json").write_text(json.dumps(rep), encoding="utf-8")
    html = c.get("/boom").get_data(as_text=True)
    for k in ("요약 문장", "삼성전자", "오르기 전 공통점", "왜 올랐나", "기법 신호와 급등", "최근 1년 급등 사례"):
        assert k in html
