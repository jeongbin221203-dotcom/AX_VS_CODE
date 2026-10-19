"""JB차트 AI 점수(core/picks.ai_chart 와 같은 계산)를 여러 종목에 대해 미리 계산해 저장 — 매도 규칙 비교(jb_exit.py)용.
python tools/research/jb_scores.py [최대 종목 수]  → data/research/jb_scores.pkl {code: (dates, open, high, low, close, volume, score, oos, kq)}
대상: data/caps-kr(시총 순위) 가 있으면 시총 1,000억 이상 보통주, 없으면 DB 의 모든 종목(500봉 이상)."""
import glob
import json
import os
import pickle
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core import db, picks, predict, service  # noqa: E402

OUT = os.path.join("data", "research", "jb_scores.pkl")
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 0


def universe():
    caps = sorted(glob.glob(os.path.join("..", "stock_html", "data", "caps-kr-*.json")))
    if caps:
        it = json.load(open(caps[-1], encoding="utf-8"))["items"]
        lo = 0 if os.environ.get("JB_JUNK") else 1e11          # JB_JUNK=1: 잡주(시총 1,000억 미만)도 포함
        return [k for k, v in sorted(it.items(), key=lambda kv: kv[1][1]) if v[0] >= lo]
    with db.get_conn() as c:
        return [r["code"] for r in c.execute("SELECT code FROM symbols")]


def main():
    obj, wf = picks.load_models(), picks.load_wf()
    if obj is None or "chart" not in obj:
        sys.exit("AI 모델이 없습니다")
    mkt = predict._mkt()
    with db.get_conn() as c:
        mk = {r["code"]: (r["market"] or "") for r in c.execute("SELECT code, market FROM symbols")}
    codes = universe()
    if LIMIT:
        codes = codes[:LIMIT]
    out = {}
    if os.path.exists(OUT):
        out = pickle.load(open(OUT, "rb"))
    t0 = time.time()
    for k, code in enumerate(codes):
        if code in out:
            continue
        df = service.load_prices(code)
        if len(df) < 500:
            continue
        kq = 1.0 if mk.get(code, "").startswith("KOSDAQ") else 0.0
        try:
            F = picks.stock_frame(df, kq, mkt)
            score, oos = picks._score_series(df, F, obj, wf)
        except Exception as e:  # 한 종목 실패는 건너뛴다
            print(code, "실패", type(e).__name__, e)
            continue
        out[code] = (df.index.strftime("%Y-%m-%d").to_numpy(), df["open"].to_numpy(float), df["high"].to_numpy(float), df["low"].to_numpy(float),
                     df["close"].to_numpy(float), df["volume"].to_numpy(float), np.asarray(score, float), np.asarray(oos, bool), kq)
        if (k + 1) % 25 == 0:
            os.makedirs(os.path.dirname(OUT), exist_ok=True)
            pickle.dump(out, open(OUT, "wb"))
            print(f"{k + 1}/{len(codes)} 종목, {time.time() - t0:.0f}초", flush=True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    pickle.dump(out, open(OUT, "wb"))
    print("끝", len(out), "종목", f"{time.time() - t0:.0f}초")


if __name__ == "__main__":
    main()
