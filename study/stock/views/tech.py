import time

from flask import Blueprint, abort, jsonify, render_template, request

from core import db, indicators as ind, service, techniques, yt

bp = Blueprint("tech", __name__)
_scan_cache: dict[str, tuple[float, list]] = {}
SCAN_TTL = 600
SCAN_LIMIT = 300


@bp.get("/api/technique/<code>")
def api_technique(code):
    name = request.args.get("name", "")
    tf = request.args.get("tf", "D")
    if tf not in ("D", "W", "M"):
        return jsonify(error="tf must be D/W/M"), 400
    bars = min(max(request.args.get("bars", 750, type=int), 30), 5000)
    if name not in techniques.REGISTRY:
        return jsonify(error="알 수 없는 기법"), 404
    spec = techniques.REGISTRY[name]
    if spec["status"] != "ready":
        return jsonify(error="아직 규칙이 확정되지 않은 기법입니다", status=spec["status"]), 409
    df = service.load_prices(code)
    if df.empty:
        return jsonify(error="데이터가 없습니다"), 404
    return jsonify(techniques.apply(name, ind.resample(df, tf), bars))


def _videos(name, limit=12):
    try:
        yt.init()
        with yt.conn() as c:
            return c.execute(
                "SELECT v.id,v.title,v.upload_date,v.kind FROM yt_tags t JOIN yt_videos v ON v.id=t.video_id "
                "WHERE t.tag=? AND v.kind<>'short' ORDER BY t.hits DESC LIMIT ?", (name, limit)).fetchall()
    except Exception:
        return []


def _sources(spec):
    ids = spec.get("sources") or []
    if not ids:
        return []
    try:
        yt.init()
        with yt.conn() as c:
            return c.execute(f"SELECT id,title,upload_date FROM yt_videos WHERE id IN ({','.join('?' * len(ids))}) ORDER BY upload_date DESC", ids).fetchall()
    except Exception:
        return []


def _scan(name):
    now = time.time()
    hit = _scan_cache.get(name)
    if hit and now - hit[0] < SCAN_TTL:
        return hit[1]
    with db.get_conn() as c:
        codes = [r["code"] for r in c.execute(
            "SELECT s.code, s.name FROM symbols s WHERE EXISTS(SELECT 1 FROM prices p WHERE p.code=s.code) "
            "ORDER BY (s.code IN (SELECT code FROM watchlist)) DESC, s.marcap DESC LIMIT ?", (SCAN_LIMIT,))]
        names = {r["code"]: r["name"] for r in c.execute("SELECT code,name FROM symbols")}
    found = []
    for code in codes:
        df = service.load_prices(code)
        if len(df) < 130:
            continue
        sig = techniques.latest_signal(name, df)
        if sig:
            found.append({"code": code, "name": names.get(code, code), "close": float(df["close"].iloc[-1]),
                          "side": sig[0]["side"], "label": sig[0]["label"], "why": sig[0]["why"]})
    _scan_cache[name] = (now, found)
    return found


@bp.get("/technique/<name>")
def page(name):
    spec = techniques.REGISTRY.get(name)
    if not spec:
        abort(404)
    found = None
    if request.args.get("scan") == "1" and spec["status"] == "ready":
        found = _scan(name)
    with db.get_conn() as c:
        wl = c.execute("SELECT w.code, s.name FROM watchlist w LEFT JOIN symbols s ON s.code=w.code").fetchall()
    return render_template("technique.html", name=name, spec=spec, videos=_videos(name), sources=_sources(spec), found=found,
                           scan_limit=SCAN_LIMIT, wl=wl)
