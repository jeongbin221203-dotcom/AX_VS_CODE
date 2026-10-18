import os,numpy as np,pandas as pd
exec(open(os.environ["TEMP"]+"/stk/j3.py",encoding="utf-8").read().split("rows=[]")[0])
def picks(col,K,win):
    d=A[A.date.isin(win)];return d.sort_values(["date",col],ascending=[True,False]).groupby("date").head(K)
for col,K in (("ens_jg",5),("ens_jg",10),("ens_j",5),("ens_j",10)):
    print(f"\n=== {col} K={K}")
    g=run(col,K,None,0.003,dates_all)
    print("연도별 연수익(%) / 잡주 전체 평균:"," | ".join(f"{Y}: {curve(g,[d for d in dates_all if d[:4]==str(Y)])[0]:+.0f}/{curve(base,[d for d in dates_all if d[:4]==str(Y)])[0]:+.0f}" for Y in range(2022,2027)))
    print("비용(추가) 0.3/0.7/1.5/3.0%p → 검증|시험:"," ; ".join(f"{curve(run(col,K,None,ex,va),va)[0]:+.0f}|{curve(run(col,K,None,ex,te),te)[0]:+.0f}" for ex in (0.003,0.007,0.015,0.03)))
    p=picks(col,K,dates_all);r=p.r20
    print("한 종목 20일 수익 분포: 평균 %.1f%% 중앙 %.1f%% | 승률 %.1f%% | 이긴 평균 %+.1f%% 진 평균 %+.1f%% | 하위5%% %.1f%% 최악 %.1f%% | -30%%이하 %.1f%% +30%%이상 %.1f%%"%(r.mean()*100,r.median()*100,(r>0).mean()*100,r[r>0].mean()*100,r[r<=0].mean()*100,r.quantile(.05)*100,r.min()*100,(r<=-0.3).mean()*100,(r>=0.3).mean()*100))
    print("고른 종목: 거래대금 중앙 %.1f억 (하위10%% %.1f억), 시총 중앙 %.0f억, 변동성 %.1f%%"%(p.liqv.median()/1e8,p.liqv.quantile(.1)/1e8,p.mcap_est.median()/1e8,p.atrp.mean()*100))
    # 월 단위(28일) 수익 분포
    gg=g.reindex(dates_all).fillna(0).to_numpy();step=np.mean([gg[k::6][:min(len(gg[j::6]) for j in range(6))] for k in range(6)],axis=0)
    print("28일 단위 수익: 플러스 %.0f%% | 평균 %+.1f%% | 최악 %+.1f%% | 하위10%% %+.1f%% | 최고 %+.1f%%"%((step>0).mean()*100,step.mean()*100,step.min()*100,np.quantile(step,.1)*100,step.max()*100))
    # 최근 날짜 후보 (재학습 이후 실제 라이브 느낌)
last=A.date.max();print("\n마지막 점수 날짜",last)
