"""대형주: 지표 규칙(5100 앱) + AI 점수 연동 방식 비교(2026-10-18).
연동 방식: (1) 매수 필터 = 그룹 안 AI 순위가 기준 이상일 때만 매수, (2) AI 청산 = 보유 중 AI 순위가 기준 아래로 떨어지면 다음 날 시가에 매도,
(3) 비중 = 매수 때 AI 순위만큼 비중을 둠. AI 점수는 걸어가며 학습한 2022~2026년뿐이라 선택은 2022~23, 시험은 2024~26.
GROUP=대형주 python q15.py"""
import os, sys, json, itertools, numpy as np, pandas as pd

os.environ.setdefault("GROUP", "대형주")
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
import q11
from q11 import T, C, DATES, ARR, merge, PERS, q3, cal, cid

THRS = (0.0, 0.3, 0.5, 0.6, 0.7, 0.8, 0.9)
AI_COL = "r_ens"


def ai_daily():
    A = pd.read_pickle(T + "AO.pkl"); A["date"] = pd.to_datetime(A["date"]); A = A[A.code.isin(C)]
    out = {}; ages = {}
    for code in C:
        dates = DATES[code]; g = A[A.code == code].set_index("date")[AI_COL].sort_index()
        if g.empty: out[code] = np.full(len(dates), np.nan); continue
        u = g.index.union(dates); v = g.reindex(u).ffill().reindex(dates)
        last = pd.Series(g.index, index=g.index).reindex(u).ffill().reindex(dates); age = (dates - pd.DatetimeIndex(last)).days
        arr = np.array(v.to_numpy(float)); arr[(np.array(age) > 7) | (dates < "2022-01-01")] = np.nan; out[code] = arr
    return out


def build_ranges(ai):
    GR = {}
    for thr in THRS:
        if thr == 0: continue
        out = {}
        for code in C:
            dates = DATES[code]; sel = dates >= "2022-01-01"; a = ai[code]
            bad = dates[sel & ~(a >= thr)]
            out[code] = merge(dates, bad)
        GR[f"g{thr}"] = out
    json.dump(GR, open(T + "q5/gate_ranges.json", "w"))


def evaluate(sig, ai, exit_x=None, weight=None, min_hold=3):
    gsum = np.zeros(len(cal)); gcnt = np.zeros(len(cal)); res = []
    for code, ss in sig.items():
        a = ARR[code]; dates = DATES[code]; o, c = a[:, 1], a[:, 4]; n = len(c); k = None; av = ai[code]
        for s in ss + [None]:
            if s is not None and s["side"] == "buy":
                k = s["i"] + 1; continue
            if k is None: continue
            e = s["i"] if s else n - 1
            if k >= n or k > e:
                k = None; continue
            closed = s is not None
            if exit_x is not None:
                seg = av[k + min_hold:e + 1]
                hit = np.flatnonzero(seg < exit_x)
                if len(hit): e = k + min_hold + hit[0]; closed = True
            sell = closed and e + 1 < n
            w = 1.0
            if weight == "lin": w = float(av[k - 1] if k >= 1 and av[k - 1] == av[k - 1] else 0.5)
            elif weight == "sq": w = float(av[k - 1] ** 2 if k >= 1 and av[k - 1] == av[k - 1] else 0.25)
            rr = np.empty(e - k + 1 + (1 if sell else 0)); rr[0] = c[k] / o[k] - 1
            if e > k: rr[1:e - k + 1] = c[k + 1:e + 1] / c[k:e] - 1
            if sell: rr[-1] = o[e + 1] / c[e] - 1
            if closed: rr[-1] -= q11.COST
            last = e + 1 if sell else e
            ids = cid.loc[dates[k:last + 1]].to_numpy(); gsum[ids] += w * rr; gcnt[ids] += w
            res.append((ids[0], np.prod(1 + rr) - 1, last - k + 1)); k = None
    return gsum, gcnt, res


if __name__ == "__main__":
    ai = ai_daily(); build_ranges(ai)
    sets = {}
    for var, ov in (("앱기본", {}), ("T1T3T6", {"only": ["T1", "T3", "T6"]})):
        for thr in THRS:
            sets[f"{var}|{thr}"] = dict(ov, **({"__gate": thr} if thr else {}))
    R = None
    json.dump(sets, open(T + "q5/sets15.json", "w"))
    import subprocess
    subprocess.run(["node", T + "q5/run4.mjs", f"cand_{os.environ['GROUP']}.json", "sets15.json", "out15.json"], check=True)
    R = json.load(open(T + "q5/out15.json", encoding="utf-8"))
    rows = []
    for nm, sig in R.items():
        var, thr = nm.split("|")
        for ex, wt in itertools.product((None, 0.2, 0.35, 0.5), (None, "lin", "sq")):
            gs, gc, res = evaluate(sig, ai, ex, wt)
            row = dict(var=var, thr=float(thr), ai_exit=ex, weight=wt)
            for k, per in PERS.items():
                for kk, vv in q3.stats(gs, gc, res, cal, per).items(): row[f"{k}_{kk}"] = vv
            rows.append(row)
    D = pd.DataFrame(rows); D.to_pickle(T + "Q15.pkl")
    pd.set_option("display.width", 250)
    cols = ["var", "thr", "ai_exit", "weight", "V_cagr", "V_mdd", "V_sharpe", "T_cagr", "T_mdd", "T_sharpe", "T_win", "T_n", "T1_cagr", "T2_cagr", "T3_cagr"]
    print("기준(AI 없음):"); print(D[(D.thr == 0) & D.ai_exit.isna() & D.weight.isna()][cols].round(2).to_string(index=False))
    ok = D[(D.V_n >= 20) & (D.T_n >= 30)].copy()
    print("\n검증 샤프 상위 10 (선택 = 2022~23):"); print(ok.sort_values("V_sharpe", ascending=False).head(10)[cols].round(2).to_string(index=False))
    t = ok.sort_values("V_sharpe", ascending=False).head(20)
    print("상위20 평균 시험: CAGR %.1f 낙폭 %.0f 샤프 %.2f | 전체 평균 %.1f | 상관(검증샤프-시험샤프) %.2f" % (t.T_cagr.mean(), t.T_mdd.mean(), t.T_sharpe.mean(), ok.T_cagr.mean(), ok[["V_sharpe", "T_sharpe"]].corr().iloc[0, 1]))
    print("\n연동 방식별 평균 시험 CAGR / 검증 CAGR:")
    for c in ("thr", "ai_exit", "weight", "var"):
        print(c, ok.assign(v=ok[c].astype(str)).groupby("v")[["V_cagr", "T_cagr", "T_mdd", "T_sharpe"]].mean().round(1).to_dict("index"))
