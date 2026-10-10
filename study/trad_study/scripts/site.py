#!/usr/bin/env python3
"""HTML 버전의 호스팅 설정(보안 헤더·캐시 정책)의 단일 원본, 같은 헤더를 내는 로컬 서버, 설정 파일 생성기.

  python scripts/site.py                로컬 서버 시작 (http://127.0.0.1:5091, --port 로 변경)
  python scripts/site.py write          index.html 의 파일 버전 주소(?v=)·CSP 메타, _headers, render.yaml 을 다시 만든다
  python scripts/site.py check          위 파일이 최신인지 검사한다(아니면 종료 코드 1)

원본(Flask 버전)은 서버가 모든 응답에 보안 헤더를 붙이고 캐시를 정했다. 서버가 없는 이 버전은 같은 일을 호스팅 설정이 해야 하므로
  - 보안 헤더 4종 + 캐시 규칙을 이 파일에서만 정의하고,
  - 로컬 서버(이 파일)가 그대로 내보내 배포 전에 같은 조건에서 시험할 수 있게 하며,
  - Netlify·Cloudflare Pages 용 _headers 와 Render 정적 사이트용 render.yaml 을 여기서 만든다.
스크립트·스타일·데이터 파일을 고친 뒤에는 `python scripts/site.py write` 로 버전 주소를 갱신한다(검사: tests/site.test.js).
"""
import argparse
import hashlib
import http.server
import json
import re
import sys
import urllib.parse
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------- 보안 헤더
CSP = [('default-src', "'self'"), ('img-src', "'self' data: blob:"), ('style-src', "'self' 'unsafe-inline'"), ('script-src', "'self'"),
       ('connect-src', "'self' https://api.openai.com"), ('base-uri', "'self'"), ('form-action', "'self'")]
FRAME_ANCESTORS = ('frame-ancestors', "'none'")      # <meta> 로는 지정할 수 없어 헤더로만


def csp(header):
    parts = CSP + ([FRAME_ANCESTORS] if header else [])
    return '; '.join(f'{k} {v}' for k, v in parts)


SECURITY = {'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY', 'Referrer-Policy': 'same-origin',
            'Content-Security-Policy': csp(True)}

# ---------------------------------------------------------------- 캐시 정책
IMMUTABLE = 'public, max-age=31536000, immutable'       # 이름이 내용 해시라 바뀌지 않는 파일
REVALIDATE = 'public, max-age=0, must-revalidate'       # 이름이 고정이라 바뀔 수 있는 큰 파일(PDF): 매번 확인(304)
DAY = 'public, max-age=86400'                           # 거의 안 바뀌는 글꼴·아이콘
NO_CACHE = 'no-cache'                                   # 화면·스크립트·스타일·데이터: 쓰기 전에 항상 확인(304), 새 배포가 바로 반영


def cache_rules():
    """[(경로, Cache-Control)] — '/*' 로 끝나면 그 아래 전체. 규칙끼리 겹치지 않게 만든다(호스팅마다 겹친 규칙을 합치는 방식이 달라서)."""
    rules = [('/img/*', IMMUTABLE), ('/data/sources/*', REVALIDATE), ('/static/fonts/*', DAY), ('/static/favicon.svg', DAY)]
    rules += [(f'/static/{p.name}', NO_CACHE) for p in sorted((ROOT / 'static').glob('*')) if p.is_file() and p.suffix in ('.js', '.css')]
    rules += [(f'/data/{p.name}', NO_CACHE) for p in sorted((ROOT / 'data').glob('*.js'))]
    rules += [('/', NO_CACHE), ('/index.html', NO_CACHE)]
    return rules


def cache_for(path):
    for pattern, value in cache_rules():
        if pattern.endswith('/*'):
            if path.startswith(pattern[:-1]):
                return value
        elif path == pattern:
            return value
    return None


# ---------------------------------------------------------------- 파일 버전(캐시 무효화)
def version_of(rel):
    return hashlib.sha1((ROOT / rel).read_bytes()).hexdigest()[:10]


LOCAL_REF = re.compile(r'(<script src="|<link rel="stylesheet" href="|<link rel="icon" href=")([^"?:]+)(\?v=[0-9a-f]+)?(")')


def stamp_index(text):
    """index.html 의 내 파일 주소에 ?v=<내용 해시>를 붙이고, CSP 메타와 정답 파일 버전 메타를 원본 정의에 맞춘다."""
    text = LOCAL_REF.sub(lambda m: f'{m.group(1)}{m.group(2)}?v={version_of(m.group(2))}{m.group(4)}', text)
    text = re.sub(r'(<meta http-equiv="Content-Security-Policy" content=")[^"]*(")', lambda m: m.group(1) + csp(False) + m.group(2), text)
    text = re.sub(r'(<meta name="answers-version" content=")[^"]*(")', lambda m: m.group(1) + version_of('data/answers.js') + m.group(2), text)
    return text


# ---------------------------------------------------------------- 만드는 파일
NOTE = '이 파일은 scripts/site.py 가 만듭니다 — 직접 고치지 말고 그 파일을 고친 뒤 `python scripts/site.py write` 를 실행하세요.'


def headers_file():
    out = [f'# {NOTE}', '# Netlify·Cloudflare Pages 형식. 규칙끼리 겹치지 않으므로 어느 쪽이 합쳐도 같은 결과입니다.', '/*']
    out += [f'  {k}: {v}' for k, v in SECURITY.items()]
    for path, value in cache_rules():
        out += [path, f'  Cache-Control: {value}']
    return '\n'.join(out) + '\n'


def render_yaml():
    q = json.dumps
    out = [f'# {NOTE}', '# Render 정적 사이트(Blueprint). 서비스를 만들 때 Blueprint 파일 경로를 study/trad_study/render.yaml 로 지정합니다.',
           'services:', '  - type: web', '    name: trad-study-html', '    runtime: static', '    rootDir: study/trad_study',
           f'    buildCommand: {q("echo 정적 파일이라 빌드할 것이 없습니다", ensure_ascii=False)}', '    staticPublishPath: .', '    autoDeploy: true', '    headers:']
    pairs = [('/*', k, v) for k, v in SECURITY.items()] + [(p, 'Cache-Control', v) for p, v in cache_rules()]
    for path, name, value in pairs:
        out += [f'      - path: {q(path)}', f'        name: {name}', f'        value: {q(value)}']
    return '\n'.join(out) + '\n'


def desired():
    return {'index.html': stamp_index((ROOT / 'index.html').read_text(encoding='utf-8')), '_headers': headers_file(), 'render.yaml': render_yaml()}


def write():
    for name, text in desired().items():
        path = ROOT / name
        old = path.read_text(encoding='utf-8') if path.exists() else None
        if old != text:
            path.write_text(text, encoding='utf-8', newline='\n')
        print(f'{name}: {"갱신" if old != text else "그대로"}')


def check():
    stale = [name for name, text in desired().items() if not (ROOT / name).exists() or (ROOT / name).read_text(encoding='utf-8') != text]
    print('최신이 아님: ' + ', '.join(stale) + '  → python scripts/site.py write' if stale else '버전 주소·_headers·render.yaml 이 최신입니다.')
    return 1 if stale else 0


# ---------------------------------------------------------------- 로컬 서버
ALLOWED = {'index.html', 'static', 'data', 'img'}       # 앱이 쓰는 것만 내보낸다(scripts·tests·문서·.git 은 404)
TYPES = {'.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.html': 'text/html; charset=utf-8',
         '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml', '.webp': 'image/webp', '.woff2': 'font/woff2',
         '.pdf': 'application/pdf', '.txt': 'text/plain; charset=utf-8'}
RANGE = re.compile(r'^bytes=(\d*)-(\d*)$')


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = 'TradeStudySite'
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        if getattr(self.server, 'verbose', False):
            super().log_message(fmt, *args)

    def do_GET(self):
        self.respond(True)

    def do_HEAD(self):
        self.respond(False)

    def common(self):
        for key, value in SECURITY.items():
            self.send_header(key, value)

    def fail(self, code, with_body):
        body = f'{code}'.encode()
        self.send_response(code)
        self.common()
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if with_body:
            self.wfile.write(body)

    def respond(self, with_body):
        raw = urllib.parse.urlsplit(self.path).path
        rel = urllib.parse.unquote(raw)
        if '\x00' in rel or '\\' in rel:
            return self.fail(400, with_body)
        parts = [p for p in (rel + ('index.html' if rel.endswith('/') else '')).split('/') if p]
        if not parts or '..' in parts or '.' in parts or parts[0] not in ALLOWED:
            return self.fail(404, with_body)
        try:
            real = (ROOT / '/'.join(parts)).resolve()
            real.relative_to(ROOT)
        except (ValueError, OSError):
            return self.fail(404, with_body)
        if not real.is_file():
            return self.fail(404, with_body)
        stat = real.stat()
        size, etag, modified = stat.st_size, f'"{size_hex(stat.st_size)}-{stat.st_mtime_ns:x}"', int(stat.st_mtime)
        cache = cache_for(raw if raw != '' else '/')
        sent = self.headers.get('If-None-Match')
        since = self.headers.get('If-Modified-Since')
        not_modified = False
        if sent:
            not_modified = any(t.strip() in (etag, '*') or t.strip() == 'W/' + etag for t in sent.split(','))
        elif since:
            try:
                not_modified = modified <= int(parsedate_to_datetime(since).timestamp())
            except (TypeError, ValueError):
                pass
        status, start, end = 200, 0, size - 1
        match = RANGE.match(self.headers.get('Range', ''))
        if match and not not_modified and (match.group(1) or match.group(2)):
            a, b = match.groups()
            start, end = (size - int(b), size - 1) if not a else (int(a), int(b) if b else size - 1)
            end = min(end, size - 1)
            if start < 0 or start > end or start >= size:
                self.send_response(416)
                self.common()
                self.send_header('Content-Range', f'bytes */{size}')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            status = 206
        self.send_response(304 if not_modified else status)
        self.common()
        self.send_header('ETag', etag)
        self.send_header('Last-Modified', formatdate(modified, usegmt=True))
        if cache:
            self.send_header('Cache-Control', cache)
        if not_modified:
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        length = end - start + 1
        self.send_header('Content-Type', TYPES.get(real.suffix.lower(), 'application/octet-stream'))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(length))
        if status == 206:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        if with_body:
            try:
                with real.open('rb') as f:
                    f.seek(start)
                    left = length
                    while left > 0:
                        chunk = f.read(min(65536, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass


def size_hex(n):
    return f'{n:x}'


def serve(port, verbose):
    server = http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.verbose = verbose
    print(f'무역연습실 HTML 버전 → http://127.0.0.1:{port}  (보안 헤더·캐시 규칙 적용, 종료: Ctrl+C)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n종료합니다.')


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('command', nargs='?', default='serve', choices=['serve', 'write', 'check'])
    parser.add_argument('--port', type=int, default=5091)
    parser.add_argument('--verbose', action='store_true', help='요청 기록을 출력')
    args = parser.parse_args()
    if args.command == 'write':
        write()
    elif args.command == 'check':
        sys.exit(check())
    else:
        serve(args.port, args.verbose)


if __name__ == '__main__':
    main()
