"""AI 점수로 5100 앱 매수 신호를 거르기: 점수가 낮은 날은 매수 금지 구간(exclude)으로 넘겨 JS 규칙을 다시 돌린다. 점수는 2022년부터라 그 전은 거르지 않음.
python q6_gate.py <기준 분위> → %TEMP%/stk/q5/excl_<기준>.json"""
import os, sys, json, numpy as np, pandas as pd
T = os.environ["TEMP"] + "/stk/"
CFG = {"대형주": "r_ens", "중형주": "r_ens", "소형주": "r_ens", "잡주": "score"}
def merge(alld, bad):
    """연속한 거래일을 하나의 [시작, 끝] 구간으로 합친다(JS 쪽 검사가 구간 수에 비례해 느려서)."""
    pos = {d: i for i, d in enumerate(alld)}; idx = sorted(pos[d] for d in bad); out = []
    for i in idx:
        if out and i == out[-1][1] + 1: out[-1][1] = i
        else: out.append([i, i])
    return [[alld[a].strftime("%Y-%m-%d"), alld[b].strftime("%Y-%m-%d")] for a, b in out]
A = pd.read_pickle(T + "AO.pkl")
C = json.load(open(T + "q5/candles.json", encoding="utf-8"))
thr = float(sys.argv[1]); out = {}
for g, col in CFG.items():
    d = A[A.grp == g][["date", "code", col]]
    d["date"] = pd.to_datetime(d["date"])
    for code, grp in d.groupby("code"):
        if code not in C: continue
        s = grp.set_index("date")[col].sort_index()
        dates = pd.DatetimeIndex(pd.to_datetime(np.array(C[code]["c"])[:, 0] + 9 * 3600, unit="s").normalize())
        dates = dates[dates >= "2022-01-01"]
        v = s.reindex(s.index.union(dates)).ffill(limit=None)
        last = pd.Series(s.index, index=s.index).reindex(s.index.union(dates)).ffill()
        v = v.reindex(dates); age = (dates - pd.DatetimeIndex(last.reindex(dates))).days
        bad = dates[(v.fillna(-1).to_numpy() < thr) | (np.array(age) > 7)]
        out[code] = merge(dates, bad)
    # 점수가 아예 없는 종목은 2022년 이후 전부 금지
    for code, v in C.items():
        if v["g"] == g and code not in out:
            dates = pd.DatetimeIndex(pd.to_datetime(np.array(v["c"])[:, 0] + 9 * 3600, unit="s").normalize()); dates = dates[dates >= "2022-01-01"]
            out[code] = merge(dates, dates)
json.dump(out, open(T + f"q5/excl_{thr}.json", "w"))
print(thr, sum(len(v) for v in out.values()))
