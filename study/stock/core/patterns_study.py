"""하락하다 다시 상승하는 패턴 / 상승 추세가 이어지는 패턴 분석.

두 상태를 정의하고(신호일 종가까지의 정보로만), 그 뒤 60거래일의 결과(+30% 도달, 저점 이탈 등)와 특징의 관계를 본다.
- 하락 후 반등 시작: 120일 고점 대비 −25% 이하까지 빠졌다가, 60일 저점에서 5~40봉 지났고, 저점 대비 +8% 이상 반등해 20일선 위에 있는 상태.
  성공 = 60거래일 안에 +30% 도달(고가) 하면서 직전 60일 저점을 다시 깨지 않음 / 실패 = 60거래일 안에 저점 이탈.
- 상승 추세 지속: 정배열(5>20>60>112) · 60일선 위 · 52주 고점 −20% 이내. 성공 = 60거래일 안에 +30% 도달 / 실패 = 60일 뒤 −10% 이하.
방법: 구간별 성공률(기준 대비 배수), 이름 붙인 패턴의 성공률, 얕은 결정 트리(깊이 3)로 규칙을 찾고 처음 보는 기간(2022~)에서 다시 확인.
"""
import math

import numpy as np
import pandas as pd

TRAIN_END = "2021-12-31"
MIN_N = 300
PCT_FEATURES = {"dist5", "dist20", "dist60", "dist112", "dist224", "slope20", "slope60", "nh250", "ret1", "ret5", "ret10", "ret20", "ret60",
                "atrp", "vol20", "range1", "gap0", "body", "mkt_r20", "mkt_r60", "mkt_dd250", "mkt_vol20", "dd120", "rebound60", "hl10",
                "range20", "hh20_gap", "rs20"}

STATES = {
    "rebound": {
        "title": "하락하다 다시 상승 (하락 후 반등 시작)",
        "define": "120일 고점 대비 −25% 이하까지 하락 → 60일 저점에서 5~40봉 경과 → 저점 대비 +8% 이상 반등 → 20일선 위",
        "success": "60거래일 안에 +30% 도달 & 직전 60일 저점을 다시 깨지 않음", "fail": "60거래일 안에 저점 이탈",
        "mask": lambda d: (d["dd120"] <= -0.25) & (d["low_age"] >= 5) & (d["low_age"] <= 40) & (d["rebound60"] >= 0.08) & (d["dist20"] > 0),
        "succ": lambda d: (d["mx60"] >= 0.30) & (d["fail60"] == 0), "failm": lambda d: d["fail60"] == 1,
        "features": ["dd120", "low_age", "rebound60", "hl10", "dist60", "dist112", "cross20", "cross60", "vdry10", "volratio", "vmax10", "vr5",
                     "vr20_60", "range20", "slope20", "atrp", "mkt_r60", "mkt_dd250", "ret20", "liq"],
        "tree": ["dd120", "low_age", "rebound60", "hl10", "dist60", "dist112", "cross20", "cross60", "vdry10", "volratio", "vmax10", "vr5",
                 "vr20_60", "range20", "atrp", "mkt_r60", "ret20", "upvol20", "obv20", "slope20"],
        "named": [
            ("저점을 지키는 반등 (최근 10일 저점이 60일 저점 이상)", lambda d: d["hl10"] >= 0),
            ("저점을 다시 건드림 (최근 10일 저점이 60일 저점 근처 −3% 이내)", lambda d: d["hl10"] < 0.03),
            ("20일선 상향돌파 10봉 이내", lambda d: d["cross20"] <= 10),
            ("60일선 위로 회복", lambda d: d["dist60"] > 0),
            ("112일선 위로 회복", lambda d: d["dist112"] > 0),
            ("반등 중 거래량 급증 (10일 최대가 20일 평균의 3배↑)", lambda d: d["vmax10"] >= 3),
            ("거래량 마름 후 반등 (10일 최소가 0.4배↓)", lambda d: d["vdry10"] < 0.4),
            ("깊은 하락 (−50% 이하)", lambda d: d["dd120"] <= -0.50),
            ("얕은 하락 (−25~−35%)", lambda d: (d["dd120"] > -0.35)),
            ("저점 후 오래 기다림 (30봉↑)", lambda d: d["low_age"] >= 30),
            ("이미 많이 반등 (저점 대비 +40%↑)", lambda d: d["rebound60"] >= 0.40),
            ("반등 초입 (저점 대비 +8~15%)", lambda d: (d["rebound60"] < 0.15)),
            ("시장 약세 (시장 60일 수익률 −5% 이하)", lambda d: d["mkt_r60"] <= -0.05),
            ("저점 지킴 + 20일선 돌파 직후 + 거래량 증가", lambda d: (d["hl10"] >= 0) & (d["cross20"] <= 10) & (d["vr5"] >= 1.2)),
        ],
    },
    "uptrend": {
        "title": "상승 추세 지속 (정배열 상승)",
        "define": "정배열(5>20>60>112일선) · 종가가 60일선 위 · 52주 고점 대비 −20% 이내",
        "success": "60거래일 안에 +30% 도달", "fail": "60거래일 뒤 종가가 진입가 대비 −10% 이하",
        "mask": lambda d: (d["aligned"] == 1) & (d["dist60"] > 0) & (d["nh250"] >= -0.20),
        "succ": lambda d: d["mx60"] >= 0.30, "failm": lambda d: d["r60"] <= -0.10,
        "features": ["dist20", "dist60", "hh20_gap", "nh250", "range20", "slope20", "slope60", "ret20", "ret60", "atrp", "vdry10", "volratio",
                     "vr5", "vr20_60", "vspike_n", "upvol20", "obv20", "mkt_r60", "mkt_dd250", "liq"],
        "tree": ["dist20", "dist60", "hh20_gap", "nh250", "range20", "slope20", "slope60", "ret20", "ret60", "atrp", "vdry10", "volratio", "vr5",
                 "vr20_60", "vspike_n", "upvol20", "obv20", "mkt_r60"],
        "named": [
            ("20일선 가까이 눌림 (종가가 20일선 위 0~3%)", lambda d: (d["dist20"] >= 0) & (d["dist20"] < 0.03)),
            ("20일선에서 멀리 벌어짐 (+15%↑)", lambda d: d["dist20"] >= 0.15),
            ("20일 고점 돌파 직전·직후 (20일 고점 대비 −2% 이내)", lambda d: d["hh20_gap"] >= -0.02),
            ("가격 응축 (20일 가격 폭 12% 이하)", lambda d: d["range20"] <= 0.12),
            ("거래량 마름 (10일 최소가 20일 평균의 0.4배↓)", lambda d: d["vdry10"] < 0.4),
            ("거래량 급증 동반 (당일 3배↑)", lambda d: d["volratio"] >= 3),
            ("52주 고점 −3% 이내", lambda d: d["nh250"] >= -0.03),
            ("60일선 기울기 가파름 (20봉 +10%↑)", lambda d: d["slope60"] >= 0.10),
            ("이미 급등 (20일 +30%↑)", lambda d: d["ret20"] >= 0.30),
            ("변동성 낮음 (ATR÷종가 3% 이하)", lambda d: d["atrp"] <= 0.03),
            ("상승일 거래량 우세 (상승일 비중 60%↑)", lambda d: d["upvol20"] >= 0.60),
            ("시장 강세 (시장 60일 +10%↑)", lambda d: d["mkt_r60"] >= 0.10),
            ("눌림 + 응축 + 거래량 마름", lambda d: (d["dist20"] < 0.03) & (d["range20"] <= 0.15) & (d["vdry10"] < 0.5)),
        ],
    },
}


def _fmt(name: str, v: float) -> str:
    return f"{v * 100:.1f}%" if name in PCT_FEATURES else f"{v:.2f}"


def _buckets(d: pd.DataFrame, succ: np.ndarray, failm: np.ndarray, feats, label):
    out = []
    base = float(succ.mean())
    for f in feats:
        x = d[f]
        if x.notna().sum() < 500:
            continue
        try:
            edges = np.unique(np.nanquantile(x, np.linspace(0, 1, 6)))
        except Exception:
            continue
        if len(edges) < 3:
            continue
        edges[0], edges[-1] = -np.inf, np.inf
        b = pd.cut(x, edges, include_lowest=True)
        g = pd.DataFrame({"b": b, "s": succ, "f": failm}).groupby("b", observed=True)
        rows = []
        for iv, v in g:
            if len(v) < 100:
                continue
            rows.append({"lo": None if not np.isfinite(iv.left) else float(iv.left), "hi": None if not np.isfinite(iv.right) else float(iv.right),
                         "n": int(len(v)), "succ": float(v["s"].mean()), "fail": float(v["f"].mean()), "lift": float(v["s"].mean() / base) if base else None})
        if len(rows) >= 3:
            spread = max(r["succ"] for r in rows) - min(r["succ"] for r in rows)
            out.append({"name": f, "label": label[f], "pct": f in PCT_FEATURES, "spread": spread, "rows": rows})
    out.sort(key=lambda r: -r["spread"])
    return out


def _named(d: pd.DataFrame, succ, failm, r60, named):
    base = float(succ.mean())
    out = []
    for name, fn in named:
        m = fn(d).to_numpy()
        if m.sum() < 200:
            continue
        out.append({"name": name, "n": int(m.sum()), "succ": float(succ[m].mean()), "fail": float(failm[m].mean()),
                    "lift": float(succ[m].mean() / base) if base else None, "r60": float(np.nanmean(r60[m]))})
    out.sort(key=lambda r: -(r["succ"]))
    return out


def tree_rules(clf, names, label, X_tr, y_tr, X_te, y_te, base_te):
    """결정 트리의 잎(규칙)마다 조건·학습/시험 기간 성공률."""
    t = clf.tree_
    paths = []

    def walk(node, conds):
        if t.children_left[node] == -1:
            paths.append((node, conds))
            return
        f, thr = names[t.feature[node]], float(t.threshold[node])
        walk(t.children_left[node], conds + [(f, "<=", thr)])
        walk(t.children_right[node], conds + [(f, ">", thr)])

    walk(0, [])
    leaf_tr, leaf_te = clf.apply(X_tr), clf.apply(X_te)
    rules = []
    for node, conds in paths:
        mt, me = leaf_tr == node, leaf_te == node
        if mt.sum() < 100:
            continue
        text = " 그리고 ".join(f"{label[f]} {'≤' if op == '<=' else '>'} {_fmt(f, thr)}" for f, op, thr in conds)
        rules.append({"rule": text, "n_train": int(mt.sum()), "rate_train": float(y_tr[mt].mean()),
                      "n_test": int(me.sum()), "rate_test": float(y_te[me].mean()) if me.sum() >= 50 else None,
                      "lift_test": float(y_te[me].mean() / base_te) if me.sum() >= 50 and base_te else None})
    rules.sort(key=lambda r: -(r["rate_train"]))
    return rules


def analyze(D: pd.DataFrame, label: dict, log=print) -> dict:
    """D: 거래 가능 표본의 특징(이름 열)·date·code·r60·mx60·fail60·r120 열을 가진 DataFrame."""
    from sklearn.tree import DecisionTreeClassifier
    out = {}
    for key, st in STATES.items():
        m = st["mask"](D).to_numpy()
        d = D[m]
        d = d[d["mx60"].notna() & d["r60"].notna()]
        if len(d) < MIN_N * 3:
            continue
        succ = st["succ"](d).to_numpy().astype(int)
        failm = st["failm"](d).to_numpy().astype(int)
        r60 = d["r60"].to_numpy(float)
        tr = (d["date"] <= TRAIN_END).to_numpy()
        res = {"title": st["title"], "define": st["define"], "success_def": st["success"], "fail_def": st["fail"],
               "n": int(len(d)), "n_all": int(len(D)), "share": float(m.mean()), "succ": float(succ.mean()), "fail": float(failm.mean()),
               "r60": float(np.nanmean(r60)), "r60_median": float(np.nanmedian(r60)),
               "succ_test": float(succ[~tr].mean()) if (~tr).sum() else None, "n_test": int((~tr).sum()),
               "r120": float(np.nanmean(d["r120"])) if d["r120"].notna().any() else None}
        # 시장 전체의 같은 결과 비율(비교 기준): 상태가 아닌 모든 표본
        rest = D[~m]
        rest = rest[rest["mx60"].notna() & rest["r60"].notna()]
        res["succ_rest"] = float(st["succ"](rest).mean()) if len(rest) else None
        res["buckets"] = _buckets(d, succ, failm, st["features"], label)[:10]
        res["named"] = _named(d, succ, failm, r60, st["named"])
        # 결정 트리 규칙(학습 ≤2021, 시험 2022~)
        cols = st["tree"]
        Xtr = d.loc[tr, cols].fillna(d[cols].median()).to_numpy(float)
        Xte = d.loc[~tr, cols].fillna(d[cols].median()).to_numpy(float)
        if tr.sum() > 2000 and (~tr).sum() > 500:
            clf = DecisionTreeClassifier(max_depth=3, min_samples_leaf=max(400, int(tr.sum() * 0.02)), random_state=0)
            clf.fit(Xtr, succ[tr])
            res["tree"] = tree_rules(clf, cols, label, Xtr, succ[tr], Xte, succ[~tr], float(succ[~tr].mean()))
        else:
            res["tree"] = []
        out[key] = res
        log(f"[패턴] {st['title']}: {res['n']:,}건 · 성공 {res['succ'] * 100:.1f}% · 실패 {res['fail'] * 100:.1f}%")
    return out


def current_matches(Xl: pd.DataFrame, names: dict, key: str, label: dict, top=15):
    """가장 최근 거래일에 그 상태인 종목들(패턴 규칙의 최고 성공률 조건에 가까운 순)."""
    st = STATES[key]
    m = st["mask"](Xl).to_numpy()
    d = Xl[m].copy()
    if d.empty:
        return []
    if key == "rebound":
        d["score"] = (d["hl10"] >= 0).astype(int) * 2 + (d["cross20"] <= 10).astype(int) + (d["dist60"] > 0).astype(int) + (d["vr5"] >= 1.2).astype(int)
    else:
        d["score"] = (d["dist20"] < 0.05).astype(int) + (d["range20"] <= 0.15).astype(int) + (d["vdry10"] < 0.5).astype(int) + (d["hh20_gap"] >= -0.03).astype(int)
    d = d.sort_values(["score", "liq"], ascending=False).head(top)
    keep = ["code", "close", "score", "dd120", "rebound60", "low_age", "hl10", "dist20", "dist60", "cross20", "volratio", "vr5", "vdry10",
            "range20", "hh20_gap", "nh250", "ret20"]
    rows = []
    for _, r in d.iterrows():
        row = {k: (None if k not in r or pd.isna(r[k]) else (float(r[k]) if k != "code" else r[k])) for k in keep}
        row["name"] = names.get(r["code"], r["code"])
        rows.append(row)
    return rows
