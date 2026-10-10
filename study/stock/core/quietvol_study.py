"""거래량은 크게 늘었는데 주가는 거의 안 움직인 날(조용한 대량 거래) — 얼마 뒤에 주가가 올랐나.

  사건 = 거래량이 직전 20일 평균의 3배 이상이면서 종가 등락이 ±3% 안(직전 20일 안에 3배 급증이 없던 첫 급증), 거래 가능 종목, 상장 250일↑.
  비교 = ① 아무 날 ② 같은 거래량 급증인데 +5%↑ 양봉 ③ 같은 급증인데 −3%↓ 음봉.
  진입 = 다음날 시가. 이후 120거래일 동안 +10·20·30·50%에 처음 닿은 날(급증 다음날이 1일째), N일 안 확률, −10% 먼저, 20·60일 안 +20% 급등일 발생.
  조건별(배수·몸통·꼬리·위치·직전 거래량·가격대·시장·다음날 반응)로 나누고, 최근 사건에 비슷한 그룹의 과거 결과를 붙인다.
"""
import json
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

import config
from core import ai, db, strategy_study as st, surge_study as sg
from core import backtest as bt

QV_PATH = config.DATA_DIR / "quietvol.json"
MIN_RATIO = 3.0
FLAT = 0.03
LOOK = sg.LOOK
HORIZ = (5, 10, 20, 40, 60, 120)
THRESH = (10, 20, 30, 50)


def _work(code):
    try:
        df = ai.service.load_prices(code)
        n = len(df)
        if n < 400:
            return None
        o, h, l, c, v = (df[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
        s = pd.Series
        with np.errstate(divide="ignore", invalid="ignore"):
            a20 = s(v).rolling(20, min_periods=20).mean().shift().to_numpy()
            vr = v / a20
            ret1 = np.r_[np.nan, c[1:] / c[:-1] - 1]
            val = s(c * v).rolling(20, min_periods=20).mean().to_numpy()
            liq = np.log10(val + 1)
            rng = np.maximum(h - l, 1e-9)
            body = (c - o) / o
            upper = (h - np.maximum(o, c)) / rng
            lower = (np.minimum(o, c) - l) / rng
            range1 = (h - l) / c
            hh250 = s(h).rolling(250, min_periods=120).max().to_numpy()
            ma224 = s(c).rolling(224, min_periods=224).mean().to_numpy()
            ma20 = s(c).rolling(20, min_periods=20).mean().to_numpy()
            vdry10 = s(v).rolling(10, min_periods=10).min().to_numpy() / a20
            vr20_60 = a20 / s(v).rolling(60, min_periods=60).mean().to_numpy()
            ret20 = c / s(c).shift(20).to_numpy() - 1
            range20 = (s(h).rolling(20, min_periods=20).max().to_numpy() - s(l).rolling(20, min_periods=20).min().to_numpy()) / c
            jump = (ret1 >= 0.20) & np.isfinite(ret1)
            fj = np.r_[jump[1:], False].astype(float)
            fwd20 = s(fj[::-1]).rolling(20, min_periods=1).max().to_numpy()[::-1].copy()
            fwd60 = s(fj[::-1]).rolling(60, min_periods=1).max().to_numpy()[::-1].copy()
            cum = np.cumsum(jump.astype(float))
            n_j250 = cum - np.r_[np.zeros(250), cum[:-250]]
        surge = (vr >= MIN_RATIO) & np.isfinite(vr)
        prior = s(surge.astype(float)).rolling(20, min_periods=1).sum().shift().fillna(0).to_numpy()
        t_all = np.arange(n)
        base_ok = (liq >= sg.LIQ_MIN) & (t_all >= 250) & (t_all + 1 + LOOK < n)
        first = surge & (prior == 0) & base_ok
        groups = {"quiet": first & (np.abs(ret1) < FLAT), "up": first & (ret1 >= 0.05), "down": first & (ret1 <= -0.03), "bs": base_ok & (t_all % sg.BASE_STEP == 0)}
        dates = df.index.strftime("%Y-%m-%d").to_numpy()
        out = {"code": code}
        feats = {"volratio": vr, "ret1": ret1, "body": body, "upper": upper, "lower": lower, "range1": range1, "nh250": c / hh250 - 1, "dist224": c / ma224 - 1, "dist20": c / ma20 - 1,
                 "vdry10": vdry10, "vr20_60": vr20_60, "ret20": ret20, "range20": range20, "price": c, "liq": liq, "n_j250": n_j250, "fwd20": fwd20, "fwd60": fwd60}
        for name, m in groups.items():
            tt = t_all[m]
            if len(tt) == 0:
                out[name] = None
                continue
            P = sg._paths(o, h, l, c, tt + 1)                        # d10·d20·d30·d50·d100, dn10, peak, r5~r120, gap
            entry = o[tt + 1]
            P["r40"] = sliding_window_view(c, LOOK)[tt + 1][:, 39] / entry - 1
            P["next_ret"] = c[tt + 1] / c[tt] - 1
            P["next_vr"] = np.nan_to_num(vr[tt + 1], nan=1.0)
            P["again5"] = s(surge.astype(float)).rolling(5, min_periods=1).max().shift(-5).to_numpy()[tt]
            for k, arr in feats.items():
                P[k] = arr[tt].astype(float)
            P["date"] = dates[tt]
            P["code"] = np.full(len(tt), code)
            if ai.MKT_DF is not None:
                m_ = ai.MKT_DF.reindex(P["date"])
                P["mkt_r60"] = m_["mkt_r60"].to_numpy(float)
                P["mkt_dd250"] = m_["mkt_dd250"].to_numpy(float)
            else:
                P["mkt_r60"] = np.full(len(tt), np.nan)
                P["mkt_dd250"] = np.full(len(tt), np.nan)
            out[name] = P
        rec = t_all[surge & (liq >= sg.LIQ_MIN) & (t_all >= 250) & (np.abs(ret1) < FLAT) & (t_all >= n - 8)]      # 최근 것은 결과가 없어도 목록에
        out["recent"] = [{"code": code, "date": dates[t], "close": float(c[t]), **{k: float(arr[t]) for k, arr in feats.items() if k not in ("fwd20", "fwd60")}} for t in rec]
        return out
    except Exception:
        return None


def _summary(D):
    if D is None or len(D["date"]) < 50:
        return None
    n = len(D["date"])
    s = {"n": int(n), "hit": {}, "days": {}, "r20": float(np.nanmean(D["r20"])), "r40": float(np.nanmean(D["r40"])), "r60": float(np.nanmean(D["r60"])), "r120": float(np.nanmean(D["r120"])),
         "peak_median": float(np.nanmedian(D["peak"])), "jump20": float(np.nanmean(D["fwd20"])), "jump60": float(np.nanmean(D["fwd60"])), "next_ret": float(np.nanmean(D["next_ret"])),
         "next_up": float(np.nanmean(D["next_ret"] > 0)), "again5": float(np.nanmean(D["again5"]))}
    for x in THRESH:
        d = D[f"d{x}"]
        s["hit"][str(x)] = {str(h): float(np.nanmean(d <= h)) for h in HORIZ}
        dd = d[~np.isnan(d)]
        s["days"][str(x)] = {"share": float(len(dd) / n), "median": float(np.median(dd)) if len(dd) else None, "q25": float(np.quantile(dd, 0.25)) if len(dd) else None, "q75": float(np.quantile(dd, 0.75)) if len(dd) else None}
    d20, dn = D["d20"], D["dn10"]
    s["dn10_first"] = float(np.mean(~np.isnan(dn) & (np.isnan(d20) | (dn <= d20))))
    s["up20_before_dn10"] = float(np.mean(~np.isnan(d20) & (np.isnan(dn) | (d20 < dn))))
    return s


def _row(name, D, m):
    m = np.asarray(m, bool)
    if m.sum() < 100:
        return None
    sub = {k: v[m] for k, v in D.items()}
    s = _summary(sub)
    if not s:
        return None
    return {"name": name, "n": s["n"], "p20_in20": s["hit"]["20"]["20"], "p20_in60": s["hit"]["20"]["60"], "p50_in120": s["hit"]["50"]["120"], "days20": s["days"]["20"]["median"],
            "dn10_first": s["dn10_first"], "r20": s["r20"], "r60": s["r60"], "jump20": s["jump20"], "next_ret": s["next_ret"]}


def _breakdown(D):
    specs = {
        "거래량 배수": [("3~5배", (D["volratio"] >= 3) & (D["volratio"] < 5)), ("5~10배", (D["volratio"] >= 5) & (D["volratio"] < 10)), ("10배 이상", D["volratio"] >= 10)],
        "캔들 모양": [("도지 (등락 ±1% 안)", np.abs(D["ret1"]) < 0.01), ("약한 양봉 (+1~+3%)", (D["ret1"] >= 0.01) & (D["ret1"] < 0.03)), ("약한 음봉 (−3~−1%)", (D["ret1"] > -0.03) & (D["ret1"] <= -0.01)),
                     ("긴 윗꼬리 (봉의 50%↑)", D["upper"] >= 0.5), ("긴 아랫꼬리 (봉의 50%↑)", D["lower"] >= 0.5), ("고저폭 작음 (3% 미만)", D["range1"] < 0.03), ("고저폭 큼 (8%↑, 장중 요동)", D["range1"] >= 0.08)],
        "주가 위치": [("52주 고점 −10% 이내", D["nh250"] >= -0.1), ("−10~−30%", (D["nh250"] < -0.1) & (D["nh250"] >= -0.3)), ("−30~−50%", (D["nh250"] < -0.3) & (D["nh250"] >= -0.5)), ("바닥권 −50% 이하", D["nh250"] < -0.5),
                   ("224일선 위", D["dist224"] > 0), ("224일선 아래", D["dist224"] <= 0), ("20일선 위", D["dist20"] > 0)],
        "직전 흐름": [("직전 10일 거래량 매우 건조 (0.3배↓)", D["vdry10"] < 0.3), ("직전 20일 −20%↓ 급락", D["ret20"] <= -0.2), ("직전 20일 ±5% 횡보", np.abs(D["ret20"]) < 0.05), ("직전 20일 +20%↑", D["ret20"] >= 0.2),
                   ("20일 폭 10% 미만 (응축)", D["range20"] < 0.1), ("250일 안 급등 전력 있음", D["n_j250"] >= 1)],
        "다음날 반응": [("다음날 +3%↑", D["next_ret"] >= 0.03), ("다음날 ±3% 안", np.abs(D["next_ret"]) < 0.03), ("다음날 −3%↓", D["next_ret"] <= -0.03), ("5일 안 3배↑ 거래량 또 나옴", D["again5"] == 1)],
        "종목·시장": [("주가 1,000원 미만", D["price"] < 1000), ("1,000~5,000원", (D["price"] >= 1000) & (D["price"] < 5000)), ("20,000원 이상", D["price"] >= 20000), ("거래대금 50억↑", D["liq"] >= math.log10(5e9)),
                   ("시장 60일 −10%↓", D["mkt_r60"] <= -0.1), ("시장 60일 +10%↑", D["mkt_r60"] >= 0.1)],
    }
    out = []
    for grp, items in specs.items():
        rows = [r for r in (_row(n, D, m) for n, m in items) if r]
        out.append({"group": grp, "rows": rows})
    return out


def _time_hist(D):
    bins = [(1, 3), (4, 5), (6, 10), (11, 20), (21, 40), (41, 60), (61, 120)]
    out = []
    for x in (10, 20, 50):
        d = D[f"d{x}"]
        out.append({"x": x, "hit_share": float(np.mean(~np.isnan(d))), "bins": [{"label": f"{a}~{b}일", "share_all": float(np.mean((d >= a) & (d <= b)))} for a, b in bins]})
    return out


def _recent(recent, Q, names, top=30):
    if not recent or Q is None:
        return []
    cand = np.abs(Q["ret1"]) < 0.01
    pos = np.where(Q["nh250"] >= -0.1, 0, np.where(Q["nh250"] >= -0.5, 1, 2))
    big = Q["volratio"] >= 10
    key = cand.astype(int) * 100 + pos * 10 + big.astype(int)
    stats = {}
    for k in np.unique(key):
        m = key == k
        if m.sum() >= 200:
            stats[int(k)] = {"n": int(m.sum()), "p20_in20": float(np.nanmean(Q["d20"][m] <= 20)), "p20_in60": float(np.nanmean(Q["d20"][m] <= 60)), "days20": float(np.nanmedian(Q["d20"][m][~np.isnan(Q["d20"][m])])) if (~np.isnan(Q["d20"][m])).any() else None,
                            "dn10_first": float(np.mean(~np.isnan(Q["dn10"][m]) & (np.isnan(Q["d20"][m]) | (Q["dn10"][m] <= Q["d20"][m])))), "r60": float(np.nanmean(Q["r60"][m])), "jump20": float(np.nanmean(Q["fwd20"][m]))}
    plab = {0: "고점권", 1: "중간", 2: "바닥권"}
    out = []
    for r in recent:
        k = int(abs(r["ret1"]) < 0.01) * 100 + (0 if r["nh250"] >= -0.1 else 1 if r["nh250"] >= -0.5 else 2) * 10 + int(r["volratio"] >= 10)
        stt = stats.get(k)
        if not stt:
            continue
        out.append({**{kk: r[kk] for kk in ("code", "date", "close", "volratio", "ret1", "nh250", "vdry10", "dist224", "n_j250")}, "name": names.get(r["code"], r["code"]),
                    "group": f"{'도지' if abs(r['ret1']) < 0.01 else '±1~3%'} · {plab[(0 if r['nh250'] >= -0.1 else 1 if r['nh250'] >= -0.5 else 2)]} · {'10배↑' if r['volratio'] >= 10 else '3~10배'}", **stt})
    out.sort(key=lambda z: (-z["p20_in20"], -z["volratio"]))
    return out[:top]


def run(workers: int = 0, log=print, limit: int = 0) -> dict:
    t0 = time.time()
    with db.get_conn() as c:
        codes = [r["code"] for r in c.execute("SELECT code FROM prices GROUP BY code HAVING COUNT(*) >= 400 ORDER BY code")]
        names = {r["code"]: r["name"] for r in c.execute("SELECT code,name FROM symbols")}
    if limit:
        codes = codes[::max(len(codes) // limit, 1)][:limit]
    workers = workers or max(1, min(6, (os.cpu_count() or 2) - 2))
    if not bt.MKT_PATH.exists():
        bt.build_market_index(codes, workers)
    parts, recent = [], []
    with ProcessPoolExecutor(max_workers=workers, initializer=ai._init_worker, initargs=(bt.MKT_PATH,)) as ex:
        for i, out in enumerate(ex.map(_work, codes, chunksize=8), 1):
            if out:
                parts.append(out)
                recent += out["recent"]
            if i % 500 == 0:
                log(f"[종목 {i}/{len(codes)}] · {time.time() - t0:.0f}초")
    G = {k: sg._cat(parts, k) for k in ("quiet", "up", "down", "bs")}
    G = {k: ({kk: v.to_numpy() if hasattr(v, "to_numpy") else v for kk, v in df.items()} if df is not None and len(df) else None) for k, df in G.items()}
    del parts
    Q = G["quiet"]
    log(f"조용한 대량 거래 {len(Q['date']):,}건 · 양봉 급증 {len(G['up']['date']):,} · 음봉 급증 {len(G['down']['date']):,} · 아무 날 {len(G['bs']['date']):,} · {time.time() - t0:.0f}초")
    rep = {"meta": {"generated": datetime.now().isoformat(timespec="seconds"), "min_ratio": MIN_RATIO, "flat": FLAT, "look": LOOK, "events": int(len(Q["date"])), "stocks": int(len(set(Q["code"]))),
                    "date_min": str(Q["date"].min()), "date_max": str(Q["date"].max()), "liq_min": ai.TRADABLE_VALUE}}
    rep["groups"] = [{"key": k, "name": nm, **_summary(G[k])} for k, nm in (("quiet", "조용한 대량 거래 (3배↑ · 등락 ±3% 안)"), ("up", "비교: 대량 거래 + 양봉 +5%↑"), ("down", "비교: 대량 거래 + 음봉 −3%↓"), ("bs", "기준: 아무 날")) if _summary(G[k])]
    rep["time_hist"] = _time_hist(Q)
    rep["curve"] = {k: [{"day": h, "p": float(np.nanmean(G[k]["d20"] <= h))} for h in (1, 2, 3, 5, 7, 10, 15, 20, 30, 40, 60, 90, 120)] for k in ("quiet", "up", "down", "bs") if G[k] is not None}
    rep["breakdown"] = _breakdown(Q)
    rep["recent"] = _recent(recent, Q, names)
    rep["text"] = narrate(rep)
    rep["meta"]["elapsed_sec"] = round(time.time() - t0)
    QV_PATH.write_text(json.dumps(rep, ensure_ascii=False, default=st._js), encoding="utf-8")
    return rep


def narrate(rep):
    g = {x["key"]: x for x in rep["groups"]}
    q, b, u, d = g["quiet"], g.get("bs"), g.get("up"), g.get("down")
    out = [f"거래량 3배↑인데 종가 등락이 ±3% 안인 날 {q['n']:,}건. 다음날 시가에 사면 +20%에 닿는 비율: 5일 안 {q['hit']['20']['5'] * 100:.0f}% · 20일 안 {q['hit']['20']['20'] * 100:.0f}% · 60일 안 {q['hit']['20']['60'] * 100:.0f}% · 120일 안 {q['hit']['20']['120'] * 100:.0f}%"
           + (f" (아무 날 {b['hit']['20']['20'] * 100:.0f}% · {b['hit']['20']['60'] * 100:.0f}% · {b['hit']['20']['120'] * 100:.0f}%)" if b else "") + "."]
    dd = q["days"]["20"]
    out.append(f"닿은 경우 걸린 날 중앙 {dd['median']:.0f}일(빠른 25% {dd['q25']:.0f}일 · 느린 25% {dd['q75']:.0f}일), +10%는 {q['days']['10']['median']:.0f}일, +50%는 {q['days']['50']['median']:.0f}일. −10%를 먼저 맞은 비율 {q['dn10_first'] * 100:.0f}%, 20·60·120일 평균 {q['r20'] * 100:+.1f}%·{q['r60'] * 100:+.1f}%·{q['r120'] * 100:+.1f}%"
               + (f"(아무 날 {b['r20'] * 100:+.1f}%·{b['r60'] * 100:+.1f}%·{b['r120'] * 100:+.1f}%)" if b else "") + f". 20일 안 +20% 급등일이 나온 비율 {q['jump20'] * 100:.1f}%" + (f"(아무 날 {b['jump20'] * 100:.1f}%)" if b else "") + ".")
    if u and d:
        out.append(f"같은 거래량 급증이라도 양봉(+5%↑)은 20일 안 +20% {u['hit']['20']['20'] * 100:.0f}%·20일 평균 {u['r20'] * 100:+.1f}%, 음봉(−3%↓)은 {d['hit']['20']['20'] * 100:.0f}%·{d['r20'] * 100:+.1f}%, 조용한 날은 {q['hit']['20']['20'] * 100:.0f}%·{q['r20'] * 100:+.1f}%.")
    rows = [r for grp in rep["breakdown"] for r in grp["rows"]]
    hi = sorted(rows, key=lambda r: -r["p20_in20"])[:4]
    lo = sorted(rows, key=lambda r: r["p20_in20"])[:3]
    out.append("조용한 대량 거래 중 20일 안 +20% 확률이 높은 조건: " + ", ".join(f"{r['name']} {r['p20_in20'] * 100:.0f}%({r['days20']:.0f}일)" for r in hi) + " / 낮은 조건: " + ", ".join(f"{r['name']} {r['p20_in20'] * 100:.0f}%" for r in lo) + ".")
    if rep["recent"]:
        out.append(f"최근 8거래일 안 조용한 대량 거래 {len(rep['recent'])}종목에 비슷한 과거 그룹의 결과를 붙였습니다(상위: " + ", ".join(f"{r['name']} {r['p20_in20'] * 100:.0f}%" for r in rep["recent"][:5]) + ").")
    return out


if __name__ == "__main__":
    run()
