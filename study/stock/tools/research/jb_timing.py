"""저점·고점을 더 정확히 — JB 규칙의 매수 신호는 그대로 두고, 실제로 사는 때와 파는 때만 바꿔 본다(2026-10-19, 대형주·중형주).
저점 정확도 = 산 가격이 그 무렵 최저가(신호 앞 5봉 ~ 뒤 15봉 저가 중 최저)보다 몇 % 위인가(작을수록 저점에 가까움)
고점 정확도 = 판 가격이 그 무렵 최고가(매도 앞뒤 10봉 고가 중 최고)보다 몇 % 아래인가(작을수록 고점에 가까움)
받아들이는 기준: 두 기간(~2021·2022~) 모두 평균이 지금보다 낮지 않고 승률이 2%p 넘게 떨어지지 않으며, 저점 또는 고점 정확도가 나아질 것.
나눠 사기(−10%)는 그대로(1차 매수가 기준). 학습에 쓰지 않은 구간만, 비용 0.3%.
python tools/research/jb_timing.py 대형주"""
import json
import os
import pickle
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core import jb_rules, picks  # noqa: E402

GROUP = sys.argv[1] if len(sys.argv) > 1 else "대형주"
D = pickle.load(open(os.environ.get("JB_PKL") or os.path.join("data", "research", "jb_scores.pkl"), "rb"))
G = {k: v[0] for k, v in ((picks.load_today() or {}).get("scores") or {}).items()}
codes = [k for k in D if G.get(k) == GROUP]
RULE = jb_rules.rule_for(GROUP)
COST = jb_rules.COST


def sma(c, n):
    out = np.full(len(c), np.nan)
    if len(c) >= n:
        out[n - 1:] = np.lib.stride_tricks.sliding_window_view(c, n).mean(axis=1)
    return out


def prep(code):
    t = D[code]
    d, o, h, l, c, v = t[0], t[1], t[2], t[3], t[4], t[5]
    s = np.asarray(t[6], float); oos = np.asarray(t[7], bool)
    # 지금 규칙이 인정하는 매수 신호(필터 포함): 매도를 1봉 보유로 바꿔 신호만 뽑는다(겹침 판정은 아래에서 다시)
    tr, pend, _ = jb_rules.simulate(o, h, l, c, s, GROUP, ok=oos & np.isfinite(s), v=v, mkt_dd=jb_rules.market_dd(d), rule={**RULE, "exit": ("hold", 1)})
    sig = sorted({x[0] for x in tr})
    mid, up, lo = jb_rules.bands(c)
    sm5 = np.convolve(np.nan_to_num(s, nan=0.0), np.ones(5) / 5, mode="full")[:len(c)]
    return {"d": d, "o": o, "h": h, "l": l, "c": c, "sig": sig, "mid": mid, "up": up, "lo": lo, "m5": sma(c, 5), "m20": sma(c, 20), "sm5": sm5}


P = {k: prep(k) for k in codes}


# ---- 사는 때(신호 봉 i → (매수 봉, 가격)) ----
def buy_now(p, i):
    return (i + 1, p["o"][i + 1]) if i + 1 < len(p["c"]) else None


def buy_wait(kind, K=5):
    def f(p, i):
        o, h, l, c = p["o"], p["h"], p["l"], p["c"]
        n = len(c)
        for j in range(i + 1, min(i + 1 + K, n)):
            if kind == "반등 봉" and c[j] > o[j] and c[j] > c[j - 1]:
                return (j + 1, o[j + 1]) if j + 1 < n else None
            if kind == "볼린저 하단" and np.isfinite(p["lo"][j - 1]) and l[j] <= p["lo"][j - 1]:
                return (j, min(o[j], p["lo"][j - 1]))                       # 전날 하단 가격에 지정가
            if kind == "5일선 회복" and np.isfinite(p["m5"][j]) and c[j] > p["m5"][j]:
                return (j + 1, o[j + 1]) if j + 1 < n else None
            if kind == "전날보다 −3%" and l[j] <= c[i] * 0.97:
                return (j, min(o[j], c[i] * 0.97))                          # 신호 날 종가 −3% 지정가
        j = i + 1 + K
        return (j, o[j]) if j < n else None                                 # 조건이 안 오면 K봉 뒤 시가
    return f


# ---- 파는 때(매수 봉 b, 1차 매수가 e → (매도 봉, 가격, 이유)) ----
def sell_rule(p, b, e):
    """지금 규칙의 매도(jb_rules 와 같은 계산)."""
    o, h, c, up, sm5 = p["o"], p["h"], p["c"], p["up"], p["sm5"]
    n = len(c); x = RULE["exit"]
    if x[0] == "hold":
        j = b + x[1]
        return (j, c[j]) if j < n else None
    for j in range(b + 1, min(b + jb_rules.CAP, n)):
        u = up[j - 1]
        if np.isfinite(u) and h[j] >= u and max(o[j], u) >= e * (1 + x[2]):
            return (j, max(o[j], u))
        if j - b >= 5 and sm5[j] < x[1]:
            return (j, c[j])
    j = b + jb_rules.CAP
    return (j, c[j]) if j < n else None


def sell_large(kind, start=20, cap=60):
    """대형주: start 봉 뒤부터 고점 신호가 나면 매도, 아니면 cap 봉째."""
    def f(p, b, e):
        o, h, c = p["o"], p["h"], p["c"]
        n = len(c)
        touched = False
        for j in range(b + start, min(b + cap, n)):
            u = p["up"][j - 1]
            if kind == "상단 지정가" and np.isfinite(u) and h[j] >= u:
                return (j, max(o[j], u))
            if kind == "상단 뒤 5일선 이탈":
                touched = touched or (np.isfinite(p["up"][j]) and h[j] >= p["up"][j])
                if touched and c[j] < p["m5"][j]:
                    return (j, c[j])
            if kind == "20일선 이탈" and c[j] < p["m20"][j]:
                return (j, c[j])
            if kind == "5일선 이탈(이익일 때)" and c[j] < p["m5"][j] and c[j] > e * 1.05:
                return (j, c[j])
        j = b + cap
        return (j, c[j]) if j < n else None
    return f


def sell_mid(kind):
    """중형주: 지금 매도(AI 30·상단 +20%)에서 상단 도달 때 바로 팔지 않고 고점을 더 따라가 보기."""
    def f(p, b, e):
        o, h, c, sm5 = p["o"], p["h"], p["c"], p["sm5"]
        n = len(c); x = RULE["exit"]
        riding = False
        for j in range(b + 1, min(b + jb_rules.CAP, n)):
            u = p["up"][j - 1]
            if not riding and np.isfinite(u) and h[j] >= u and max(o[j], u) >= e * (1 + x[2]):
                if kind == "지금":
                    return (j, max(o[j], u))
                riding = True                                            # 목표 도달 — 이제 고점 따라가기
            if riding:
                if kind == "목표 뒤 5일선 이탈" and c[j] < p["m5"][j]:
                    return (j, c[j])
                if kind == "목표 뒤 고점 −7%" and c[j] <= np.max(c[b:j + 1]) * 0.93:
                    return (j, c[j])
                if kind == "목표 뒤 20일선 이탈" and c[j] < p["m20"][j]:
                    return (j, c[j])
            if j - b >= 5 and sm5[j] < x[1]:
                return (j, c[j])
        j = b + jb_rules.CAP
        return (j, c[j]) if j < n else None
    return f


def run(buy_fn, sell_fn):
    rows = []
    for code, p in P.items():
        o, h, l, c = p["o"], p["h"], p["l"], p["c"]
        n = len(c); busy = -1
        for i in p["sig"]:
            if i <= busy:
                continue
            bb = buy_fn(p, i)
            if not bb:
                continue
            b, e = bb
            ss = sell_fn(p, b, e)
            if not ss:
                busy = n
                continue
            j, px = ss
            avg = e
            for x_ in range(b, j):                                        # 나눠 사기(1차 매수가 −10%)
                if c[x_] <= e * (1 - jb_rules.SPLIT):
                    if x_ + 1 <= j:
                        avg = (e + o[x_ + 1]) / 2
                    break
            ret = px / avg - 1 - COST
            lo_ = np.min(l[max(0, i - 5):min(n, i + 16)])
            hi_ = np.max(h[max(0, j - 10):min(n, j + 11)])
            rows.append((p["d"][b], ret, j - b, e / lo_ - 1, 1 - px / hi_))
            busy = j
    return rows


def st(rows, f=lambda d: True):
    r = [x for x in rows if f(x[0])]
    a = np.array([x[1] for x in r])
    return {"n": len(r), "mean": float(a.mean()), "win": float((a > 0).mean()), "loss": float(a[a < 0].mean()) if (a < 0).any() else 0.0,
            "days": float(np.mean([x[2] for x in r])), "bottom": float(np.median([x[3] for x in r])), "top": float(np.median([x[4] for x in r]))}


E, L = (lambda d: d <= "2021"), (lambda d: d >= "2022")
fmt = lambda s: (f"{s['n']:4d}건 {s['mean'] * 100:+6.2f}% 승률 {s['win'] * 100:4.0f}% 손실 {s['loss'] * 100:+6.2f}% {s['days']:3.0f}일 | "
                 f"저점 대비 +{s['bottom'] * 100:4.1f}% · 고점 대비 −{s['top'] * 100:4.1f}%")
BUYS = {"지금(다음 날 시가)": buy_now, **{f"{k} 기다림(5봉)": buy_wait(k) for k in ("반등 봉", "볼린저 하단", "5일선 회복", "전날보다 −3%")},
        "반등 봉 기다림(3봉)": buy_wait("반등 봉", 3), "볼린저 하단 기다림(10봉)": buy_wait("볼린저 하단", 10)}
SELLS = {"지금": sell_rule}
if RULE["exit"][0] == "hold":
    for k in ("상단 지정가", "상단 뒤 5일선 이탈", "20일선 이탈", "5일선 이탈(이익일 때)"):
        for start, cap in ((20, 60), (30, 60), (20, 40)):
            SELLS[f"{start}봉 뒤 {k}(최대 {cap})"] = sell_large(k, start, cap)
else:
    for k in ("목표 뒤 5일선 이탈", "목표 뒤 고점 −7%", "목표 뒤 20일선 이탈"):
        SELLS[k] = sell_mid(k)

cur = run(buy_now, sell_rule)
C, CE, CL = st(cur), st(cur, E), st(cur, L)
print(f"{GROUP} 지금 규칙\n  전체 {fmt(C)}\n  ~2021 {fmt(CE)}\n  2022~ {fmt(CL)}")
res = []
for bn, bf in BUYS.items():
    for sn, sf in SELLS.items():
        if bn.startswith("지금") and sn == "지금":
            continue
        rows = run(bf, sf)
        res.append((bn, sn, st(rows), st(rows, E), st(rows, L)))


def good(x):
    return (x[3]["mean"] >= CE["mean"] and x[4]["mean"] >= CL["mean"] and x[2]["win"] >= C["win"] - 0.02
            and (x[2]["bottom"] < C["bottom"] - 0.002 or x[2]["top"] < C["top"] - 0.002))


ok = sorted([x for x in res if good(x)], key=lambda x: -x[2]["mean"])
print(f"\n기준 통과 {len(ok)}개(전체 평균 순):")
for x in ok[:12]:
    print(f"  매수 {x[0]:18s} · 매도 {x[1]:28s} | 전체 {fmt(x[2])} | ~2021 {x[3]['mean'] * 100:+6.2f}% · 2022~ {x[4]['mean'] * 100:+6.2f}%")
print("\n참고 — 매수만 바꾼 것(매도 지금):")
for x in [x for x in res if x[1] == "지금"]:
    print(f"  {x[0]:20s} | 전체 {fmt(x[2])} | ~2021 {x[3]['mean'] * 100:+6.2f}% · 2022~ {x[4]['mean'] * 100:+6.2f}%")
print("\n참고 — 매도만 바꾼 것(매수 지금):")
for x in [x for x in res if x[0].startswith("지금")]:
    print(f"  {x[1]:30s} | 전체 {fmt(x[2])} | ~2021 {x[3]['mean'] * 100:+6.2f}% · 2022~ {x[4]['mean'] * 100:+6.2f}%")
json.dump({"group": GROUP, "cur": C, "ok": [{"buy": x[0], "sell": x[1], "all": x[2], "early": x[3], "late": x[4]} for x in ok[:12]]},
          open(os.path.join("data", "research", f"jb_timing_{GROUP}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
