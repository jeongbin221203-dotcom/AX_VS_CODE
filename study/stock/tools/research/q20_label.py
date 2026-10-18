"""다음 거래일 수익률 라벨(2026-10-18): 표본 행(종목×날짜)마다 다음 거래일의 시가→종가(r1o), 종가→종가(r1c), 종가→다음 시가(gap)."""
import os, sqlite3, numpy as np, pandas as pd
T = os.environ["TEMP"] + "/stk/"
D = pd.read_pickle(T + "D6g.pkl")[["code", "date"]]
con = sqlite3.connect("data/stock.db")
P = pd.read_sql("select code,date,open,close from prices where date>='2015-06-01'", con)
P = P.sort_values(["code", "date"])
g = P.groupby("code")
P["n_open"] = g["open"].shift(-1); P["n_close"] = g["close"].shift(-1)
P["r1o"] = P.n_close / P.n_open - 1; P["r1c"] = P.n_close / P.close - 1; P["gap"] = P.n_open / P.close - 1
M = D.merge(P[["code", "date", "r1o", "r1c", "gap"]], on=["code", "date"], how="left")
M.to_pickle(T + "D1.pkl")
print(len(M), M[["r1o", "r1c", "gap"]].notna().mean().round(3).to_dict(), M[["r1o", "r1c"]].describe().round(4).to_dict())
