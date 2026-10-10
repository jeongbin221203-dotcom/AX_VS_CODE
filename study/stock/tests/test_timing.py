import json

import numpy as np

import config
from core import timing_study as ts


def _T(n, seed=0):
    rng = np.random.default_rng(seed)
    T = {k: rng.normal(0.01, 0.1, n) for _, k in ts.ENTRIES + ts.EXITS}
    T["conf_ret"][rng.random(n) < 0.5] = np.nan                      # 미체결
    return T


def test_method_rows_flags_better_only_when_both_periods_improve():
    n = 2000
    T = _T(n)
    tr = np.arange(n) < 1000
    te = ~tr
    m = np.ones(n, bool)
    ex = T["r20"] - T["r20"].mean()
    T["x5"] = T["r20"] + 0.05                                        # 항상 더 좋음
    T["x10"] = np.where(tr, T["r20"] + 0.05, T["r20"] - 0.05)       # 학습만 좋음
    rows = {r["key"]: r for r in ts._method_rows(T, m, tr, te, ex)}
    assert rows["x5"]["better"] and not rows["x10"]["better"] and not rows["r20"]["better"]
    assert rows["conf_ret"]["te"]["coverage"] < 0.7 and abs(rows["conf_ret"]["te"]["per_signal"] - rows["conf_ret"]["te"]["coverage"] * rows["conf_ret"]["te"]["mean"]) < 1e-9


def test_cond_rows_and_page(tmp_path, monkeypatch):
    n = 2000
    T = _T(n, 1)
    tr = np.arange(n) < 1000
    te = ~tr
    m = np.ones(n, bool)
    ex = T["r20"] - T["r20"].mean()
    gap_ok = np.random.default_rng(2).random(n) < 0.9
    good = T["r20"] > 0.02                                           # 결과를 아는 조건(검사용) → 두 시기 모두 좋아져야 함
    rows = {r["name"]: r for r in ts._cond_rows(T, None, m, tr, te, ex, gap_ok, good, np.ones(n, bool), np.ones(n, bool))}
    assert rows["시장 52주 고점 −10% 아래일 때만"]["better"] and not rows["조건 없음"]["better"]
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(ts, "TIMING_PATH", tmp_path / "t.json")
    from app import create_app
    c = create_app().test_client()
    assert "아직 분석 결과가 없습니다" in c.get("/timing").get_data(as_text=True)
    s = {"n": 500, "coverage": 1.0, "mean": 0.01, "per_signal": 0.01, "p_up": 0.5, "p_big_up": 0.2, "p_dn10": 0.2, "q05": -0.2, "std": 0.1, "excess": 0.005}
    meth = [{"name": "다음날 시가 전액", "key": "r20", "kind": "진입", "tr": s, "te": s, "better": False}, {"name": "5일 뒤 시가", "key": "x5", "kind": "진입", "tr": s, "te": s, "better": True}]
    cond = [{"name": "조건 없음", "n_te": 500, "share_te": 1.0, "tr": s, "te": s, "better": False}]
    tech = {"label": "52주 신고가", "dante": False, "n_tr": 1000, "n_te": 500, "share": 0.05, "base": meth[0], "methods": meth, "conds": cond, "best_entry": "5일 뒤 시가", "best_exit": "20일 뒤 종가", "best_cond": "조건 없음", "best_p_up": 0.55, "robust": True}
    rep = {"meta": {"generated": "x", "rows": 1000, "split": {"train_end": "2021-12-31"}, "cost": 0.003, "ai": True, "gap_max": 0.03}, "techs": [tech], "base_all": {"tr": s, "te": s},
           "today": {"date": "2026-10-08", "n": 1, "mkt_dd250": -0.2, "mkt_under": True, "rows": [{"code": "005930", "name": "삼성전자", "close": 100000.0, "signals": ["52주 신고가"], "best_signal": "52주 신고가", "entry": "5일 뒤 시가", "exit": "20일 뒤 종가", "cond": "조건 없음", "cond_met": True, "p_up": 0.55, "robust": True, "prob": 0.2, "ai_top": True, "halt_p": 0.01, "avoid": False, "ret20": 0.1, "nh250": -0.01, "atrp": 0.03}]},
           "text": ["요약 문장"]}
    (tmp_path / "t.json").write_text(json.dumps(rep), encoding="utf-8")
    html = c.get("/timing").get_data(as_text=True)
    for k in ("요약 문장", "삼성전자", "권장 매수 시점", "기법별 요약", "기법별 상세"):
        assert k in html
