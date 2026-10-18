import os,numpy as np,pandas as pd
exec(open(os.environ["TEMP"]+"/stk/o2.py",encoding="utf-8").read().split("rows=[]")[0])
CF={"대형주":(20,"r_ens",5,3e8,False),"중형주":(20,"r_ens",10,3e8,False),"소형주":(20,"r_ens",7,3e8,True),"잡주":(20,"score",5,3e8,False)}
def series(g,win,extra=0.003): return run(g,*CF[g],win,extra)
def combo(groups,win,extra=0.003,w=None):
    S=pd.concat([series(g,win,extra) for g in groups],axis=1).reindex(win).fillna(0.0)
    w=np.ones(len(groups))/len(groups) if w is None else np.array(w)
    return (S*w).sum(axis=1)
print("== 그룹별 추천 설정 (20일 보유)")
for g in CF:
    cv,ct=curve(series(g,va),va,6),curve(series(g,te),te,6)
    print(f"{g}: K={CF[g][2]} 위험필터={CF[g][4]} 검증 {cv[0]:+.1f}% (샤프 {cv[1]:.2f}, 낙폭 {cv[2]:.1f}%) | 시험 {ct[0]:+.1f}% (샤프 {ct[1]:.2f}, 낙폭 {ct[2]:.1f}%)")
for name,groups in (("4그룹 동일 비중",["대형주","중형주","소형주","잡주"]),("중형주 제외 3그룹",["대형주","소형주","잡주"]),("대형+잡주",["대형주","잡주"])):
    print(f"\n== {name}")
    for ex in (0.003,0.007,0.015):
        gv,gt=combo(groups,va,ex),combo(groups,te,ex);cv,ct=curve(gv,va,6),curve(gt,te,6)
        print(f"  추가비용 {ex*100:.1f}%p: 검증 {cv[0]:+.1f}% (샤프 {cv[1]:.2f}, 낙폭 {cv[2]:.1f}%, 배수 {cv[3]:.2f}) | 시험 {ct[0]:+.1f}% (샤프 {ct[1]:.2f}, 낙폭 {ct[2]:.1f}%, 배수 {ct[3]:.2f})")
g3=combo(["대형주","소형주","잡주"],dates_all)
print("\n연도별(중형주 제외 3그룹):"," | ".join(f"{Y}: {curve(g3,[d for d in dates_all if d[:4]==str(Y)],6)[0]:+.0f}%" for Y in range(2022,2027)))
mk=A.groupby("date").r20.mean();print("전체 시장 평균(비교): 검증 %.1f%% 시험 %.1f%%"%(curve(mk,va,6)[0],curve(mk,te,6)[0]))
for g in CF:
    print(g,"연도별:"," | ".join(f"{Y}: {curve(series(g,dates_all),[d for d in dates_all if d[:4]==str(Y)],6)[0]:+.0f}%" for Y in range(2022,2027)))
