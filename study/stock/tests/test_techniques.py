import numpy as np
import pandas as pd
import pytest

import config
from core import collector, db, techniques


def make(n=400, seed=1):
    rng = np.random.default_rng(seed)
    c = 100 * np.cumprod(1 + rng.normal(0.0008, 0.015, n))
    o = c * (1 + rng.normal(0, 0.004, n))
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.007, "low": np.minimum(o, c) * 0.993,
                         "close": c, "volume": rng.integers(800, 1200, n).astype(float)},
                        index=pd.date_range("2024-01-01", periods=n, freq="B"))


@pytest.mark.parametrize("name", [n for n in techniques.ORDER if techniques.REGISTRY[n]["status"] == "ready"])
def test_every_ready_technique_runs(name):
    r = techniques.apply(name, make())
    assert set(r) >= {"markers", "lines", "levels", "summary", "name"}
    for m in r["markers"]:
        assert m["side"] in ("buy", "sell", "info") and m["why"]
    for ln in r["lines"]:
        assert ln["data"] and ln["color"]


@pytest.mark.parametrize("name", ["재료·테마", "초보·기초"])
def test_pending_and_na_not_applied(name):
    assert techniques.apply(name, make()) is None


def test_short_history_is_safe():
    assert "부족" in techniques.apply("이평선", make(50))["summary"]


@pytest.mark.parametrize("name", ["이평선", "눌림목", "돌파·신고가", "거래량", "단타", "캔들", "256기법", "이평 때리기", "밥그릇", "공구리", "지지·저항", "매집봉", "역매공파", "세력선"])
def test_no_lookahead(name):
    """마지막 60봉을 잘라 내도 그 이전 마커가 달라지면 안 된다(미래 정보 사용 금지)."""
    df = make(500, seed=3)
    full = {(m["date"], m["label"]) for m in techniques.apply(name, df, 5000)["markers"]}
    part = {(m["date"], m["label"]) for m in techniques.apply(name, df.iloc[:-60], 5000)["markers"]}
    assert part <= full


def test_volume_surge_marker():
    df = make()
    df.iloc[-1, df.columns.get_loc("volume")] = 9000
    df.iloc[-1, df.columns.get_loc("close")] = df["open"].iloc[-1] * 1.02
    labels = [m["label"] for m in techniques.latest_signal("거래량", df)]
    assert "거래량↑양봉" in labels


def test_risk_reward_levels_are_ordered():
    lv = techniques.apply("손익비·매매원칙", make())["levels"]
    prices = [l["price"] for l in lv]
    assert prices[1] < prices[0] < prices[2] < prices[3]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    from core import yt
    monkeypatch.setattr(yt, "DB_PATH", tmp_path / "yt.db")
    from app import create_app
    app = create_app()
    collector.save_prices("T1", make(400))
    with db.get_conn() as c:
        c.execute("INSERT INTO symbols(code,name,marcap) VALUES('T1','테스트',1)")
    return app.test_client()


def test_technique_api(client):
    j = client.get("/api/technique/T1?name=이평선").get_json()
    assert j["lines"] and j["summary"]
    assert client.get("/api/technique/T1?name=재료·테마").status_code == 409
    assert client.get("/api/technique/T1?name=없는기법").status_code == 404
    assert client.get("/api/technique/NOPE?name=이평선").status_code == 404
    assert client.get("/api/technique/T1?name=이평선&tf=X").status_code == 400


def test_chart_page_prechecks_defaults_and_honours_query(client):
    html = client.get("/chart/T1").get_data(as_text=True)
    assert 'class="btn tech on" data-tech="이평선"' in html and 'data-tech="캔들"' in html
    assert 'class="btn tech on" data-tech="캔들"' not in html
    html = client.get("/chart/T1?tech=캔들").get_data(as_text=True)
    assert 'class="btn tech on" data-tech="캔들"' in html and 'class="btn tech on" data-tech="이평선"' not in html


def test_technique_pages(client):
    for n in ("눌림목", "역매공파", "매집봉", "세력선", "초보·기초"):
        assert client.get(f"/technique/{n}").status_code == 200
    assert client.get("/technique/없음").status_code == 404
    assert client.get("/technique/이평선?scan=1").status_code == 200


def bowl_series():
    """급락 → 224일선 아래 횡보 → 반등(밥그릇 모양). 6개 구간을 이어 붙인다."""
    parts = [np.linspace(100, 200, 260),      # 상승(224일선이 높아짐)
             np.linspace(200, 90, 60),        # 급락(저평가)
             np.full(120, 90.0) + np.sin(np.arange(120) / 5) * 2,   # 4개월+ 횡보(매집)
             np.linspace(90, 150, 80),        # 반등(224일선·112일선 돌파)
             np.linspace(150, 140, 15),       # 눌림
             np.linspace(140, 170, 40)]
    c = np.concatenate(parts)
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.005, "low": np.minimum(o, c) * 0.995, "close": c,
                         "volume": np.full(len(c), 1000.0)}, index=pd.date_range("2022-01-03", periods=len(c), freq="B"))


def test_256_marks_rebound_after_reverse_alignment():
    labels = {m["label"] for m in techniques.apply("256기법", bowl_series(), 2000)["markers"]}
    assert "256 자리(단기)" in labels and "256 완성(단기)" in labels


def test_256_not_marked_in_pure_uptrend():
    up = make(400)
    up["close"] = np.linspace(100, 300, 400)
    up["open"], up["high"], up["low"] = up["close"] * 0.999, up["close"] * 1.002, up["close"] * 0.997
    assert not [m for m in techniques.apply("256기법", up, 2000)["markers"] if "256" in m["label"]]


def test_ma_strike_needs_prior_spread():
    labels = {m["label"] for m in techniques.apply("이평 때리기", bowl_series(), 2000)["markers"]}
    assert "이평 때리기(112)" in labels
    flat = make(500)
    flat["close"] = 100.0 + np.sin(np.arange(500) / 20)
    assert not [m for m in techniques.apply("이평 때리기", flat, 2000)["markers"] if m["label"].startswith("이평 때리기")]


def test_rice_bowl_breakout_after_long_base():
    labels = {m["label"] for m in techniques.apply("밥그릇", bowl_series(), 2000)["markers"]}
    assert "밥그릇 돌파" in labels


def test_concrete_breakout_and_failure():
    c = np.r_[np.full(80, 100.0), np.linspace(100, 120, 20), np.linspace(120, 100, 15), np.linspace(100, 119, 20), [121.0], np.linspace(121, 112, 6)]
    o = np.r_[c[0], c[:-1]]
    df = pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.002, "low": np.minimum(o, c) * 0.998, "close": c,
                       "volume": np.full(len(c), 1000.0)}, index=pd.date_range("2024-01-01", periods=len(c), freq="B"))
    labels = [m["label"] for m in techniques.apply("공구리", df, 500)["markers"]]
    assert "공구리(언덕 돌파)" in labels and "공구리 실패" in labels


def test_trendline_follows_last_two_pivots():
    df = bowl_series()
    r = techniques.apply("지지·저항", df, 500)
    assert {l["name"] for l in r["lines"]} <= {"고점 추세선", "저점 추세선"} and r["lines"]
    for l in r["lines"]:
        assert len(l["data"]) == 2 and l["data"][0]["time"] < l["data"][1]["time"]


def test_risk_reward_needed_win_rate_text():
    r = techniques.apply("손익비·매매원칙", make())
    assert "3:1" in r["levels"][2]["title"] and "5:1" in r["levels"][3]["title"]


def test_chart_payload_has_both_ma_sets(client):
    j = client.get("/api/chart/T1?bars=300").get_json()
    for n in ("5", "20", "60", "112", "224", "14", "28", "56", "448"):
        assert n in j["ma"], n
    assert j["ma"]["14"] and j["ma"]["224"]


def test_ma_preset_param(client):
    for v, on in (("a", "a"), ("b", "b"), ("ab", "ab"), ("none", "none"), ("zzz", "a")):
        html = client.get(f"/chart/T1?ma={v}").get_data(as_text=True)
        assert f'class="btn on" data-set="{on}"' in html


def test_compare_page(client):
    html = client.get("/compare?a=T1").get_data(as_text=True)
    assert 'data-side="a"' in html and 'data-side="b"' in html
    assert '<option value="b" selected>' in html          # 같은 종목이면 오른쪽은 14·28·56 세트
    html = client.get("/compare?a=T1&b=T1&sa=none&sb=a&tb=W").get_data(as_text=True)
    assert '<option value="W" selected>' in html and '<option value="none" selected>' in html
    assert client.get("/compare?a=bad;code").status_code == 400
    assert client.get("/compare?a=T1&b=%3Cx%3E").status_code == 400
    assert "차트 비교" in client.get("/").get_data(as_text=True)


def acc_series():
    """224일선 아래 바닥을 다진 뒤 거래량이 터진 장대양봉(매집봉)이 나오고, 그 고가를 뚫는 시리즈."""
    c = np.r_[np.linspace(200, 100, 120), np.full(150, 100.0) + np.sin(np.arange(150) / 6) * 1.5]
    c = np.r_[c, [112.0], np.full(8, 108.0), np.linspace(108, 125, 12)]
    o = np.r_[c[0], c[:-1]]
    o[270] = 100.0                                 # 매집봉: 시가 100 → 종가 112 (+12%)
    v = np.full(len(c), 1000.0)
    v[270] = 6000.0
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.004, "low": np.minimum(o, c) * 0.996, "close": c, "volume": v},
                        index=pd.date_range("2022-01-03", periods=len(c), freq="B"))


def test_accumulation_candle_found_at_base_and_breakout_after():
    r = techniques.apply("매집봉", acc_series(), 2000)
    labels = [m["label"] for m in r["markers"]]
    assert any(l.startswith("매집봉(") for l in labels)
    assert "매집봉 돌파" in labels
    brk = next(m for m in r["markers"] if m["label"] == "매집봉 돌파")
    assert brk["ctx"]["acc_low"] < brk["ctx"]["acc_high"]


def test_accumulation_ignored_while_still_falling():
    """내려가는 도중(1번 자리)의 장대양봉은 매집봉으로 치지 않는다."""
    c = np.linspace(200, 80, 300)
    o = np.r_[c[0], c[:-1]]
    o[250] = c[250] / 1.10
    v = np.full(300, 1000.0)
    v[250] = 9000.0
    df = pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.004, "low": np.minimum(o, c) * 0.996, "close": c, "volume": v},
                      index=pd.date_range("2022-01-03", periods=300, freq="B"))
    acc, _ = techniques.acc_candles(df)
    assert not acc[250]


def test_accumulation_needs_volume():
    df = acc_series()
    df["volume"] = 1000.0
    acc, _ = techniques.acc_candles(df)
    assert not acc.any()


def test_yeok_mae_gong_pa_requires_accumulation_candle():
    with_acc = techniques.apply("역매공파", acc_series(), 2000)
    no_vol = acc_series()
    no_vol["volume"] = 1000.0
    without = techniques.apply("역매공파", no_vol, 2000)
    assert len(without["markers"]) == 0
    assert all(m["label"] == "역매공파" for m in with_acc["markers"])


def test_force_lines_mark_support_only_in_aligned_uptrend():
    up = make(400)
    up["close"] = np.linspace(100, 300, 400) + np.sin(np.arange(400) / 3) * 4
    up["open"] = up["close"].shift().fillna(100.0)
    up["high"] = np.maximum(up["open"], up["close"]) * 1.003
    up["low"] = np.minimum(up["open"], up["close"]) * 0.997
    r = techniques.apply("세력선", up, 2000)
    assert {l["name"] for l in r["lines"]} == {"7일선", "15일선", "33일선"}
    assert r["markers"] and all(m["label"].endswith("일선 지지") for m in r["markers"])
    down = make(400)
    down["close"] = np.linspace(300, 100, 400)
    assert not techniques.apply("세력선", down, 2000)["markers"]
