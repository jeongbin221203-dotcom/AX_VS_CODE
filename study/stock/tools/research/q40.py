"""손절·시간 손절·분할을 더한 최적 조합(2026-10-18): q39의 틀에서 손절 폭(15~40%)과 '오래 손실이면 정리'(시간 손절)를 바꿔 3~6자리 순환 매매 연수익을 비교.
선택 = 2017~2021, 확인 = 2022~2026."""
import os, sys, json, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
src = open(os.path.join(os.path.dirname(__file__), "q39.py"), encoding="utf-8").read().split("O25, O20, O30, O50 =")[0]
exec(compile(src, "q39_head", "exec"))
_old = leg_exit
def leg_exit(D, k, rule, p, user):
    if rule == "tstop":
        c, o = D["c"], D["o"]; n = len(c); end = min(k + CAPS, n - 1); ent = o[k]
        for j in range(k, end):
            if user is not None and j >= user: return user
            if j - k >= p["d"] and c[j] / ent - 1 < p["g"]: return j
        return None
    return _old(D, k, rule, p, user)
U = ("user", {}); O25 = ("oneil", dict(x=0.25))
V = {"내 규칙": [(1, [U])]}
for x in (0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4): V[f"내 규칙 + 손절 -{int(x*100)}%"] = [(1, [U, ("stop", dict(x=x))])]
for d, g in ((60, 0.0), (120, 0.0), (120, -0.1), (250, 0.0), (250, -0.1)): V[f"내 규칙 + 시간 손절({d}일 지나 수익<{int(g*100)}%)"] = [(1, [U, ("tstop", dict(d=d, g=g))])]
V["내 규칙 + 손절 -25% + 시간 손절(250일, 수익<0)"] = [(1, [U, ("stop", dict(x=0.25)), ("tstop", dict(d=250, g=0.0))])]
V["내 규칙 + 손절 -30% + 시간 손절(120일, 수익<-10%)"] = [(1, [U, ("stop", dict(x=0.3)), ("tstop", dict(d=120, g=-0.1))])]
V["분할(절반 +25%) + 손절 -30%"] = [(0.5, [U, O25, ("stop", dict(x=0.3))]), (0.5, [U, ("stop", dict(x=0.3))])]
rows = []
for name, var in V.items():
    EV = build(var); r = np.array([e["ret"] for e in EV]); y = np.array([e["year"] for e in EV])
    row = dict(name=name, mean=r.mean() * 100, win=(r > 0).mean() * 100, days=np.mean([e["days"] for e in EV]), worst=r.min() * 100)
    for N in (3, 4, 5, 6):
        port = sim(EV, N); row[f"N{N}"] = pstats(port, "2017-01-01", "2026-12-31")[0]; row[f"N{N}_mdd"] = pstats(port, "2017-01-01", "2026-12-31")[1]
        row[f"N{N}_a"] = pstats(port, "2017-01-01", "2021-12-31")[0]; row[f"N{N}_b"] = pstats(port, "2022-01-01", "2026-12-31")[0]
        row[f"N{N}_min"] = min(pstats(port, f"{yy}-01-01", f"{yy}-12-31")[0] for yy in range(2017, 2027))
    rows.append(row)
R = pd.DataFrame(rows); R.to_pickle(T + "Q40.pkl"); pd.set_option("display.width", 270); pd.set_option("display.max_colwidth", 46)
print("거래 단위: 평균 / 승률 / 보유일 / 최악"); print(R[["name", "mean", "win", "days", "worst"]].round(1).to_string(index=False))
for N in (3, 4, 5, 6):
    print(f"\n{N}자리: 전체 연수익 / 낙폭 / 2017~21 / 2022~26 / 해별 최저"); print(R[["name", f"N{N}", f"N{N}_mdd", f"N{N}_a", f"N{N}_b", f"N{N}_min"]].round(1).to_string(index=False))
