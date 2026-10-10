// scripts/site.py — 호스팅 설정(_headers·render.yaml·파일 버전 주소)이 최신인지, 로컬 서버가 같은 헤더를 내는지 확인한다.
const test = require('node:test');
const assert = require('node:assert/strict');
const { spawnSync, spawn } = require('node:child_process');
const http = require('node:http');
const path = require('node:path');
const root = path.join(__dirname, '..');
const py = process.platform === 'win32' ? 'python' : 'python3';
const hasPy = !spawnSync(py, ['--version']).error;

test('site.py check: 버전 주소·_headers·render.yaml 이 최신', { skip: !hasPy }, () => {
  const r = spawnSync(py, [path.join(root, 'scripts', 'site.py'), 'check'], { encoding: 'utf-8' });
  assert.equal(r.status, 0, r.stdout + '\n파일을 고쳤다면 python scripts/site.py write 를 실행하세요');
});

const get = (port, p, headers = {}, method = 'GET') => new Promise((resolve, reject) => {
  http.request({ host: '127.0.0.1', port, path: p, method, headers }, res => {
    const chunks = []; res.on('data', c => chunks.push(c)); res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks) }));
  }).on('error', reject).end();
});

test('로컬 서버: 보안 헤더·캐시·범위 요청·접근 제한', { skip: !hasPy }, async () => {
  const port = 5990 + Math.floor(Math.random() * 9);
  const srv = spawn(py, [path.join(root, 'scripts', 'site.py'), '--port', String(port)], { stdio: 'ignore' });
  try {
    let home;
    for (let i = 0; i < 50 && !home; i++) { try { home = await get(port, '/'); } catch { await new Promise(r => setTimeout(r, 200)); } }
    assert.equal(home.status, 200);
    for (const [k, v] of [['x-content-type-options', 'nosniff'], ['x-frame-options', 'DENY'], ['referrer-policy', 'same-origin']]) assert.equal(home.headers[k], v);
    assert.match(home.headers['content-security-policy'], /frame-ancestors 'none'/);
    assert.equal(home.headers['cache-control'], 'no-cache');
    const etag = home.headers.etag;
    assert.equal((await get(port, '/', { 'If-None-Match': etag })).status, 304);
    const css = await get(port, '/static/style.css?v=abc');
    assert.equal(css.status, 200); assert.equal(css.headers['cache-control'], 'no-cache');
    const img = await get(port, '/' + require('fs').readdirSync(path.join(root, 'img'))[0].replace(/^/, 'img/'));
    assert.match(img.headers['cache-control'], /immutable/);
    const pdf = await get(port, '/data/sources/59_questions.pdf', { Range: 'bytes=0-9' });
    assert.equal(pdf.status, 206); assert.equal(pdf.body.length, 10); assert.equal(pdf.body.toString('latin1', 0, 4), '%PDF');
    assert.equal((await get(port, '/data/sources/59_questions.pdf', { Range: 'bytes=999999999-' })).status, 416);
    for (const p of ['/README.md', '/scripts/site.py', '/tests/data.test.js', '/.git/config', '/static/../README.md', '/%2e%2e/README.md']) assert.equal((await get(port, p)).status, 404, p);
    assert.equal((await get(port, '/', {}, 'HEAD')).body.length, 0);
  } finally { srv.kill(); }
});
