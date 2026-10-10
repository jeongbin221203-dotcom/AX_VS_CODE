import json

import numpy as np
import pandas as pd

import config
from core import ai, presignal_study as ps


def test_setups_cover_dante_techs_and_are_boolean():
    n = 300
    rng = np.random.default_rng(0)
    D = pd.DataFrame({k: rng.normal(0, 0.1, n) for k in ps.FEATS})
    for k in ("acc_near_high", "reversed", "aligned", "box_top", "higher_lows"):
        D[k] = (rng.random(n) < 0.3).astype(float)
    D["below224_days"] = rng.integers(0, 120, n).astype(float)
    out = ps._setups(D)
    techs = {t for t, _, _ in out}
    assert {"매집봉 돌파", "224 돌파", "밥그릇 돌파", "공구리(언덕 돌파)", "256 완성(단기)", "이평 때리기(112)"} <= techs
    assert all(np.asarray(m).dtype == bool and len(m) == n for _, _, m in out) and techs <= set(ai.SIGNAL_LABELS)


def test_work_labels_pre_days_and_future_signal(tmp_path, monkeypatch):
    from core import collector, db
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(ps.pre, "LIQ_MIN", 0.0)
    db.init_db()
    n = 700
    rng = np.random.default_rng(3)
    c = 100 * np.cumprod(1 + rng.normal(0.0, 0.01, n))
    o = np.r_[c[0], c[:-1]]
    v = rng.uniform(5000, 15000, n)
    df = pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.01, "low": np.minimum(o, c) * 0.99, "close": c, "volume": v}, index=pd.date_range("2019-01-01", periods=n, freq="B"))
    collector.save_prices("S1", df)
    ai.MKT_DF = None
    out = ps._work("S1")
    assert out is not None
    R = out["rows"]
    assert set(ps.FEATS) <= set(R) and "fwd_any" in R and R["r20"].shape == R["date"].shape
    # 신호가 난 날의 5일 전 행은 pre_day=1 이고 그 행의 해당 fwd 라벨은 1
    for j in ps.DANTE:
        sig_rows = np.flatnonzero(R[f"t{j:02d}"] == 1)
        if len(sig_rows):
            d = pd.to_datetime(R["date"][sig_rows[0]])
            prev = np.flatnonzero((pd.to_datetime(R["date"]) == d - pd.offsets.BDay(5)) & (R["pre_day"] == 1))
            if len(prev):
                assert R[f"f{j:02d}"][prev[0]] == 1
            break


def test_presignal_page(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(ps, "PRESIG_PATH", tmp_path / "p.json")
    from app import create_app
    c = create_app().test_client()
    assert "아직 분석 결과가 없습니다" in c.get("/presignal").get_data(as_text=True)
    s = {"n": 500, "mean": 0.01, "p_up": 0.5, "p_big_up": 0.2, "p_dn10": 0.2, "q05": -0.2, "touch20": 0.3, "mae": -0.08}
    setup = {"name": "매집봉 고가 바로 아래", "n": 500, "share": 0.02, "precision": 0.15, "lift": 8.0, "recall": 0.3, "tr": s, "te": s, "came": s, "not_came": s, "robust": True}
    rep = {"meta": {"generated": "x", "rows": 1000, "sampled": 900, "ahead": 5, "split": {"train_end": "2021-12-31"}, "cost": 0.003},
           "base": {"tr": s, "te": s, "rate_any": 0.2},
           "techs": [{"label": "매집봉 돌파", "n_sig": 100, "rate5": 0.02, "at_signal": {"tr": s, "te": s}, "pre5_actual": {"tr": s, "te": s}, "setups": [setup]}],
           "model": {"auc": 0.78, "base": 0.2, "deciles": [{"bin": i, "p": 0.2, "actual": 0.2, "r20": 0.0, "p_up": 0.4} for i in range(1, 11)], "top_came": s, "top_not": s, "top_tr_check": s, "importance": [{"name": "dist224", "label": "224일선 대비", "gain": 0.01}]},
           "today": {"date": "2026-10-08", "n": 1000, "rows": [{"code": "005930", "name": "삼성전자", "close": 100000.0, "prob": 0.6, "setups": ["매집봉 돌파: 매집봉 고가 바로 아래 ✔"], "dist224": 0.1, "dist20": 0.01, "acc_near_high": 1.0, "hill_gap": -0.05, "ret20": 0.05, "nh250": -0.1, "atrp": 0.03}]},
           "text": ["요약 문장"]}
    (tmp_path / "p.json").write_text(json.dumps(rep), encoding="utf-8")
    html = c.get("/presignal").get_data(as_text=True)
    for k in ("요약 문장", "삼성전자", "신호일 매수 vs 5일 전 매수", "예측 모델", "신호가 임박한 종목"):
        assert k in html
