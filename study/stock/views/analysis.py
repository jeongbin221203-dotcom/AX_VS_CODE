"""기법 정확도 분석 화면 (core/backtest.py 가 만든 data/backtest.json, core/why.py 의 data/why.json)."""
from flask import Blueprint, render_template
from jinja2 import Undefined
from markupsafe import Markup

from core import backtest, techniques, why

bp = Blueprint("analysis", __name__)
MIN_N = 100
ON, OFF = backtest.ACC_SUFFIX_ON, backtest.ACC_SUFFIX_OFF


@bp.app_template_filter("pct")
def pct(v, d=1, sign=False):
    if v is None or isinstance(v, Undefined):
        return Markup("&ndash;")
    return f"{v * 100:+.{d}f}%" if sign else f"{v * 100:.{d}f}%"


bp.add_app_template_global(pct, "pct")   # 템플릿에서 pct(값, 자릿수, 부호) 로도 호출


def verdict(d):
    """시장 평균(같은 날 아무 종목이나 샀을 때)을 뺀 20일 초과수익이 유의하게 +이고 최근 5년에도 +이면 '우위',
    유의하게 −이면 '열위'. 같은 날 신호는 한 표본으로 묶은 t 값을 쓴다(시장 전체가 오른 날 신호가 몰리는 문제 보정)."""
    if d["n_signals"] < MIN_N:
        return "표본 부족", "na"
    ex, rc = d.get("excess"), d.get("excess_recent")
    if not ex or ex.get("t_day") is None:
        return "판단 불가", "na"
    t = ex["t_day"]
    if t >= 2 and ex["mean"] > 0 and (not rc or rc["mean"] > 0):
        return "시장보다 우위", "good"
    if t <= -2:
        return "시장보다 열위", "bad"
    return "차이 없음", "mid"


def _order(labels):
    rank = {n: i for i, n in enumerate(techniques.ORDER)}
    return sorted(labels.items(), key=lambda kv: (rank.get(kv[1]["tech"], 99), -kv[1]["n_signals"]))


def _is_variant(label):
    return label.endswith((ON, OFF))


@bp.get("/analysis")
def page():
    rep = backtest.load_report()
    if not rep:
        return render_template("analysis.html", rep=None)
    labels = rep["labels"]
    rows, acc_rows = [], []
    for label, d in _order({k: v for k, v in labels.items() if not _is_variant(k)}):
        v, cls = verdict(d)
        rows.append({"label": label, "d": d, "verdict": v, "cls": cls, "sim": d["sim"]})
        if label + ON in labels and label + OFF in labels:
            acc_rows.append({"label": label, "base": d, "on": labels[label + ON], "off": labels[label + OFF],
                             "von": verdict(labels[label + ON]), "voff": verdict(labels[label + OFF])})
    return render_template("analysis.html", rep=rep, rows=rows, acc_rows=acc_rows, base=rep["baseline"], min_n=MIN_N,
                           tkeys=("2R", "3R", "5R", "자연1", "자연2"), horizons=("5", "10", "20", "60"),
                           has_why=why.load() is not None)


MODE_TEXT = {"stop_market": "시장이 같이 빠짐(그 뒤 20일 시장 −5% 이하)", "stop_fast": "진입 직후 이탈(3봉 안에 손절)",
             "stop_whip": "손절 뒤 회복(손절이 너무 빡빡)", "stop_slow": "서서히 밀리다 손절",
             "time_loss": "60봉 안에 손절은 안 났지만 손실", "time_win": "손절 없이 60봉 보유 → 이익"}


@bp.get("/analysis/why")
def why_page():
    w = why.load()
    if not w:
        return render_template("why.html", w=None)
    rank = {n: i for i, n in enumerate(techniques.ORDER)}
    items = sorted(w["labels"].items(), key=lambda kv: (rank.get(kv[1].get("tech", ""), 99), -kv[1]["n"]))
    return render_template("why.html", w=w, items=items, modes=MODE_TEXT, base=w.get("baseline"))
