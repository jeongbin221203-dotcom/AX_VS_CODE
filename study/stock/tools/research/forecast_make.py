"""다음 거래일(2026-10-19) 예측을 만들어 data/forecasts/<날짜>.json 에 저장 — 내일 장 마감 뒤 forecast_verify.py 로 실제와 비교한다.
과거 데이터(표본 종목×날짜, 다음 거래일 시가→종가 수익률 라벨)로 학습한 모델을 쓰고, 기준일 종가까지의 지표로 다음 거래일을 예측한다.
python tools/research/forecast_make.py  (예측은 한 번 저장하면 덮어쓰지 않는다 — 결과를 보고 고치는 것을 막기 위해)"""
import os, sys, json, hashlib, time
from datetime import datetime
import numpy as np, pandas as pd, joblib

sys.path.insert(0, os.getcwd())
from sklearn.ensemble import HistGradientBoostingClassifier as HC, HistGradientBoostingRegressor as HR
from core import picks, db, ai, backtest as bt

T = os.environ["TEMP"] + "/stk/"
TARGET = next((x for x in sys.argv[1:] if not x.startswith("--")), "2026-10-19")
OUT = os.path.join("data", "forecasts", f"{TARGET}.json")

MODEL = os.path.join("data", "forecasts", "model.joblib")
GMAP = {"대형주": 0, "중형주": 1, "소형주": 2, "잡주": 3}


def get_model(retrain=False):
    """학습한 모델을 data/forecasts/model.joblib 에 보관하고 다음부터는 다시 쓴다(매일 다시 학습하지 않음)."""
    if os.path.exists(MODEL) and not retrain:
        return joblib.load(MODEL)
    t0 = time.time()
    D = pd.read_pickle(T + "D6g.pkl"); L = pd.read_pickle(T + "D1.pkl")
    D = D.merge(L, on=["code", "date"], how="left")
    cols0 = joblib.load(T + "m1_sets.joblib")["sets"]["C +차트 지표 전체"]
    rk = [c for c in cols0 if D[c].nunique() > 10]
    R = D.groupby("date")[rk].rank(pct=True).astype("float32"); R.columns = [c + "_r" for c in rk]
    D = pd.concat([D, R], axis=1)
    D["gid"] = D.grp.map(GMAP)
    cols = cols0 + list(R.columns) + ["gid"]
    D = D.dropna(subset=["r1o"]); D = D[D.yr >= 2016]
    D["y_rank"] = D.groupby("date")["r1o"].rank(pct=True).astype("float32"); D["up"] = (D.r1o > 0).astype(int)
    tr = D.sample(600000, random_state=0) if len(D) > 600000 else D
    X = tr[cols].astype("float32")
    reg = HR(max_iter=150, learning_rate=0.06, max_depth=6, min_samples_leaf=200, l2_regularization=2.0, random_state=0).fit(X, tr.y_rank)
    clf = HC(max_iter=150, learning_rate=0.06, max_depth=5, random_state=0).fit(X, tr.up)
    obj = {"reg": reg, "clf": clf, "cols": cols, "rk": rk, "trained_through": D.date.max(), "rows": len(tr), "trained_at": datetime.now().isoformat(timespec="seconds")}
    joblib.dump(obj, MODEL)
    print("학습", len(tr), "행, 라벨 마지막", obj["trained_through"], f"{time.time() - t0:.0f}초", flush=True)
    return obj


if __name__ == "__main__":
    t0 = time.time()
    if os.path.exists(OUT):
        print("이미 예측이 저장되어 있습니다(덮어쓰지 않음):", OUT); sys.exit(0)
    M = get_model("--retrain" in sys.argv)
    reg, clf, cols, rk, trained_through, gmap = M["reg"], M["clf"], M["cols"], M["rk"], M["trained_through"], GMAP
    # 오늘(DB 마지막 거래일) 행
    with db.get_conn() as c:
        codes = [r[0] for r in c.execute("SELECT code FROM symbols")]
    kq = picks._kosdaq_map()
    rows = picks._pool(picks._work_today, [(c, kq.get(c, 0.0)) for c in codes], 0)
    Tn = pd.concat(rows, ignore_index=True); asof = Tn["date"].max(); Tn = Tn[Tn["date"] == asof].reset_index(drop=True)
    info = picks._mcap_info(); Tn = Tn.merge(info, on="code", how="left")
    Tn["mcap_est"] = Tn["marcap"] * Tn["close"] / Tn["close_now"]; Tn = Tn.dropna(subset=["mcap_est"]).reset_index(drop=True)
    Tn["grp"] = picks.assign_group(Tn)
    RT = Tn.groupby("date")[rk].rank(pct=True).astype("float32"); RT.columns = [c + "_r" for c in rk]
    Tn = pd.concat([Tn, RT], axis=1); Tn["gid"] = Tn.grp.map(gmap)
    for c in cols:
        if c not in Tn.columns: Tn[c] = np.nan
    Xt = Tn[cols].astype("float32")
    Tn["p_rank"] = reg.predict(Xt); Tn["p_up"] = clf.predict_proba(Xt)[:, 1]
    Tn["score"] = (Tn.p_rank.rank(pct=True) + Tn.p_up.rank(pct=True)) / 2
    Tn = Tn.sort_values("score", ascending=False).reset_index(drop=True)
    stocks = {r.code: {"name": r.name, "grp": r.grp, "p_up": round(float(r.p_up), 4), "p_rank": round(float(r.p_rank), 4), "score": round(float(r.score), 4), "close": float(r.close)} for r in Tn.itertuples()}
    summ = {"종목 수": int(len(Tn)), "평균 상승확률": round(float(Tn.p_up.mean()), 4), "상승확률>0.5 비율": round(float((Tn.p_up > 0.5).mean()), 4)}
    for g in gmap:
        s = Tn[Tn.grp == g]
        summ[g] = {"n": int(len(s)), "평균 상승확률": round(float(s.p_up.mean()), 4), "상승확률>0.5 비율": round(float((s.p_up > 0.5).mean()), 4)}
    top = {g: [dict(code=r.code, name=r.name, p_up=round(float(r.p_up), 3), score=round(float(r.score), 3)) for r in Tn[Tn.grp == g].head(10).itertuples()] for g in gmap}
    bot = {g: [dict(code=r.code, name=r.name, p_up=round(float(r.p_up), 3), score=round(float(r.score), 3)) for r in Tn[Tn.grp == g].tail(10).itertuples()] for g in gmap}
    body = {"target_date": TARGET, "asof": asof, "trained_labels_through": trained_through, "generated": datetime.now().isoformat(timespec="seconds"),
            "note": "p_up = 다음 거래일 시가→종가가 플러스일 확률(모델 추정), score = 그날 전체 종목 안 순위 점수(0~1). 과거 걸어가며 검증에서 방향 적중률은 약 50~55% 수준이라 개별 종목 예측은 약합니다.",
            "summary": summ, "top": top, "bottom": bot, "stocks": stocks}
    txt = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    open(OUT, "w", encoding="utf-8").write(txt)
    open(OUT + ".sha256", "w").write(hashlib.sha256(txt.encode("utf-8")).hexdigest())
    print("저장", OUT, len(stocks), "종목, 기준일", asof, "→", TARGET, f"{time.time() - t0:.0f}초")
    print(json.dumps(summ, ensure_ascii=False))
