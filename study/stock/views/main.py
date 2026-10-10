import threading
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

from core import collector, db, service, techniques

bp = Blueprint("main", __name__)
_job = {"running": False, "done": 0, "total": 0, "msg": ""}


def _name(code):
    with db.get_conn() as c:
        r = c.execute("SELECT name FROM symbols WHERE code=?", (code,)).fetchone()
    return r["name"] if r else code


def _valid_code(code):
    return 0 < len(code) <= 12 and code.replace(".", "").replace("-", "").isalnum()


@bp.get("/")
def home():
    """첫 화면 = 신호(왼쪽 예측 · 오른쪽 AI, 점검 충족률 90% 이상 추천). data/signals.json (core/signal_scan.py)."""
    from core import signal_scan
    asked = (request.args.get("date") or "").strip()
    min_sig = min(max(request.args.get("min", 1, type=int) or 1, 1), 6)      # 신호가 이 개수 이상 겹친 종목만
    lo, hi = signal_scan.dates_available()

    def narrow(rep):
        if rep and min_sig > 1:
            for k in ("pred", "ai", "high"):
                rep[k] = [r for r in rep.get(k, []) if len(r["signals"]) >= min_sig]
        return rep
    if asked:
        try:
            if len(asked) != 10:
                raise ValueError
            datetime.strptime(asked, "%Y-%m-%d")
        except ValueError:
            return "날짜는 YYYY-MM-DD", 400
        rep = narrow(signal_scan.for_date(asked) if lo else None)
        return render_template("signals.html", rep=rep, asked=asked, lo=lo, hi=hi, mode="date", min_sig=min_sig)
    return render_template("signals.html", rep=narrow(signal_scan.load()), asked="", lo=lo, hi=hi, mode="today", min_sig=min_sig)


@bp.get("/watch")
def watch():
    with db.get_conn() as c:
        wl = c.execute("SELECT w.code, s.name FROM watchlist w LEFT JOIN symbols s ON s.code=w.code ORDER BY w.added_at").fetchall()
        n_sym = c.execute("SELECT COUNT(*) n FROM symbols").fetchone()["n"]
        n_px = c.execute("SELECT COUNT(DISTINCT code) n FROM prices").fetchone()["n"]
    items = []
    for r in wl:
        p = service.chart_payload(r["code"], bars=30)
        items.append({"code": r["code"], "name": r["name"] or r["code"],
                      "last": p["last"] if p else None,
                      "signals": [s for s in (p["signals"] if p else []) if s["side"] != "info"][-3:]})
    return render_template("home.html", items=items, n_sym=n_sym, n_px=n_px)


@bp.get("/chart/<code>")
def chart(code):
    if not _valid_code(code):
        return "잘못된 종목코드", 400
    with db.get_conn() as c:
        star = c.execute("SELECT 1 FROM watchlist WHERE code=?", (code,)).fetchone() is not None
    cat = techniques.catalog()
    ready = {t["name"] for t in cat if t["status"] == "ready"}
    asked = request.args.get("tech")
    active = [n for n in asked.split(",") if n in ready] if asked is not None else [t["name"] for t in cat if t["default"]]
    ma = request.args.get("ma", "a")
    if ma not in ("a", "b", "ab", "none"):
        ma = "a"
    return render_template("chart.html", code=code, name=_name(code), star=star, catalog=cat, active=active, ma=ma)


@bp.get("/compare")
def compare():
    """두 차트를 나란히. 한쪽을 확대·이동하면 다른 쪽도 같이 움직인다(브라우저에서 동기화)."""
    a = request.args.get("a", "005930")
    b = request.args.get("b") or a
    for c in (a, b):
        if not _valid_code(c):
            return "잘못된 종목코드", 400

    def side(code, set_, tf, default_set, kind, default_kind):
        return {"code": code, "name": _name(code), "tf": tf if tf in ("D", "W", "M") else "D",
                "set": set_ if set_ in ("a", "b", "none") else default_set,
                "kind": kind if kind in ("none", "dante", "ai") else default_kind}   # 겹쳐 그리기: 왼쪽 '예측'(단테 기법), 오른쪽 'AI'
    pa = side(a, request.args.get("sa"), request.args.get("ta"), "a", request.args.get("ka"), "dante")
    pb = side(b, request.args.get("sb"), request.args.get("tb"), "b" if b == a else "a", request.args.get("kb"), "ai")
    focus = (request.args.get("date") or "").strip()      # 신호 화면에서 기준일을 넘겨 받으면 그날을 중심으로 보여 준다
    try:
        if len(focus) != 10:
            raise ValueError
        datetime.strptime(focus, "%Y-%m-%d")
    except ValueError:
        focus = ""
    return render_template("compare.html", pa=pa, pb=pb, focus=focus)


@bp.get("/api/chart/<code>")
def api_chart(code):
    tf = request.args.get("tf", "D")
    if tf not in ("D", "W", "M"):
        return jsonify(error="tf must be D/W/M"), 400
    bars = min(max(request.args.get("bars", 500, type=int), 30), 5000)
    if not _valid_code(code):
        return jsonify(error="잘못된 종목코드"), 400
    p = service.chart_payload(code, tf, bars)
    if p is None:  # 아직 없으면 한 번 받아 본다
        try:
            collector.update_prices(code)
        except Exception as e:
            return jsonify(error=f"데이터를 받지 못했습니다: {e}"), 404
        p = service.chart_payload(code, tf, bars)
        if p is None:
            return jsonify(error="데이터가 없습니다"), 404
    return jsonify(p)


@bp.get("/api/search")
def api_search():
    q = request.args.get("q", "").strip()[:30]
    if not q:
        return jsonify([])
    like = "%" + q.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
    with db.get_conn() as c:
        rows = c.execute(
            "SELECT code,name,market FROM symbols WHERE name LIKE ? ESCAPE '!' OR code LIKE ? ESCAPE '!' "
            "ORDER BY (name=?) DESC, marcap DESC LIMIT 15", (like, like, q)).fetchall()
    return jsonify([dict(r) for r in rows])


@bp.post("/api/watch/<code>")
def api_watch(code):
    if not _valid_code(code):
        return jsonify(error="잘못된 종목코드"), 400
    with db.get_conn() as c:
        if c.execute("SELECT 1 FROM watchlist WHERE code=?", (code,)).fetchone():
            c.execute("DELETE FROM watchlist WHERE code=?", (code,))
            star = False
        else:
            c.execute("INSERT INTO watchlist(code,added_at) VALUES(?,?)", (code, datetime.now().isoformat(timespec="seconds")))
            star = True
    return jsonify(star=star)


@bp.get("/data")
def data_page():
    with db.get_conn() as c:
        n_sym = c.execute("SELECT COUNT(*) n FROM symbols").fetchone()["n"]
        last = c.execute("SELECT MAX(date) d FROM prices").fetchone()["d"]
        fails = c.execute("SELECT code,at,message FROM collect_log WHERE ok=0 ORDER BY id DESC LIMIT 20").fetchall()
        auto = c.execute("SELECT value FROM meta WHERE key='last_auto_update'").fetchone()
    return render_template("data.html", n_sym=n_sym, last=last, fails=fails, auto=auto["value"] if auto else None)


@bp.post("/api/update")
def api_update():
    """종목 목록 + 관심종목 + 시총 상위 N개 일봉을 백그라운드로 갱신."""
    if _job["running"]:
        return jsonify(error="이미 실행 중"), 409
    body = request.get_json(silent=True) or {}
    top = min(max(int(body.get("top", 0) or 0), 0), 5000)
    _job.update(running=True, done=0, total=0, msg="시작")

    def run():
        try:
            _job["msg"] = "종목 목록 갱신"
            collector.update_symbols("KRX")
            with db.get_conn() as c:
                codes = [r["code"] for r in c.execute("SELECT code FROM watchlist")]
                if top:
                    codes += [r["code"] for r in c.execute("SELECT code FROM symbols ORDER BY marcap DESC LIMIT ?", (top,))]
            codes = list(dict.fromkeys(codes))
            _job["total"] = len(codes)

            def prog(i, n, code, msg):
                _job.update(done=i, msg=f"{code} {msg}")
            res = collector.update_many(codes, progress=prog)
            _job["msg"] = f"완료: 성공 {res['ok']}, 실패 {res['fail']}"
        except Exception as e:
            _job["msg"] = f"오류: {e}"
        finally:
            _job["running"] = False

    threading.Thread(target=run, daemon=True).start()
    return jsonify(started=True)


@bp.get("/api/update/status")
def api_status():
    return jsonify(_job)
