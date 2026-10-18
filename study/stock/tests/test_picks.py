import json

import numpy as np
import pandas as pd
import pytest

import config
from core import picks


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setenv("STOCK_SCHEDULER", "0")
    from app import create_app
    return create_app().test_client()


def test_assign_group_rank_and_junk_rules():
    n = 400
    D = pd.DataFrame({"date": "2026-10-16", "mcap_est": np.linspace(n, 1, n) * 1e10, "close": 5000.0, "liqv": 50e8})
    D.loc[D.index[-1], ["mcap_est"]] = 500e8                         # 시총 가장 작음 → 잡주(시총 1,000억 미만)
    D.loc[D.index[350], ["close"]] = 800.0                            # 301위 밖 + 주가 1,000원 미만 → 잡주
    D.loc[D.index[360], ["liqv"]] = 4e8                               # 301위 밖 + 거래대금 5억 미만 → 잡주
    g = picks.assign_group(D)
    assert g.iloc[0] == "대형주" and g.iloc[99] == "대형주" and g.iloc[100] == "중형주" and g.iloc[299] == "중형주"
    assert g.iloc[300] == "소형주" and g.iloc[350] == "잡주" and g.iloc[360] == "잡주" and g.iloc[-1] == "잡주"
    assert g.iloc[1] != "잡주" and (g.iloc[:300] != "잡주").all()      # 대형·중형은 잡주가 될 수 없다


class _Fake:
    """점수가 첫 번째 특징 값과 같은 가짜 모델."""
    def predict(self, X):
        return X.iloc[:, 0].to_numpy()

    def predict_proba(self, X):
        v = X.iloc[:, 0].to_numpy()
        return np.column_stack([1 - v, v])


def test_score_today_ranks_within_each_group():
    n = 40
    f = np.linspace(0, 1, n)
    D = pd.DataFrame({"date": "2026-10-16", "code": [f"A{i:02d}" for i in range(n)], "f1": f, "f2": f[::-1].copy(),
                      "grp": ["대형주"] * 10 + ["중형주"] * 10 + ["소형주"] * 10 + ["잡주"] * 10})
    four = {k: _Fake() for k in ("rank", "up", "reg", "r60")}
    obj = {"feats": ["f1", "f2"], "cols": ["f1", "f2"], "global": four, "groups": {g: four for g in picks.GROUPS}}
    S = picks.score_today(D, obj)
    assert len(S) == n and set(S["grp"]) == set(picks.GROUPS)
    for g, sub in S.groupby("grp"):
        assert sub["grp_n"].iloc[0] == 10 and sorted(sub["grp_rank"]) == list(range(1, 11))   # 순위는 그룹 안에서
        assert sub.sort_values("grp_rank")["score"].is_monotonic_decreasing
    junk = S[S["grp"] == "잡주"].sort_values("grp_rank")
    assert junk["code"].iloc[0] == "A39" and junk["code"].iloc[-1] == "A30"          # 특징 값이 클수록 점수 높음


def test_ai_chart_score_and_group_badge(tmp_path, monkeypatch):
    import config
    from core import db
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    with db.get_conn() as c:
        c.execute("INSERT INTO symbols(code,name,market) VALUES('T1','테스트','KOSDAQ')")
    rng = np.random.default_rng(1)
    idx = pd.date_range("2023-01-02", periods=700, freq="B")
    close = 5000 * np.cumprod(1 + rng.normal(0.0004, 0.02, len(idx)))
    df = pd.DataFrame({"open": close, "high": close * 1.02, "low": close * 0.98, "close": close, "volume": rng.integers(1e5, 3e5, len(idx))}, index=idx)
    feats = picks.feature_columns(picks.stock_frame(df, 1.0).columns)

    class Const:
        def __init__(self, v):
            self.v = v

        def predict(self, X):
            return np.full(len(X), self.v)

        def predict_proba(self, X):
            return np.column_stack([np.full(len(X), 1 - self.v), np.full(len(X), self.v)])
    chart = {"rank": Const(0.95), "up": Const(0.95), "reg": Const(0.95), "r60": Const(0.95)}
    q = {k: np.linspace(0, 1, 101) for k in chart}                    # 출력 0.95 → 95점
    monkeypatch.setattr(picks, "load_models", lambda: {"chart": chart, "chart_q": q, "chart_cols": feats, "through": "2026-09-02"})
    monkeypatch.setattr(picks, "load_wf", lambda: None)               # 걸어가며 검증 모델 파일(실제 data)에 기대지 않게
    monkeypatch.setattr(picks, "load_today", lambda: {"date": "2026-10-16", "scores": {"T1": ["잡주", 0.97, 4, 300]}})
    out = picks.ai_chart(df, "T1", "D", 700)
    assert out["kind"] == "ai" and out["label"] == "AI 점수" and out["thr"] == picks.AI_TOP
    assert abs(out["latest"]["prob"] - 0.96) < 0.011 and out["latest"]["top"]       # 점수 약 95~96점 → 상위 10% 경계(90점) 위
    assert out["group"] == {"name": "잡주", "rank": 4, "n": 300, "date": "2026-10-16"}
    assert len(out["prob"]) >= 250 and "잡주" in out["summary"] and "2026-09-02" in out["note"]
    # 점수가 처음 유효해진 날 95점을 넘으니 강한 매수 신호 1건 → 다음 날 시가 매수 · 20거래일째 종가 매도
    buys = [m for m in out["markers"] if m["side"] == "buy"]
    sells = [m for m in out["markers"] if m["side"] == "sell"]
    assert buys and sells and buys[0]["label"] == "매수" and buys[0]["tier"] == "강" and sells[0]["label"].startswith("매도 ")
    t = out["trades"][0]
    assert t["buy"] < t["sell"] and abs(t["ret"] - (t["sell_px"] / t["buy_px"] - 1 - 0.003)) < 1e-3 and t["seen"] is True    # 걸어가며 검증 모델이 없으니 학습 구간 포함 표시
    assert out["accuracy"]["all"]["n"] >= 1 and out["accuracy"]["oos"] is False and "학습에 쓴" in out["summary"]


def test_picks_page_with_and_without_data(client, monkeypatch, tmp_path):
    monkeypatch.setattr(picks, "TODAY_PATH", tmp_path / "none.json")
    html = client.get("/picks").get_data(as_text=True)
    assert "picks-train" in html and "잡주 후보" in html                  # 결과가 없으면 만드는 방법 안내
    items = [{"rank": i, "code": f"00000{i}", "name": f"종목{i}", "close": 1234.0, "value_eok": 4.5, "mcap_eok": 700, "score": 0.9 - i / 100,
              "ret20": 0.05, "main": i <= 5, "kosdaq": True} for i in range(1, 11)]
    rep = {"date": "2026-10-16", "generated": "x", "model_through": "2026-09-02", "universe": 1500, "junk": 480, "picks": items,
           "groups": {g: {"n": 100, "picks": items} for g in picks.GROUPS}}
    p = tmp_path / "t.json"
    p.write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(picks, "TODAY_PATH", p)
    html = client.get("/picks").get_data(as_text=True)
    assert "종목1" in html and "종목10" in html and html.count("★") >= 20 and "2026-10-16" in html      # 그룹 4개 × 상위 5개
    assert all(g in html for g in picks.GROUPS)
    assert "/jb?a=000001" in html                                        # 이름을 누르면 JB차트로
