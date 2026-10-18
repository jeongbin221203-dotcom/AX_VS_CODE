"""안정성 보강 시험(2026-10-18): 20일 보유(AI 점수 상위)를 기본으로, 시장 동향(지수가 N일선 아래) 때 매수 중단/절반 비중, 보유 중 지수가 무너지면 정리, 큰 손절만 얹어 수익·낙폭 비교.
실행: python tools/research/q4.py → %TEMP%/stk/Q4_<그룹>.pkl"""
import os, sys, itertools, numpy as np, pandas as pd
from concurrent.futures import ProcessPoolExecutor
sys.path.insert(0, os.getcwd()); sys.path.insert(0, os.path.dirname(__file__))
import q3
from q3 import T, COST, PER, CFG


def simulate(trades, pr, regs, cal_id, ncal, p):
    gsum = np.zeros(ncal); gcnt = np.zeros(ncal); res = []
    rcache = {}
    for code, sd in trades:
        ch = pr.get(code)
        if ch is None: continue
        w = 1.0
        if p["reg"] is not None:
            r = regs[p["reg"]]
            if sd not in r.index: continue
            if not bool(r.loc[sd]):
                if p["w_bad"] <= 0: continue
                w = p["w_bad"]
        k = ch.dates.searchsorted(sd, side="right"); n = len(ch.c)
        if k >= n - 1: continue
        o, c = ch.o, ch.c; ent = o[k]
        if not ent > 0: continue
        hi = min(k + p["H"], n - 1); seg = slice(k, hi + 1); cs = c[seg]
        ex = np.zeros(len(cs), bool)
        if p["sl"] is not None: ex |= cs <= ent * (1 + p["sl"])
        if p["regexit"] and p["reg"] is not None:
            key = (code, p["reg"])
            if key not in rcache: rcache[key] = regs[p["reg"]].reindex(ch.dates, method="ffill").fillna(True).to_numpy(bool)
            ex |= ~rcache[key][seg]
            ex[:p["mh"] - 1] = False
        hit = np.flatnonzero(ex); e = k + (hit[0] if len(hit) else len(cs) - 1); sell = e + 1 < n
        rr = np.empty(e - k + 1 + (1 if sell else 0)); rr[0] = c[k] / ent - 1
        if e > k: rr[1:e - k + 1] = c[k + 1:e + 1] / c[k:e] - 1
        if sell: rr[-1] = o[e + 1] / c[e] - 1
        rr[-1] -= COST; last = e + 1 if sell else e
        ids = cal_id[ch.dates[k:last + 1]]
        gsum[ids] += w * rr; gcnt[ids] += w
        res.append((ids[0], np.prod(1 + rr) - 1, last - k + 1))
    return gsum, gcnt, res


def work(g):
    d, pr, regs = q3.load(g)
    trades = list(zip(d.code, d.date))
    cal = pd.DatetimeIndex(sorted({x for ch in pr.values() for x in ch.dates})); cid = pd.Series(np.arange(len(cal)), index=cal)
    class CI:
        def __getitem__(self, ix): return cid.loc[ix].to_numpy()
    rows = []
    for H, sl, reg, wb, rx, mh in itertools.product((20, 30), (None, -0.15, -0.2), (None, 60, 120, 200), (0.0, 0.5), (False, True), (1, 5)):
        if reg is None and (wb or rx): continue
        if not rx and mh == 5: continue
        p = dict(H=H, sl=sl, reg=reg, w_bad=wb, regexit=rx, mh=mh)
        gs, gc, res = simulate(trades, pr, regs, CI(), len(cal), p)
        row = dict(p)
        for k, per in PER.items():
            for kk, vv in q3.stats(gs, gc, res, cal, per).items(): row[f"{k}_{kk}"] = vv
        rows.append(row)
    pd.DataFrame(rows).to_pickle(T + f"Q4_{g}.pkl"); print(g, "완료", len(rows), flush=True)


if __name__ == "__main__":
    gs = sys.argv[1:] or list(CFG)
    with ProcessPoolExecutor(len(gs)) as ex: list(ex.map(work, gs))
