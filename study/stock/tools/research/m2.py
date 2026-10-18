import os,sys,time,numpy as np,pandas as pd,joblib
from sklearn.ensemble import HistGradientBoostingRegressor as HR, HistGradientBoostingClassifier as HC
D=pd.read_pickle(os.environ["TEMP"]+"/stk/D6.pkl")
sets=joblib.load(os.environ["TEMP"]+"/stk/m1_sets.joblib")["sets"]
colsC=sets["C +차트 지표 전체"];colsD=[c for c in sets["D C + 단면 순위"]]
rk_src=[c for c in colsC if D[c].nunique()>10]
R=D.groupby("date")[rk_src].rank(pct=True).astype("float32");R.columns=[c+"_r" for c in rk_src]
D=pd.concat([D,R],axis=1)
D["y_rank"]=D.groupby("date")["r20"].rank(pct=True).astype("float32")
D["dn10"]=(D.r20<=-0.10).astype(int)
out=[]
for Y in range(2022,2027):
    t=time.time()
    tr=D[(D.yr<Y)].dropna(subset=["y_rank"])
    # 라벨이 확정된 날짜까지만(20일 뒤 결과가 필요) — 해 초 직전 30일은 제외
    cut=f"{Y-1}-12-01";tr=tr[tr.date<cut]
    trs=tr.sample(min(700000,len(tr)),random_state=0)
    te=D[D.yr==Y]
    mr=HR(max_iter=300,learning_rate=0.06,max_depth=6,min_samples_leaf=200,l2_regularization=2.0,random_state=0).fit(trs[colsD].astype("float32"),trs.y_rank)
    mc=HC(max_iter=200,learning_rate=0.08,max_depth=5,random_state=0).fit(trs[colsC].astype("float32"),trs.dn10)
    o=te[["date","code","yr","r20","x20","r60","x60","mae20","liqv","atrp","vol60","kosdaq"]].copy()
    o["score"]=mr.predict(te[colsD].astype("float32"));o["P"]=mc.predict_proba(te[colsC].astype("float32"))[:,1]
    out.append(o);print(Y,len(te),f"{time.time()-t:.0f}초",flush=True)
    if Y==2026:
        joblib.dump({"rank":mr,"risk":mc,"colsD":colsD,"colsC":colsC},os.environ["TEMP"]+"/stk/m2_models_2026.joblib")
S=pd.concat(out,ignore_index=True);S.to_pickle(os.environ["TEMP"]+"/stk/S6.pkl");print(len(S))
