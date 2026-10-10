import json

import numpy as np
import pandas as pd

import config
from core import ai, combined_study as cs


def _fake(n=6000, seed=7):
    rng = np.random.default_rng(seed)
    D = pd.DataFrame(rng.normal(0, 0.1, (n, len(ai.NAMES))).astype(np.float32), columns=ai.NAMES)
    for j in range(len(ai.SIGNAL_LABELS)):
        D[f"sig{j:02d}"] = rng.integers(0, 61, n).astype(float)
    D["aligned"] = (rng.random(n) < 0.3).astype(float)
    D["acc_recent"] = (rng.random(n) < 0.3).astype(float)
    D["volratio"] = rng.uniform(0.5, 6, n)
    D["low_age"] = rng.integers(0, 60, n).astype(float)
    D["close_px"] = rng.uniform(500, 50000, n)
    for k in ("zero_vol60", "gapdn20", "down_streak"):
        D[k] = rng.integers(0, 4, n).astype(float)
    D["gap1"] = rng.normal(0.0, 0.02, n)
    return D


def test_candidates_are_boolean_and_lookahead_free_columns():
    D = _fake()
    c = cs.candidates(D)
    assert len(c) >= 10 and all(m.dtype == bool and len(m) == len(D) for _, m, _ in c)
    assert all(isinstance(why, str) and why for _, _, why in c)


def test_stage_and_pass_logic():
    n = 6000
    rng = np.random.default_rng(1)
    date = np.array([f"{2010 + i % 16}-03-{1 + i % 28:02d}" for i in range(n)])
    T = {"r20": rng.normal(0.01, 0.1, n), "r60": rng.normal(0, 0.2, n), "r120": rng.normal(0, 0.3, n), "touch": rng.uniform(0, 0.5, n),
         "split3": rng.normal(0.01, 0.08, n), "mae0": rng.uniform(-0.2, 0, n), "mae_split": rng.uniform(-0.15, 0, n), "stop_ret": rng.normal(0, 0.08, n)}
    ex = T["r20"] - pd.Series(T["r20"]).groupby(pd.Series(date)).transform("mean").to_numpy()
    tr = date <= ai.VALID_END
    te = ~tr
    m = np.ones(n, bool)
    s = cs._stage("x", m, T, ex, date, tr, te)
    assert s["all"]["n"] == n and s["te"]["n"] == te.sum() and s["per_month_te"] > 0 and "t" in s["te"] and s["te"]["dates"] > 0
    s2 = cs._stage("y", m, T, ex, date, tr, te, ret_key="split3", mae_key="mae_split")
    assert abs(s2["all"]["mean"] - (T["split3"].mean() - cs.COST)) < 1e-9
    y = cs._yearly(m, T, ex, date)
    assert len(y) == 16 and all(abs(r["mean"] - r["market"] - r["excess"]) < 1e-9 for r in y)
    mo = cs._monthly(te, T, date)
    assert mo and mo["months"] >= 1 and 0 <= mo["p_pos"] <= 1 and len(mo["last"]) <= 24


def test_today_filters_avoid_and_requires_signal():
    X = np.zeros(len(ai.NAMES), np.float32)
    ix = {n: i for i, n in enumerate(ai.NAMES)}
    X[ix["liq"]] = 10
    X[ix["atrp"]] = 0.03
    for j in range(len(ai.SIGNAL_LABELS)):
        X[ix[f"sig{j:02d}"]] = 60
    base = {"date": "2026-10-07", "close": 1000.0, "ok": True, "zero_vol60": 0.0, "gapdn20": 0.0, "down_streak": 0.0, "close_px": 1000.0}
    a = {**base, "code": "A", "X": X.copy()}
    a["X"][ix[f"sig{ai.SIGNAL_LABELS.index('52주 신고가'):02d}"]] = 0           # 채택 신호
    b = {**base, "code": "B", "X": a["X"].copy()}
    b["X"][ix["atrp"]] = 0.10                                                   # 피함(변동성 8%↑)
    c = {**base, "code": "C", "X": X.copy()}                                    # 신호 없음
    out = cs._today([a, b, c], {"A": "에이"}, None, ["52주 신고가"])
    assert [r["code"] for r in out["rows"]] == ["A"] and out["n_tradable"] == 3 and out["n_keep"] == 2 and out["rows"][0]["name"] == "에이"


def test_combined_page(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(cs, "COMBINED_PATH", tmp_path / "c.json")
    from app import create_app
    c = create_app().test_client()
    assert "아직 분석 결과가 없습니다" in c.get("/combined").get_data(as_text=True)
    st = {"n": 500, "mean": 0.01, "median": 0.0, "std": 0.1, "p_up": 0.5, "p_big_up": 0.2, "p_dn10": 0.1, "p_dn20": 0.05, "q05": -0.2, "excess": 0.005, "mae": -0.08, "r60": 0.02, "r120": 0.03, "touch20": 0.2, "t": 2.5, "dates": 100}
    stage = lambda name: {"name": name, "note": "", "ret_key": "r20", "all": st, "tr": st, "te": st, "per_month_te": 12.0, "share": 0.1}  # noqa: E731
    rep = {"meta": {"generated": "x", "rows": 1000, "stocks": 10, "cost": 0.003, "hold": 20, "split": {"train_end": "2021-12-31", "date_min": "2000-01-01", "date_max": "2026-10-07"},
                    "gap_max": 0.03, "sig_win": 3, "ai": True, "ai_top": 0.1},
           "avoid": {"rows": [{"name": "변동성 높음", "n": 100, "share": 0.1, "excess_te": -0.02}], "share": 0.17},
           "candidates": [{**stage("52주 신고가"), "why": "근거", "pass": True}, {**stage("AI 점수 상위 10%"), "why": "근거", "pass": False, "ai": True}],
           "chosen": ["52주 신고가"], "stages": [stage("0. 기준"), stage("1. 피함"), stage("2. 채택"), stage("3. 갭"), stage("4. 3분할"), stage("5. AI")],
           "final": {"n": 500, "n_te": 100, "yearly": [{"year": "2023", "n": 50, "mean": 0.01, "excess": 0.002, "p_up": 0.5, "market": 0.008, "p_dn20": 0.05}], "yearly_full": [],
                     "monthly_te": {"months": 12, "p_pos": 0.6, "mean": 0.01, "worst": -0.05, "best": 0.08, "cum": 0.1, "last": [{"mon": "2026-09", "mean": 0.02, "n": 10}]},
                     "regime": [{"name": "시장 강세", "n": 100, "share": 0.3, "mean": 0.01, "excess": 0.002, "excess_te": 0.001, "p_up": 0.5, "p_dn20": 0.05, "median": 0, "std": 0.1, "p_big_up": 0.2, "p_dn10": 0.1, "q05": -0.2}],
                     "by_signal": [stage("52주 신고가")], "overlap": [stage("채택 신호 1개 겹침")]},
           "today": {"date": "2026-10-07", "rows": [{"code": "005930", "name": "삼성전자", "date": "2026-10-07", "close": 100000.0, "signals": ["52주 신고가"], "n_sig": 1, "prob": 0.2,
                                                   "volratio": 2.0, "ret20": 0.05, "nh250": -0.01, "atrp": 0.03, "dist20": 0.02, "mkt_r60": 0.05}], "n_rows": 1, "n_tradable": 500, "n_keep": 400},
           "text": ["요약 문장"]}
    (tmp_path / "c.json").write_text(json.dumps(rep), encoding="utf-8")
    html = c.get("/combined").get_data(as_text=True)
    for s in ("요약 문장", "삼성전자", "규칙을 하나씩 쌓았을 때", "최종 규칙의 안정성", "2026-09"):
        assert s in html
