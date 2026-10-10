import json

import numpy as np
import pandas as pd

import config
from core import quietvol_study as qv


def test_work_classifies_quiet_up_down(tmp_path, monkeypatch):
    from core import ai, collector, db, surge_study as sg
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(sg, "LIQ_MIN", 0.0)
    db.init_db()
    n = 800
    rng = np.random.default_rng(2)
    c = 100 * np.cumprod(1 + rng.normal(0.0, 0.003, n))
    v = np.full(n, 10000.0)
    v[300] = 50000.0; c[300] = c[299] * 1.005                     # 조용한 대량 거래(+0.5%)
    v[400] = 50000.0; c[400] = c[399] * 1.08; c[401:] = c[401:] * 1.08    # 양봉 급증
    v[500] = 50000.0; c[500] = c[499] * 0.95; c[501:] = c[501:] * 0.95    # 음봉 급증
    c[320:] = c[320:] * 1.3                                      # 조용한 대량 거래 뒤 20일째 +30% 점프
    o = np.r_[c[0], c[:-1]]
    df = pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.002, "low": np.minimum(o, c) * 0.998, "close": c, "volume": v}, index=pd.date_range("2020-01-01", periods=n, freq="B"))
    collector.save_prices("Q1", df)
    ai.MKT_DF = None
    out = qv._work("Q1")
    d = df.index
    assert list(out["quiet"]["date"]) == [d[300].strftime("%Y-%m-%d")] and list(out["up"]["date"]) == [d[400].strftime("%Y-%m-%d")] and list(out["down"]["date"]) == [d[500].strftime("%Y-%m-%d")]
    q = out["quiet"]
    assert 18 <= q["d20"][0] <= 20 and q["again5"][0] == 0 and abs(q["next_ret"][0]) < 0.05 and q["fwd20"][0] == 1   # 20일째 +30% 점프 = 급등일
    s = qv._summary({k: np.repeat(v_, 60, axis=0) if hasattr(v_, "shape") and v_.ndim else v_ for k, v_ in q.items()})
    assert s and s["n"] == 60 and s["hit"]["20"]["20"] == 1.0


def test_quietvol_page(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(qv, "QV_PATH", tmp_path / "q.json")
    from app import create_app
    c = create_app().test_client()
    assert "아직 분석 결과가 없습니다" in c.get("/quietvol").get_data(as_text=True)
    hit = {str(x): {str(h): 0.2 for h in qv.HORIZ} for x in qv.THRESH}
    days = {str(x): {"share": 0.5, "median": 15.0, "q25": 5.0, "q75": 40.0} for x in qv.THRESH}
    g = lambda k, nm: {"key": k, "name": nm, "n": 1000, "hit": hit, "days": days, "r20": 0.01, "r40": 0.01, "r60": 0.02, "r120": 0.03, "peak_median": 30.0, "jump20": 0.05, "jump60": 0.1, "next_ret": 0.001, "next_up": 0.5, "again5": 0.3, "dn10_first": 0.6, "up20_before_dn10": 0.3}  # noqa: E731
    rep = {"meta": {"generated": "x", "min_ratio": 3.0, "flat": 0.03, "look": 120, "events": 1000, "stocks": 100, "date_min": "2000-01-01", "date_max": "2026-10-10", "liq_min": 3e8},
           "groups": [g("quiet", "조용한 대량 거래"), g("up", "양봉"), g("down", "음봉"), g("bs", "아무 날")],
           "time_hist": [{"x": 20, "hit_share": 0.5, "bins": [{"label": "1~3일", "share_all": 0.05}]}],
           "curve": {k: [{"day": 5, "p": 0.1}, {"day": 20, "p": 0.2}] for k in ("quiet", "up", "down", "bs")},
           "breakdown": [{"group": "거래량 배수", "rows": [{"name": "3~5배", "n": 500, "p20_in20": 0.2, "p20_in60": 0.4, "p50_in120": 0.2, "days20": 15.0, "dn10_first": 0.6, "r20": 0.01, "r60": 0.02, "jump20": 0.05, "next_ret": 0.001}]}],
           "recent": [{"code": "005930", "name": "삼성전자", "date": "2026-10-08", "close": 100000.0, "volratio": 4.0, "ret1": 0.01, "nh250": -0.1, "dist224": 0.05, "vdry10": 0.4, "n_j250": 0.0, "group": "도지 · 중간 · 3~10배", "n": 500, "p20_in20": 0.25, "p20_in60": 0.45, "days20": 14.0, "dn10_first": 0.6, "r60": 0.02, "jump20": 0.05}],
           "text": ["요약 문장"]}
    (tmp_path / "q.json").write_text(json.dumps(rep), encoding="utf-8")
    html = c.get("/quietvol").get_data(as_text=True)
    for k in ("요약 문장", "삼성전자", "얼마 뒤에 올랐나", "어떤 조용한 대량 거래가 올랐나"):
        assert k in html
