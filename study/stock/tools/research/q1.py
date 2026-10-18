"""'싸게 사서 비싸게 판다' 연구용 일봉 패널(2026-10-18): 그룹별 표본 종목의 일봉 + 지표 + 그 날의 시총 그룹.
실행: STOCK_SCHEDULER=0 python tools/research/q1.py → %TEMP%/stk/Q1.pkl (dict: code -> DataFrame)"""
import os, sys, numpy as np, pandas as pd

sys.path.insert(0, os.getcwd())
from core import service, picks

T = os.environ["TEMP"] + "/stk/"
N_PER = 150
D = pd.read_pickle(T + "D6g.pkl")[["code", "date", "grp", "yr"]]
D = D[D.yr >= 2016]
share = D.groupby(["code", "grp"]).size().unstack(fill_value=0)
rng = np.random.RandomState(0)
codes = {}
for g in share.columns:
    # 그 그룹에 머문 날이 많은 종목 우선(표본에서 그룹이 자주 바뀌는 종목 제외)
    tot = share.sum(axis=1)
    cand = share.index[(share[g] / tot >= 0.6) & (tot >= 150)]
    codes[g] = list(rng.choice(cand, min(N_PER, len(cand)), replace=False))
    print(g, len(cand), "→", len(codes[g]), flush=True)
D["date"] = pd.to_datetime(D["date"])
out = {}
for g, cl in codes.items():
    for c in cl:
        df = service.load_prices(c)
        df = df[df.index >= "2015-01-01"].copy()
        if len(df) < 400:
            continue
        f = picks.extra_features(df)
        c_ = df["close"]
        f["dist20"] = c_ / c_.rolling(20).mean() - 1
        f["dist60"] = c_ / c_.rolling(60).mean() - 1
        f["ma200"] = c_.rolling(200).mean()
        x = pd.concat([df[["open", "high", "low", "close", "volume"]], f[["rsi14", "bb_pctb", "stoch_k", "cci20", "mfi14", "dist20", "dist60", "ma200", "bb_width"]]], axis=1)
        x = x[x.index >= "2016-01-01"]
        lab = D[D.code == c].sort_values("date").drop_duplicates("date").set_index("date")["grp"]
        x["grp"] = lab.reindex(x.index.union(lab.index)).ffill().reindex(x.index)
        out[c] = (g, x.dropna(subset=["open", "close"]))
pd.to_pickle(out, T + "Q1.pkl")
print("종목", len(out), {g: sum(1 for v in out.values() if v[0] == g) for g in codes})
