"""저장해 둔 예측(data/forecasts/<날짜>.json)을 그날 실제 시세와 비교한다. 장 마감 뒤 시세를 DB에 갱신(수집)한 다음 실행.
python tools/research/forecast_verify.py [날짜]"""
import os, sys, json, sqlite3, hashlib
import numpy as np, pandas as pd
from sklearn.metrics import roc_auc_score

TARGET = sys.argv[1] if len(sys.argv) > 1 else "2026-10-19"
P = os.path.join("data", "forecasts", f"{TARGET}.json")
raw = open(P, encoding="utf-8").read()
ok = hashlib.sha256(raw.encode("utf-8")).hexdigest() == open(P + ".sha256").read().strip()
F = json.loads(raw)
print(f"예측 파일 무결성(저장 뒤 수정 안 됨): {'확인' if ok else '불일치!'} | 생성 {F['generated']} | 기준일 {F['asof']} → 대상 {F['target_date']}")
con = sqlite3.connect("data/stock.db")
a = pd.read_sql("select code,open as o1,close as c1 from prices where date=?", con, params=[TARGET])
p = pd.read_sql("select code,close as c0 from prices where date=?", con, params=[F["asof"]])
if a.empty:
    print(f"DB에 {TARGET} 시세가 아직 없습니다. 장 마감 뒤 시세 수집을 갱신한 다음 다시 실행하세요."); sys.exit(1)
S = pd.DataFrame(F["stocks"]).T.reset_index().rename(columns={"index": "code"})
for c in ("p_up", "p_rank", "score"): S[c] = S[c].astype(float)
M = S.merge(a, on="code").merge(p, on="code")
M["r1o"] = M.c1 / M.o1 - 1          # 시가→종가(거래 가능한 수익)
M["r1c"] = M.c1 / M.c0 - 1          # 전 거래일 종가→종가
M = M.dropna(subset=["r1o"])
print(f"비교 가능 종목 {len(M)}개 (예측 {len(S)}개)")
print(f"\n[시장 전체] 실제 상승 종목 비율(시가→종가) {(M.r1o > 0).mean()*100:.1f}% | 예측 평균 상승확률 {F['summary']['평균 상승확률']*100:.1f}% | 실제 평균 수익 {M.r1o.mean()*100:+.2f}%")
print(f"  전일 종가 대비 상승 비율 {(M.r1c > 0).mean()*100:.1f}%, 평균 {M.r1c.mean()*100:+.2f}%")
y = (M.r1o > 0).astype(int)
if y.nunique() == 2:
    print(f"\n[종목 예측] AUC {roc_auc_score(y, M.score):.3f} (0.5=무작위) | 상관(예측 점수 vs 실제 수익) {M.score.corr(M.r1o):.3f}")
M["dec"] = M.score.rank(pct=True)
top, bot = M[M.dec > 0.9], M[M.dec <= 0.1]
print(f"  예측 상위 10%({len(top)}개): 상승 비율 {(top.r1o>0).mean()*100:.1f}%, 평균 {top.r1o.mean()*100:+.2f}% | 하위 10%({len(bot)}개): 상승 비율 {(bot.r1o>0).mean()*100:.1f}%, 평균 {bot.r1o.mean()*100:+.2f}%")
print("\n[그룹별]")
for g in ("대형주", "중형주", "소형주", "잡주"):
    d = M[M.grp == g]
    if len(d) < 20: continue
    yy = (d.r1o > 0).astype(int); auc = roc_auc_score(yy, d.score) if yy.nunique() == 2 else float("nan")
    t = d[d.score.rank(pct=True) > 0.8]
    print(f"  {g}: {len(d)}개 | 실제 상승 {(d.r1o>0).mean()*100:.0f}% (예측 평균 {F['summary'][g]['평균 상승확률']*100:.0f}%) | AUC {auc:.3f} | 상위20% 평균 {t.r1o.mean()*100:+.2f}% vs 그룹 평균 {d.r1o.mean()*100:+.2f}%")
print("\n[예측 상위 5종목 실제] (그룹별)")
for g, lst in F["top"].items():
    for it in lst[:5]:
        r = M[M.code == it["code"]]
        if len(r): print(f"  {g} {it['name']}({it['code']}) 예측 p_up {it['p_up']:.2f} → 실제 {r.r1o.iloc[0]*100:+.2f}%")
