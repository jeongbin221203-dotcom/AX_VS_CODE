"""5100 앱 규칙(현대차·삼성전기·LG에너지솔루션에 맞춘 규칙)을 대형주 전체에 같은 값으로 적용했을 때의 성과와 구성 요소별 기여(하나씩 끄기).
python q7.py → 표 출력"""
import os, sys, json, subprocess, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
import q3
T = os.environ["TEMP"] + "/stk/"; COST = 0.006
C = json.load(open(T + "q5/candles.json", encoding="utf-8"))
C = {k: v for k, v in C.items() if v["g"] == "대형주"}
EXTRA = [c for c in ("005380", "009150", "373220") if c not in C]
if EXTRA:
    from core import service
    for c in EXTRA:
        df = service.load_prices(c); df = df[df.index >= "2013-01-01"]
        t = ((df.index.astype("datetime64[s]").astype("int64")) - 9 * 3600).tolist()
        C[c] = {"g": "x", "c": [[a, float(o), float(h), float(l), float(cl), float(v)] for a, o, h, l, cl, v in zip(t, df.open, df.high, df.low, df.close, df.volume)]}
json.dump(C, open(T + "q5/large.json", "w"))
SETS = {"기본(5100 앱, 종목별 제외 구간 없음)": {},
        "돌파 끔": {"useSurge": False}, "T6 과대낙폭 끔": {"useDeep": False}, "고점 매도 보류(OBV) 끔": {"holdMode": None}, "추세 보유(20일선 2일 이탈) 끔": {"trendExit": None},
        "횡보 후 상단 재터치 매도 끔": {"retouch": False}, "장악형 매도 끔": {"useEngulf": False}, "큰 음봉 매도 끔": {"useBigBear": False}, "과열 RSI85 매도 끔": {"useHot": False},
        "횡보 이탈 매도 끔": {"breakBand": 0.0}, "횡보장 매수금지 끔": {"chopBlock": None}, "신규상장 400봉 대기 끔": {"warmup": 0},
        "매수 T1만": {"only": ["T1"]}, "매수 T3만": {"only": ["T3"]}, "매수 T4만": {"only": ["T4"]}, "매수 T6만": {"only": ["T6"]}, "매수 돌파만": {"only": ["돌파"]}}
json.dump(SETS, open(T + "q5/sets.json", "w"))
subprocess.run(["node", T + "q5/run3.mjs", "large.json", "sets.json", "out3.json"], check=True)
R = json.load(open(T + "q5/out3.json", encoding="utf-8"))
cal = pd.DatetimeIndex(sorted({pd.Timestamp(x[0] + 9 * 3600, unit="s").normalize() for v in C.values() for x in v["c"]})); cid = pd.Series(np.arange(len(cal)), index=cal)
pers = {"A": ("2017-01-01", "2021-12-31"), "V": q3.PER["V"], "T": q3.PER["T"]}
def evaluate(sig, only_codes=None):
    gsum = np.zeros(len(cal)); gcnt = np.zeros(len(cal)); res = []
    for code, ss in sig.items():
        if only_codes and code not in only_codes: continue
        a = np.array(C[code]["c"]); dates = pd.DatetimeIndex(pd.to_datetime(a[:, 0] + 9 * 3600, unit="s").normalize()); o, c = a[:, 1], a[:, 4]; n = len(c); k = None; tag = None
        for s in ss + [None]:
            if s is not None and s["side"] == "buy": k = s["i"] + 1; tag = s["tag"]; continue
            if k is None: continue
            e = s["i"] if s else n - 1
            if k >= n or k > e: k = None; continue
            sell = s is not None and e + 1 < n
            rr = np.empty(e - k + 1 + (1 if sell else 0)); rr[0] = c[k] / o[k] - 1
            if e > k: rr[1:e - k + 1] = c[k + 1:e + 1] / c[k:e] - 1
            if sell: rr[-1] = o[e + 1] / c[e] - 1
            if s is not None: rr[-1] -= COST
            last = e + 1 if sell else e
            ids = cid.loc[dates[k:last + 1]].to_numpy(); gsum[ids] += rr; gcnt[ids] += 1
            res.append((ids[0], np.prod(1 + rr) - 1, last - k + 1, code, tag, dates[k], dates[last], s["why"] if s else "보유중")); k = None
    return gsum, gcnt, res
if __name__ == "__main__":
    big = {c for c, v in C.items() if v["g"] == "대형주"}
    print("구성: 하나씩 끄기 / 하나만 쓰기 (대형주 표본 90종목, 비교용 연수익(낙폭) / 승률)")
    for name, sig in R.items():
        gs, gc, res = evaluate(sig, big)
        row = []
        for k, per in pers.items():
            st = q3.stats(gs, gc, res, cal, per); row.append(f"{k}: {st['cagr']:+.1f}% ({st['mdd']:.0f}) 샤프{st['sharpe']:.2f} n{int(st['n'])} 승{st['win']:.0f}%")
        print(f"{name:34s} | " + " | ".join(row))
