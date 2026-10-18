import os,sys,time,numpy as np,pandas as pd,joblib
from sklearn.ensemble import HistGradientBoostingRegressor as HR, HistGradientBoostingClassifier as HC
D=pd.read_pickle(os.environ["TEMP"]+"/stk/D6.pkl")
sets=joblib.load(os.environ["TEMP"]+"/stk/m1_sets.joblib")["sets"]
colsC=sets["C +차트 지표 전체"];colsD=sets["D C + 단면 순위"]
rk_src=[c for c in colsC if D[c].nunique()>10]
R=D.groupby("date")[rk_src].rank(pct=True).astype("float32");R.columns=[c+"_r" for c in rk_src];D=pd.concat([D,R],axis=1)
D["y_rank"]=D.groupby("date")["r20"].rank(pct=True).astype("float32")
D["y_rank60"]=D.groupby("date")["r60"].rank(pct=True).astype("float32")
D["up_ex"]=(D.x20>=0.08).astype(int);D["x20c"]=D.x20.clip(-0.5,0.5)
out=[]
for Y in range(2022,2027):
    t=time.time()
    tr=D[(D.yr<Y)&(D.date<f"{Y-1}-12-01")]
    trs=tr.dropna(subset=["y_rank"]).sample(700000,random_state=1)
    te=D[D.yr==Y]
    o=te[["date","code","yr","r20","x20","r60","x60","liqv","atrp"]].copy()
    mu=HC(max_iter=250,learning_rate=0.07,max_depth=5,random_state=0).fit(trs[colsD].astype("float32"),trs.up_ex);o["s_up"]=mu.predict_proba(te[colsD].astype("float32"))[:,1]
    mg=HR(max_iter=250,learning_rate=0.07,max_depth=5,min_samples_leaf=200,random_state=0).fit(trs[colsD].astype("float32"),trs.x20c);o["s_reg"]=mg.predict(te[colsD].astype("float32"))
    t60=trs.dropna(subset=["y_rank60"])
    m6=HR(max_iter=250,learning_rate=0.07,max_depth=6,min_samples_leaf=200,l2_regularization=2.0,random_state=0).fit(t60[colsD].astype("float32"),t60.y_rank60);o["s_r60"]=m6.predict(te[colsD].astype("float32"))
    out.append(o);print(Y,f"{time.time()-t:.0f}초",flush=True)
A=pd.concat(out,ignore_index=True);A.to_pickle(os.environ["TEMP"]+"/stk/S6b.pkl")
