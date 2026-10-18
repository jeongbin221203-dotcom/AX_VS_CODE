"""혼합 방식 탐색(2026-10-18): 매수 = 그룹별 AI 점수 상위(검증된 설정, 걸어가며 학습한 2022~26년 점수), 매도 = 지표 규칙(볼린저 상단·RSI 과열·이동평균 이탈·고점 대비 하락·손절·최대 보유일),
시장 동향 = 시장 지수가 N일선 위일 때만 매수. 메인 지표(볼린저 기간·배수, 이동평균 길이)와 보조 지표(RSI 기간·수치)의 수치를 함께 바꿔 가며 비교.
선택은 2022~23년, 시험은 2024~26년. 다음 날 시가 체결, 비용 0.3% + 추가 0.3%p. 그 날 보유 종목 균등 배분(없으면 현금).
실행: python tools/research/q3.py → %TEMP%/stk/Q3_<그룹>.pkl"""
import os, sys, time, numpy as np, pandas as pd
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.getcwd())
T = os.environ["TEMP"] + "/stk/"
COST = 0.006
NSAMP = int(os.environ.get("NSAMP", 1500))
CFG = {"대형주": ("r_ens", 5, False), "중형주": ("r_ens", 10, False), "소형주": ("r_ens", 7, True), "잡주": ("score", 5, False)}
PER = {"V": ("2022-01-01", "2023-12-31"), "T": ("2024-01-01", "2026-12-31"), "T1": ("2024-01-01", "2024-12-31"), "T2": ("2025-01-01", "2025-12-31"), "T3": ("2026-01-01", "2026-12-31")}


def space(rng):
    pick = lambda xs: xs[rng.randint(len(xs))]
    return dict(bn=pick([10, 20, 30]), bk=pick([1.5, 2.0, 2.5]), b_hi=pick([None, 0.8, 1.0, 1.1]), rn=pick([7, 14, 21]), r_hi=pick([None, 65, 70, 75, 80]),
                ma=pick([None, 10, 20, 60]), trail=pick([None, -0.06, -0.1, -0.15]), sl=pick([None, -0.06, -0.1, -0.15]), mh=pick([1, 3, 5, 10]),
                H=pick([20, 30, 40, 60]), reg=pick([None, 60, 120, 200]))


def ind(df, bn, bk, rn):
    c = df.close
    m, s = c.rolling(bn).mean(), c.rolling(bn).std(ddof=0)
    pb = ((c - (m - bk * s)) / (2 * bk * s).replace(0, np.nan)).to_numpy(float)
    d = c.diff(); up = d.clip(lower=0).ewm(alpha=1 / rn, adjust=False).mean(); dn = (-d).clip(lower=0).ewm(alpha=1 / rn, adjust=False).mean()
    rsi = (100 - 100 / (1 + up / dn.replace(0, np.nan))).to_numpy(float)
    return pb, rsi


class Cache:
    def __init__(self, df):
        self.o = df.open.to_numpy(float); self.c = df.close.to_numpy(float); self.df = df; self.d = {}
        self.ma = {n: df.close.rolling(n).mean().to_numpy(float) for n in (10, 20, 60)}
        self.dates = df.index

    def ind(self, bn, bk, rn):
        key = (bn, bk, rn)
        if key not in self.d: self.d[key] = ind(self.df, bn, bk, rn)
        return self.d[key]


def load(g):
    from core import service
    A = pd.read_pickle(T + "AO.pkl")
    by, k, risk = CFG[g]
    d = A[(A.grp == g) & (A.liqv >= 3e8)]
    if risk: d = d[d.rP <= 0.5]
    d = d.sort_values(["date", by], ascending=[True, False]).groupby("date").head(k)[["date", "code"]]
    M = pd.read_pickle("data/market_idx.pkl")
    lvl = (1 + M["mkt_ret"].fillna(0)).cumprod(); lvl.index = pd.to_datetime(lvl.index)
    regs = {n: (lvl > lvl.rolling(n).mean()) for n in (60, 120, 200)}
    pr = {}
    for c in d.code.unique():
        df = service.load_prices(c)
        if len(df) > 300: pr[c] = Cache(df[df.index >= "2020-01-01"])
    d["date"] = pd.to_datetime(d["date"])
    return d, pr, regs


def simulate(trades, pr, regs, cal_id, ncal, p):
    gsum = np.zeros(ncal); gcnt = np.zeros(ncal); res = []
    for code, sd in trades:
        ch = pr.get(code)
        if ch is None: continue
        if p["reg"] is not None:
            r = regs[p["reg"]]
            if sd not in r.index or not bool(r.loc[sd]): continue
        k = ch.dates.searchsorted(sd, side="right")          # 신호 다음 거래일 시가
        n = len(ch.c)
        if k >= n - 1: continue
        o, c = ch.o, ch.c; ent = o[k]
        if not ent > 0: continue
        hi = min(k + p["H"], n - 1)
        pb, rsi = ch.ind(p["bn"], p["bk"], p["rn"])
        seg = slice(k, hi + 1); cs = c[seg]
        ex = np.zeros(len(cs), bool)
        with np.errstate(invalid="ignore"):
            if p["b_hi"] is not None: ex |= pb[seg] >= p["b_hi"]
            if p["r_hi"] is not None: ex |= rsi[seg] >= p["r_hi"]
            if p["ma"] is not None: ex |= cs < ch.ma[p["ma"]][seg]
            if p["trail"] is not None: ex |= cs <= np.maximum.accumulate(cs) * (1 + p["trail"])
        ex[:p["mh"] - 1] = False                           # 최소 보유 전에는 지표 매도 무시
        if p["sl"] is not None: ex |= cs <= ent * (1 + p["sl"])
        hit = np.flatnonzero(ex)
        e = k + (hit[0] if len(hit) else len(cs) - 1)
        sell = e + 1 < n
        rr = np.empty(e - k + 1 + (1 if sell else 0))
        rr[0] = c[k] / ent - 1
        if e > k: rr[1:e - k + 1] = c[k + 1:e + 1] / c[k:e] - 1
        if sell: rr[-1] = o[e + 1] / c[e] - 1
        rr[-1] -= COST
        last = e + 1 if sell else e
        ids = cal_id[ch.dates[k:last + 1]]
        gsum[ids] += rr; gcnt[ids] += 1
        res.append((ids[0], np.prod(1 + rr) - 1, last - k + 1))
    return gsum, gcnt, res


def stats(gsum, gcnt, res, cal, per):
    a, b = per; m = np.array([a <= str(d)[:10] <= b for d in cal])
    r = np.where(gcnt > 0, gsum / np.maximum(gcnt, 1), 0.0)[m]
    if len(r) < 5: return {}
    eq = np.cumprod(1 + r); yrs = len(r) / 245
    idx = np.flatnonzero(m); tr = [(x[1], x[2]) for x in res if idx[0] <= x[0] <= idx[-1]]
    return dict(cagr=(eq[-1] ** (1 / yrs) - 1) * 100, sharpe=(r.mean() / r.std() * np.sqrt(245) if r.std() > 0 else np.nan),
                mdd=(eq / np.maximum.accumulate(eq) - 1).min() * 100, n=len(tr), win=np.mean([x[0] > 0 for x in tr]) * 100 if tr else np.nan,
                avg=np.mean([x[0] for x in tr]) * 100 if tr else np.nan, hold=np.mean([x[1] for x in tr]) if tr else np.nan, active=(gcnt[m] > 0).mean() * 100)


def work(g):
    t0 = time.time()
    d, pr, regs = load(g)
    trades = list(zip(d.code, d.date))
    cal = sorted({x for ch in pr.values() for x in ch.dates}); cal = pd.DatetimeIndex(cal)
    cal_id = pd.Series(np.arange(len(cal)), index=cal)
    class CI:                                              # DatetimeIndex → 번호
        def __getitem__(self, ix): return cal_id.loc[ix].to_numpy()
    ci = CI()
    rng = np.random.RandomState(7)
    base = dict(bn=20, bk=2.0, b_hi=None, rn=14, r_hi=None, ma=None, trail=None, sl=None, mh=1, H=20, reg=None)
    rows = []
    for p in [base] + [space(rng) for _ in range(NSAMP)]:
        gs, gc, res = simulate(trades, pr, regs, ci, len(cal), p)
        row = dict(p)
        for k, per in PER.items():
            for kk, vv in stats(gs, gc, res, cal, per).items(): row[f"{k}_{kk}"] = vv
        rows.append(row)
    pd.DataFrame(rows).to_pickle(T + f"Q3_{g}.pkl")
    print(g, "완료", len(rows), "picks", len(trades), f"{time.time() - t0:.0f}초", flush=True)


if __name__ == "__main__":
    gs = sys.argv[1:] or list(CFG)
    with ProcessPoolExecutor(len(gs)) as ex:
        list(ex.map(work, gs))
