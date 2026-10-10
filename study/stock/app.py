import os
from pathlib import Path

from flask import Flask

import config
from core import db


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = config.SECRET_KEY
    db.init_db()
    from views.main import bp
    app.register_blueprint(bp)
    from views.surge import bp as surge_bp
    app.register_blueprint(surge_bp)
    from views.combined import bp as combined_bp
    app.register_blueprint(combined_bp)
    from views.strategy import bp as strategy_bp
    app.register_blueprint(strategy_bp)
    from views.risk import bp as risk_bp
    app.register_blueprint(risk_bp)
    from views.boom import bp as boom_bp
    app.register_blueprint(boom_bp)
    from views.jump import bp as jump_bp
    app.register_blueprint(jump_bp)
    from views.precursor import bp as precursor_bp
    app.register_blueprint(precursor_bp)
    from views.pattern import bp as pattern_bp
    app.register_blueprint(pattern_bp)
    from views.quietvol import bp as quietvol_bp
    app.register_blueprint(quietvol_bp)
    from views.timing import bp as timing_bp
    app.register_blueprint(timing_bp)
    from views.presignal import bp as presignal_bp
    app.register_blueprint(presignal_bp)
    from views.bestday import bp as bestday_bp
    app.register_blueprint(bestday_bp)
    from views.plan import bp as plan_bp
    app.register_blueprint(plan_bp)
    from views.avoid import bp as avoid_bp
    app.register_blueprint(avoid_bp)
    from views.entry import bp as entry_bp
    app.register_blueprint(entry_bp)
    from views.ai import bp as ai_bp
    app.register_blueprint(ai_bp)
    from views.analysis import bp as analysis_bp
    app.register_blueprint(analysis_bp)
    from views.tech import bp as tech_bp
    app.register_blueprint(tech_bp)
    from views.predict import bp as predict_bp
    app.register_blueprint(predict_bp)
    from views.notes import bp as notes_bp
    app.register_blueprint(notes_bp)

    if config.SCHEDULER and not app.testing and not os.environ.get("PYTEST_CURRENT_TEST"):
        from core import scheduler
        scheduler.start_background()

    @app.after_request
    def headers(resp):
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://i.ytimg.com")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    @app.context_processor
    def inject():
        return {"disclaimer": config.DISCLAIMER}

    @app.url_defaults
    def static_version(endpoint, values):
        """정적 파일 주소에 ?v=수정시각 — JS·CSS 를 고쳐도 브라우저가 옛 파일을 쓰지 않게."""
        if endpoint == "static" and "filename" in values:
            try:
                values["v"] = int((Path(app.static_folder) / values["filename"]).stat().st_mtime)
            except OSError:
                pass

    @app.template_filter("won")
    def won(v):
        return "-" if v is None else f"{v:,.0f}"

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=config.PORT, debug=False)
