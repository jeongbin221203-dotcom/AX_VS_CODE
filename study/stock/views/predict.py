"""차트 비교 화면의 겹쳐 그리기 API — 예측(단테 기법 신호) · AI(상승 확률)."""
from flask import Blueprint, jsonify, request

from core import ai, intraday, predict, service
from views.main import _valid_code

bp = Blueprint("predict", __name__)


@bp.get("/api/predict/<code>")
def api_predict(code):
    kind = request.args.get("kind", "dante")
    tf = request.args.get("tf", "D")
    bars = min(max(request.args.get("bars", 750, type=int), 30), 5000)
    if kind not in ("dante", "ai"):
        return jsonify(error="kind must be dante/ai"), 400
    if tf not in ("D", "W", "M") and not intraday.is_intraday(tf):
        return jsonify(error="tf must be D/W/M 또는 1m/5m/15m/30m/60m"), 400
    if not _valid_code(code):
        return jsonify(error="잘못된 종목코드"), 400
    if intraday.is_intraday(tf):                      # 분봉: 예측(단테 기법)만 — AI 는 일봉으로 학습해 분봉에는 없음
        if kind == "ai":
            return jsonify(error="AI(상승 확률)는 일봉·주봉·월봉에서만 볼 수 있습니다"), 400
        try:
            return jsonify(intraday.dante(code, tf, bars))
        except intraday.IntradayError as e:
            return jsonify(error=str(e)), 502
    df = service.load_prices(code)
    if len(df) < ai.MIN_BARS:
        return jsonify(error=f"데이터가 부족합니다({ai.MIN_BARS}봉 이상 필요)"), 404
    try:
        out = predict.dante(df, tf, bars) if kind == "dante" else predict.ai_pred(df, tf, bars)
    except RuntimeError as e:
        return jsonify(error=str(e)), 503
    return jsonify(out)
