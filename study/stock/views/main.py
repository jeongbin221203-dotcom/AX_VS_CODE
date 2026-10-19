import threading
from datetime import datetime

from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from core import collector, db, intraday, service, techniques

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

    from core import jb_daily, picks
    grp_of = {k: v[0] for k, v in ((picks.load_today() or {}).get("scores") or {}).items()}

    def narrow(rep):
        if rep and min_sig > 1:
            for k in ("pred", "ai", "high"):
                rep[k] = [r for r in rep.get(k, []) if len(r["signals"]) >= min_sig]
        if rep:                                            # 시총 구간(대형·중형·소형·잡주) — 표를 구간별로 나눠 보인다
            for k in ("pred", "ai", "high"):
                for r in rep.get(k, []):
                    r["grp"] = grp_of.get(r["code"], "기타")
        return rep
    if asked:
        try:
            if len(asked) != 10:
                raise ValueError
            datetime.strptime(asked, "%Y-%m-%d")
        except ValueError:
            return "날짜는 YYYY-MM-DD", 400
        rep = narrow(signal_scan.for_date(asked) if lo else None)
        return render_template("signals.html", rep=rep, asked=asked, lo=lo, hi=hi, mode="date", min_sig=min_sig, act=None)
    from core import live_scan
    rep, lv = signal_scan.load(), live_scan.load()
    if (lv and rep and lv.get("meta", {}).get("date", "") > rep.get("meta", {}).get("date", "") and lv["meta"].get("prev") == rep.get("meta", {}).get("date")
            and request.args.get("final") != "1"):   # 같은 확정 신호(전 거래일) 위에서 만든 장중 신호일 때만
        rep = lv            # 오늘 장중 신호(미완성 봉) — 마감 뒤 확정 신호가 나오면 그쪽이 더 새것이 된다
    return render_template("signals.html", rep=narrow(rep), asked="", lo=lo, hi=hi, mode="today", min_sig=min_sig, live_state=live_scan._state, act=jb_daily.load())


@bp.post("/api/live-scan")
def api_live_scan():
    """장중 신호를 지금 다시 훑기(전 종목, 약 5~10분). 이미 돌고 있으면 그대로."""
    from core import live_scan
    started = live_scan.start_thread()
    if request.form.get("back"):                       # 신호 화면의 '지금 다시 훑기' 버튼(폼) → 신호 화면으로
        return redirect(url_for("main.home"))
    return jsonify(started=started, state=live_scan._state)


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
    """예전 개별 차트 주소 — 차트는 JB차트 하나로 합쳤다(2026-10-19). 기준일(date)은 그대로 넘긴다."""
    if not _valid_code(code):
        return "잘못된 종목코드", 400
    q = {"a": code}
    if request.args.get("date", "").replace("-", "").isdigit():
        q["date"] = request.args["date"]
    return redirect(url_for("main.jb", **q))


@bp.get("/compare")
def compare():
    """예전 차트 비교 주소 — 같은 설정 그대로 JB차트로."""
    return redirect(url_for("main.jb", **request.args.to_dict()))


@bp.get("/jb")
def jb():
    """JB차트 — 두 차트를 나란히(한쪽을 확대·이동하면 다른 쪽도 같이 움직임). 모든 종목 링크가 여기로 온다."""
    return _compare_page("jb")


def _grp(code):
    """종목의 시총 구간 이름(대형주·중형주·소형주·잡주) — JB차트 '자동' 이름용. 모르면 빈 문자열."""
    try:
        from core import picks
        v = ((picks.load_today() or {}).get("scores") or {}).get(code)
        return v[0] if v else ""
    except Exception:
        return ""


def _compare_page(page):
    """두 차트를 나란히. 한쪽을 확대·이동하면 다른 쪽도 같이 움직인다(브라우저에서 동기화)."""
    a = request.args.get("a", "005930")
    b = request.args.get("b") or a
    for c in (a, b):
        if not _valid_code(c):
            return "잘못된 종목코드", 400

    def side(code, set_, tf, default_set, kind, default_kind, risk=None, bb=None):
        return {"code": code, "name": _name(code), "tf": tf if (tf in ("D", "W", "M") or intraday.is_intraday(tf)) else "D",
                "set": set_ if set_ in ("a", "b", "none") else default_set,
                "kind": "both" if kind in ("dante", "ai") else kind if kind in ("none", "both", "cross") else default_kind, "grp": _grp(code),
                "risk": risk == "1", "bb": bb != "0"}   # 하락 위험 띠는 기본 꺼짐(주소에 1 이 있을 때만 켬)   # 겹쳐 그리기: 왼쪽 '예측'(단테 기법), 오른쪽 'AI'
    pa = side(a, request.args.get("sa"), request.args.get("ta"), "a", request.args.get("ka"), "both", request.args.get("ra"), request.args.get("ba"))
    pb = side(b, request.args.get("sb"), request.args.get("tb"), "b" if b == a else "a", request.args.get("kb"), "both", request.args.get("rb"), request.args.get("bb"))
    focus = (request.args.get("date") or "").strip()      # 신호 화면에서 기준일을 넘겨 받으면 그날을 중심으로 보여 준다
    try:
        if len(focus) != 10:
            raise ValueError
        datetime.strptime(focus, "%Y-%m-%d")
    except ValueError:
        focus = ""
    return render_template("compare.html", pa=pa, pb=pb, focus=focus, page="jb", show_all=request.args.get("all") == "1")


@bp.get("/api/chart/<code>")
def api_chart(code):
    tf = request.args.get("tf", "D")
    if tf not in ("D", "W", "M") and not intraday.is_intraday(tf):
        return jsonify(error="tf must be D/W/M 또는 1m/5m/15m/30m/60m"), 400
    bars = min(max(request.args.get("bars", 500, type=int), 30), 5000)
    if not _valid_code(code):
        return jsonify(error="잘못된 종목코드"), 400
    if intraday.is_intraday(tf):
        try:
            return jsonify(intraday.payload(code, tf, bars))
        except intraday.IntradayError as e:
            return jsonify(error=str(e)), 502
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
