import os,sys,time,numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor as HR
D=pd.read_pickle(os.environ["TEMP"]+"/stk/D6.pkl")
meta=["date","code","yr","r5","r20","r60","r120","x5","x20","x60","x120","mae20","liqv","dow","month"]
allf=[c for c in D.columns if c not in meta]
nomkt=[c for c in allf if not c.startswith("mkt_") and c not in("price_level","hist_bars")]
print(len(allf),"특징 후보,",len(nomkt),"시장지수 제외")
base_ai=[c for c in nomkt if c in ['dist5','dist20','dist60','dist112','dist224','slope20','slope60','pos60','nh250','aligned','reversed','ret1','ret5','ret10','ret20','ret60','atrp','vol20','range1','gap0','body','upper','lower','volratio','vr5','vr20_60','vmax10','vdry10','upvol20','obv20','liq','liq_chg','vspike_n','acc_recent','dd120','low_age','rebound60','hl10','range20','hh20_gap','cross20','cross60','cross112']]
exp=["mom12_1","mom6_1","vol60","rsi2" if "rsi2" in nomkt else "rsi14","above200","turn"]
newind=[c for c in nomkt if c not in base_ai and c not in exp]
sets={"A 기존 특징":base_ai,"B +전문가(모멘텀·변동성·거래회전)":base_ai+[c for c in exp if c in nomkt],"C +차트 지표 전체":base_ai+[c for c in exp if c in nomkt]+newind}
# 단면 순위(날짜별 백분위) 특징
rk_src=[c for c in sets["C +차트 지표 전체"] if D[c].nunique()>10]
t=time.time()
R=D.groupby("date")[rk_src].rank(pct=True).astype("float32");R.columns=[c+"_r" for c in rk_src]
D=pd.concat([D,R],axis=1);print("순위 특징",len(rk_src),round(time.time()-t),"초",flush=True)
sets["D C + 단면 순위"]=sets["C +차트 지표 전체"]+list(R.columns)
D["y_rank"]=D.groupby("date")["r20"].rank(pct=True).astype("float32")
tr=D[D.yr<=2021].dropna(subset=["y_rank"]);va=D[(D.yr>=2022)&(D.yr<=2023)];te=D[D.yr>=2024]
trs=tr.sample(600000,random_state=0)
def evalset(df,score,label):
    d=df.assign(s=score);out={}
    ic=d.groupby("date").apply(lambda g:g.s.corr(g.y_rank,method="spearman") if len(g)>50 else np.nan)
    d["q"]=d.groupby("date").s.rank(pct=True)
    top=d[d.q>=0.9];top30=d.sort_values(["date","s"],ascending=[True,False]).groupby("date").head(30)
    return f"{label:8s} 순위IC {ic.mean():+.3f} (t {ic.mean()/ic.std()*np.sqrt(len(ic.dropna())):.1f}) 상위10% 20일초과 {top.x20.mean()*100:+.2f}%p 승률 {(top.r20>0).mean()*100:.1f}% | 상위30종목 {top30.x20.mean()*100:+.2f}%p -10%↓ {(top30.r20<=-0.10).mean()*100:.1f}%"
res={}
for nm,cols in sets.items():
    t=time.time()
    m=HR(max_iter=300,learning_rate=0.06,max_depth=6,min_samples_leaf=200,l2_regularization=2.0,random_state=0).fit(trs[cols].astype("float32"),trs.y_rank)
    sv=m.predict(va[cols].astype("float32"));st=m.predict(te[cols].astype("float32"))
    print(f"[{nm}] ({len(cols)}개, {time.time()-t:.0f}초)");print("  검증 2022-23:",evalset(va,sv,"검증"));print("  시험 2024-26:",evalset(te,st,"시험"),flush=True)
    res[nm]=m
import joblib;joblib.dump({"sets":sets},os.environ["TEMP"]+"/stk/m1_sets.joblib")
