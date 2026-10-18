import os,numpy as np,pandas as pd
exec(open(os.environ["TEMP"]+"/stk/m5.py",encoding="utf-8").read().split("print(\"시장 평균")[0])
allw=[d for d in dates_all]
def yr_ret(g,Y):
    dts=[d for d in dates_all if d[:4]==str(Y)];return curve(g,dts)
def pick(col,K,pcut,L,extra,win=None):
    d=S[(S.liqv>=L)]
    if win: d=d[d.date.isin(win)]
    if pcut is not None: d=d[d.P<pcut]
    d=d.sort_values(["date",col],ascending=[True,False]).groupby("date").head(K)
    return d
print("== ens K=20 : 연도별(추가 슬리피지 0.3%p 포함, 6슬리브)")
for pc in (None,0.27):
    d=pick("ens",20,pc,3e8,0.003);g=d.assign(r=d.r20-0.003).groupby("date").r.mean()
    mk=S.groupby("date").r20.mean()
    line=[]
    for Y in range(2022,2027):
        c=yr_ret(g,Y);m=yr_ret(mk,Y);line.append(f"{Y}: {c[0]:+.0f}% (시장 {m[0]:+.0f}%)")
    print("P컷",pc,"|"," | ".join(line))
    print("   픽 특성: 20일 평균거래대금 중앙값 %.1f억, 하위10%% %.1f억, 변동성(ATR/종가) 평균 %.1f%%, 코스닥 %.0f%%"%(d.liqv.median()/1e8,d.liqv.quantile(.1)/1e8,d.atrp.mean()*100,(S.loc[d.index,'kosdaq'].mean()*100) if 'kosdaq' in S else float('nan')))
print("\n== 민감도(ens, P컷 없음): K / 최소거래대금 / 추가비용  → 검증CAGR 시험CAGR 시험MDD 시험샤프")
for K in (10,20,30,50):
    for L in (3e8,10e8,30e8):
        for ex in (0.003,0.007,0.012):
            d=pick("ens",K,None,L,ex);g=d.assign(r=d.r20-ex).groupby("date").r.mean()
            cv,ct=curve(g,va),curve(g,te)
            print(f"K={K:2d} L={L/1e8:4.0f}억 추가비용 {ex*100:.1f}%p  {cv[0]:+6.1f}% {ct[0]:+6.1f}% {ct[2]:+6.1f}% {ct[1]:.2f}")
