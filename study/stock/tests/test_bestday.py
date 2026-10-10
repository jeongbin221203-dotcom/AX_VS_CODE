import json

import numpy as np
import pandas as pd

import config
from core import ai, bestday_study as bd


def test_work_computes_best5_and_oracle(tmp_path, monkeypatch):
    from core import collector, db
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    n = 600
    rng = np.random.default_rng(1)
    c = 100 * np.cumprod(1 + rng.normal(0.0, 0.01, n))
    o = np.r_[c[0], c[:-1]]
    df = pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.01, "low": np.minimum(o, c) * 0.99, "close": c, "volume": rng.uniform(5000, 15000, n)}, index=pd.date_range("2020-01-01", periods=n, freq="B"))
    collector.save_prices("B1", df)
    ai.MKT_DF = None
    d = df.index.strftime("%Y-%m-%d")
    bd._DATES = {"B1": [d[400], d[450]]}
    out = bd._work("B1")
    assert out is not None and list(out["date"]) == [d[400], d[450]]
    for t in (400, 450):
        i = list(out["date"]).index(d[t])
        mn = o[t + 1:t + 6].min()
        assert out["best5"][i] == float(o[t + 1] <= mn * 1.01) and abs(out["oracle_r20"][i] - (c[t + 21] / mn - 1)) < 1e-5 and 1 <= out["best_day"][i] <= 5
    assert set(bd.PRE_KEYS + bd.PAT_KEYS) <= set(out)


def test_bestday_page(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(bd, "BEST_PATH", tmp_path / "b.json")
    from app import create_app
    c = create_app().test_client()
    assert "아직 분석 결과가 없습니다" in c.get("/bestday").get_data(as_text=True)
    s = {"n": 500, "mean": 0.02, "p_up": 0.55, "p_big_up": 0.2, "p_dn10": 0.15, "q05": -0.2, "median": 0.01, "excess": 0.01}
    top = {"hit": 0.6, **s}
    rep = {"meta": {"generated": "x", "rows": 1000, "features": 128, "split": {"train_end": "2021-12-31"}, "cost": 0.003},
           "compare": [{"target": "시장 대비 플러스 (20일)", "features": "기존 AI (76개 특징)", "auc": 0.54, "base": 0.5, "n_te": 1000, "top1": top, "top5": top, "top10": top},
                       {"target": "시장 대비 플러스 (20일)", "features": "통합 (모든 신호·패턴·전조·위험)", "auc": 0.55, "base": 0.5, "n_te": 1000, "top1": top, "top5": top, "top10": top}],
           "importance": [{"name": "atrp", "label": "변동성", "gain": 0.01}],
           "stages": [{"name": "통합 점수 상위 10%", "tr": s, "te": s, "per_month_te": 100.0}, {"name": "+ 피함 제외", "tr": s, "te": s, "per_month_te": 80.0}],
           "timing": [{"name": "오늘 (다음날 시가)", "coverage": 1.0, "tr": s, "te": s, "per_signal_te": 0.02}, {"name": "오라클: 앞 5일 중 최저 시가에 샀다면 (실현 불가 상한)", "coverage": 1.0, "tr": s, "te": s, "per_signal_te": 0.04}],
           "best_day_dist": [{"day": d, "share": 0.2} for d in range(1, 6)], "best5_rate": 0.35,
           "timing_model": {"auc": 0.58, "base_best5": 0.35, "hi_best5": 0.5, "lo_best5": 0.3, "hi_today": s, "hi_x5": s, "lo_today": s, "lo_x5": s, "rules": [{"name": "규칙", "tr": s, "te": s}]},
           "walk": {"rows": [{"year": 2024, "final": s, "base": s}], "total": {"final": s, "base": s}, "years_better": 1, "years": 1},
           "today": {"date": "2026-10-08", "n": 1000, "mkt_dd250": -0.2, "mkt_under": True, "thr10": 0.55, "rows": [{"code": "005930", "name": "삼성전자", "close": 100000.0, "score": 0.6, "timing": 0.5, "buy_today": True, "avoid": False, "halt_p": 0.01, "ret20": 0.05, "nh250": -0.1, "atrp": 0.03, "dist20": 0.01, "signals": ["52주 신고가"], "patterns": ["매집봉 20일 안"]}]},
           "text": ["요약 문장"]}
    (tmp_path / "b.json").write_text(json.dumps(rep), encoding="utf-8")
    html = c.get("/bestday").get_data(as_text=True)
    for k in ("요약 문장", "삼성전자", "매수일 판정", "통합 점수 vs 기존 AI", "걸어가며 검증"):
        assert k in html
