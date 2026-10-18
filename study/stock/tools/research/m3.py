import os,itertools,numpy as np,pandas as pd
S=pd.read_pickle(os.environ["TEMP"]+"/stk/S6.pkl")
S["rP"]=S.groupby("date")["P"].rank(pct=True);S["rS"]=S.groupby("date")["score"].rank(pct=True)
dates_all=sorted(S.date.unique())
def curve(g,dates):
    g=g.reindex(dates).fillna(0.0).to_numpy();sl=[g[k::6] for k in range(6)];L=min(len(s) for s in sl)
    step=np.mean([s[:L] for s in sl],axis=0);eq=np.cumprod(1+step);yrs=L*28/365
    cagr=eq[-1]**(1/yrs)-1;vol=step.std()*np.sqrt(365/28);sh=(step.mean()*365/28)/vol if vol>0 else np.nan
    mdd=(eq/np.maximum.accumulate(eq)-1).min();return cagr*100,sh,mdd*100,(step>0).mean()*100,eq[-1]
mk=S.groupby("date").r20.mean()
def run(K,pcut,lam,wt,L,extra,win):
    d=S[(S.liqv>=L)&S.date.isin(win)]
    if pcut is not None: d=d[d.P<pcut]
    d=d.assign(s2=d.rS-lam*d.rP)
    d=d.sort_values(["date","s2"],ascending=[True,False]).groupby("date").head(K)
    d=d.assign(w=1.0 if wt=="eq" else 1.0/d.atrp.clip(lower=0.01),r=d.r20-extra)
    g=d.groupby("date").apply(lambda x:(x.r*x.w).sum()/x.w.sum() if len(x)>=3 else np.nan)
    return g
va=[d for d in dates_all if "2022"<=d[:4]<="2023"];te=[d for d in dates_all if d[:4]>="2024"]
print("시장 평균(전 종목): 검증 CAGR %.1f%% 시험 %.1f%%"%(curve(mk,va)[0],curve(mk,te)[0]))
grid=list(itertools.product((10,20,30,50),(None,0.27,0.18),(0.0,0.3,0.6),("eq","iv"),(3e8,10e8)))
rows=[]
for K,pc,lam,wt,L in grid:
    gv=run(K,pc,lam,wt,L,0.003,va);gt=run(K,pc,lam,wt,L,0.003,te)
    cv=curve(gv,va);ct=curve(gt,te)
    rows.append((K,pc,lam,wt,L/1e8,*cv,*ct))
R=pd.DataFrame(rows,columns=["K","P컷","λ","비중","최소거래대금(억)","검증CAGR","검증샤프","검증MDD","검증+%","검증배수","시험CAGR","시험샤프","시험MDD","시험+%","시험배수"])
R.to_pickle(os.environ["TEMP"]+"/stk/R3.pkl")
pd.set_option("display.width",250)
print("검증 샤프 상위 10 (선택 기준=검증만, 추가 슬리피지 0.3%p 반영)")
print(R.sort_values("검증샤프",ascending=False).head(10).round(2).to_string(index=False))
top=R.sort_values("검증샤프",ascending=False).head(20)
print("\n검증 상위 20개 설정의 시험 평균: CAGR %.1f%% 샤프 %.2f MDD %.1f%%"%(top.시험CAGR.mean(),top.시험샤프.mean(),top.시험MDD.mean()))
print("전체 %d개 설정의 시험 평균: CAGR %.1f%% 샤프 %.2f"%(len(R),R.시험CAGR.mean(),R.시험샤프.mean()))
print("검증-시험 샤프 상관 %.2f"%R[["검증샤프","시험샤프"]].corr().iloc[0,1])
