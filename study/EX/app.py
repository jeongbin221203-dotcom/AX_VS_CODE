"""엑셀 연습장 — 함수·기능 학습, 파일 → 대시보드 분석, 대시보드 만들기 실습 (Flask)."""
import os
import secrets
from pathlib import Path

from flask import Flask, abort, redirect, request, session, url_for
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
        # 공개 서버: 저작물(공식 예제·교재 파일)을 받거나 가져오는 기능을 끈다
        PUBLIC=os.environ.get('EX_PUBLIC') == '1',
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
    app.permanent_session_lifetime = __import__('datetime').timedelta(days=400)
    if os.environ.get('EX_VAULT_KEY') and not app.config.get('TESTING'):
        from core import vault
        try:
            vault.restore_on_start(ROOT / 'content' / 'private' / 'vault.bin', os.environ['EX_VAULT_KEY'],
                                   app.config['DATA_DIR'])
        except ValueError as e:
            app.logger.error('개인 자료 복원 실패: %s', e)

    @app.before_request
    def csrf_protect():
        session.setdefault('csrf', secrets.token_urlsafe(24))
        if request.path == '/api/sync':              # 쿠키가 아니라 개인 링크 토큰(Bearer)으로 확인
            return None
        if request.method in ('POST', 'PUT', 'DELETE') and not app.config.get('TESTING_NO_CSRF'):
            token = request.headers.get('X-CSRF-Token') or request.form.get('_csrf', '')
            if not secrets.compare_digest(token.encode('utf-8'), session['csrf'].encode('utf-8')):
                abort(400, '페이지를 새로 고친 뒤 다시 시도해 주세요.')

    @app.after_request
    def headers(resp):
        resp.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'")
        resp.headers['X-Content-Type-Options'] = 'nosniff'
        resp.headers['Referrer-Policy'] = 'same-origin'
        return resp

    from views import analyze, build, exam, functions, learn, main, practice
    for bp in (main.bp, learn.bp, functions.bp, analyze.bp, build.bp, exam.bp, practice.bp):
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
                'CAT_NAMES': content.CAT_NAMES, 'asset': asset_url, 'public': app.config['PUBLIC'],
                'owner': bool(session.get('owner')) or not app.config['PUBLIC']}

    def asset_url(filename):
        """정적 파일 주소 + 수정 시각(바꾸면 브라우저가 새 파일을 받는다)."""
        path = Path(app.static_folder) / filename
        v = int(path.stat().st_mtime) if path.exists() else 0
        return url_for('static', filename=filename, v=v)

    @app.route('/me/<token>')
    def owner_login(token):
        """개인 링크: 이 기기에서 내 교재·공식 예제 자료를 보이게 한다(비밀번호 대신 긴 무작위 주소)."""
        want = os.environ.get('EX_OWNER_TOKEN') or app.config.get('OWNER_TOKEN')
        if not want or not secrets.compare_digest(token.encode('utf-8'), want.encode('utf-8')):
            abort(404)
        session['owner'] = True
        session.permanent = True
        return redirect(url_for('practice.index', msg='이 기기에서 내 자료가 보입니다.'))

    @app.route('/api/sync', methods=['POST'])
    def api_sync():
        """PC 앱이 보낸 기록을 개인 링크 기기('owner') 기록에 합치고, 합친 결과를 돌려준다(gzip JSON)."""
        from core import backup, sync
        want = os.environ.get('EX_OWNER_TOKEN') or app.config.get('OWNER_TOKEN')
        got = request.headers.get('Authorization', '')
        if not want or not app.config['PUBLIC'] or not secrets.compare_digest(
                got.encode('utf-8'), f'Bearer {want}'.encode('utf-8')):
            abort(404)
        request.max_content_length = 12 * 1024 * 1024
        try:
            data = sync.unpack(request.get_data()) if request.headers.get('Content-Encoding') == 'gzip' \
                else request.get_json(force=True)
            conn = db.get()
            added = sync.merge(conn, 'owner', data)
        except (ValueError, OSError, EOFError) as e:
            abort(400, str(e))
        body = sync.pack({'added': added, 'data': backup.export(conn, 'owner')})
        return body, 200, {'Content-Type': 'application/json', 'Content-Encoding': 'gzip'}

    @app.route('/healthz')
    def healthz():
        return {'ok': True}

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
    application = create_app()
    from core import sync
    sync.start(application)                    # data/sync.json 이 있으면 배포 서버와 학습 기록 맞추기
    application.run(host='127.0.0.1', port=port, debug=os.environ.get('EX_DEBUG') == '1')
