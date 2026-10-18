import os,sys,time,numpy as np,pandas as pd,joblib
from sklearn.ensemble import HistGradientBoostingRegressor as HR, HistGradientBoostingClassifier as HC
D=pd.read_pickle(os.environ["TEMP"]+"/stk/D6g.pkl")
sets=joblib.load(os.environ["TEMP"]+"/stk/m1_sets.joblib")["sets"];colsC=sets["C +차트 지표 전체"]
D["y20"]=D.groupby("date")["r20"].rank(pct=True).astype("float32");D["y60"]=D.groupby("date")["r60"].rank(pct=True).astype("float32")
D["up"]=(D.x20>=0.08).astype(int);D["x20c"]=D.x20.clip(-0.5,0.5)
out=[]
for Y in range(2022,2027):
    t=time.time()
    tr=D[(D.yr<Y)&(D.date<f"{Y-1}-12-01")].dropna(subset=["y20"]);te=D[D.yr==Y]
    trs=tr.sample(700000,random_state=1);X=trs[colsC].astype("float32");Xt=te[colsC].astype("float32")
    ms={"rank":HR(max_iter=250,learning_rate=0.06,max_depth=6,min_samples_leaf=200,l2_regularization=2.0,random_state=0).fit(X,trs.y20),
        "up":HC(max_iter=250,learning_rate=0.07,max_depth=5,random_state=0).fit(X,trs.up),
        "reg":HR(max_iter=250,learning_rate=0.07,max_depth=5,min_samples_leaf=200,random_state=0).fit(X,trs.x20c)}
    t60=trs.dropna(subset=["y60"]);ms["r60"]=HR(max_iter=250,learning_rate=0.06,max_depth=6,min_samples_leaf=200,l2_regularization=2.0,random_state=0).fit(t60[colsC].astype("float32"),t60.y60)
    def pred(m,k,X): return m.predict_proba(X)[:,1] if k=="up" else m.predict(X)
    samp=X.sample(150000,random_state=2);cols=[]
    o=te[["date","code","yr","r20","x20","mae20","liqv","grp"]].copy()
    sc=[]
    for k,m in ms.items():
        q=np.quantile(pred(m,k,samp),np.linspace(0,1,101));sc.append(np.searchsorted(q,pred(m,k,Xt),side="right")/100.0)
    o["ai"]=np.clip(np.mean(sc,axis=0),0,1);out.append(o);print(Y,f"{time.time()-t:.0f}초",flush=True)
A=pd.concat(out,ignore_index=True);A.to_pickle(os.environ["TEMP"]+"/stk/AIC.pkl")
