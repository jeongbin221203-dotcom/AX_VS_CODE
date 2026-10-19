"""JB차트 신호 하나로 합치기 시험(2026-10-19, 사용자 "모든 신호·분석·기법·규칙을 통합해 하나의 신호로").
지금 차트에 따로 그려지는 것: 구간별 AI 매수·매도 규칙(주 신호) · 단테 신호(동그라미) · 하락 위험(띠·배지) · 골든·데드크로스.
이것들을 매수 '조건'으로 넣으면 실제로 나아지는지를 '2021년까지'로 고르고 2022년 이후로 확인(둘 다 좋아져야 채택).
하락 위험 모델은 2021년까지로 학습했으므로 2021년까지 값은 학습 구간(좋게 나옴) — 판단은 2022년 이후를 우선.
1단계(특징): 종목마다 ai.compute_features → 위험 확률·단테 신호 수(10봉)·14/56 이평 위아래 → data/research/jb_unify_feats.pkl
2단계(비교): 구간마다 조건 후보를 simulate 의 ok(신호를 믿는 봉)에 넣어 다시 계산.
python tools/research/jb_unify.py [--rebuild]"""
import json
import os
import pickle
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core import ai, jb_rules, picks  # noqa: E402

FEATS = os.path.join("data", "research", "jb_unify_feats.pkl")


def _feat(code):
    try:
        from core import backtest as bt, drop_risk, service
        if ai.MKT_DF is None:
            ai._init_worker(bt.MKT_PATH)
        df = service.load_prices(code)
        if len(df) < 300:
            return code, None
        X = ai.compute_features(df, ai.MKT_DF, True)
        P, _, _ = drop_risk.probs(X)
        c = df["close"].to_numpy(float)
        m14 = np.convolve(c, np.ones(14) / 14, "full")[:len(c)]
        m56 = np.convolve(c, np.ones(56) / 56, "full")[:len(c)]
        return code, {"date": df.index.strftime("%Y-%m-%d").to_numpy(), "risk": P.astype(np.float32),
                      "dante": X[:, ai.NAMES.index("dante_n10")].astype(np.float32), "sig": X[:, ai.NAMES.index("sig_n10")].astype(np.float32),
                      "gold": (m14 > m56).astype(np.int8)}
    except Exception as e:
        return code, {"error": str(e)}


def build(codes):
    out = {}
    with ProcessPoolExecutor(6) as ex:
        for k, (code, r) in enumerate(ex.map(_feat, codes, chunksize=10)):
            if r is not None and "error" not in r:
                out[code] = r
            if k % 200 == 0:
                print(f"  특징 {k}/{len(codes)}", flush=True)
    pickle.dump(out, open(FEATS, "wb"))
    return out


if __name__ == "__main__":
    D = pickle.load(open(os.environ.get("JB_PKL") or os.path.join("data", "research", "jb_scores.pkl"), "rb"))
    G = {k: v[0] for k, v in ((picks.load_today() or {}).get("scores") or {}).items()}
    if "--rebuild" in sys.argv or not os.path.exists(FEATS):
        F = build(sorted(D))
    else:
        F = pickle.load(open(FEATS, "rb"))
    print("특징 종목", len(F))
    MK = {}

    def align(code, key):
        f = F.get(code)
        if f is None:
            return None
        m = dict(zip(f["date"], f[key]))
        return np.array([m.get(d, np.nan) for d in D[code][0]], float)

    CACHE = {}

    def feats(code):
        if code not in CACHE:
            CACHE[code] = {k: align(code, k) for k in ("risk", "dante", "sig", "gold")}
        return CACHE[code]

    def run(group, cond=None):
        rows = []
        for code, t in D.items():
            if G.get(code) != group:
                continue
            if code not in MK:
                MK[code] = jb_rules.market_dd(t[0])
            s = np.asarray(t[6], float); ok = np.asarray(t[7], bool) & np.isfinite(s)
            if cond:
                f = feats(code)
                if f["risk"] is None:
                    continue
                ok = ok & cond(f)
            tr, _, _ = jb_rules.simulate(t[1], t[2], t[3], t[4], s, group, ok=ok, v=t[5], mkt_dd=MK[code])
            rows += [(t[0][b], float(r)) for i, b, j, px, r, w in tr if j is not None]
        return rows

    def st(rows, f=lambda d: True):
        r = np.array([x[1] for x in rows if f(x[0])])
        if not len(r):
            return {"n": 0}
        return {"n": int(len(r)), "mean": float(r.mean()), "med": float(np.median(r)), "win": float((r > 0).mean()),
                "loss": float(r[r < 0].mean()) if (r < 0).any() else 0.0, "sum": float(r.sum())}

    E, L = (lambda d: d <= "2021"), (lambda d: d >= "2022")
    fmt = lambda s: f"{s['n']:5d}건 평균 {s['mean'] * 100:+6.2f}% 승률 {s['win'] * 100:3.0f}% 손실 {s['loss'] * 100:+6.2f}% 합 {s['sum']:+6.1f}" if s["n"] else "없음"
    nz = lambda a: np.nan_to_num(a, nan=0)
    CONDS = {
        "지금(조건 없음)": None,
        "하락 위험 18% 미만": lambda f: nz(f["risk"]) < 0.18,
        "하락 위험 24% 미만": lambda f: nz(f["risk"]) < 0.24,
        "하락 위험 30% 미만": lambda f: nz(f["risk"]) < 0.30,
        "하락 위험 35% 미만(높음 제외)": lambda f: nz(f["risk"]) < 0.35,
        "단테 신호 10봉 안 1개↑": lambda f: nz(f["dante"]) >= 1,
        "단테 신호 없음": lambda f: nz(f["dante"]) == 0,
        "예측 신호 10봉 안 1개↑": lambda f: nz(f["sig"]) >= 1,
        "14일선 > 56일선(골든 상태)": lambda f: nz(f["gold"]) == 1,
        "14일선 < 56일선(데드 상태)": lambda f: nz(f["gold"]) == 0,
        "위험 35% 미만 + 단테 1개↑": lambda f: (nz(f["risk"]) < 0.35) & (nz(f["dante"]) >= 1),
    }
    out = {}
    for g in ("대형주", "중형주", "소형주", "잡주"):
        print(f"\n== {g}")
        o = {}
        for k, cond in CONDS.items():
            rows = run(g, cond)
            o[k] = {"all": st(rows), "early": st(rows, E), "late": st(rows, L)}
            print(f"  {k:24s} | ~2021 {fmt(o[k]['early'])} | 2022~ {fmt(o[k]['late'])}", flush=True)
        out[g] = o
    json.dump(out, open(os.path.join("data", "research", "jb_unify.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
