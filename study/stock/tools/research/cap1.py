import os,sqlite3,numpy as np,pandas as pd
S=pd.read_pickle(os.environ["TEMP"]+"/stk/S6.pkl");B=pd.read_pickle(os.environ["TEMP"]+"/stk/S6b.pkl")
S=S.merge(B[["date","code","s_up","s_reg","s_r60"]],on=["date","code"],how="left")
for c in ("score","s_up","s_reg","s_r60"): S["r_"+c]=S.groupby("date")[c].rank(pct=True)
S["ens"]=(S.r_score+S.r_s_r60+S.r_s_up+S.r_s_reg)/4
con=sqlite3.connect("data/stock.db")
sym=pd.read_sql("select code,name,market,marcap from symbols",con)
last=pd.read_sql("select p.code,p.close as close_now from prices p join (select code,max(date) d from prices group by code) m on p.code=m.code and p.date=m.d",con)
dates=sorted(S.date.unique())
q=",".join("'%s'"%d for d in dates)
px=pd.read_sql(f"select code,date,close from prices where date in ({q})",con)
S=S.merge(px,on=["code","date"],how="left").merge(sym,on="code",how="left").merge(last,on="code",how="left")
# 그때의 시가총액 추정 = 현재 시총 × (그때 수정종가 / 현재 종가) — 증자·감자는 반영 못 하는 근사
S["mcap_est"]=S.marcap*S.close/S.close_now
print("시총 추정 가능",S.mcap_est.notna().mean().round(3),"행",len(S))
S=S.dropna(subset=["mcap_est"])
S["qcap"]=S.groupby("date").mcap_est.transform(lambda s:pd.qcut(s.rank(method="first"),4,labels=[1,2,3,4])).astype(int)
S.to_pickle(os.environ["TEMP"]+"/stk/S6cap.pkl")
for qn in (1,2,3,4):
    d=S[(S.qcap==qn)&(S.date>="2024")]
    print(qn,"시총 추정(억원) 중앙 %.0f, 25%% %.0f, 75%% %.0f | 거래대금 중앙 %.1f억 | 코스닥 %.0f%% | 변동성 %.1f%% | 20일 평균수익(전체) %.2f%%"%(d.mcap_est.median()/1e8,d.mcap_est.quantile(.25)/1e8,d.mcap_est.quantile(.75)/1e8,d.liqv.median()/1e8,d.market.fillna("").str.startswith("KOSDAQ").mean()*100,d.atrp.mean()*100,d.r20.mean()*100))
