import json

import numpy as np
import pandas as pd

import config
from core import ai, jump_study as js


def test_work_finds_jumps_history_and_outcomes(tmp_path, monkeypatch):
    from core import collector, db
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(js, "LIQ_MIN", 0.0)
    db.init_db()
    n = 900
    rng = np.random.default_rng(5)
    c = 100 * np.cumprod(1 + rng.normal(0.0, 0.003, n))
    c[300] = c[299] * 1.25                    # 첫 급등
    c[301:] = c[301:] * 1.25
    c[600] = c[599] * 1.22                    # 두 번째 급등(300일 뒤)
    c[601:] = c[601:] * 1.22
    o = np.r_[c[0], c[:-1]]
    df = pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.003, "low": np.minimum(o, c) * 0.997, "close": c, "volume": np.full(n, 5000.0)},
                      index=pd.date_range("2019-01-01", periods=n, freq="B"))
    collector.save_prices("J1", df)
    ai.MKT_DF = None
    out = js._work("J1")
    assert out is not None and list(out["date"]) == [df.index[300].strftime("%Y-%m-%d"), df.index[600].strftime("%Y-%m-%d")]
    assert out["n_j250"][0] == 0 and out["n_j250"][1] == 0 and out["n_j750"][1] == 1 and out["days_since"][1] == 300 and out["days_since"][0] == 2000
    assert np.isnan(out["prev_r20"][0]) and np.isfinite(out["prev_r20"][1])          # 두 번째 급등은 첫 급등의 20일 결과를 안다
    assert out["next_jump250"][0] == 0 and np.isfinite(out["r20"][1]) and abs(out["jump"][0] - 0.25) < 1e-9
    assert out["hist_rate"][1] > 0 and out["touch20"][0] == 0


def test_group_and_stats():
    n = 1000
    rng = np.random.default_rng(1)
    E = {k: rng.normal(0, 0.1, n) for k in ("touch20", "giveback", "next_jump60", "next_jump250", "r1", "r60")}
    r = rng.normal(0.0, 0.1, n)
    tr = np.arange(n) < 600
    rows = js._group(E, r, tr, ~tr, [("전부", np.ones(n, bool)), ("너무 적음", np.arange(n) < 10)])
    assert len(rows) == 1 and rows[0]["all"]["n"] == n and rows[0]["te"]["n"] == 400 and "touch20" in rows[0]["all"]


def test_jump_page(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(js, "JUMP_PATH", tmp_path / "j.json")
    from app import create_app
    c = create_app().test_client()
    assert "아직 분석 결과가 없습니다" in c.get("/jump").get_data(as_text=True)
    s = {"n": 500, "mean": -0.02, "median": -0.03, "p_up": 0.35, "p_big_up": 0.2, "p_dn10": 0.3, "p_dn20": 0.1, "q05": -0.4, "touch20": 0.4, "giveback": 0.5, "next_jump60": 0.3, "next_jump250": 0.7, "r1": 0.0, "r60": -0.05}
    g = lambda name: {"name": name, "n": 500, "share": 0.5, "all": s, "tr": s, "te": s}  # noqa: E731
    mr = {"name": "x", "auc": 0.55, "n_te": 1000, "base": 0.3, "top10_hit": 0.4, "bot10_hit": 0.2, "top10_r20": 0.01, "bot10_r20": -0.05, "top10_up": 0.4, "all_up": 0.3, "yearly": [{"year": "2024", "n": 50, "hit": 0.4, "base": 0.3}]}
    rep = {"meta": {"generated": "x", "jump": 0.2, "events": 1000, "stocks": 100, "date_min": "2000-01-01", "date_max": "2026-10-10", "split": {"train_end": "2021-12-31"}, "cost": 0.003, "liq_min": 3e8},
           "overall": {"all": s, "tr": s, "te": s, "entry_gap": 0.03, "entry_gap_med": 0.02, "r1_up": 0.45, "r5": -0.01, "dd20": -0.2},
           "by_year": [{"year": 2024, "n": 100, "r20": -0.02, "p_up": 0.35, "touch": 0.4}], "history": [g("처음 급등")], "repeat": [{"name": "처음 급등", "n": 500, "p60": 0.3, "p250": 0.6, "r20": -0.01}],
           "context": [g("상한가")], "models": [{"target": "20일 뒤 플러스", "rows": [mr, {**mr, "name": "+ 이전 급등 내역"}], "importance": [{"name": "n_j250", "label": "최근 250일 급등 횟수", "hist": True, "gain": 0.01}]}],
           "today": {"since": "2026-09-25", "n": 1, "rows": [{"code": "005930", "name": "삼성전자", "date": "2026-10-01", "close": 100000.0, "jump": 0.22, "prob": 0.4, "n_j250": 1, "days_since": 30, "prev_r20": 0.1, "hist_rate": 1.5, "volratio": 8.0, "nh250": -0.1, "close_pos": 0.9, "r1": 0.02}]},
           "text": ["요약 문장"]}
    (tmp_path / "j.json").write_text(json.dumps(rep), encoding="utf-8")
    html = c.get("/jump").get_data(as_text=True)
    for k in ("요약 문장", "삼성전자", "이전 급등 내역별", "정확성", "최근 급등일"):
        assert k in html
