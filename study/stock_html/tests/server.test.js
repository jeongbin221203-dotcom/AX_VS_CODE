import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { marketOf, loadEnv, server } from '../server.mjs';

test('marketOf: 6자리 국내 코드(영문 섞인 코드 포함)는 kr, 티커는 us', () => {
  assert.equal(marketOf('005930'), 'kr');
  assert.equal(marketOf('0101N0'), 'kr');
  assert.equal(marketOf('AAPL'), 'us');
  assert.equal(marketOf('BRK.B'), 'us');
  assert.equal(marketOf('GOOGL'), 'us');
});

test('loadEnv: 주석·따옴표·빈 줄 처리, 파일 없으면 빈 객체', () => {
  const f = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'stk-')), '.env');
  fs.writeFileSync(f, '# c\nA=1\n\nB="two words"\nC = x=y\n');
  assert.deepEqual(loadEnv(f), { A: '1', B: 'two words', C: 'x=y' });
  assert.deepEqual(loadEnv(f + '.none'), {});
});

test('HTTP: 입력 검증·경로 탈출 차단 (토스 호출 없이 확인되는 것만)', async () => {
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    const get = async (p, init) => fetch(base + p, init);
    assert.equal((await get('/api/candles?symbol=../x')).status, 400);
    assert.equal((await get('/api/candles?symbol=AAPL&interval=5m')).status, 400);
    assert.equal((await get('/api/prices?symbols=')).status, 400);
    assert.equal((await get('/api/candles?symbol=AAPL', { method: 'POST' })).status, 405);
    assert.equal((await get('/%2e%2e/server.mjs')).status, 404);
    assert.equal((await get('/.env')).status, 404);
    const home = await get('/');
    assert.equal(home.status, 200);
    assert.match(await home.text(), /실시간 신호 차트/);
    const st = await (await get('/api/status')).json();
    assert.equal(st.clients, 0);
  } finally {
    server.close();
  }
});

test('tossErrorText: 객체형·문자열형 오류, 404 는 종목 안내', async () => {
  const { tossErrorText } = await import('../server.mjs');
  assert.equal(tossErrorText(400, { error: { code: 'invalid-request', message: '잘못된 요청' } }), '잘못된 요청');
  assert.equal(tossErrorText(400, { error: { code: 'invalid-request', message: '' } }), '토스 오류: invalid-request');
  assert.match(tossErrorText(404, { error: { code: 'symbol-not-found' } }), /종목을 찾을 수 없습니다 \(symbol-not-found\)/);
  assert.equal(tossErrorText(500, null), '토스 응답 500');
  assert.equal(tossErrorText(403, { message: 'IP', code: 'x' }), 'IP');
});

test('HTTP: candles before 형식 검증 (토스 호출 전에 거부)', async () => {
  const { server: srv } = await import('../server.mjs');
  await new Promise((r) => srv.listen(0, '127.0.0.1', r));
  try {
    const base = `http://127.0.0.1:${srv.address().port}`;
    const bad = await fetch(`${base}/api/candles?symbol=AAPL&interval=1m&before=${encodeURIComponent('x; drop')}`);
    assert.equal(bad.status, 400);
    assert.match((await bad.json()).error, /before/);
  } finally {
    srv.close();
  }
});

test('HTTP: /api/stocks·/api/prices 심볼 검증 (토스 호출 전에 거부)', async () => {
  const { server: srv } = await import('../server.mjs');
  await new Promise((r) => srv.listen(0, '127.0.0.1', r));
  try {
    const base = `http://127.0.0.1:${srv.address().port}`;
    assert.equal((await fetch(`${base}/api/stocks?symbols=`)).status, 400);
    assert.equal((await fetch(`${base}/api/stocks?symbols=${encodeURIComponent('../x')}`)).status, 400);
    const many = Array.from({ length: 201 }, (_, i) => `A${i}`).join(',');
    assert.equal((await fetch(`${base}/api/stocks?symbols=${many}`)).status, 400);
    assert.equal((await fetch(`${base}/api/prices?symbols=${many}`)).status, 400);
  } finally {
    srv.close();
  }
});

test('HTTP: /api/index·/api/investor 입력 검증 (토스 호출 전에 거부)', async () => {
  const { server: srv } = await import('../server.mjs');
  await new Promise((r) => srv.listen(0, '127.0.0.1', r));
  try {
    const base = `http://127.0.0.1:${srv.address().port}`;
    assert.equal((await fetch(`${base}/api/index?symbol=`)).status, 400);
    assert.equal((await fetch(`${base}/api/index?symbol=${encodeURIComponent('../x')}`)).status, 400);
    assert.equal((await fetch(`${base}/api/investor?symbol=AAPL`)).status, 400); // 해외 종목은 지원 안 함
    assert.equal((await fetch(`${base}/api/investor?symbol=005930&until=2026-1-1`)).status, 400);
    assert.equal((await fetch(`${base}/api/investor?symbol=`)).status, 400);
  } finally {
    srv.close();
  }
});
