"""휴대폰·외부에서 보는 읽기 전용 '오늘의 신호' 스냅샷 (Render 배포용).

본 앱(app.py)은 DB(약 660MB)·AI 모델·표본 캐시가 있어야 돌아서 서버에 올리지 않고,
PC 에서 전 종목 훑기 뒤 만든 snapshot/signals.json 한 파일만 읽어 신호 화면(추천·강/상·예상 확률)을 보여 준다.
종목 링크·기준일 조회·차트는 없다. STOCK_SNAPSHOT_PASSWORD 가 있으면 브라우저 로그인 창(HTTP Basic, 아이디 아무거나)을 띄운다.
"""
import hmac
import json
import os
from pathlib import Path

from flask import Flask, Response, abort, render_template, request
from jinja2 import Undefined
from markupsafe import Markup

BASE = Path(__file__).resolve().parent
SNAPSHOT = Path(os.environ.get("STOCK_SNAPSHOT_FILE", BASE / "snapshot" / "signals.json"))
DISCLAIMER = "투자 참고용 학습 도구이며 수익을 보장하지 않습니다. 투자 판단과 책임은 본인에게 있습니다."


def pct(v, d=1, sign=False):
    if v is None or isinstance(v, Undefined):
        return Markup("&ndash;")
    return f"{v * 100:+.{d}f}%" if sign else f"{v * 100:.{d}f}%"


def load():
    try:
        return json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def create_app():
    app = Flask(__name__)
    app.jinja_env.filters["pct"] = pct
    app.jinja_env.filters["won"] = lambda v: "-" if v is None else f"{v:,.0f}"
    app.jinja_env.globals["pct"] = pct

    @app.before_request
    def guard():
        pw = os.environ.get("STOCK_SNAPSHOT_PASSWORD", "")
        if not pw or request.path == "/healthz":
            return None
        auth = request.authorization
        if auth and hmac.compare_digest((auth.password or "").encode(), pw.encode()):
            return None
        return Response("로그인이 필요합니다.", 401, {"WWW-Authenticate": 'Basic realm="stock"'})

    @app.get("/healthz")
    def healthz():
        return "ok"

    @app.get("/")
    def home():
        rep = load()
        min_sig = min(max(request.args.get("min", 1, type=int) or 1, 1), 6)
        if rep and min_sig > 1:
            for k in ("pred", "ai", "high"):
                rep[k] = [r for r in rep.get(k, []) if len(r["signals"]) >= min_sig]
        return render_template("signals.html", rep=rep, asked="", lo=None, hi=None, mode="today", min_sig=min_sig,
                               snapshot=True, base_tpl="snapshot_base.html", disclaimer=DISCLAIMER)

    @app.after_request
    def headers(resp):
        resp.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(404)
    def nf(_):
        return "없는 주소입니다. 이 사이트는 오늘의 신호 한 화면만 보여 줍니다.", 404
    return app


app = create_app()
