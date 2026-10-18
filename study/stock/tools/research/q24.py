"""세 갈래 라벨(2026-10-18): 표본 행(종목×날짜)에서 종가 기준으로 이후 60거래일 안에 +U% 위로(고가) 먼저 가는지 −D% 아래로(저가) 먼저 가는지.
(10%/7%)와 (20%/10%). 같은 날 둘 다면 하락 먼저. 둘 다 없으면 만기(60일 뒤 종가 수익). → %TEMP%/stk/L24.pkl"""
import os, sys, time, numpy as np, pandas as pd
sys.path.insert(0, os.getcwd())
from concurrent.futures import ProcessPoolExecutor
T = os.environ["TEMP"] + "/stk/"
H = 60
PAIRS = ((0.10, 0.07), (0.20, 0.10))


def work(args):
    code, dates = args
    try:
        from core import service
        df = service.load_prices(code)
        idx = df.index.strftime("%Y-%m-%d"); pos = {d: i for i, d in enumerate(idx)}
        c, h, l = df.close.to_numpy(float), df.high.to_numpy(float), df.low.to_numpy(float); n = len(c)
        rows = []
        for d in dates:
            i = pos.get(d)
            if i is None or i + 20 >= n or not c[i] > 0: continue
            j = min(i + H, n - 1)
            hh = h[i + 1:j + 1] / c[i] - 1; ll = l[i + 1:j + 1] / c[i] - 1
            rec = [code, d, c[j] / c[i] - 1, j - i, hh.max(), ll.min()]
            for U, Dn in PAIRS:
                up = np.flatnonzero(hh >= U); dn = np.flatnonzero(ll <= -Dn)
                fu = up[0] if len(up) else 10 ** 6; fd = dn[0] if len(dn) else 10 ** 6
                if fu == fd == 10 ** 6: rec += [np.nan, 0]
                elif fd <= fu: rec += [0.0, fd + 1]
                else: rec += [1.0, fu + 1]
            rows.append(rec)
        return rows
    except Exception:
        return None


if __name__ == "__main__":
    t0 = time.time()
    D = pd.read_pickle(T + "D6g.pkl")[["code", "date", "yr"]]; D = D[D.yr >= 2016]
    by = {c: list(g.date) for c, g in D.groupby("code")}
    with ProcessPoolExecutor(6) as ex:
        res = [r for r in ex.map(work, list(by.items()), chunksize=10) if r]
    cols = ["code", "date", "end_ret", "end_n", "mfe", "mae", "y10", "t10", "y20", "t20"]
    L = pd.DataFrame([r for rr in res for r in rr], columns=cols); L.to_pickle(T + "L24.pkl")
    print(len(L), f"{time.time() - t0:.0f}초", L[["y10", "y20"]].describe().loc[["count", "mean"]].round(3).to_dict())
