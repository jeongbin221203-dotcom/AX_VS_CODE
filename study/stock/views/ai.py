"""AI(머신러닝) 분석 화면 — data/ai.json (core/ai.py 가 만든다)."""
import json

from flask import Blueprint, render_template

from core import ai

bp = Blueprint("ai", __name__)
VOLUME_FEATS = ("volratio", "vr5", "vdry10", "vspike_n", "upvol20", "obv20", "liq", "liq_chg")


def load():
    try:
        return json.loads(ai.AI_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@bp.get("/ai")
def page():
    rep = load()
    if not rep:
        return render_template("ai.html", rep=None)
    vol = [(f, ai.LABEL[f], rep["buckets"][f]) for f in VOLUME_FEATS if f in rep["buckets"]]
    other = [(f, ai.LABEL.get(f, f), rows) for f, rows in rep["buckets"].items() if f not in VOLUME_FEATS]
    imp = rep["importance"][:15]
    top_imp = max([r["imp"] for r in imp] + [1e-9])
    groups = sorted(rep["group_importance"].items(), key=lambda kv: -kv[1])
    top_g = max([v for _, v in groups] + [1e-9])
    horizons = sorted(rep.get("horizons", {}).items(), key=lambda kv: kv[1]["hold"])
    return render_template("ai.html", rep=rep, horizons=horizons, vol=vol, other=other, imp=imp, top_imp=top_imp, groups=groups, top_g=top_g,
                           group_of=ai.GROUP, methods=rep["topn"]["methods"])


@bp.get("/patterns")
def patterns():
    rep = load()
    pats = (rep or {}).get("patterns")
    if not pats:
        return render_template("patterns.html", pats=None)
    return render_template("patterns.html", pats=pats, meta=rep["meta"], feature_label=ai.LABEL, pct_names=ai.PCT_NAMES)
