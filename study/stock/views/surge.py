"""거래량 급증 뒤 큰 상승 화면 — data/surge.json (core/surge_study.py)."""
import json

from flask import Blueprint, render_template

from core import surge_study

bp = Blueprint("surge", __name__)
FEATURE_LABEL = {"volratio": "급증일 거래량 ÷ 20일 평균", "ret1": "급증일 등락률", "body": "급증일 몸통", "nh250": "52주 고점 대비", "dist224": "224일선 대비",
                 "dist20": "20일선 대비", "vdry10": "직전 10일 최소 거래량 ÷ 20일 평균", "ret20": "급증 전 20일 등락률", "atrp": "변동성(ATR÷종가)",
                 "price_level": "주가 수준(로그 10)", "mkt_r60": "시장 60일 수익률", "upper": "윗꼬리 비율", "liq": "거래대금 규모(로그)"}
PCT = {"ret1", "body", "nh250", "dist224", "dist20", "ret20", "atrp", "mkt_r60"}


def load():
    try:
        return json.loads(surge_study.SURGE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@bp.get("/surge")
def page():
    rep = load()
    return render_template("surge.html", rep=rep, label=FEATURE_LABEL, pct_feats=PCT)
