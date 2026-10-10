import json

import numpy as np
import pandas as pd

import config
from core import ai, plan_study as pl


def _T(n, seed=0):
    rng = np.random.default_rng(seed)
    T = {k: rng.normal(0.01, 0.1, n) for _, k in pl.ENTRIES + pl.EXITS}
    T["mae0"] = -np.abs(rng.normal(0.05, 0.04, n))
    T["gap1"] = rng.normal(0.0, 0.02, n)
    for k in ("tgt10_d", "tgt20_d"):
        T[k] = rng.integers(1, 21, n).astype(float)
    for k in ("trail10_d", "ma20x_d"):
        T[k] = rng.integers(1, 61, n).astype(float)
    return T


def test_pick_uses_only_past_and_prefers_better_entry():
    n = 4000
    T = _T(n)
    T["x5"] = T["r20"] + 0.05                                    # 5일 뒤가 항상 더 좋음
    T["tgt20"] = T["r20"] + 0.08                                 # 청산은 목표 +20% 가 최고
    past = np.arange(n) < 3000
    m = np.ones(n, bool)
    conds = [("조건 없음", np.ones(n, bool)), ("나쁜 조건", np.arange(n) % 2 == 0)]
    T["r20"][np.arange(n) % 2 == 0] -= 0.03                      # 짝수 행은 더 나쁨 → '나쁜 조건' 은 안 골라야
    pk = pl._pick(T, m, conds, past)
    assert pk["cond"][0] == "조건 없음" and pk["entry"][0] == "5일 뒤 시가" and pk["exit"][0] == "목표 +20%"
    assert pk["past"]["n"] == 3000
    assert pl._pick(T, m, conds, np.arange(n) < 100) is None      # 표본 부족


def test_mae_and_days_tables():
    n = 2000
    T = _T(n, 2)
    m = np.ones(n, bool)
    mt = pl._mae_table(T, m)
    assert mt["rows"] and all(0 <= r["share"] <= 1 and 0 <= r["win_after"] <= 1 for r in mt["rows"]) and mt["mae_win_med"] <= 0
    shares = [r["share"] for r in mt["rows"]]
    assert shares == sorted(shares, reverse=True)                # 깊을수록 걸리는 비율은 줄어든다
    dt = pl._days_table(T, m)
    assert "목표 +10%" in dt and 0 <= dt["목표 +10%"]["hit"] <= 1


def test_plan_page(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(pl, "PLAN_PATH", tmp_path / "p.json")
    from app import create_app
    c = create_app().test_client()
    assert "아직 분석 결과가 없습니다" in c.get("/plan").get_data(as_text=True)
    s = {"n": 500, "mean": 0.01, "p_up": 0.5, "p_big_up": 0.2, "p_dn10": 0.2, "q05": -0.2, "median": 0.0, "coverage": 0.8, "per_signal": 0.008}
    mae = {"rows": [{"level": 0.05, "share": 0.4, "win_after": 0.3, "mean_after": -0.02, "win_share": 0.2, "lose_share": 0.6}], "mae_win_med": -0.03, "mae_lose_med": -0.1, "n": 1000}
    days = {"목표 +10%": {"hit": 0.4, "days_med": 8.0, "mean": 0.02, "p_up": 0.6}}
    hist = [{"year": 2024, "cond": "조건 없음", "entry": "다음날 시가 전액", "exit": "목표 +10%", "n": 100, "entry_stat": s, "exit_stat": s, "naive": s}]
    tech = {"label": "52주 신고가", "dante": False, "n": 1000, "years": 1, "oos": {"entry": s, "exit": s, "naive": s, "share": 0.5, "years_pos": 1}, "rule_now": {"cond": "조건 없음", "entry": "다음날 시가 전액", "exit": "목표 +10%"}, "freq": {}, "hist": hist, "mae": mae, "days": days}
    rep = {"meta": {"generated": "x", "rows": 1000, "wf_start": 2012, "cost": 0.003, "min_n": 300}, "techs": [tech], "base": {"naive": s, "mae": mae},
           "today": {"date": "2026-10-08", "n": 1, "mkt_dd250": -0.2, "rows": [{"code": "005930", "name": "삼성전자", "close": 100000.0, "signals": ["52주 신고가"], "ai_top": True, "best": "52주 신고가", "good": True, "cond": "조건 없음", "entry": "다음날 시가 전액", "exit": "목표 +10%", "cond_met": True, "avoid": False, "prob": 0.2, "oos_mean": 0.01, "oos_up": 0.5}]},
           "text": ["요약 문장"]}
    (tmp_path / "p.json").write_text(json.dumps(rep), encoding="utf-8")
    html = c.get("/plan").get_data(as_text=True)
    for k in ("요약 문장", "삼성전자", "처음 보는 해들의 성과", "매매 계획", "손절 위치"):
        assert k in html
