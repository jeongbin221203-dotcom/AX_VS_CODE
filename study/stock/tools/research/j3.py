import os,itertools,numpy as np,pandas as pd
A=pd.read_pickle(os.environ["TEMP"]+"/stk/SJ.pkl")
G=pd.read_pickle(os.environ["TEMP"]+"/stk/S6cap2.pkl")[["date","code","ens","score","s_up","s_reg","s_r60","P"]]
A=A.merge(G,on=["date","code"],how="left")
for c in ("j_rank","j_up","j_reg","j_r60","ens"): A["r_"+c]=A.groupby("date")[c].rank(pct=True)
A["ens_j"]=(A.r_j_rank+A.r_j_up+A.r_j_reg+A.r_j_r60)/4
A["ens_jg"]=(A.ens_j+A.r_ens)/2
A["ens_j2"]=(A.r_j_rank+A.r_j_r60)/2
dates_all=sorted(A.date.unique())
def curve(g,dates):
    g=g.reindex(dates).fillna(0.0).to_numpy();sl=[g[k::6] for k in range(6)];L=min(len(s) for s in sl)
    step=np.mean([s[:L] for s in sl],axis=0);eq=np.cumprod(1+step);yrs=L*28/365
    cagr=eq[-1]**(1/yrs)-1;vol=step.std()*np.sqrt(365/28);sh=(step.mean()*365/28)/vol if vol>0 else np.nan
    return cagr*100,sh,(eq/np.maximum.accumulate(eq)-1).min()*100,eq[-1]
va=[d for d in dates_all if d[:4]<="2023"];te=[d for d in dates_all if d[:4]>="2024"]
base=A.groupby("date").r20.mean();print("잡주 전체 평균: 검증 %.1f%% 시험 %.1f%%"%(curve(base,va)[0],curve(base,te)[0]))
def run(col,K,stop,extra,win,minliq=0,maxatr=1.0):
    d=A[A.date.isin(win)&(A.liqv>=minliq)&(A.atrp<=maxatr)]
    d=d.sort_values(["date",col],ascending=[True,False]).groupby("date").head(K)
    r=d.r20-extra
    if stop: r=np.where(d.mae20<=-stop,-stop-0.005-extra,r)
    return pd.Series(r,index=d.index).groupby(d.date).mean()
rows=[]
for col in ("j_rank","j_up","j_reg","j_r60","ens_j","ens_j2","ens","ens_jg"):
    for K in (5,10,15,20,30):
        for stop in (None,0.10,0.15,0.20):
            gv,gt=run(col,K,stop,0.003,va),run(col,K,stop,0.003,te)
            cv,ct=curve(gv,va),curve(gt,te);rows.append((col,K,stop or 0,*cv,*ct))
R=pd.DataFrame(rows,columns=["점수","K","손절","검증CAGR","검증샤프","검증MDD","검증배수","시험CAGR","시험샤프","시험MDD","시험배수"])
R.to_pickle(os.environ["TEMP"]+"/stk/RJ.pkl");pd.set_option("display.width",250)
print("검증 연수익 상위 12 (선택=검증)");print(R.sort_values("검증CAGR",ascending=False).head(12).round(2).to_string(index=False))
print("\n검증 샤프 상위 8");print(R.sort_values("검증샤프",ascending=False).head(8).round(2).to_string(index=False))
print("\n점수별 평균(손절 무시)");print(R.groupby("점수")[["검증CAGR","검증샤프","시험CAGR","시험샤프","시험MDD"]].mean().round(2))
print("\nK별 평균");print(R.groupby("K")[["검증CAGR","시험CAGR","시험샤프","시험MDD"]].mean().round(2))
print("\n손절별 평균");print(R.groupby("손절")[["검증CAGR","시험CAGR","시험샤프","시험MDD"]].mean().round(2))
print("검증-시험 CAGR 상관 %.2f"%R[["검증CAGR","시험CAGR"]].corr().iloc[0,1])
