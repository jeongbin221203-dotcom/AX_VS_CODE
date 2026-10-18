import os,numpy as np,pandas as pd
SG=pd.read_pickle(os.environ["TEMP"]+"/stk/SG.pkl");SJ=pd.read_pickle(os.environ["TEMP"]+"/stk/SJ.pkl");SJ["grp"]="잡주"
G=pd.read_pickle(os.environ["TEMP"]+"/stk/S6cap2.pkl")[["date","code","ens"]]
A=pd.concat([SG,SJ[SG.columns.intersection(SJ.columns)]],ignore_index=True)
A=A.merge(G,on=["date","code"],how="left")
for c in ("j_rank","j_up","j_reg","j_r60"): A["r_"+c]=A.groupby(["date","grp"])[c].rank(pct=True)
A["r_ens"]=A.groupby(["date","grp"])["ens"].rank(pct=True)
A["ens_g"]=(A.r_j_rank+A.r_j_up+A.r_j_reg+A.r_j_r60)/4
A["score"]=(A.ens_g+A.r_ens)/2
A.to_pickle(os.environ["TEMP"]+"/stk/AG.pkl")
dates_all=sorted(A.date.unique())
def curve(g,dates):
    g=g.reindex(dates).fillna(0.0).to_numpy();sl=[g[k::6] for k in range(6)];L=min(len(s) for s in sl)
    step=np.mean([s[:L] for s in sl],axis=0);eq=np.cumprod(1+step);yrs=L*28/365
    cagr=eq[-1]**(1/yrs)-1;vol=step.std()*np.sqrt(365/28);sh=(step.mean()*365/28)/vol if vol>0 else np.nan
    return cagr*100,sh,(eq/np.maximum.accumulate(eq)-1).min()*100,eq[-1],(step>0).mean()*100
va=[d for d in dates_all if d[:4]<="2023"];te=[d for d in dates_all if d[:4]>="2024"]
def run(g,K,extra,win,col="score"):
    d=A[(A.grp==g)&A.date.isin(win)].sort_values(["date",col],ascending=[True,False]).groupby("date").head(K)
    return d.assign(r=d.r20-extra).groupby("date").r.mean(),d
res={}
print("그룹 | K | 검증 연수익 | 그룹평균 | 시험 연수익 | 그룹평균 | 시험샤프 | 시험낙폭 | 28일플러스(시험) | 승률(시험) | -10%이하(시험)")
for g in ("대형주","중형주","소형주","잡주"):
    base=A[A.grp==g].groupby("date").r20.mean()
    for K in (5,10):
        gv,_=run(g,K,0.003,va);gt,dt=run(g,K,0.003,te)
        cv,ct=curve(gv,va),curve(gt,te)
        print(f"{g} K={K:2d} | {cv[0]:+6.1f}% | {curve(base,va)[0]:+6.1f}% | {ct[0]:+6.1f}% | {curve(base,te)[0]:+6.1f}% | {ct[1]:.2f} | {ct[2]:+6.1f}% | {ct[4]:.0f}% | {(dt.r20>0).mean()*100:.1f}% | {(dt.r20<=-0.10).mean()*100:.1f}%")
        res[(g,K)]=(cv,ct)
print("\n비용 민감도(검증|시험) 추가 0.3/0.7/1.5/3.0 %p")
for g in ("대형주","중형주","소형주","잡주"):
    for K in (5,10):
        print(g,K," ; ".join(f"{curve(run(g,K,ex,va)[0],va)[0]:+.0f}|{curve(run(g,K,ex,te)[0],te)[0]:+.0f}" for ex in (0.003,0.007,0.015,0.03)))
print("\n연도별(K=5 / K=10, %)")
for g in ("대형주","중형주","소형주","잡주"):
    for K in (5,10):
        gg,_=run(g,K,0.003,dates_all);print(g,K," | ".join(f"{Y}: {curve(gg,[d for d in dates_all if d[:4]==str(Y)])[0]:+.0f}" for Y in range(2022,2027)))
print("\n옛 방식(전체 모델 점수만으로 그룹 안 상위 10, 참고)")
for g in ("대형주","중형주","소형주","잡주"):
    gv,_=run(g,10,0.003,va,"ens");gt,_=run(g,10,0.003,te,"ens");print(g,"%+.0f%% | %+.0f%%"%(curve(gv,va)[0],curve(gt,te)[0]))
