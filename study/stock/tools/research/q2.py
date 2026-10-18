"""'싸게 사서 비싸게 판다' 규칙 탐색(2026-10-18): 그룹마다 매수 조건(볼린저 %B·RSI·스토캐스틱·CCI·20일선 이격·200일선)과
매도 조건(볼린저 상단·RSI 과열·손절·최대 보유일)의 수치를 무작위로 바꿔 가며 수익을 비교한다.
선택은 2016~2023년(앞 구간 2016~21 + 2022~23 둘 다 좋은 것), 시험은 2024~2026년. 신호 다음 날 시가에 사고 팔며 비용 0.3% + 추가 0.3%p.
투입 방식: 그 날 보유 중인 종목에 균등 배분(보유가 없는 날은 현금). 실행: python tools/research/q2.py → %TEMP%/stk/Q2_<그룹>.pkl"""
import os, sys, time, numpy as np, pandas as pd
from concurrent.futures import ProcessPoolExecutor

T = os.environ["TEMP"] + "/stk/"
COST = 0.006
NSAMP = int(os.environ.get("NSAMP", 700))
PER = {"A": ("2016-01-01", "2021-12-31"), "V": ("2022-01-01", "2023-12-31"), "T": ("2024-01-01", "2026-12-31")}


def space(rng):
    pick = lambda xs: xs[rng.randint(len(xs))]
    return dict(b_lo=pick([0.0, 0.05, 0.1, 0.2, 0.3]), b_hi=pick([0.6, 0.8, 1.0, 1.1]), r_lo=pick([None, 25, 30, 35, 40]),
                r_hi=pick([None, 60, 65, 70, 75, 80]), s_lo=pick([None, 10, 20, 30]), c_lo=pick([None, -200, -150, -100]),
                ma200=pick([0, 1]), d20=pick([None, -0.05, -0.1, -0.15]), sl=pick([None, -0.05, -0.08, -0.12, -0.2]), H=pick([20, 40, 60, 120]))


def prep(panel, g):
    """종목별 numpy 배열과 날짜 번호"""
    cal = sorted({d for _, (gg, x) in panel.items() if gg == g for d in x.index})
    did = {d: i for i, d in enumerate(cal)}
    S = []
    for c, (gg, x) in panel.items():
        if gg != g:
            continue
        liq = (x.close * x.volume).rolling(20).mean().to_numpy()
        S.append(dict(o=x.open.to_numpy(float), c=x.close.to_numpy(float), bb=x.bb_pctb.to_numpy(float), rsi=x.rsi14.to_numpy(float),
                      st=x.stoch_k.to_numpy(float), cci=x.cci20.to_numpy(float), d20=x.dist20.to_numpy(float), ma=x.ma200.to_numpy(float),
                      ok=(x.grp.to_numpy() == g) & (liq >= 3e8), ids=np.array([did[d] for d in x.index])))
    return cal, S


def simulate(S, ncal, p):
    gsum = np.zeros(ncal); gcnt = np.zeros(ncal); trades = []
    for s in S:
        o, c = s["o"], s["c"]; n = len(c)
        with np.errstate(invalid="ignore"):
            en = s["ok"] & (s["bb"] <= p["b_lo"])
            if p["r_lo"] is not None: en &= s["rsi"] <= p["r_lo"]
            if p["s_lo"] is not None: en &= s["st"] <= p["s_lo"]
            if p["c_lo"] is not None: en &= s["cci"] <= p["c_lo"]
            if p["d20"] is not None: en &= s["d20"] <= p["d20"]
            if p["ma200"]: en &= c > s["ma"]
            ex = s["bb"] >= p["b_hi"]
            if p["r_hi"] is not None: ex |= s["rsi"] >= p["r_hi"]
        cand = np.flatnonzero(en[:-1]); ex_idx = np.flatnonzero(ex)
        dret = np.empty(n); dret[0] = 0; dret[1:] = c[1:] / c[:-1] - 1
        i = 0
        while i < len(cand):
            t = cand[i]; k = t + 1                       # 다음 날 시가 매수
            ent = o[k]
            if not ent > 0: i += 1; continue
            hi = min(k + p["H"], n - 1)
            e = hi
            j = np.searchsorted(ex_idx, k)               # 신호는 매수한 날 종가부터 확인
            if j < len(ex_idx) and ex_idx[j] < e: e = ex_idx[j]
            if p["sl"] is not None:
                seg = c[k:e + 1]; hit = np.flatnonzero(seg <= ent * (1 + p["sl"]))
                if len(hit): e = k + hit[0]
            sell = e + 1 < n
            r = np.empty(e - k + 1 + (1 if sell else 0))
            r[0] = c[k] / ent - 1
            if e > k: r[1:e - k + 1] = dret[k + 1:e + 1]
            if sell: r[-1] = o[e + 1] / c[e] - 1
            r[-1] -= COST
            last = e + 1 if sell else e
            gsum[s["ids"][k:last + 1]] += r; gcnt[s["ids"][k:last + 1]] += 1
            trades.append((s["ids"][k], np.prod(1 + r) - 1, last - k + 1))
            i = np.searchsorted(cand, last + 1)
    return gsum, gcnt, trades


def stats(gsum, gcnt, trades, cal, per):
    a, b = per; m = np.array([a <= str(d)[:10] <= b for d in cal])
    r = np.where(gcnt > 0, gsum / np.maximum(gcnt, 1), 0.0)[m]; days = m.sum()
    eq = np.cumprod(1 + r); yrs = days / 245
    cagr = eq[-1] ** (1 / yrs) - 1 if yrs > 0 else np.nan
    sh = r.mean() / r.std() * np.sqrt(245) if r.std() > 0 else np.nan
    mdd = (eq / np.maximum.accumulate(eq) - 1).min()
    idx = np.flatnonzero(m); lo, hi = idx[0], idx[-1]
    tr = [(x[1], x[2]) for x in trades if lo <= x[0] <= hi]
    n = len(tr); win = np.mean([x[0] > 0 for x in tr]) if n else np.nan
    return dict(cagr=cagr * 100, sharpe=sh, mdd=mdd * 100, n=n, win=win * 100, avg=(np.mean([x[0] for x in tr]) * 100 if n else np.nan),
                hold=(np.mean([x[1] for x in tr]) if n else np.nan), active=(gcnt[m] > 0).mean() * 100)


def work(g):
    panel = pd.read_pickle(T + "Q1.pkl")
    cal, S = prep(panel, g)
    rng = np.random.RandomState(1)
    rows = []; t0 = time.time()
    base = dict(b_lo=0.0, b_hi=1.0, r_lo=None, r_hi=None, s_lo=None, c_lo=None, ma200=0, d20=None, sl=None, H=20)
    plist = [base] + [space(rng) for _ in range(NSAMP)]
    for p in plist:
        gs, gc, tr = simulate(S, len(cal), p)
        row = dict(p)
        for k, per in PER.items():
            for kk, vv in stats(gs, gc, tr, cal, per).items(): row[f"{k}_{kk}"] = vv
        rows.append(row)
    R = pd.DataFrame(rows)
    # 20일 단순 보유 기준선(같은 종목): 매일 균등 보유 — 수익은 구간 평균 종가 수익
    R.to_pickle(T + f"Q2_{g}.pkl")
    print(g, "완료", len(R), f"{time.time() - t0:.0f}초", flush=True)
    return g


if __name__ == "__main__":
    gs = sys.argv[1:] or ["대형주", "중형주", "소형주", "잡주"]
    with ProcessPoolExecutor(len(gs)) as ex:
        list(ex.map(work, gs))
