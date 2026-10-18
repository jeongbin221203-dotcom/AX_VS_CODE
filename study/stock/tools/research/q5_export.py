"""5100 앱 규칙 비교용: 그룹별 표본 종목의 일봉을 JSON 으로 내보낸다(time = KST 자정의 unix 초)."""
import os, sys, json, pandas as pd
sys.path.insert(0, os.getcwd())
from core import service
T = os.environ["TEMP"] + "/stk/"
P = pd.read_pickle(T + "Q1.pkl")
out = {}
for g in sys.argv[1:] or ["대형주"]:
    for c, (gg, x) in P.items():
        if gg != g: continue
        df = service.load_prices(c)
        df = df[df.index >= "2013-01-01"]
        t = ((df.index.astype("datetime64[s]").astype("int64")) - 9 * 3600).tolist()
        out[c] = {"g": g, "c": [[a, float(o), float(h), float(l), float(cl), float(v)] for a, o, h, l, cl, v in zip(t, df.open, df.high, df.low, df.close, df.volume)]}
json.dump(out, open(T + "q5/candles.json", "w"))
print(len(out))
