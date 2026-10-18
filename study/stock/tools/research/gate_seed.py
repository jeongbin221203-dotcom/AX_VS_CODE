"""AI 필터 기록 처음 채우기: 걸어가며 학습한 2022~2026년 점수(AO.pkl)에서 대형주·중형주의 그룹 안 순위(r_ens)를 data/picks_gate.json 으로.
python tools/research/gate_seed.py → 이후 매일 picks.refresh 가 한 줄씩 더한다."""
import os, sys, json, pandas as pd
sys.path.insert(0, os.getcwd())
from core import picks
A = pd.read_pickle(os.environ["TEMP"] + "/stk/AO.pkl")[["date", "code", "grp", "r_ens"]]
dates = sorted(A.date.unique()); di = {d: i for i, d in enumerate(dates)}
codes = {}
for r in A.itertuples():
    ent = codes.setdefault(r.code, [r.grp, [-1] * len(dates)])
    ent[0] = r.grp
    if r.grp in picks.GATE_GROUPS: ent[1][di[r.date]] = int(round(float(r.r_ens) * 100))
G = {"dates": dates, "codes": {c: v for c, v in codes.items() if any(x >= 0 for x in v[1])}}
picks.GATE_PATH.write_text(json.dumps(G, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print(len(dates), len(G["codes"]), picks.GATE_PATH.stat().st_size // 1024, "KB")
