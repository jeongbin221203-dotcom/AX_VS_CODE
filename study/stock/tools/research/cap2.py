import os,numpy as np,pandas as pd
S=pd.read_pickle(os.environ["TEMP"]+"/stk/S6cap.pkl")
dates_all=sorted(S.date.unique())
def curve(g,dates):
    g=g.reindex(dates).fillna(0.0).to_numpy();sl=[g[k::6] for k in range(6)];L=min(len(s) for s in sl)
    step=np.mean([s[:L] for s in sl],axis=0);eq=np.cumprod(1+step);yrs=L*28/365
    cagr=eq[-1]**(1/yrs)-1;vol=step.std()*np.sqrt(365/28);sh=(step.mean()*365/28)/vol if vol>0 else np.nan
    return cagr*100,sh,(eq/np.maximum.accumulate(eq)-1).min()*100,eq[-1]
va=[d for d in dates_all if d[:4]<="2023"];te=[d for d in dates_all if d[:4]>="2024"];allw=va+te
EX=0.003
mk=S.groupby("date").r20.mean()
def pick(pool,K):
    return pool.sort_values(["date","ens"],ascending=[True,False]).groupby("date").head(K)
print("시장 전체 평균(전 종목 동일비중): 검증 %.1f%% 시험 %.1f%%"%(curve(mk,va)[0],curve(mk,te)[0]))
for K in (10,20):
    print(f"\n=== 시총 4분위별, 각 구간 안에서 상위 {K}종목 (추가 슬리피지 {EX*100:.1f}%p)")
    rows=[]
    for qn in (1,2,3,4):
        pool=S[S.qcap==qn]
        g=pick(pool,K);gg=g.assign(r=g.r20-EX).groupby("date").r.mean()
        base=pool.groupby("date").r20.mean()
        cv,ct=curve(gg,va),curve(gg,te);bv,bt=curve(base,va),curve(base,te)
        gt=g[g.date>="2024"];gv=g[g.date<"2024"]
        rows.append([qn,round(cv[0],1),round(bv[0],1),round(ct[0],1),round(bt[0],1),round(ct[1],2),round(ct[2],1),round((gt.r20>0).mean()*100,1),round((gt.r20<=-0.10).mean()*100,1),round((gv.r20>0).mean()*100,1)])
    print(pd.DataFrame(rows,columns=["시총","검증 전략%","검증 그 구간 평균%","시험 전략%","시험 그 구간 평균%","시험 샤프","시험 최대낙폭%","시험 승률%","시험 -10%이하 비율%","검증 승률%"]).to_string(index=False))
print("\n=== 연도별 전략 수익(상위10, %) / 그 구간 평균(%)")
for qn in (1,2,3,4):
    pool=S[S.qcap==qn];g=pick(pool,10);gg=g.assign(r=g.r20-EX).groupby("date").r.mean();base=pool.groupby("date").r20.mean()
    print(qn," | ".join(f"{Y}: {curve(gg,[d for d in dates_all if d[:4]==str(Y)])[0]:+.0f} / {curve(base,[d for d in dates_all if d[:4]==str(Y)])[0]:+.0f}" for Y in range(2022,2027)))
print("\n=== 전체에서 상위 20개를 뽑았을 때 어느 시총에서 나오나(2022~)")
g=pick(S,20);mix=g.qcap.value_counts(normalize=True).sort_index()*100;print(mix.round(1).to_dict())
print(g.groupby("qcap").agg(n=("r20","size"),평균20일=("r20",lambda s:s.mean()*100),승률=("r20",lambda s:(s>0).mean()*100),하락10=("r20",lambda s:(s<=-0.1).mean()*100)).round(2).to_string())
# 시총 구간별 같은 종목 수 (시장 평균 대비 초과가 아닌 '그 구간 평균 대비 초과' 순위 IC)
d=S.assign(y=S.groupby("date").r20.rank(pct=True))
print("\n=== 구간별 순위 예측력(IC, 높을수록 점수가 잘 맞음)")
for qn in (1,2,3,4):
    x=d[d.qcap==qn];x=x.assign(yy=x.groupby("date").r20.rank(pct=True))
    ic=x.groupby("date").apply(lambda a:a.ens.corr(a.yy,method="spearman"))
    print(qn,"검증 %.3f 시험 %.3f"%(ic[ic.index<"2024"].mean(),ic[ic.index>="2024"].mean()))
