import os,itertools,numpy as np,pandas as pd
A=pd.read_pickle(os.environ["TEMP"]+"/stk/AO.pkl")
dates_all=sorted(A.date.unique());va=[d for d in dates_all if d[:4]<="2023"];te=[d for d in dates_all if d[:4]>="2024"]
RET={20:"r20",60:"r60",120:"r120"};NSL={20:6,60:17,120:34}
def curve(g,dates,nsl):
    g=g.reindex(dates).fillna(0.0).to_numpy();sl=[g[k::nsl] for k in range(nsl)];L=min(len(s) for s in sl)
    if L<3: return (np.nan,)*4
    step=np.mean([s[:L] for s in sl],axis=0);eq=np.cumprod(1+step);per=nsl*5;yrs=L*per/365
    cagr=eq[-1]**(1/yrs)-1;vol=step.std()*np.sqrt(365/per);sh=(step.mean()*365/per)/vol if vol>0 else np.nan
    return cagr*100,sh,(eq/np.maximum.accumulate(eq)-1).min()*100,eq[-1]
def run(g,hold,score,K,liq,risk,win,extra=0.003):
    d=A[(A.grp==g)&A.date.isin(win)&(A.liqv>=liq)]
    if risk: d=d[d.rP<=0.5]
    d=d.dropna(subset=[RET[hold]]).sort_values(["date",score],ascending=[True,False]).groupby("date").head(K)
    return d.assign(r=d[RET[hold]]-extra).groupby("date").r.mean()
rows=[]
for g in ("대형주","중형주","소형주","잡주"):
    cfgs=[]
    for K in (3,5,7,10,15,20):
        for liq in (3e8,10e8):
            for risk in (False,True):
                for sc in ("score","ens_g","r_ens"): cfgs.append((20,sc,K,liq,risk))
                for sc in ("h60","score"):
                    cfgs.append((60,sc,K,liq,risk));cfgs.append((120,sc,K,liq,risk))
    for hold,sc,K,liq,risk in cfgs:
        gv=run(g,hold,sc,K,liq,risk,va);gt=run(g,hold,sc,K,liq,risk,te)
        if len(gv)<10 or len(gt)<10: continue
        cv=curve(gv,va,NSL[hold]);ct=curve(gt,te,NSL[hold])
        rows.append((g,hold,sc,K,int(liq/1e8),int(risk),*cv,*ct))
R=pd.DataFrame(rows,columns=["그룹","보유일","점수","K","거래대금억","위험필터","검증CAGR","검증샤프","검증MDD","검증배수","시험CAGR","시험샤프","시험MDD","시험배수"])
R.to_pickle(os.environ["TEMP"]+"/stk/RO.pkl");pd.set_option("display.width",250)
for g in ("대형주","중형주","소형주","잡주"):
    r=R[R.그룹==g];print("\n=====",g,"설정",len(r),"개: 전체 평균 검증 %.1f%% 시험 %.1f%% | 검증-시험 CAGR 상관 %.2f"%(r.검증CAGR.mean(),r.시험CAGR.mean(),r[["검증CAGR","시험CAGR"]].corr().iloc[0,1]))
    top=r.sort_values("검증샤프",ascending=False).head(5);print("검증 샤프 상위 5 (선택=검증):");print(top.drop(columns="그룹").round(2).to_string(index=False))
    print("검증 상위 20개의 시험 평균: CAGR %.1f%% 샤프 %.2f MDD %.1f%%"%(r.sort_values("검증샤프",ascending=False).head(20).시험CAGR.mean(),r.sort_values("검증샤프",ascending=False).head(20).시험샤프.mean(),r.sort_values("검증샤프",ascending=False).head(20).시험MDD.mean()))
    print("보유일별 평균(시험):",r.groupby("보유일")[["검증CAGR","시험CAGR","시험MDD"]].mean().round(1).to_dict("index"))
