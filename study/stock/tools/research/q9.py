import os, sys, json, subprocess, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
import q7
T = os.environ["TEMP"] + "/stk/"
json.dump({"app": {"__keepExclude": True}}, open(T + "q5/sets9.json", "w"))
subprocess.run(["node", T + "q5/run3.mjs", "large.json", "sets9.json", "out9.json"], check=True)
sig = json.load(open(T + "q5/out9.json", encoding="utf-8"))["app"]
names = {"005380": "현대차", "009150": "삼성전기", "373220": "LG에너지솔루션"}
for code, nm in names.items():
    _, _, res = q7.evaluate({code: sig[code]}, None)
    a = np.array(q7.C[code]["c"]); d0 = pd.Timestamp(a[0, 0] + 9 * 3600, unit="s")
    print(f"\n== {nm} ({code})")
    tot = 1
    for r in res:
        tot *= 1 + r[1]
        print(f"  {r[4]:3s} {r[5].date()} → {r[6].date()} {r[1]*100:+7.1f}% ({r[2]}일) 매도:{r[7]}")
    first = res[0][5]; ix = pd.DatetimeIndex(pd.to_datetime(a[:, 0] + 9 * 3600, unit="s").normalize()); c = a[:, 4]; o = a[:, 1]
    bh = c[-1] / o[ix.searchsorted(first)] - 1
    print(f"  규칙 누적 {100*(tot-1):+.0f}% (거래 {len(res)}회, 승 {sum(r[1]>0 for r in res)}) | 같은 기간({first.date()}~) 사서 보유 {bh*100:+.0f}% | 보유 일수 비중 {sum(r[2] for r in res)/(len(ix)-ix.searchsorted(first))*100:.0f}%")
big = {c for c, v in q7.C.items() if v["g"] == "대형주"}
_, _, res = q7.evaluate(sig, big)
df = pd.DataFrame(res, columns=["i", "ret", "hold", "code", "tag", "in", "out", "why"])
print("\n== 대형주 90종목 전체 거래", len(df))
print("태그별:", df.groupby("tag").ret.agg(["count", "mean", lambda s: (s > 0).mean()]).round(3).to_string())
df["bucket"] = pd.cut(df.hold, [0, 20, 60, 120, 250, 2000])
print(df.groupby("bucket", observed=True).ret.agg(["count", "mean", "median", lambda s: (s > 0).mean()]).round(3).to_string())
tot = (1 + df.ret).prod()
s = df.sort_values("ret", ascending=False)
print("수익 상위 10거래의 평균 %.0f%%, 전체 합 대비 상위10 비중(단순합) %.0f%%" % (s.ret.head(10).mean() * 100, s.ret.head(10).sum() / s.ret.sum() * 100))
print("매도 사유별:", df.groupby("why").ret.agg(["count", "mean"]).sort_values("count", ascending=False).round(3).head(10).to_string())
