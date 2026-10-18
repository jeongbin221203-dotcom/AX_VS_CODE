"""연기금(국민연금 등) 일별 순매수 내려받기: 5100 서버(/api/investor, 토스 investor-trading)에서 대형주 99종목의 2019-04~ 일별 투자자별 매매(연기금·기관·외국인·개인).
python q36_fetch.py → %TEMP%/investor/<code>.json ([date, 연기금 순매수, 연기금 매수, 연기금 매도, 기관 순매수, 외국인 순매수, 개인 순매수] 수량)"""
import os, sys, json, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
OUT = os.path.join(os.environ["TEMP"], "investor")
codes = list(json.load(open(os.environ.get("CAND_FILE") or os.path.join(os.environ["TEMP"], "rotation_candles.json"), encoding="utf-8"))["stocks"])
def get(q, tries=6):
    for t in range(tries):
        try: return json.load(urllib.request.urlopen("http://127.0.0.1:5100/api/investor?" + q, timeout=40))["result"]
        except urllib.error.HTTPError as e:
            time.sleep(1.5 * (t + 1))
        except Exception:
            time.sleep(1.5 * (t + 1))
    return None
def one(code):
    path = os.path.join(OUT, code + ".json")
    if os.path.exists(path): return code, -1
    rows = []; until = None
    for _ in range(40):
        r = get(f"symbol={code}&count=100" + (f"&until={until}" if until else ""))
        if not r or not r.get("records"): break
        for x in r["records"]:
            b = (x.get("institution") or {}).get("breakdown") or {}; pf = b.get("pensionFund") or {}
            f = lambda d: float((d or {}).get("netBuyVolume") or 0)
            rows.append([x["date"], float(pf.get("netBuyVolume") or 0), float(pf.get("buyVolume") or 0), float(pf.get("sellVolume") or 0), f(x.get("institution")), f(x.get("foreigner")), f(x.get("individual"))])
        until = r.get("nextUntil")
        if not until or rows[-1][0] < "2019-04-01": break
        time.sleep(0.15)
    json.dump(rows, open(path, "w")); return code, len(rows)
t0 = time.time()
with ThreadPoolExecutor(4) as ex:
    for code, n in ex.map(one, codes): print(code, n, f"{time.time() - t0:.0f}초", flush=True)
