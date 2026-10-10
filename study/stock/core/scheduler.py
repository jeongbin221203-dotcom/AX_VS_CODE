"""장 마감 후 자동 갱신: 평일 UPDATE_AT(기본 16:10) 이후 하루 한 번 전체 종목 일봉을 이어 받는다."""
import threading
import time
from datetime import datetime

from core import collector, db

UPDATE_AT = (16, 10)
_state = {"running": False, "last": "", "msg": "", "done": 0, "total": 0}


def all_codes() -> list[str]:
    """관심종목 먼저, 그 다음 시가총액 순. 상장폐지·우선주 등 데이터가 없는 종목은 호출이 0행으로 끝난다."""
    with db.get_conn() as c:
        wl = [r["code"] for r in c.execute("SELECT code FROM watchlist")]
        rest = [r["code"] for r in c.execute("SELECT code FROM symbols ORDER BY marcap DESC")]
    return list(dict.fromkeys(wl + rest))


def run_update(limit: int = 0) -> dict:
    if _state["running"]:
        return {"error": "이미 실행 중"}
    _state.update(running=True, done=0, msg="종목 목록 갱신")
    try:
        collector.update_symbols("KRX")
        codes = all_codes()[: limit or None]
        _state["total"] = len(codes)

        def prog(i, n, code, msg):
            _state.update(done=i, msg=f"{code} {msg}")
        res = collector.update_many(codes, progress=prog)
        _state["msg"] = f"완료: 성공 {res['ok']}, 실패 {res['fail']}"
        _state["last"] = datetime.now().isoformat(timespec="seconds")
        with db.get_conn() as c:
            c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('last_auto_update',?)", (_state["last"],))
        try:                                                     # 첫 화면 '신호'(예측·AI 추천)를 새 일봉으로 다시 훑는다
            from core import signal_scan
            _state["msg"] += " · 신호 훑는 중"
            signal_scan.run(log=lambda m: _state.update(msg=f"신호: {m}"))
            _state["msg"] = f"완료: 성공 {res['ok']}, 실패 {res['fail']} · 신호 갱신"
        except Exception as e:
            _state["msg"] = f"완료: 성공 {res['ok']}, 실패 {res['fail']} · 신호 갱신 실패: {e}"
        return res
    except Exception as e:
        _state["msg"] = f"오류: {e}"
        return {"error": str(e)}
    finally:
        _state["running"] = False


def due(now: datetime | None = None) -> bool:
    """평일, 갱신 시각 이후, 오늘 아직 안 돌았으면 True."""
    now = now or datetime.now()
    if now.weekday() >= 5 or (now.hour, now.minute) < UPDATE_AT:
        return False
    with db.get_conn() as c:
        r = c.execute("SELECT value FROM meta WHERE key='last_auto_update'").fetchone()
    return not (r and r["value"][:10] == now.strftime("%Y-%m-%d"))


def start_background(check_every: int = 60):
    def loop():
        while True:
            try:
                if due() and not _state["running"]:
                    run_update()
            except Exception as e:  # 스레드가 죽지 않게
                _state["msg"] = f"스케줄러 오류: {e}"
            time.sleep(check_every)
    threading.Thread(target=loop, daemon=True, name="stock-scheduler").start()


def status() -> dict:
    return dict(_state)
