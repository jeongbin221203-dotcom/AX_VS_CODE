import os,itertools,numpy as np,pandas as pd
A=pd.read_pickle(os.environ["TEMP"]+"/stk/AG.pkl")
D6=pd.read_pickle(os.environ["TEMP"]+"/stk/D6g.pkl")[["code","date","r5","r120","mae20"]].rename(columns={"mae20":"mae20b"})
B=pd.read_pickle(os.environ["TEMP"]+"/stk/S6b.pkl")[["date","code","s_r60"]]
S6=pd.read_pickle(os.environ["TEMP"]+"/stk/S6.pkl")[["date","code","P"]]
A=A.merge(D6,on=["code","date"],how="left").merge(B,on=["date","code"],how="left").merge(S6,on=["date","code"],how="left")
A["r_s_r60"]=A.groupby(["date","grp"])["s_r60"].rank(pct=True)
A["h60"]=(A.r_j_r60+A.r_s_r60)/2                       # 60일 보유용 점수: 그룹 60일 모델 + 전체 60일 모델
A["rP"]=A.groupby(["date","grp"])["P"].rank(pct=True)
A.to_pickle(os.environ["TEMP"]+"/stk/AO.pkl")
print(len(A),A[["r5","r120","P","h60"]].notna().mean().round(2).to_dict())
