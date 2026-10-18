import os,sys,time,numpy as np,pandas as pd,joblib
from sklearn.ensemble import HistGradientBoostingRegressor as HR, HistGradientBoostingClassifier as HC
D=pd.read_pickle(os.environ["TEMP"]+"/stk/D6g.pkl")
sets=joblib.load(os.environ["TEMP"]+"/stk/m1_sets.joblib")["sets"];colsC=sets["C +차트 지표 전체"]
rk_src=[c for c in colsC if D[c].nunique()>10]
R=D.groupby("date")[rk_src].rank(pct=True).astype("float32");R.columns=[c+"_r" for c in rk_src]
cols=colsC+list(R.columns);D=pd.concat([D,R],axis=1)
D["y20"]=D.groupby(["date","grp"])["r20"].rank(pct=True).astype("float32");D["y60"]=D.groupby(["date","grp"])["r60"].rank(pct=True).astype("float32")
D["up"]=(D.x20>=0.08).astype(int);D["x20c"]=D.x20.clip(-0.5,0.5)
out=[]
for gname in ("대형주","중형주","소형주"):
    J=D[D.grp==gname]
    print(gname,len(J),flush=True)
    for Y in range(2022,2027):
        t=time.time()
        tr=J[(J.yr<Y)&(J.date<f"{Y-1}-12-01")].dropna(subset=["y20"]);te=J[J.yr==Y]
        o=te[["date","code","yr","r20","x20","r60","x60","mae20","liqv","atrp","close","mcap_est","grp"]].copy()
        if len(tr)>600000: tr=tr.sample(600000,random_state=1)
        X=tr[cols].astype("float32");Xt=te[cols].astype("float32");leaf=100 if len(tr)<150000 else 200
        o["j_rank"]=HR(max_iter=250,learning_rate=0.06,max_depth=6,min_samples_leaf=leaf,l2_regularization=2.0,random_state=0).fit(X,tr.y20).predict(Xt)
        o["j_up"]=HC(max_iter=250,learning_rate=0.07,max_depth=5,random_state=0).fit(X,tr.up).predict_proba(Xt)[:,1]
        o["j_reg"]=HR(max_iter=250,learning_rate=0.07,max_depth=5,min_samples_leaf=leaf,random_state=0).fit(X,tr.x20c).predict(Xt)
        t60=tr.dropna(subset=["y60"])
        o["j_r60"]=HR(max_iter=250,learning_rate=0.06,max_depth=6,min_samples_leaf=leaf,l2_regularization=2.0,random_state=0).fit(t60[cols].astype("float32"),t60.y60).predict(Xt)
        out.append(o);print(gname,Y,len(tr),len(te),f"{time.time()-t:.0f}초",flush=True)
A=pd.concat(out,ignore_index=True);A.to_pickle(os.environ["TEMP"]+"/stk/SG.pkl")
