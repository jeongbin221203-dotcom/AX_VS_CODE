import json

import numpy as np
import pandas as pd

import config
from core import precursor_study as ps


def _df(n=700, seed=0):
    rng = np.random.default_rng(seed)
    c = 100 * np.cumprod(1 + rng.normal(0.0, 0.004, n))
    c[500] = c[499] * 1.25                       # 급등일
    c[501:] = c[501:] * 1.25
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.002, "low": np.minimum(o, c) * 0.998, "close": c, "volume": np.full(n, 3000.0)}, index=pd.date_range("2019-01-01", periods=n, freq="B"))


def test_features_forward_labels_and_history_are_lookahead_safe():
    df = _df()
    F, jump, c, v, vr = ps._features(df)
    assert jump[500] and not jump[499]
    assert F["fwd20"][480] == 1 and F["fwd20"][479] == 0 and F["fwd5"][495] == 1 and F["fwd5"][494] == 0     # 앞으로 20일/5일 안 급등
    assert F["n_j250"][500] == 1 and F["n_j250"][499] == 0 and F["days_since"][499] == 2000 and F["days_since"][520] == 20
    assert np.isnan(F["fwd20"][-1]) and F["above224_days"][300] >= 0 and F["ma_below_n"][300] in (0, 1, 2, 3, 4)


def test_work_uses_day_before_jump(tmp_path, monkeypatch):
    from core import collector, db, ai
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(ps, "LIQ_MIN", 0.0)
    db.init_db()
    df = _df()
    collector.save_prices("P1", df)
    ai.MKT_DF = None
    out = ps._work("P1")
    assert out["ev"] is not None and list(out["ev"]["date"]) == [df.index[499].strftime("%Y-%m-%d")]       # 급등 '전날'
    assert out["ev"]["fwd5"][0] == 1 and out["ev"]["path_c"].shape == (1, ps.PRE) and abs(out["ev"]["path_c"][0, -1] - 1) < 1e-6
    assert out["bs"] is not None and out["latest"]["code"] == "P1"


def test_precursor_page(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(ps, "PRE_PATH", tmp_path / "p.json")
    from app import create_app
    c = create_app().test_client()
    assert "아직 분석 결과가 없습니다" in c.get("/precursor").get_data(as_text=True)
    cond = lambda g, n: {"group": g, "name": n, "share_pre": 0.3, "share_base": 0.1, "lift_share": 3.0, "p20": 0.06, "lift20": 1.5, "p5": 0.02, "n_pre": 100, "n_base": 1000, "p_tr": 0.06, "p_te": 0.06, "lift_tr": 1.5, "lift_te": 1.4, "robust": True}  # noqa: E731
    mrow = {"auc": 0.7, "base": 0.04, "deciles": [{"bin": i, "p": 0.04, "actual": 0.04} for i in range(1, 11)], "top5": 0.2, "top1": 0.3, "importance": [{"name": "atrp", "label": "변동성", "gain": 0.01}]}
    rep = {"meta": {"generated": "x", "jump": 0.2, "events": 1000, "baseline": 50000, "stocks": 100, "date_min": "2000-01-01", "date_max": "2026-10-10", "split": {"train_end": "2021-12-31"}, "liq_min": 3e8, "base_step": 11},
           "base": {"p20": 0.04, "p5": 0.01, "p_tr": 0.04, "p_te": 0.04}, "conditions": [cond(g, g + " 조건") for g in ["224일선", "이평선 배열", "가격", "거래량", "이전 급등", "종목·시장"]],
           "paths": [{"d": d, "d224": 0.0, "v": 0.8, "v2": 0.1, "c": 1.0, "above224": 0.5} for d in range(-ps.PRE + 1, 1)],
           "diff": [{"name": "atrp", "label": "변동성", "pre": 0.05, "base": 0.03, "d": 0.5}],
           "model": {"전조 특징 (이전 급등 내역 제외)": mrow, "+ 이전 급등 내역": mrow},
           "today": {"date": "2026-10-10", "n": 1000, "base": 0.04, "rows": [{"code": "005930", "name": "삼성전자", "close": 100000.0, "prob": 0.2, "dist224": 0.1, "dist20": 0.02, "ma_conv": 0.1, "ret20": 0.05, "nh250": -0.05, "vr5": 1.2, "atrp": 0.03, "n_j250": 0.0, "range20": 0.1}]},
           "text": ["요약 문장"]}
    (tmp_path / "p.json").write_text(json.dumps(rep), encoding="utf-8")
    html = c.get("/precursor").get_data(as_text=True)
    for k in ("요약 문장", "삼성전자", "조건별 전조", "급등 전 60일 경로", "전조로 급등을 맞힐 수 있나"):
        assert k in html
