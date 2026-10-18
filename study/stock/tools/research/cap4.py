import os,numpy as np,pandas as pd
exec(open(os.environ["TEMP"]+"/stk/cap2.py",encoding="utf-8").read().split("mk=S.groupby")[0])
S["rk"]=S.groupby("date").mcap_est.rank(ascending=False,method="first")
junk=(S.rk>300)&((S.mcap_est<1000e8)|(S.close<1000)|(S.liqv<5e8))
S["grp"]=np.where(S.rk<=100,"대형주",np.where(S.rk<=300,"중형주",np.where(junk,"잡주","소형주")))
order=["대형주","중형주","소형주","잡주"]
print("== 그룹 구성(2024~26 날짜당 평균 종목 수 / 시총 중앙(억) / 25~75% / 거래대금 중앙(억) / 코스닥 비율 / 변동성)")
for gname in order:
    d=S[(S.grp==gname)&(S.date>="2024")]
    n=d.groupby("date").size().mean()
    print(f"{gname} {n:5.0f}종목 시총 {d.mcap_est.median()/1e8:7.0f} ({d.mcap_est.quantile(.25)/1e8:.0f}~{d.mcap_est.quantile(.75)/1e8:.0f}) 거래대금 {d.liqv.median()/1e8:6.1f}억 코스닥 {d.market.fillna('').str.startswith('KOSDAQ').mean()*100:3.0f}% 변동성 {d.atrp.mean()*100:.1f}% 그 구간 20일 평균 {d.r20.mean()*100:+.2f}%")
mk=S.groupby("date").r20.mean()
def pick(pool,K): return pool.sort_values(["date","ens"],ascending=[True,False]).groupby("date").head(K)
print("\n== 그룹 안에서 상위 10 (추가 슬리피지 0.3%p)")
rows=[];yr={}
for gname in order:
    pool=S[S.grp==gname];g=pick(pool,10);gg=g.assign(r=g.r20-0.003).groupby("date").r.mean();base=pool.groupby("date").r20.mean()
    cv,ct,bv,bt=curve(gg,va),curve(gg,te),curve(base,va),curve(base,te)
    gt,gv=g[g.date>="2024"],g[g.date<"2024"]
    rows.append([gname,round(cv[0]),round(bv[0]),round(ct[0]),round(bt[0]),round(ct[1],2),round(ct[2],1),round((gt.r20>0).mean()*100,1),round((gt.r20<=-0.10).mean()*100,1),round(gt.liqv.median()/1e8,1)])
    yr[gname]=" | ".join(f"{Y}: {curve(gg,[d for d in dates_all if d[:4]==str(Y)])[0]:+.0f}/{curve(base,[d for d in dates_all if d[:4]==str(Y)])[0]:+.0f}" for Y in range(2022,2027))
print(pd.DataFrame(rows,columns=["그룹","검증 전략%","검증 그룹평균%","시험 전략%","시험 그룹평균%","시험 샤프","시험 낙폭%","시험 승률%","-10%이하%","고른 종목 거래대금(억)"]).to_string(index=False))
print("\n연도별 전략/그룹평균(%)");[print(k,v) for k,v in yr.items()]
print("\n== 추가 비용 0.3/0.7/1.5%p 시 연수익(검증|시험)")
for gname in order:
    pool=S[S.grp==gname];g=pick(pool,10);line=[]
    for ex in (0.003,0.007,0.015):
        gg=g.assign(r=g.r20-ex).groupby("date").r.mean();line.append(f"{curve(gg,va)[0]:+.0f}|{curve(gg,te)[0]:+.0f}")
    print(gname," ; ".join(line))
print("\n== 거래대금 10억 이상만 허용(상위 10, 추가 0.3%p): 검증|시험|후보 비율")
for gname in order:
    pool=S[S.grp==gname];p2=pool[pool.liqv>=10e8]
    if len(p2)<300: print(gname,"후보 부족(%.0f%%)"%(len(p2)/len(pool)*100));continue
    g=pick(p2,10);gg=g.assign(r=g.r20-0.003).groupby("date").r.mean();print(gname,"%+.0f|%+.0f| 후보 %.0f%%"%(curve(gg,va)[0],curve(gg,te)[0],len(p2)/len(pool)*100))
print("\n== 전체 상위 20에서 그룹 비중 / 그 종목들의 20일 평균·승률·-10%이하")
g=pick(S,20);print((g.grp.value_counts(normalize=True)*100).round(1).reindex(order).to_dict())
print(g.groupby("grp").agg(평균20일=("r20",lambda s:s.mean()*100),승률=("r20",lambda s:(s>0).mean()*100),하락10=("r20",lambda s:(s<=-0.1).mean()*100)).reindex(order).round(2).to_string())
import sqlite3
con=sqlite3.connect("data/stock.db");nm=pd.read_sql("select code,name from symbols",con)
s=S[S.date==S.date.max()].merge(nm,on="code",suffixes=("","_n"))
for gname in order:
    x=s[s.grp==gname].sort_values("mcap_est");print(gname,len(x),[r.name_n for r in x.iloc[[len(x)//6,len(x)//2,len(x)*5//6]].itertuples()])
S.to_pickle(os.environ["TEMP"]+"/stk/S6cap2.pkl")
