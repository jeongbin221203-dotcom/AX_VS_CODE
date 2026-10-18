import os,itertools,numpy as np,pandas as pd
S=pd.read_pickle(os.environ["TEMP"]+"/stk/S6.pkl");B=pd.read_pickle(os.environ["TEMP"]+"/stk/S6b.pkl")
S=S.merge(B[["date","code","s_up","s_reg","s_r60"]],on=["date","code"],how="left")
for c in ("score","s_up","s_reg","s_r60"): S["r_"+c]=S.groupby("date")[c].rank(pct=True)
S["ens"]=(S.r_score+S.r_s_r60+S.r_s_up+S.r_s_reg)/4
S["ens2"]=(S.r_score+S.r_s_r60)/2
S["rP"]=S.groupby("date")["P"].rank(pct=True)
dates_all=sorted(S.date.unique())
def curve(g,dates):
    g=g.reindex(dates).fillna(0.0).to_numpy();sl=[g[k::6] for k in range(6)];L=min(len(s) for s in sl)
    step=np.mean([s[:L] for s in sl],axis=0);eq=np.cumprod(1+step);yrs=L*28/365
    cagr=eq[-1]**(1/yrs)-1;vol=step.std()*np.sqrt(365/28);sh=(step.mean()*365/28)/vol if vol>0 else np.nan
    return cagr*100,sh,(eq/np.maximum.accumulate(eq)-1).min()*100,eq[-1]
va=[d for d in dates_all if d[:4]<="2023"];te=[d for d in dates_all if d[:4]>="2024"]
def run(col,K,pcut,lam,win,extra=0.003,L=3e8):
    d=S[(S.liqv>=L)&S.date.isin(win)]
    if pcut is not None: d=d[d.P<pcut]
    d=d.assign(s2=d["r_"+col if "r_"+col in d else col]-lam*d.rP)
    d=d.sort_values(["date","s2"],ascending=[True,False]).groupby("date").head(K)
    return d.assign(r=d.r20-extra).groupby("date").r.mean()
def ic(col,win):
    d=S[S.date.isin(win)];d=d.assign(y=d.groupby("date").r20.rank(pct=True))
    v=d.groupby("date").apply(lambda g:g[col].corr(g.y,method="spearman"));return v.mean()
print("시장 평균: 검증 %.1f%% / 시험 %.1f%%"%(curve(S.groupby('date').r20.mean(),va)[0],curve(S.groupby('date').r20.mean(),te)[0]))
rows=[]
for col in ("score","s_up","s_reg","s_r60","ens2","ens"):
    for K in (20,30):
        for pc,lam in ((None,0.0),(0.27,0.0),(None,0.5),(0.27,0.5)):
            cv=curve(run(col,K,pc,lam,va),va);ct=curve(run(col,K,pc,lam,te),te)
            rows.append((col,K,pc,lam,round(ic(col,va),3),round(ic(col,te),3),*cv,*ct))
R=pd.DataFrame(rows,columns=["점수","K","P컷","λ","검증IC","시험IC","검증CAGR","검증샤프","검증MDD","검증배수","시험CAGR","시험샤프","시험MDD","시험배수"])
pd.set_option("display.width",250);print(R.round(2).to_string(index=False))
print("\n점수별 평균:");print(R.groupby("점수")[["검증IC","시험IC","검증CAGR","검증샤프","시험CAGR","시험샤프"]].mean().round(2))
R.to_pickle(os.environ["TEMP"]+"/stk/R5.pkl")
