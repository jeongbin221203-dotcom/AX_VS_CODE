import os,numpy as np,pandas as pd
A=pd.read_pickle(os.environ["TEMP"]+"/stk/AIC.pkl").sort_values(["code","date"])
A["prev"]=A.groupby("code")["ai"].shift(1)       # 직전 표본(약 5일 전) 점수
A["mk"]=A.groupby("date").r20.transform("mean")
A["ex"]=A.r20-A.mk
va=A.yr<=2023;te=A.yr>=2024
def row(name,m):
    out=[name]
    for lab,w in (("검증",va),("시험",te)):
        d=A[m&w]
        out+= [len(d),round(d.r20.mean()*100,2),round(d.ex.mean()*100,2),round((d.r20>0).mean()*100,1),round((d.r20<=-0.1).mean()*100,1)] if len(d)>100 else [len(d),np.nan,np.nan,np.nan,np.nan]
    return out
cols=["조건","검증 n","검증 20일%","초과%p","승률%","-10%↓%","시험 n","시험 20일%","초과%p","승률%","-10%↓%"]
rows=[row("아무 날",A.ai>-1)]
for thr in (0.70,0.80,0.90,0.95,0.98):
    rows.append(row(f"점수 ≥{thr*100:.0f} (상태)",A.ai>=thr))
    rows.append(row(f"점수 ≥{thr*100:.0f} 처음 넘음(직전<{thr*100:.0f})",(A.ai>=thr)&(A.prev<thr)))
rows.append(row("점수 ≤30 (약함, 참고)",A.ai<=0.30))
pd.set_option("display.width",250);print(pd.DataFrame(rows,columns=cols).round(2).to_string(index=False))
print("\n그룹별 점수≥90 처음 넘음 (시험)")
for g in ("대형주","중형주","소형주","잡주"):
    print(g,row(g,(A.ai>=0.90)&(A.prev<0.90)&(A.grp==g))[1:])
print("\n그룹별 점수≥95 (상태, 시험)")
for g in ("대형주","중형주","소형주","잡주"):
    print(g,row(g,(A.ai>=0.95)&(A.grp==g))[1:])
