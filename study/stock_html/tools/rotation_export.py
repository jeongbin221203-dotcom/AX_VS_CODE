"""대형주 순환 매매용 일봉 내보내기: data/caps-kr-*.json 의 시총 순위 1~100위 종목의 일봉을 study/stock 의 DB에서 읽어 임시 JSON 으로 저장.
python tools/rotation_export.py [large|mid] [출력 경로]  (large = 시총 1~100위, mid = 101~300위이면서 시총 5,000억 원 이상)  → rotation_build.mjs 가 읽는다. time = KST 자정의 unix 초(앱과 같은 형식)."""
import glob, json, os, sqlite3, sys
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DB = os.environ.get("STOCK_DB", os.path.join(ROOT, "..", "stock", "data", "stock.db"))
ARGS = [a for a in sys.argv[1:]]
TIER = ARGS.pop(0) if ARGS and ARGS[0] in ("large", "mid") else "large"
OUT = ARGS[0] if ARGS else os.path.join(os.environ.get("TEMP", "."), "rotation_candles.json" if TIER == "large" else "rotation_candles_mid.json")
caps = json.load(open(sorted(glob.glob(os.path.join(ROOT, "data", "caps-kr-*.json")))[-1], encoding="utf-8"))
ranked = sorted(((v[1], k) for k, v in caps["items"].items()))
if TIER == "large": top = ranked[:100]
else: top = [(r, k) for r, k in ranked if 100 < r <= 300 and caps["items"][k][0] >= 5e11]
con = sqlite3.connect(DB)
names = dict(con.execute("select code,name from symbols").fetchall())
out = {}
for rank, code in top:
    df = pd.read_sql("select date,open,high,low,close,volume from prices where code=? and date>='2013-01-01' order by date", con, params=[code])
    if len(df) < 300: continue
    t = ((pd.to_datetime(df["date"]).astype("datetime64[s]").astype("int64")) - 9 * 3600).tolist()
    out[code] = {"name": names.get(code, code), "rank": rank, "c": [[a, float(o), float(h), float(l), float(c), float(v)] for a, o, h, l, c, v in zip(t, df.open, df.high, df.low, df.close, df.volume)]}
json.dump({"caps_date": caps["date"], "stocks": out}, open(OUT, "w", encoding="utf-8"))
print(len(out), "종목, 마지막 일봉", df["date"].iloc[-1], "→", OUT)
