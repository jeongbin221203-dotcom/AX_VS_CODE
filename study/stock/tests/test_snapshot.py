import json

import pytest

import snapshot_app
from core import publish


def _rep():
    row = {"code": "005930", "name": "삼성전자", "close": 70000.0, "prob": 0.2, "halt_p": 0.001, "avoid": False, "ai_top": True, "r5": None, "r20": None,
           "est": {"p5": 0.51, "p20": 0.49, "m5": 0.01, "m20": 0.04, "n": 100, "band": "상위 1~2%"}, "checks": [True] * 10, "score": 1.0,
           "signals": [{"label": "공구리(언덕 돌파)", "ago": 0, "good": True}], "tier": "상"}
    return {"meta": {"date": "2026-10-08", "rows": 1, "thr10": 0.138, "thr3": 0.16, "thr2": 0.2, "mkt_dd250": -0.2, "mkt_r20": 0.02, "recommend": 0.9, "recent": 5,
                     "generated": "2026-10-10T18:00:00"},
            "checks": {"pred": ["a"] * 10, "ai": ["b"] * 10}, "pred": [row], "ai": [row], "high": [row], "high_note": "n", "strong_mkt": {"last": "2026-08-03", "share": 0.13}, "text": ["요약"]}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    f = tmp_path / "signals.json"
    f.write_text(json.dumps(_rep(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(snapshot_app, "SNAPSHOT", f)
    monkeypatch.setattr(snapshot_app, "CHARTS", tmp_path / "no_charts")      # 실제 차트 폴더와 분리
    monkeypatch.delenv("STOCK_SNAPSHOT_PASSWORD", raising=False)
    return snapshot_app.create_app().test_client()


def test_snapshot_page_has_no_links_or_forms(client):
    html = client.get("/").get_data(as_text=True)
    assert "삼성전자" in html and "읽기 전용 스냅샷" in html and "상 — 대기 후보" in html
    assert "/compare" not in html and "/chart/005930" not in html     # 차트 파일이 없는 종목은 링크 없음
    assert "2개 이상 겹침" in html and client.get("/?min=2").status_code == 200
    assert "신호 2개 이상 겹친 종목만" in client.get("/?min=2").get_data(as_text=True)
    assert client.get("/healthz").get_data(as_text=True) == "ok"
    assert client.get("/nope").status_code == 404


def test_snapshot_dates(tmp_path, monkeypatch):
    days = tmp_path / "days"
    days.mkdir()
    for d, name in (("2026-10-06", "화요일종목"), ("2026-10-08", "목요일종목")):
        rep = _rep()
        rep["meta"]["date"] = d
        rep["pred"][0]["name"] = name
        (days / f"{d}.json").write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(snapshot_app, "DAYS", days)
    monkeypatch.setattr(snapshot_app, "SNAPSHOT", days / "2026-10-08.json")
    c = snapshot_app.create_app().test_client()
    html = c.get("/?date=2026-10-07").get_data(as_text=True)               # 보관일이 아니면 그 전 보관일
    assert "화요일종목" in html and "2026-10-08 ▶" in html and "2026-10-07" in html
    assert "목요일종목" in c.get("/?date=2026-10-08").get_data(as_text=True)
    assert 'type="date"' in c.get("/").get_data(as_text=True) and 'min="2026-10-06"' in c.get("/").get_data(as_text=True)
    assert "이전의 이력이 없습니다" in c.get("/?date=2020-01-01").get_data(as_text=True)
    assert c.get("/?date=abc").status_code == 400


def test_snapshot_password(client, monkeypatch):
    monkeypatch.setenv("STOCK_SNAPSHOT_PASSWORD", "pw123")
    assert client.get("/").status_code == 401 and client.get("/healthz").status_code == 200
    import base64
    ok = {"Authorization": "Basic " + base64.b64encode(b"me:pw123").decode()}
    bad = {"Authorization": "Basic " + base64.b64encode(b"me:wrong").decode()}
    assert client.get("/", headers=ok).status_code == 200 and client.get("/", headers=bad).status_code == 401


def test_snapshot_without_file(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot_app, "SNAPSHOT", tmp_path / "none.json")
    assert "아직 훑어 둔 결과가 없습니다" in snapshot_app.create_app().test_client().get("/").get_data(as_text=True)


def test_publish_refresh_copies_only_when_changed(tmp_path, monkeypatch):
    src, dst = tmp_path / "signals.json", tmp_path / "snap" / "signals.json"
    monkeypatch.setattr(publish, "SRC", src)
    monkeypatch.setattr(publish, "DST", dst)
    monkeypatch.setattr(publish, "DAYS", tmp_path / "snap" / "days")
    src.write_text(json.dumps(_rep()), encoding="utf-8")
    assert publish.refresh() is True and dst.exists() and (tmp_path / "snap" / "days" / "2026-10-08.json").exists()
    assert publish.refresh() is False                                   # 같은 내용이면 다시 올리지 않음
    src.write_text(json.dumps({"meta": {"date": None}}), encoding="utf-8")
    with pytest.raises(RuntimeError):
        publish.refresh()                                               # 빈 결과로 덮어쓰지 않음


def test_trim_keeps_only_recommended(tmp_path, monkeypatch):
    rep = _rep()
    rep["pred"].append(dict(rep["pred"][0], name="낮은점수", score=0.5))
    rep["ai"].append(dict(rep["ai"][0], name="낮은점수AI", score=0.4))
    t = publish.trim(rep)
    assert [r["name"] for r in t["pred"]] == ["삼성전자"] and t["meta"]["n_pred"] == 2 and t["meta"]["n_ai"] == 2 and t["meta"]["trimmed"]
    assert publish.trim(t) is t                                           # 두 번 압축해도 그대로
    days = tmp_path / "days"
    days.mkdir()
    monkeypatch.setattr(publish, "DAYS", days)
    monkeypatch.setattr(publish, "FULL_DAYS", 1)
    for d in ("2026-10-06", "2026-10-07", "2026-10-08"):
        r = json.loads(json.dumps(_rep()))
        r["meta"]["date"] = d
        r["pred"].append(dict(r["pred"][0], name="낮은점수", score=0.5))
        (days / f"{d}.json").write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
    assert publish.compact_old() is True
    assert json.loads((days / "2026-10-06.json").read_text(encoding="utf-8"))["meta"]["trimmed"] is True
    assert "trimmed" not in json.loads((days / "2026-10-08.json").read_text(encoding="utf-8"))["meta"]   # 최근 날은 전체 유지
    assert publish.compact_old() is False


def _chart_fixture(tmp_path, monkeypatch):
    charts = tmp_path / "charts"
    charts.mkdir()
    n = 30
    cal = [f"2026-09-{d:02d}" for d in range(1, 31)]                       # 9/1(화)~9/30
    closes = [100 + i for i in range(n)]
    obj = {"v": 2, "n": "삼성전자", "last": cal[-1], "d": list(range(n)), "o": closes, "h": [x + 2 for x in closes], "l": [x - 2 for x in closes], "c": closes,
           "vol": [1000] * n, "p": [-1] * 5 + [150] * 25, "g": [[28, 1 << 8], [29, (1 << 11) | (1 << 12)]]}
    (charts / "005930.json").write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    meta = {"cal": cal, "labels": ["l%d" % i for i in range(24)], "dante": [8, 11, 12], "good": ["l12"], "why": {"l8": "공구리 설명"},
            "tech": {"l8": {"mean": 0.01, "p_up": 0.44, "years": "8/15", "rule": "규칙A", "good": False, "ai": False},
                     "l12": {"mean": 0.02, "p_up": 0.43, "years": "10/15", "rule": "규칙B", "good": True, "ai": False},
                     "AI": {"mean": 0.027, "p_up": 0.47, "years": "10/14", "rule": "규칙C", "good": True, "ai": True}},
            "thr": {"2026-09-30": 0.138}, "thr_now": 0.138}
    (tmp_path / "chartmeta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    f = tmp_path / "signals.json"
    f.write_text(json.dumps(_rep(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(snapshot_app, "CHARTS", charts)
    monkeypatch.setattr(snapshot_app, "META", tmp_path / "chartmeta.json")
    monkeypatch.setattr(snapshot_app, "SNAPSHOT", f)
    monkeypatch.delenv("STOCK_SNAPSHOT_PASSWORD", raising=False)
    return snapshot_app.create_app().test_client()


def test_snapshot_uses_local_compare_screen(tmp_path, monkeypatch):
    c = _chart_fixture(tmp_path, monkeypatch)
    assert "/compare?a=005930&amp;date=2026-10-08" in c.get("/").get_data(as_text=True)      # 목록 종목 → PC 와 같은 차트 비교 화면
    page = c.get("/compare?a=005930&date=2026-09-30").get_data(as_text=True)
    assert "js/compare.js" in page and "예측 (단테 기법)" in page and "AI (상승 확률)" in page and '"focus": "2026-09-30"' in page
    assert c.get("/chart/005930?date=2026-09-30", follow_redirects=False).status_code == 302
    assert c.get("/compare?a=NOPE").status_code == 404 and c.get("/compare?a=005930&date=abc").status_code == 400
    j = c.get("/api/chart/005930?tf=D&bars=5000").get_json()
    assert len(j["candles"]) == 30 and j["last"]["close"] == 129 and len(j["ma"]["5"]) == 26 and j["volume"][0]["up"] is True
    w = c.get("/api/chart/005930?tf=W&bars=5000").get_json()                     # 주봉: 9/1~9/4 ... 한 주에 한 봉
    assert 5 <= len(w["candles"]) <= 6 and w["candles"][-1]["time"] == "2026-09-30"
    assert c.get("/api/chart/NOPE").status_code == 404 and c.get("/api/chart/005930?tf=X").status_code == 400


def test_snapshot_predict_and_search(tmp_path, monkeypatch):
    c = _chart_fixture(tmp_path, monkeypatch)
    d = c.get("/api/predict/005930?kind=dante&tf=D&bars=5000").get_json()
    labs = {(m["date"], m["label"]): m for m in d["markers"]}
    assert ("2026-09-29", "l8") in labs and labs[("2026-09-30", "l12")]["good"] is True and "처음 보는 해 평균 +2.00%" in labs[("2026-09-30", "l12")]["stat"]
    assert [r["label"] for r in d["recent"]] == ["l11", "l12", "l8"] or {r["label"] for r in d["recent"]} == {"l8", "l11", "l12"}
    assert "규칙(l12): 규칙B" in d["summary"]
    dw = c.get("/api/predict/005930?kind=dante&tf=W&bars=5000").get_json()
    assert all(m["date"] == "2026-09-30" for m in dw["markers"] if m["label"] in ("l8", "l11", "l12"))        # 주봉에서는 그 주의 마지막 봉으로
    a = c.get("/api/predict/005930?kind=ai&tf=D&bars=5000").get_json()
    assert a["thr"] == 0.138 and a["prob"][0]["time"] == "2026-09-06" and a["latest"]["top"] is True and a["markers"][0]["date"] == "2026-09-06"
    assert "오늘 AI 확률 15.0%" in a["summary"] and "규칙C" in a["summary"]
    assert c.get("/api/predict/005930?kind=x").status_code == 400 and c.get("/api/predict/NOPE?kind=ai").status_code == 404
    assert [r["code"] for r in c.get("/api/search?q=삼성").get_json()] == ["005930"] and c.get("/api/search?q=").get_json() == []
