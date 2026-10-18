import os,numpy as np,pandas as pd
exec(open(os.environ["TEMP"]+"/stk/cap2.py",encoding="utf-8").read().split("mk=S.groupby")[0])
def pick(pool,K): return pool.sort_values(["date","ens"],ascending=[True,False]).groupby("date").head(K)
print("시총 | 추가비용 0.3 / 0.7 / 1.5 %p 일 때 연수익(검증 | 시험) , 시험 배수")
for qn in (1,2,3,4):
    pool=S[S.qcap==qn];g=pick(pool,10);line=[]
    for ex in (0.003,0.007,0.015):
        gg=g.assign(r=g.r20-ex).groupby("date").r.mean();cv,ct=curve(gg,va),curve(gg,te);line.append(f"{cv[0]:+.0f}% | {ct[0]:+.0f}% (x{ct[3]:.2f})")
    print(qn," ; ".join(line))
# 거래대금 하한 적용(구간 안에서 10억 이상만)
print("\n거래대금 10억 이상만 허용(상위 10, 추가 0.3%p): 시총 | 검증 | 시험 | 후보 비율")
for qn in (1,2,3,4):
    pool=S[(S.qcap==qn)];p2=pool[pool.liqv>=10e8];g=pick(p2,10);gg=g.assign(r=g.r20-0.003).groupby("date").r.mean()
    print(qn,"%+.0f%% | %+.0f%% | 후보 %.0f%%"%(curve(gg,va)[0],curve(gg,te)[0],len(p2)/len(pool)*100))
# 종목 예: 2026-10 시점 구간별 대표 종목(현재 시총 기준)
import sqlite3
con=sqlite3.connect("data/stock.db")
sym=pd.read_sql("select code,name,marcap from symbols where marcap is not null",con)
s=S[S.date==S.date.max()].merge(sym[["code","name"]],on="code",suffixes=("","_n"))
for qn in (1,2,3,4):
    x=s[s.qcap==qn].sort_values("mcap_est");print(qn,[ (r.name_n if "name_n" in x.columns else r.name) for r in x.iloc[[len(x)//10,len(x)//2,len(x)*9//10]].itertuples()])
