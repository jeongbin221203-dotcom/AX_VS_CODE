"""손절을 줄이고 손실 거래를 수익으로 바꾸는 방법 비교(2026-10-19) — 시총 구간별 지금 규칙(core/jb_rules)의 같은 매수점에서 매수 뒤 대응만 바꾼다.
A 기다렸다 팔기: 매도 신호 때 손실이면 본전(비용 포함)까지 최대 K봉 기다림(그 안에 못 오르면 K봉째 종가)
B 20일선 회복에 팔기: 매도 신호 때 손실이면 종가가 20일선 위로 올라선 날(최대 K봉)
C 나눠서 사기: 신호 다음 날 시가에 절반, 매도 전 처음으로 종가가 −d% 이하이면 다음 날 시가에 나머지 절반(못 사면 절반만), 매도는 지금 규칙
D 볼린저 하단에 나머지: C 와 같되 추가 매수 = 매도 전 처음으로 저가가 볼린저 하단에 닿은 날 그 가격
E = C/D + A(나눠 사고, 손실이면 본전까지 기다림)
학습에 쓰지 않은 구간만, 비용 0.3%. 한 종목 다음 신호와의 겹침은 무시(거래별 비교).
python tools/research/jb_loss.py"""
import os
import pickle
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core import jb_rules, picks  # noqa: E402

COST = 0.003
D = pickle.load(open(os.path.join("data", "research", "jb_scores.pkl"), "rb"))
G = {k: v[0] for k, v in ((picks.load_today() or {}).get("scores") or {}).items()}


def wait_even(c, b, j, entry, K):
    """j 이후 종가가 본전(+비용)에 닿는 첫날, 없으면 j+K(데이터 끝이면 마지막)."""
    for x in range(j, min(j + K, len(c))):
        if c[x] >= entry * (1 + COST):
            return x, c[x]
    x = min(j + K, len(c) - 1)
    return x, c[x]


def wait_ma(c, m20, j, K):
    for x in range(j, min(j + K, len(c))):
        if np.isfinite(m20[x]) and c[x] > m20[x]:
            return x, c[x]
    x = min(j + K, len(c) - 1)
    return x, c[x]


def main():
    out = {}
    for grp in ("대형주", "중형주", "소형주", "잡주"):
        rows = {k: [] for k in ("지금 규칙", "A 본전까지 20봉", "A 본전까지 40봉", "A 본전까지 60봉", "B 20일선 회복(40봉)", "C 나눠 사기 −7%", "C 나눠 사기 −10%",
                                "D 볼린저 하단에 나머지", "E 나눠 사기 −10% + 본전 40봉", "E 볼린저 하단 + 본전 40봉")}
        for code, t in D.items():
            if G.get(code) != grp:
                continue
            dates, o, h, l, c, v, s, oos = t[0], t[1], t[2], t[3], t[4], t[5], np.asarray(t[6], float), np.asarray(t[7], bool)
            tr, _, bb = jb_rules.simulate(o, h, l, c, s, grp, ok=oos & np.isfinite(s), v=v, mkt_dd=jb_rules.market_dd(dates))
            m20, lo = bb["mid"], bb["lo"]
            for i, b, j, px, ret, why in tr:
                if j is None:
                    continue
                e = o[b]
                key = dates[b][:4]
                rows["지금 규칙"].append((key, ret, j - b))
                for K in (20, 40, 60):
                    if ret < 0:
                        x, p2 = wait_even(c, b, j, e, K)
                        rows[f"A 본전까지 {K}봉"].append((key, p2 / e - 1 - COST, x - b))
                    else:
                        rows[f"A 본전까지 {K}봉"].append((key, ret, j - b))
                if ret < 0:
                    x, p2 = wait_ma(c, m20, j, 40)
                    rows["B 20일선 회복(40봉)"].append((key, p2 / e - 1 - COST, x - b))
                else:
                    rows["B 20일선 회복(40봉)"].append((key, ret, j - b))

                def split(add_at):
                    """나머지 절반을 산 가격(없으면 None)."""
                    for x in range(b, j):
                        p_ = add_at(x)
                        if p_:
                            return p_
                    return None
                for d in (0.07, 0.10):
                    p_add = split(lambda x, d=d: (o[x + 1] if x + 1 <= j and c[x] <= e * (1 - d) else None))
                    avg = e if p_add is None else (e + p_add) / 2
                    rows[f"C 나눠 사기 −{d * 100:.0f}%"].append((key, px / avg - 1 - COST, j - b))
                p_add = split(lambda x: (min(o[x], lo[x]) if np.isfinite(lo[x]) and l[x] <= lo[x] and x > b else None))
                avg = e if p_add is None else (e + p_add) / 2
                rows["D 볼린저 하단에 나머지"].append((key, px / avg - 1 - COST, j - b))
                for name, add_fn in (("E 나눠 사기 −10% + 본전 40봉", lambda x: (o[x + 1] if x + 1 <= j and c[x] <= e * 0.90 else None)),
                                     ("E 볼린저 하단 + 본전 40봉", lambda x: (min(o[x], lo[x]) if np.isfinite(lo[x]) and l[x] <= lo[x] and x > b else None))):
                    p_add = split(add_fn)
                    avg = e if p_add is None else (e + p_add) / 2
                    r_ = px / avg - 1 - COST
                    if r_ < 0:
                        x, p2 = wait_even(c, b, j, avg, 40)
                        rows[name].append((key, p2 / avg - 1 - COST, x - b))
                    else:
                        rows[name].append((key, r_, j - b))
        print(f"\n======== {grp} ========")
        res = {}
        for name, R in rows.items():
            if not R:
                continue
            r = np.array([x[1] for x in R]); d = np.array([x[2] for x in R], float)
            e_ = np.array([x[1] for x in R if x[0] <= "2021"]); l_ = np.array([x[1] for x in R if x[0] >= "2022"])
            lossy = r[r < 0]
            res[name] = {"n": len(r), "mean": float(r.mean()), "win": float((r > 0).mean()), "loss_n": int(len(lossy)), "loss_mean": float(lossy.mean()) if len(lossy) else 0.0,
                         "worst": float(r.min()), "days": float(d.mean()), "early": float(e_.mean()) if len(e_) else None, "late": float(l_.mean()) if len(l_) else None}
            q = res[name]
            print(f"  {name:24s} | {q['n']:4d}건 평균 {q['mean'] * 100:+6.2f}% 승률 {q['win'] * 100:4.1f}% | 손실 {q['loss_n']:3d}건 평균 {q['loss_mean'] * 100:+6.2f}% 최악 {q['worst'] * 100:+6.1f}% | 보유 {q['days']:5.1f}일 | ~2021 {q['early'] * 100 if q['early'] is not None else 0:+6.2f}% 2022~ {q['late'] * 100 if q['late'] is not None else 0:+6.2f}%")
        out[grp] = res
    import json
    json.dump(out, open(os.path.join("data", "research", "jb_loss.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
