import json

import numpy as np
import pandas as pd
import pytest

import config
from core import collector, db, signal_scan


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(signal_scan, "SIGNALS_PATH", tmp_path / "signals.json")
    from app import create_app
    app = create_app()
    rng = np.random.default_rng(5)
    idx = pd.date_range("2024-01-01", periods=600, freq="B")
    for i, code in enumerate(("TEST1", "TEST2")):
        close = 10000 * np.cumprod(1 + rng.normal(0.001 * (i + 1), 0.02, len(idx)))
        df = pd.DataFrame({"open": close * (1 + rng.normal(0, 0.005, len(idx))), "high": close * 1.02, "low": close * 0.98, "close": close,
                           "volume": rng.integers(200000, 600000, len(idx))}, index=idx)      # 거래대금 3억↑(거래 가능)
        collector.save_prices(code, df)
    with db.get_conn() as c:
        for i, code in enumerate(("TEST1", "TEST2")):
            c.execute("INSERT INTO symbols(code,name,market) VALUES(?,?,'KOSPI')", (code, f"테스트{i + 1}"))
    return app.test_client()


def test_scan_writes_scored_lists(client):
    rep = signal_scan.run(workers=1, log=lambda m: None)
    assert signal_scan.SIGNALS_PATH.exists() and rep["meta"]["scanned"] == 2 and rep["meta"]["date"]
    assert rep["checks"]["pred"] == signal_scan.CHECKS_PRED and len(rep["checks"]["ai"]) == 10
    for kind in ("pred", "ai"):
        for r in rep[kind]:
            assert len(r["checks"]) == 10 and 0 <= r["score"] <= 1 and abs(r["score"] - sum(r["checks"]) / 10) < 1e-9
            assert set(r) >= {"code", "name", "close", "prob", "halt_p", "avoid", "signals"}
    assert all(s["ago"] <= signal_scan.RECENT for r in rep["pred"] for s in r["signals"])
    html = client.get("/").get_data(as_text=True)
    assert "예측" in html and "AI" in html and "점검 항목" in html
    # 기준일 이력: HIST_START 이후 날짜마다 신호·점검 값이 표에 들어간다
    lo, hi = signal_scan.dates_available()
    assert lo and hi == rep["meta"]["date"] and rep["meta"]["hist_rows"] > 100
    with db.get_conn() as c:
        n = c.execute("SELECT COUNT(DISTINCT date) n FROM daily_checks").fetchone()["n"]
    assert n > 100


def test_for_date_past_has_outcomes(client):
    signal_scan.run(workers=1, log=lambda m: None)
    lo, hi = signal_scan.dates_available()
    rep = signal_scan.for_date("2025-06-01")                    # 일요일 → 그 전 거래일
    assert rep["meta"]["date"] == "2025-05-30" and rep["meta"]["asked"] == "2025-06-01" and rep["meta"]["past"] is True
    assert rep["meta"]["prev"] < rep["meta"]["date"] < rep["meta"]["next"] and rep["outcome"]["all"]["n"] == 2
    assert all(len(r["checks"]) == 10 for k in ("pred", "ai") for r in rep[k])
    assert all(r["r20"] is None or -1 < r["r20"] < 5 for k in ("pred", "ai") for r in rep[k])
    assert signal_scan.for_date("2000-01-01") is None
    latest = signal_scan.for_date(hi)
    assert latest["meta"]["past"] is False and "outcome" not in latest
    html = client.get("/?date=2025-06-01").get_data(as_text=True)
    assert "<b>2025-05-30</b>" in html and "그 뒤 실제 결과" in html and "2025-06-01은 거래일이 아니라" in html
    assert client.get("/?date=2025-6-1").status_code == 400
    assert "/compare?a=TEST1&amp;date=2025-05-30" in html or "/compare?a=TEST2&amp;date=2025-05-30" in html   # 종목을 누르면 그날 차트로
    assert '"focus": "2025-05-30"' in client.get("/compare?a=TEST1&date=2025-05-30").get_data(as_text=True)
    assert '"focus": ""' in client.get("/compare?a=TEST1&date=bad").get_data(as_text=True)


def test_home_recommends_90_percent(client):
    row = {"code": "TEST1", "name": "테스트1", "close": 12345.0, "prob": 0.2, "halt_p": 0.001, "avoid": False, "ret20": 0.1, "ai_top": True,
           "signals": [{"label": "공구리(언덕 돌파)", "ago": 0, "good": True}]}
    rep = {"meta": {"date": "2026-10-08", "rows": 1, "thr10": 0.138, "thr3": 0.16, "mkt_dd250": -0.2, "recommend": 0.9, "recent": 5, "generated": "x"},
           "checks": {"pred": signal_scan.CHECKS_PRED, "ai": signal_scan.CHECKS_AI},
           "pred": [dict(row, checks=[True] * 9 + [False], score=0.9), dict(row, code="TEST2", name="테스트2", checks=[True] * 5 + [False] * 5, score=0.5)],
           "ai": [dict(row, checks=[True] * 10, score=1.0)], "text": ["요약"]}
    signal_scan.SIGNALS_PATH.write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
    html = client.get("/").get_data(as_text=True)
    assert html.count('class="rec') == 2                       # 90% 이상 두 줄(예측 1 · AI 1)만 추천 표시
    assert "그 밖의 종목 1개" in html and "9/10" in html and "10/10" in html and "/compare?a=TEST1" in html


def test_empty_db_does_not_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "e.db")
    monkeypatch.setattr(signal_scan, "SIGNALS_PATH", tmp_path / "keep.json")
    db.init_db()
    signal_scan.SIGNALS_PATH.write_text('{"meta": {"date": "x"}}', encoding="utf-8")
    rep = signal_scan.run(workers=1, log=lambda m: None)
    assert rep["meta"]["scanned"] == 0 and json.loads(signal_scan.SIGNALS_PATH.read_text(encoding="utf-8"))["meta"]["date"] == "x"


def test_high_tier_rule():
    """상승 확률 높은 추천 = AI 상위 2% + 피함 제외 + 시장 필터 (강 = + AI 3% + 시장 20일 −5% 미만)."""
    def rec(code, prob, avoid=0):
        return {"code": code, "close": 1000.0, "prob": prob, "halt": 0.001, "avoid": avoid, "above20": 1, "ma20_60": 1, "volup": 1, "liq_ok": 1,
                "sig0": 0, "sig1": 0, "sig5": 0, "sign10": 0}
    recs = [rec("A", 0.30), rec("B", 0.21), rec("C", 0.30, avoid=1), rec("D", 0.10)]
    names = {c: c for c in "ABCD"}
    r = signal_scan._assemble(recs, "2026-01-02", 0.2, 0.25, 0.2, -0.2, -0.08, names, {})
    assert [(z["code"], z["tier"]) for z in r["high"]] == [("A", "강"), ("B", "상")]        # C 는 피함, D 는 AI 하위
    r = signal_scan._assemble(recs, "2026-01-02", 0.2, 0.25, 0.2, -0.2, -0.02, names, {})   # 시장이 급히 빠지지 않은 날은 '강' 없음
    assert [z["tier"] for z in r["high"]] == ["상", "상"]
    r = signal_scan._assemble(recs, "2026-01-02", 0.2, 0.25, 0.2, -0.05, -0.08, names, {})  # 시장 필터 미통과
    assert r["high"] == []


def test_home_without_scan(client):
    html = client.get("/").get_data(as_text=True)
    assert "signals-scan" in html
