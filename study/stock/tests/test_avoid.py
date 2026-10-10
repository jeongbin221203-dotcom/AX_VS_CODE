import json

import numpy as np
import pandas as pd
import pytest

import config
from core import ai, avoid_study as av


def frame_for_rules(n=20000, seed=0):
    rng = np.random.default_rng(seed)
    d = pd.DataFrame({f: rng.normal(size=n) for f in ai.NAMES})
    d["close_px"] = rng.choice([300.0, 800.0, 3000.0, 20000.0], n)
    d["atrp"] = rng.uniform(0.01, 0.15, n)
    d["zero_vol60"] = rng.integers(0, 6, n).astype(float)
    d["gapdn20"] = rng.integers(0, 4, n).astype(float)
    d["down_streak"] = rng.integers(0, 8, n).astype(float)
    d["liq"] = rng.uniform(8.4, 10.5, n)
    d["ret20"] = rng.normal(0, 0.2, n)
    d["ret5"] = rng.normal(0, 0.08, n)
    return d


def test_rule_table_marks_a_truly_bad_condition_and_ignores_noise():
    rng = np.random.default_rng(1)
    d = frame_for_rules()
    n = len(d)
    date = np.array([f"{2012 + i % 14}-{i % 12 + 1:02d}-{i % 27 + 1:02d}" for i in range(n)])
    bad = (d["close_px"] < 1000) & (d["atrp"] >= 0.08)                         # 이 조합만 진짜로 나쁨(두 기간 모두)
    r = rng.normal(0.0, 0.1, n) - np.where(bad, 0.08, 0.0) - np.where(bad & (rng.random(n) < 0.3), 0.2, 0.0)
    excess = r - pd.Series(r).groupby(date).transform("mean").to_numpy()
    base, base_te, rows = av.avoid_study_table(d, r, excess, date > "2021-12-31") if hasattr(av, "avoid_study_table") else av._rule_table(d, r, excess, date > "2021-12-31")
    by = {x["name"]: x for x in rows}
    combo = by["저가주 + 고변동 (1,000원↓ & ATR 8%↑)"]
    assert combo["verdict"] == "피함" and combo["lift_dn20"] > 1.5 and combo["excess_tr"] < -0.005 and combo["excess_te"] < -0.005
    noise = by["연속 하락 5일↑"]
    assert noise["verdict"] == "관계 약함"                                        # 무작위 조건은 걸러지지 않음
    assert rows[0]["verdict"] == "피함"                                           # 피함이 맨 위로


def test_gap_study_buckets_and_methods():
    rng = np.random.default_rng(2)
    n = 9000
    gap = rng.normal(0.004, 0.02, n)
    r20 = rng.normal(0.0, 0.1, n) - 0.5 * np.maximum(gap, 0)                      # 갭상승이 클수록 이후 수익이 낮음
    T = {"gap1": gap, "r20": r20, "r20c": r20 + gap, "lc_ret": np.where(rng.random(n) < 0.6, r20 + 0.01, np.nan), "vwap_ret": r20 + 0.3 * gap}
    out = av._gap_study(T, np.ones(n, bool))
    assert out["dist"]["n"] == n and out["dist"]["p90"] > out["dist"]["median"]
    b = {x["label"]: x for x in out["buckets"]}
    assert b["+3~+5%"]["mean_open"] < b["−1~0%"]["mean_open"]
    ms = {m["name"]: m for m in out["methods"]}
    assert ms["갭 +1% 이하일 때만 진입"]["coverage"] < 1 and ms["갭 +1% 이하일 때만 진입"]["mean"] > ms["다음날 시가에 전액 (기준)"]["mean"]
    lc = ms["신호일 종가에 지정가 (다음날 그 값까지 내려오면 체결)"]
    assert 0.5 < lc["coverage"] < 0.7 and lc["per_signal"] == pytest.approx(lc["coverage"] * lc["mean"])


def test_avoid_extras_values():
    n = 120
    c = np.linspace(100, 120, n)
    o = c.copy()
    o[100] = c[99] * 0.9                                   # 100번째 날 −10% 갭 하락
    v = np.full(n, 1000.0)
    v[90:95] = 0.0
    df = pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.01, "low": np.minimum(o, c) * 0.99, "close": c, "volume": v},
                      index=pd.date_range("2024-01-01", periods=n, freq="B"))
    t = np.array([60, 105])
    r = ai._avoid_extras(df, o, df["high"].to_numpy(), df["low"].to_numpy(), c, t, t + 1)
    assert r["zero_vol60"][1] == 5 and r["gapdn20"][1] == 1 and r["gapdn20"][0] == 0
    assert r["gap1"][0] == pytest.approx(o[61] / c[60] - 1)
    assert r["lc_ret"][0] == pytest.approx(c[81] / min(o[61], c[60]) - 1) or np.isnan(r["lc_ret"][0])
    assert r["close_px"][0] == c[60]
    last = ai._avoid_latest(df)
    assert last["zero_vol60"] == 5 and last["close_px"] == c[-1]


def test_avoid_page(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(av, "AVOID_PATH", tmp_path / "avoid.json")
    from app import create_app
    c = create_app().test_client()
    assert "아직 분석 결과가 없습니다" in c.get("/avoid").get_data(as_text=True)
    st = av._stats(np.random.default_rng(0).normal(0, 0.1, 500))
    rule = {"name": "저가주 + 고변동 (1,000원↓ & ATR 8%↑)", "n": 900, "share": 0.05, "mean": -0.03, "excess": -0.03, "p_up": 0.3, "p_dn10": 0.4, "p_dn20": 0.2, "q05": -0.3,
            "p_dn20_rest": 0.07, "lift_dn20": 2.8, "excess_tr": -0.03, "excess_te": -0.04, "p_dn20_tr": 0.2, "p_dn20_te": 0.2, "n_te": 200, "verdict": "피함"}
    ba = {"before": st, "excluded": st, "after": st}
    rep = {"meta": {"generated": "x", "rows": 1000, "stocks": 10, "cost": 0.003,
                    "split": {"train_end": "2018-12-31", "valid_end": "2021-12-31", "date_min": "2000-01-01", "date_max": "2026-01-01"}},
           "rules": {"base": st, "base_test": st, "rows": [rule]},
           "narrowing": {"rules": [rule["name"]], "excluded_share": 0.05, "excluded_share_test": 0.06, "전체 기간": ba, "시험 기간(2022~)": ba,
                         "ai_top10": {"before": st, "after": st, "removed_share": 0.1}, "after_crash_filter": {"before": st, "after": st, "ai_top10_after": st},
                         "crash_model": {"auc": 0.77, "base": 0.07, "top5": {"precision": 0.4, "lift": 5.0, "n": 100}, "top10": {"precision": 0.3, "lift": 4.0},
                                         "deciles": [{"bin": i, "p_crash": 0.02 * i, "mean": 0.0} for i in range(1, 11)]}},
           "gap": {"dist": {"n": 100, "mean": 0.003, "median": 0.0, "p90": 0.02, "p99": 0.06, "share_1": 0.2, "share_3": 0.05, "share_5": 0.02, "share_down1": 0.1},
                   "buckets": [{"label": "+3~+5%", "n": 500, "gap": 0.04, "mean_open": -0.01, "mean_close": 0.01, "p_up_open": 0.4, "p_dn10": 0.2}],
                   "methods": [{"name": "다음날 시가에 전액 (기준)", "coverage": 1.0, "mean": 0.0, "per_signal": 0.0, "std": 0.1, "p_up": 0.4, "p_dn10": 0.2, "q05": -0.2, "entry_gap": 0.003}],
                   "by_signal": [{"label": "박스 돌파", "n": 5000, "gap": 0.0076, "tech": "돌파·신고가"}, {"label": "망치형", "n": 5000, "gap": -0.001, "tech": "캔들"}]},
           "today": {"n_universe": 100, "flagged": 1, "base_danger": 0.07, "rows": [{"code": "005930", "name": "삼성전자", "date": "2026-10-07", "close": 272000.0, "n_rules": 1, "danger": 0.3, "reasons": [rule["name"]]}],
                     "top_danger": [{"code": "000660", "name": "SK하이닉스", "close": 1000000.0, "danger": 0.4, "reasons": []}]},
           "text": ["요약 문장"]}
    (tmp_path / "avoid.json").write_text(json.dumps(rep), encoding="utf-8")
    html = c.get("/avoid").get_data(as_text=True)
    for s in ("요약 문장", "삼성전자", "갭을 줄이는 방법 비교", "지금 피해야 할 종목", "피함"):
        assert s in html
