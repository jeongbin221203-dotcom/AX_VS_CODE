"""엑셀 연습장 — 함수·기능 학습, 파일 → 대시보드 분석, 대시보드 만들기 실습 (Flask)."""
import os
import secrets
from pathlib import Path

from flask import Flask, abort, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from core import db
from core import formula as fx

ROOT = Path(__file__).resolve().parent
DEFAULT_PORT = 5005


def _secret(data_dir):
    if os.environ.get('EX_SECRET_KEY'):
        return os.environ['EX_SECRET_KEY']
    p = Path(data_dir) / 'secret.key'
    if not p.exists():
        p.write_text(secrets.token_hex(32), encoding='ascii')
    return p.read_text(encoding='ascii').strip()


def create_app(config=None):
    app = Flask(__name__)
    data_dir = Path(os.environ.get('EX_DATA_DIR', ROOT / 'data'))
    app.config.update(
        DATA_DIR=str(data_dir),
        DATABASE=os.environ.get('EX_DB_PATH', str(data_dir / 'ex.db')),
        UPLOADS=str(data_dir / 'uploads'),
        MAX_CONTENT_LENGTH=8 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_NAME='ex_session',
        PROXY=os.environ.get('EX_PROXY') == '1',
    )
    if config:
        app.config.update(config)
        if 'DATA_DIR' in config and 'UPLOADS' not in config:
            app.config['UPLOADS'] = str(Path(config['DATA_DIR']) / 'uploads')
    Path(app.config['DATA_DIR']).mkdir(parents=True, exist_ok=True)
    Path(app.config['DATABASE']).parent.mkdir(parents=True, exist_ok=True)
    app.secret_key = app.config.get('SECRET_KEY') or _secret(app.config['DATA_DIR'])
    if app.config['PROXY']:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
        app.config['SESSION_COOKIE_SECURE'] = True
    db.init(app.config['DATABASE'])
    app.teardown_appcontext(db.close)

    @app.before_request
    def csrf_protect():
        session.setdefault('csrf', secrets.token_urlsafe(24))
        if request.method in ('POST', 'PUT', 'DELETE') and not app.config.get('TESTING_NO_CSRF'):
            token = request.headers.get('X-CSRF-Token') or request.form.get('_csrf', '')
            if not secrets.compare_digest(token, session['csrf']):
                abort(400, '페이지를 새로 고친 뒤 다시 시도해 주세요.')

    @app.after_request
    def headers(resp):
        resp.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'")
        resp.headers['X-Content-Type-Options'] = 'nosniff'
        resp.headers['Referrer-Policy'] = 'same-origin'
        return resp

    from views import analyze, build, exam, functions, learn, main
    for bp in (main.bp, learn.bp, functions.bp, analyze.bp, build.bp, exam.bp):
        app.register_blueprint(bp)

    @app.template_filter('num')
    def num(v, digits=0):
        if v is None or v == '':
            return '–'
        if isinstance(v, str):
            return v
        if isinstance(v, float) and digits == 0 and v != int(v):
            return f'{v:,.2f}'.rstrip('0').rstrip('.')
        return f'{v:,.{digits}f}'

    @app.template_filter('cell')
    def cell(v):
        return '' if v is None else (fx.display(v) if not isinstance(v, str) else v)

    @app.context_processor
    def inject():
        from core import content
        return {'csrf_token': session.get('csrf', ''), 'track': current_track(), 'TRACKS': content.TRACKS,
                'CAT_NAMES': content.CAT_NAMES, 'asset': asset_url}

    def asset_url(filename):
        """정적 파일 주소 + 수정 시각(바꾸면 브라우저가 새 파일을 받는다)."""
        path = Path(app.static_folder) / filename
        v = int(path.stat().st_mtime) if path.exists() else 0
        return url_for('static', filename=filename, v=v)

    @app.errorhandler(413)
    def too_big(_e):
        return '파일이 너무 큽니다(최대 8MB).', 413

    return app


def current_track():
    t = db.setting('track', '')
    from core import content
    return t if t in content.TRACKS else ''


if __name__ == '__main__':
    port = int(os.environ.get('EX_PORT', DEFAULT_PORT))
    create_app().run(host='127.0.0.1', port=port, debug=os.environ.get('EX_DEBUG') == '1')
