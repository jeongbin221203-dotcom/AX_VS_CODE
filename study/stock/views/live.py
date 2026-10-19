"""실시간 시세 주소 — 차트 화면(차트·JB차트·차트 비교)이 마지막 봉을 장중에 고칠 때 쓴다. 자세한 동작은 core/live.py."""
from flask import Blueprint, Response, jsonify, stream_with_context

from core import live

bp = Blueprint("live", __name__)


def _valid(code):
    return 0 < len(code) <= 12 and code.isalnum()


@bp.get("/api/live/<code>/today")
def today(code):
    if not _valid(code):
        return jsonify(error="잘못된 종목코드"), 400
    return jsonify(bar=live.today_bar(code), market_open=live.market_open())


@bp.get("/api/live/<code>")
def stream(code):
    if not _valid(code):
        return jsonify(error="잘못된 종목코드"), 400
    return Response(stream_with_context(live.stream(code)), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
