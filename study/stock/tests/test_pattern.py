import json

import numpy as np
import pandas as pd

import config
from core import ai, pattern_study as pt, precursor_study as pre


def _df(n=700, seed=0):
    rng = np.random.default_rng(seed)
    c = 100 * np.cumprod(1 + rng.normal(0.0, 0.004, n))
    o = np.r_[c[0], c[:-1]]
    v = np.full(n, 3000.0)
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.002, "low": np.minimum(o, c) * 0.998, "close": c, "volume": v}, index=pd.date_range("2019-01-01", periods=n, freq="B"))


def test_patterns_have_expected_shapes_and_flags():
    df = _df()
    # 400일째 장대양봉 + 거래량 3배(대량 거래 양봉) → 뒤 3일 −3% 눌림
    c = df["close"].to_numpy().copy(); o = df["open"].to_numpy().copy(); v = df["volume"].to_numpy().copy()
    c[400] = o[400] * 1.06; v[400] = 9000
    c[401] = c[400] * 0.98; c[402] = c[401] * 0.99
    df["close"], df["volume"] = c, v
    df["open"] = np.r_[c[0], c[:-1]]
    df["high"] = np.maximum(df["open"], df["close"]) * 1.002
    df["low"] = np.minimum(df["open"], df["close"]) * 0.998
    F, jump, cc, vv, vr = pre._features(df)
    P = pt._patterns(df, F)
    assert set(pt.PAT) <= set(P) and all(len(P[k]) == len(df) for k in pt.PAT)
    assert P["big_then_rest"][402] == 1 and P["big_then_rest"][400] == 0
    assert P["vol_surge_dry"][410] in (0.0, 1.0) and P["vol_min60"][450] in (0.0, 1.0)
    for k in pt.PAT:
        if k not in ("acc_60n", "acc_since", "ma_conv", "range20"):
            assert set(np.unique(P[k][300:])) <= {0.0, 1.0}


def test_specs_cover_pattern_and_signal_names():
    n = 500
    rng = np.random.default_rng(1)
    D = pd.DataFrame({k: rng.integers(0, 2, n).astype(float) for k in pt.PAT})
    D["acc_60n"] = rng.integers(0, 4, n).astype(float); D["acc_since"] = rng.integers(0, 60, n).astype(float)
    for k in ("vdry10", "dist224", "n_j250"):
        D[k] = rng.normal(0, 1, n)
    for j in range(len(ai.SIGNAL_LABELS)):
        D[f"sig{j:02d}"] = rng.integers(0, 61, n).astype(float)
    D["sig_n10"] = rng.integers(0, 6, n).astype(float)
    specs = pt._specs(D)
    names = [n_ for _, n_, _ in specs]
    assert pt.LABEL["acc_20"] in names and "매집봉 돌파 (3봉 안)" in names and len(names) == len(set(names))


def test_pattern_page(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(pt, "PATTERN_PATH", tmp_path / "p.json")
    from app import create_app
    c = create_app().test_client()
    assert "아직 분석 결과가 없습니다" in c.get("/pattern").get_data(as_text=True)
    row = lambda g, n: {"group": g, "name": n, "share_pre": 0.2, "share_base": 0.1, "lift_share": 2.0, "p20": 0.06, "lift20": 1.5, "n_pre": 100, "n_base": 1000, "p_tr": 0.05, "p_te": 0.07, "lift_tr": 1.4, "lift_te": 1.5, "robust": True}  # noqa: E731
    mr = {"auc": 0.7, "base": 0.04, "deciles": [0.01] * 9 + [0.2], "top1": 0.3, "importance": [{"name": "acc_20", "label": "매집봉 20일 안", "gain": 0.01}]}
    rep = {"meta": {"generated": "x", "jump": 0.2, "events": 1000, "baseline": 50000, "stocks": 100, "date_min": "2000-01-01", "date_max": "2026-10-10", "split": {"train_end": "2021-12-31"}, "sig_win": 3},
           "base": {"p20": 0.04, "p_tr": 0.03, "p_te": 0.05}, "rows": [row(g, g + " 패턴") for g in ["매집봉", "거래량", "가격", "기법 신호"]],
           "combos": [{"name": "A + B", "n": 500, "p20": 0.1, "lift": 2.5, "p_tr": 0.09, "p_te": 0.11}], "model": {"패턴·신호만": mr, "패턴·신호 + 기본 특징(변동성·위치·이전 급등)": mr, "기본 특징만 (비교)": mr},
           "today": {"date": "2026-10-10", "n": 1000, "rows": [{"code": "005930", "name": "삼성전자", "close": 100000.0, "prob": 0.2, "patterns": ["매집봉 20일 안"], "signals": ["매집봉 돌파"], "n_j250": 1.0, "atrp": 0.05, "dist224": 0.1, "acc_60n": 2.0}]},
           "text": ["요약 문장"]}
    (tmp_path / "p.json").write_text(json.dumps(rep), encoding="utf-8")
    html = c.get("/pattern").get_data(as_text=True)
    for k in ("요약 문장", "삼성전자", "패턴별", "패턴 겹침", "패턴으로 급등을 맞힐 수 있나"):
        assert k in html
