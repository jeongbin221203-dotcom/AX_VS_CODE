"""휴대폰·외부에서 보는 읽기 전용 '오늘의 신호' 스냅샷 (Render 배포용).

본 앱(app.py)은 DB(약 660MB)·AI 모델·표본 캐시가 있어야 돌아서 서버에 올리지 않고,
PC 에서 전 종목 훑기 뒤 만든 snapshot/signals.json 한 파일만 읽어 신호 화면(추천·강/상·예상 확률)을 보여 준다.
종목 링크·기준일 조회·차트는 없다. STOCK_SNAPSHOT_PASSWORD 가 있으면 브라우저 로그인 창(HTTP Basic, 아이디 아무거나)을 띄운다.
"""
import hmac
import json
import os
from pathlib import Path

import re

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, send_from_directory

import snapshot_api
from jinja2 import Undefined
from markupsafe import Markup

BASE = Path(__file__).resolve().parent
SNAPSHOT = Path(os.environ.get("STOCK_SNAPSHOT_FILE", BASE / "snapshot" / "signals.json"))     # 최신
DAYS = Path(os.environ.get("STOCK_SNAPSHOT_DAYS", BASE / "snapshot" / "days"))                 # 날짜별: YYYY-MM-DD.json
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


CHARTS = Path(os.environ.get("STOCK_SNAPSHOT_CHARTS", BASE / "snapshot" / "charts"))
META = Path(os.environ.get("STOCK_SNAPSHOT_META", BASE / "snapshot" / "chartmeta.json"))
CODE_RE = re.compile(r"^[A-Za-z0-9]{1,12}$")


def days():
    try:
        return sorted(p.stem for p in DAYS.glob("*.json"))
    except OSError:
        return []


def load_day(date):
    try:
        return json.loads((DAYS / f"{date}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def chart_codes():
    try:
        return {p.stem for p in CHARTS.glob("*.json")}
    except OSError:
        return set()


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
        asked = (request.args.get("date") or "").strip()
        ds = days()
        rep = None
        if asked:
            if len(asked) != 10 or not asked.replace("-", "").isdigit():
                abort(400)
            use = max((d for d in ds if d <= asked), default=None)       # 그날이 없으면 그 전 보관일
            rep = load_day(use) if use else None
            if rep is not None:
                i = ds.index(use)
                rep["meta"].update(asked=asked, prev=ds[i - 1] if i else None, next=ds[i + 1] if i + 1 < len(ds) else None)
        else:
            rep = load()
            if rep and ds and rep["meta"].get("date") in ds:
                i = ds.index(rep["meta"]["date"])
                rep["meta"]["prev"] = ds[i - 1] if i else None
        min_sig = min(max(request.args.get("min", 1, type=int) or 1, 1), 6)
        if rep and min_sig > 1:
            for k in ("pred", "ai", "high"):
                rep[k] = [r for r in rep.get(k, []) if len(r["signals"]) >= min_sig]
        return render_template("signals.html", rep=rep, asked=asked, lo=ds[0] if ds else None, hi=ds[-1] if ds else None, mode="date" if asked else "today", min_sig=min_sig, snap_days=ds, charts_avail=chart_codes(),
                               snapshot=True, base_tpl="snapshot_base.html", disclaimer=DISCLAIMER)

    store = snapshot_api.Store(CHARTS, META)

    def side(code, set_, tf, default_set, kind, default_kind):
        try:
            name = store.raw(code).get("n", code)
        except OSError:
            name = code
        return {"code": code, "name": name, "tf": tf if tf in ("D", "W", "M") else "D", "set": set_ if set_ in ("a", "b", "none") else default_set,
                "kind": kind if kind in ("none", "dante", "ai") else default_kind}

    @app.get("/compare")
    def compare():
        """PC 앱의 '차트 비교' 화면과 같은 화면(같은 HTML·JS). 데이터만 저장된 파일에서 온다."""
        a = request.args.get("a", "")
        b = request.args.get("b") or a
        for c in (a, b):
            if not store.has(c):
                abort(404)
        focus = (request.args.get("date") or "").strip()
        if focus and (len(focus) != 10 or not focus.replace("-", "").isdigit()):
            abort(400)
        pa = side(a, request.args.get("sa"), request.args.get("ta"), "a", request.args.get("ka"), "dante")
        pb = side(b, request.args.get("sb"), request.args.get("tb"), "b" if b == a else "a", request.args.get("kb"), "ai")
        return render_template("compare.html", pa=pa, pb=pb, focus=focus, base_tpl="snapshot_base.html", rep=None, disclaimer=DISCLAIMER)

    @app.get("/chart/<code>")
    def chart_page(code):
        if not store.has(code):
            abort(404)
        return redirect(f"/compare?a={code}" + (f"&date={request.args['date']}" if request.args.get("date", "").replace("-", "").isdigit() else ""))

    def _tf_bars():
        tf = request.args.get("tf", "D")
        if tf not in ("D", "W", "M"):
            abort(400)
        return tf, min(max(request.args.get("bars", 500, type=int), 30), 5000)

    @app.get("/api/chart/<code>")
    def api_chart(code):
        if not store.has(code):
            return jsonify(error="이 종목의 데이터가 없습니다(스냅샷에는 추천·강/상으로 나온 종목만 있음)"), 404
        tf, nbars = _tf_bars()
        return jsonify(snapshot_api.chart_payload(store, code, tf, nbars))

    @app.get("/api/predict/<code>")
    def api_predict(code):
        kind = request.args.get("kind", "dante")
        if kind not in ("dante", "ai"):
            return jsonify(error="kind must be dante/ai"), 400
        if not store.has(code):
            return jsonify(error="이 종목의 데이터가 없습니다"), 404
        tf, nbars = _tf_bars()
        fn = snapshot_api.dante if kind == "dante" else snapshot_api.ai_pred
        return jsonify(fn(store, code, tf, nbars))

    @app.get("/api/search")
    def api_search():
        return jsonify(snapshot_api.search(store, request.args.get("q", "")))

    @app.get("/chartdata/<code>.json")
    def chart_data(code):
        if not CODE_RE.match(code):
            abort(404)
        return send_from_directory(CHARTS, f"{code}.json", mimetype="application/json", max_age=3600)

    @app.get("/chartmeta.json")
    def chart_meta():
        return send_from_directory(META.parent, META.name, mimetype="application/json", max_age=600)

    @app.after_request
    def headers(resp):
        resp.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        if not resp.headers.get("Cache-Control"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(404)
    def nf(_):
        return "없는 주소입니다. 이 사이트는 오늘의 신호 한 화면만 보여 줍니다.", 404
    return app


app = create_app()
