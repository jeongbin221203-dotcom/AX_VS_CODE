"""JB차트 AI 점수 매수·매도 규칙 비교 — 지금 규칙(95·90점 상향 돌파 → 다음 날 시가 매수 → 20거래일째 종가 매도)과 다른 매도·매수 규칙.
입력: tools/research/jb_scores.py 가 만든 data/research/jb_scores.pkl. 학습에 쓰지 않은 구간(oos)의 신호만 쓴다.
한 종목에서 한 번에 한 거래만(보유 중 신호는 건너뜀 — 차트에 그릴 때와 같은 규칙). 비용 0.3%(왕복).
python tools/research/jb_exit.py  → 규칙별 거래 수·평균·중앙·승률·평균 보유일·하루당 수익, 기간·시총 구간별."""
import glob
import json
import os
import pickle
import sys

import numpy as np

COST = 0.003
TOP, WEAK, GAP = 0.95, 0.90, 10
D = pickle.load(open(os.environ.get("JB_PKL") or os.path.join("data", "research", "jb_scores.pkl"), "rb"))
caps = sorted(glob.glob(os.path.join("..", "stock_html", "data", "caps-kr-*.json")))
CAP = json.load(open(caps[-1], encoding="utf-8"))["items"] if caps else {}


def tier(code):
    v = CAP.get(code)
    if not v:
        return "?"
    cap, rank = v
    return "대형" if rank <= 100 else "중형" if cap >= 5e11 else "소형" if cap >= 1e11 else "잡주"


def sma(x, n):
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        c = np.cumsum(np.insert(x, 0, 0.0))
        out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def prep(code):
    dates, o, h, l, c, v, sc, oos, kq = D[code]
    s = np.nan_to_num(sc, nan=0.0)
    m20 = sma(c, 20)
    sd = np.full(len(c), np.nan)
    for i in range(19, len(c)):
        sd[i] = c[i - 19:i + 1].std()
    sm5 = np.convolve(s, np.ones(5) / 5, mode="full")[:len(s)]
    return dict(dates=dates, o=o, h=h, l=l, c=c, v=v, s=s, oos=oos, m20=m20, up=m20 + 2 * sd, lo=m20 - 2 * sd, m60=sma(c, 60), m224=sma(c, 224), sm5=sm5)


def signals(P, entry=None):
    """(신호 봉, 강/약) — ai_chart 와 같은 규칙. entry: 추가 매수 조건 f(P, i) → bool."""
    s, out, last, lt = P["s"], [], -10**9, ""
    for i in range(1, len(s)):
        strong, weak = s[i] >= TOP and s[i - 1] < TOP, s[i] >= WEAK and s[i - 1] < WEAK
        if not (strong or weak) or not P["oos"][i]:
            continue
        t = "강" if s[i] >= TOP else "약"
        if i - last < GAP and not (strong and lt == "약"):
            continue
        last, lt = i, t
        if entry and not entry(P, i):
            continue
        out.append((i, t))
    return out


# ---- 매도 규칙: f(P, b) → (매도 봉, 매도가) 또는 None(데이터 끝까지 보유) — b = 매수 봉(다음 날 시가) ----
def fixed(n):
    return lambda P, b: (b + n, P["c"][b + n]) if b + n < len(P["c"]) else None


def trail(t, cap=250):
    def f(P, b):
        c, hi = P["c"], P["c"][b]
        for j in range(b, min(b + cap, len(c))):
            hi = max(hi, c[j])
            if c[j] < hi * (1 - t):
                return j, c[j]
        j = b + cap
        return (j, c[j]) if j < len(c) else None
    return f


def score_exit(x, min_hold=5, cap=120):
    def f(P, b):
        c, s5 = P["c"], P["sm5"]
        for j in range(b + min_hold, min(b + cap, len(c))):
            if s5[j] < x:
                return j, c[j]
        j = b + cap
        return (j, c[j]) if j < len(c) else None
    return f


def bb_upper(min_hold=1, cap=60):
    """볼린저 상단(20, 2)에 닿으면 그날 종가에 매도(최대 cap 봉)."""
    def f(P, b):
        c, h, up = P["c"], P["h"], P["up"]
        for j in range(b + min_hold, min(b + cap, len(c))):
            if np.isfinite(up[j]) and h[j] >= up[j]:
                return j, max(c[j], min(up[j], h[j]))      # 상단 가격(장중에 닿은 값)과 종가 중 큰 쪽 — 상단 지정가 매도를 흉내
        j = b + cap
        return (j, c[j]) if j < len(c) else None
    return f


def bb_upper_close(min_hold=1, cap=60):
    def f(P, b):
        c, up = P["c"], P["up"]
        for j in range(b + min_hold, min(b + cap, len(c))):
            if np.isfinite(up[j]) and c[j] >= up[j]:
                return j, c[j]
        j = b + cap
        return (j, c[j]) if j < len(c) else None
    return f


def hold_then_ma(n=20, ma="m20", cap=250):
    """n 봉 보유 뒤에도 종가가 이평선 위면 계속 보유, 이평선 아래로 마감하면 매도."""
    def f(P, b):
        c, m = P["c"], P[ma]
        for j in range(b + n, min(b + cap, len(c))):
            if np.isfinite(m[j]) and c[j] < m[j]:
                return j, c[j]
        j = b + cap
        return (j, c[j]) if j < len(c) else None
    return f


def with_stop(base, stop):
    def f(P, b):
        r = base(P, b)
        end = r[0] if r else len(P["c"]) - 1
        c, entry = P["c"], P["o"][b]
        for j in range(b, end):
            if c[j] <= entry * (1 - stop):
                return j, c[j]
        return r
    return f


def bb_mid_after_upper(cap=120):
    """상단에 한 번 닿은 뒤 종가가 20일선(가운데) 아래로 내려오면 매도. 상단에 닿지 못하고 20봉이 지나 20일선 아래면 매도."""
    def f(P, b):
        c, h, up, m = P["c"], P["h"], P["up"], P["m20"]
        touched = False
        for j in range(b + 1, min(b + cap, len(c))):
            if np.isfinite(up[j]) and h[j] >= up[j]:
                touched = True
            if np.isfinite(m[j]) and c[j] < m[j] and (touched or j - b >= 20):
                return j, c[j]
        j = b + cap
        return (j, c[j]) if j < len(c) else None
    return f


def bb_prev_limit(min_hold=1, cap=60):
    """전날 볼린저 상단을 지정가로 걸어 두고, 그날 고가가 닿으면 그 가격(시가가 이미 위면 시가)에 매도 — 미리 보기 없음."""
    def f(P, b):
        o, h, c, up = P["o"], P["h"], P["c"], P["up"]
        for j in range(b + min_hold, min(b + cap, len(c))):
            u = up[j - 1]
            if np.isfinite(u) and h[j] >= u:
                return j, max(o[j], u)
        j = b + cap
        return (j, c[j]) if j < len(c) else None
    return f


def score_or_bb(x=0.5, gain=0.0, min_hold=5, cap=120):
    """AI 점수(5봉 평균)가 x 아래로 내려오거나, 수익이 gain 이상인 상태에서 전날 볼린저 상단에 닿으면 매도(먼저 오는 쪽)."""
    def f(P, b):
        o, h, c, up, s5 = P["o"], P["h"], P["c"], P["up"], P["sm5"]
        entry = o[b]
        for j in range(b + 1, min(b + cap, len(c))):
            u = up[j - 1]
            if np.isfinite(u) and h[j] >= u and max(o[j], u) >= entry * (1 + gain):
                return j, max(o[j], u)
            if j - b >= min_hold and s5[j] < x:
                return j, c[j]
        j = b + cap
        return (j, c[j]) if j < len(c) else None
    return f


EXITS = {
    "지금(20일 보유)": fixed(20),
    "10일 보유": fixed(10), "40일 보유": fixed(40), "60일 보유": fixed(60),
    "고점 대비 −10% 이탈": trail(0.10), "고점 대비 −15% 이탈": trail(0.15), "고점 대비 −20% 이탈": trail(0.20),
    "AI 점수(5봉 평균) 50점 아래": score_exit(0.5), "AI 점수 70점 아래": score_exit(0.7),
    "볼린저 상단 닿으면(지정가)": bb_upper(), "볼린저 상단 위 마감": bb_upper_close(),
    "상단 닿은 뒤 20일선 이탈": bb_mid_after_upper(),
    "20일 보유 후 20일선 이탈": hold_then_ma(20, "m20"), "20일 보유 후 60일선 이탈": hold_then_ma(20, "m60"),
    "20일 보유 + 손절 −10%": with_stop(fixed(20), 0.10),
    "상단 닿은 뒤 20일선 이탈 + 손절 −10%": with_stop(bb_mid_after_upper(), 0.10),
    "전날 상단 지정가(미리 보기 없음)": bb_prev_limit(),
    "AI 50점 아래 또는 상단(수익 0%↑)": score_or_bb(0.5, 0.0),
    "AI 50점 아래 또는 상단(수익 10%↑)": score_or_bb(0.5, 0.10),
    "AI 50점 아래 또는 상단(수익 20%↑)": score_or_bb(0.5, 0.20),
}
ENTRIES = {
    "지금(조건 없음)": None,
    "종가 > 60일선": lambda P, i: np.isfinite(P["m60"][i]) and P["c"][i] > P["m60"][i],
    "종가 > 224일선": lambda P, i: np.isfinite(P["m224"][i]) and P["c"][i] > P["m224"][i],
    "볼린저 상단 아래(과열 아님)": lambda P, i: np.isfinite(P["up"][i]) and P["c"][i] < P["up"][i],
    "볼린저 %B ≤ 0.5(가운데 이하)": lambda P, i: np.isfinite(P["up"][i]) and (P["c"][i] - P["lo"][i]) <= 0.5 * (P["up"][i] - P["lo"][i]),
    "강한 신호(95점)만": lambda P, i: P["s"][i] >= TOP,
    "강한 신호 + %B ≤ 0.5": lambda P, i: P["s"][i] >= TOP and np.isfinite(P["up"][i]) and (P["c"][i] - P["lo"][i]) <= 0.5 * (P["up"][i] - P["lo"][i]),
}


def run(exit_fn, entry=None):
    rows = []
    for code in D:
        P = PP[code]
        busy = -1
        for i, t in signals(P, entry):
            b = i + 1
            if b >= len(P["c"]) or i <= busy:
                continue
            r = exit_fn(P, b)
            if r is None:
                busy = len(P["c"])
                continue
            j, px = r
            busy = j
            rows.append((P["dates"][b][:4], TIERS[code], t, px / P["o"][b] - 1 - COST, j - b))
    return rows


def stat(rows):
    if not rows:
        return "-"
    r = np.array([x[3] for x in rows]); d = np.array([x[4] for x in rows], float)
    return f"{len(r):5d}건 평균 {r.mean() * 100:+6.2f}% 중앙 {np.median(r) * 100:+6.2f}% 승률 {(r > 0).mean() * 100:4.1f}% 보유 {d.mean():5.1f}일 하루당 {r.mean() / max(d.mean(), 1) * 100:+.3f}%"


PP, TIERS = {}, {}


def setup():
    PP.update({k: prep(k) for k in D})
    TIERS.update({k: tier(k) for k in D})


def main():
    setup()
    print("종목", len(D), "구간", {t: sum(1 for x in TIERS.values() if x == t) for t in set(TIERS.values())})
    print("\n[매도 규칙] (매수 = 지금 규칙)")
    res = {}
    ONLY = [x for x in os.environ.get("ONLY", "").split(",") if x]
    for name, f in EXITS.items():
        if ONLY and name not in ONLY:
            continue
        rows = run(f)
        res[name] = rows
        early = [x for x in rows if x[0] <= "2021"]; late = [x for x in rows if x[0] >= "2022"]
        print(f"{name:26s} | {stat(rows)}")
        print(f"{'':26s} |   ~2021 {stat(early)}")
        print(f"{'':26s} |   2022~ {stat(late)}")
    print("\n[시총 구간별]")
    for name in res:
        print(name[:20].ljust(20), " | ".join(t + " " + stat([x for x in res[name] if x[1] == t]).split("하루당")[0] for t in ("대형", "중형", "소형")))
    best = sys.argv[1] if len(sys.argv) > 1 else None
    if best:
        print("\n[매수 조건] (매도 =", best, ")")
        for name, e in ENTRIES.items():
            rows = run(EXITS[best], e)
            print(f"{name:24s} | {stat(rows)} | ~2021 {stat([x for x in rows if x[0] <= '2021']).split('하루당')[0]} | 2022~ {stat([x for x in rows if x[0] >= '2022']).split('하루당')[0]}")


if __name__ == "__main__":
    main()
