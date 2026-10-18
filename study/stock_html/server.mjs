// 토스증권 Open API 로컬 중계 서버 — 키는 이 프로세스만 가진다.
//   GET /api/candles?symbol=005930&interval=1m|1d&count=600
//   GET /api/index?symbol=KOSPI|KOSDAQ   (시장 지수 일봉 전체)
//   GET /api/investor?symbol=005930&count=100&until=YYYY-MM-DD   (투자자별 매매동향)
//   GET /api/prices?symbols=005930,AAPL
//   GET /api/stocks?symbols=005930,AAPL   (종목 기본 정보: 발행주식수·보통주 여부 등)
//   GET /api/universe?country=kr|us   (전체 종목, 하루 1회 캐시)
//   GET /api/caps?country=kr   (코스피·코스닥 보통주 시가총액 순위, 하루 1회 캐시)
//   GET /api/status
//   GET /stream?symbols=005930,AAPL   (SSE: 실시간 체결)
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { WebSocket } from 'ws';

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const PUBLIC = path.join(ROOT, 'public');
const REST = 'https://openapi.tossinvest.com';
const WS_URL = 'wss://openapi-ws.tossinvest.com/ws/v1';

// ---------- .env ----------
export function loadEnv(file = path.join(ROOT, '.env')) {
  const env = {};
  if (!fs.existsSync(file)) return env;
  for (const line of fs.readFileSync(file, 'utf8').split(/\r?\n/)) {
    const t = line.trim();
    if (!t || t.startsWith('#') || !t.includes('=')) continue;
    const i = t.indexOf('=');
    env[t.slice(0, i).trim()] = t.slice(i + 1).trim().replace(/^["']|["']$/g, '');
  }
  return env;
}
const env = { ...loadEnv(), ...process.env };
const PORT = Number(env.STOCK_PORT || 5100);

// ---------- 심볼 ----------
const SYMBOL_RE = /^[A-Za-z0-9.\-]{1,12}$/;
/** 국내 6자리 코드(숫자 포함)면 kr, 아니면 us */
export function marketOf(symbol) {
  return /^\d[0-9A-Z]{5}$/.test(symbol) && symbol.length === 6 ? 'kr' : 'us';
}

// ---------- 토큰 (클라이언트당 1개만 유효 → 이 서버가 독점) ----------
let tok = null;
let tokPending = null;
async function getToken(force = false) {
  if (!force && tok && Date.now() < tok.exp - 600_000) return tok.value;
  if (tokPending) return tokPending;
  tokPending = (async () => {
    if (!env.TOSS_CLIENT_ID || !env.TOSS_CLIENT_SECRET) throw httpError(500, '.env 에 TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 이 없습니다.');
    const r = await fetch(`${REST}/oauth2/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        grant_type: 'client_credentials',
        client_id: env.TOSS_CLIENT_ID,
        client_secret: env.TOSS_CLIENT_SECRET,
      }),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.access_token) {
      const hint = r.status === 403 ? ' (이 PC의 공인 IP가 토스 Open API 허용 목록에 없습니다)' : r.status === 401 ? ' (client_id/secret 확인)' : '';
      throw httpError(r.status || 502, `토큰 발급 실패 ${r.status}${hint}`);
    }
    tok = { value: j.access_token, exp: Date.now() + (j.expires_in || 3600) * 1000 };
    return tok.value;
  })().finally(() => { tokPending = null; });
  return tokPending;
}

function httpError(status, message) {
  const e = new Error(message);
  e.status = status;
  return e;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** 토스 오류 본문 {error:{code,message}} (또는 평평한 모양)에서 사람이 읽을 문구를 만든다 */
export function tossErrorText(status, body) {
  const e = body && typeof body.error === 'object' && body.error ? body.error : body || {};
  const msg = typeof e.message === 'string' ? e.message : '';
  const code = typeof e.code === 'string' ? e.code : typeof body?.error === 'string' ? body.error : '';
  if (status === 404) return `종목을 찾을 수 없습니다${code ? ` (${code})` : ''}. 코드를 확인하세요.`;
  return msg || (code ? `토스 오류: ${code}` : `토스 응답 ${status}`);
}

/** 토스 REST GET — 401 은 토큰 재발급 후 1회, 429 는 대기 후 재시도 */
async function tossGet(pathname, params = {}) {
  const url = new URL(REST + pathname);
  for (const [k, v] of Object.entries(params)) if (v != null) url.searchParams.set(k, v);
  let refreshed = false;
  for (let attempt = 0; attempt < 4; attempt++) {
    const token = await getToken();
    let r;
    try {
      r = await fetch(url, { headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(10_000) });
    } catch (e) {
      throw httpError(502, `토스 서버 연결 실패: ${e.message}`);
    }
    if (r.status === 401 && !refreshed) { refreshed = true; await getToken(true); continue; }
    if (r.status === 429) {
      const wait = Number(r.headers.get('retry-after')) || 1;
      await sleep(Math.min(wait, 5) * 1000 + 100);
      continue;
    }
    const text = await r.text();
    let j = null;
    try { j = JSON.parse(text); } catch { /* 본문이 JSON 이 아님 */ }
    if (!r.ok) throw httpError(r.status, tossErrorText(r.status, j));
    return j?.result ?? j;
  }
  throw httpError(429, '요청 한도 초과 — 잠시 후 다시 시도하세요.');
}

// ---------- 캔들 ----------
const num = (s) => Number(s);
/**
 * 캔들 조회. count 를 생략하면 토스가 주는 전체 기간을 끝까지 가져온다(maxBars·budgetMs 한도 안에서).
 * 반환 candles 는 오래된 순. truncated=true 면 한도 때문에 더 과거가 남아 있다는 뜻.
 */
export async function fetchCandles(symbol, interval, { count = Infinity, maxBars = 150_000, budgetMs = 90_000, before: startBefore = null, path: endpoint = null } = {}) {
  const out = new Map();
  const limit = Math.min(count, maxBars);
  const started = Date.now();
  let before = startBefore;
  let nextBefore = startBefore;
  let currency = '';
  let truncated = false;
  while (out.size < limit) {
    if (Date.now() - started > budgetMs) { truncated = true; break; }
    // endpoint 가 있으면(시장 지수 등) 종목 코드는 경로에 들어 있으므로 symbol 쿼리를 보내지 않는다
    const page = await tossGet(endpoint || '/api/v1/candles', {
      ...(endpoint ? {} : { symbol }), interval, count: Math.min(200, limit - out.size), before,
    });
    for (const c of page.candles || []) {
      currency = c.currency || currency;
      const t = Math.floor(Date.parse(c.timestamp) / 1000);
      out.set(t, { time: t, open: num(c.openPrice), high: num(c.highPrice), low: num(c.lowPrice), close: num(c.closePrice), volume: num(c.volume) });
    }
    nextBefore = page.nextBefore || null;
    if (!nextBefore || !(page.candles || []).length) { nextBefore = null; break; }
    before = nextBefore;
    if (out.size >= maxBars) { truncated = true; break; }
  }
  return { currency, truncated, nextBefore, candles: [...out.values()].sort((a, b) => a.time - b.time) };
}

// ---------- 전체 종목 (하루 1회 조회 후 data/ 에 캐시) ----------
const MARKETS = { kr: ['KOSPI', 'KOSDAQ', 'KR_ETC'], us: ['NYSE', 'NASDAQ', 'AMEX', 'US_ETC'] };
const DATA_DIR = path.join(ROOT, 'data');
const kstDate = () => new Date(Date.now() + 9 * 3600_000).toISOString().slice(0, 10);
const uniMem = new Map(); // country → { date, items }
const uniJobs = new Map();

/** 항목: [symbol, name, market, securityType] (표 크기를 줄이려고 배열) */
export async function getUniverse(country) {
  const date = kstDate();
  const mem = uniMem.get(country);
  if (mem && mem.date === date) return mem;
  const file = path.join(DATA_DIR, `universe-${country}-${date}.json`);
  if (fs.existsSync(file)) {
    try { const j = JSON.parse(fs.readFileSync(file, 'utf8')); uniMem.set(country, j); return j; } catch { /* 깨진 캐시는 다시 받음 */ }
  }
  if (uniJobs.has(country)) return uniJobs.get(country);
  const job = (async () => {
    const seen = new Set();
    const items = [];
    for (const market of MARKETS[country]) {
      const list = await tossGet('/api/v1/stocks/all', { market }); // STOCK_ALL 한도 1/s → 429 는 tossGet 이 대기 후 재시도
      for (const s of list || []) {
        if (seen.has(s.symbol)) continue;
        seen.add(s.symbol);
        items.push([s.symbol, s.name, market, s.securityType]);
      }
      await sleep(1100);
    }
    const j = { country, date, count: items.length, items };
    fs.mkdirSync(DATA_DIR, { recursive: true });
    fs.writeFileSync(file, JSON.stringify(j));
    for (const f of fs.readdirSync(DATA_DIR)) if (f.startsWith(`universe-${country}-`) && f !== path.basename(file)) fs.rmSync(path.join(DATA_DIR, f), { force: true });
    uniMem.set(country, j);
    return j;
  })().finally(() => uniJobs.delete(country));
  uniJobs.set(country, job);
  return job;
}

// ---------- 국내 시가총액 순위 (하루 1회 계산 후 data/ 에 캐시) ----------
/**
 * 순수 함수: 종목 정보와 현재가로 시가총액 순위를 만든다. 보통주·정상 거래 종목만 대상.
 * info: 토스 /stocks 항목들, prices: Map(symbol → 현재가). 반환: { symbol: [시가총액(원), 순위] }
 */
export function rankCaps(info, prices) {
  const rows = [];
  for (const s of info) {
    const price = prices.get(s.symbol);
    const shares = Number(s.sharesOutstanding);
    if (!s.isCommonShare || s.status !== 'ACTIVE' || s.koreanMarketDetail?.krxTradingSuspended) continue;
    if (!(price > 0) || !(shares > 0)) continue;
    rows.push([s.symbol, shares * price]);
  }
  rows.sort((a, b) => b[1] - a[1]);
  const out = {};
  rows.forEach(([sym, cap], i) => { out[sym] = [cap, i + 1]; });
  return out;
}

const capsMem = { date: '', data: null };
let capsJob = null;
export async function getCaps() {
  const date = kstDate();
  if (capsMem.data && capsMem.date === date) return capsMem.data;
  const file = path.join(DATA_DIR, `caps-kr-${date}.json`);
  if (fs.existsSync(file)) {
    try { capsMem.data = JSON.parse(fs.readFileSync(file, 'utf8')); capsMem.date = date; return capsMem.data; } catch { /* 깨진 캐시는 다시 계산 */ }
  }
  if (capsJob) return capsJob;
  capsJob = (async () => {
    const uni = await getUniverse('kr');
    const symbols = uni.items.filter((x) => (x[2] === 'KOSPI' || x[2] === 'KOSDAQ') && x[3] === 'STOCK').map((x) => x[0]);
    const info = [];
    for (let i = 0; i < symbols.length; i += 200) {
      const r = await tossGet('/api/v1/stocks', { symbols: symbols.slice(i, i + 200).join(',') });
      info.push(...(r || []));
    }
    const ok = info.filter((s) => s.isCommonShare && s.status === 'ACTIVE');
    const prices = new Map();
    for (let i = 0; i < ok.length; i += 200) {
      const r = await tossGet('/api/v1/prices', { symbols: ok.slice(i, i + 200).map((s) => s.symbol).join(',') });
      for (const p of r || []) prices.set(p.symbol, Number(p.lastPrice));
    }
    const items = rankCaps(info, prices);
    const j = { date, count: Object.keys(items).length, items };
    fs.mkdirSync(DATA_DIR, { recursive: true });
    fs.writeFileSync(file, JSON.stringify(j));
    for (const f of fs.readdirSync(DATA_DIR)) if (f.startsWith('caps-kr-') && f !== path.basename(file)) fs.rmSync(path.join(DATA_DIR, f), { force: true });
    capsMem.data = j; capsMem.date = date;
    return j;
  })().finally(() => { capsJob = null; });
  return capsJob;
}

// ---------- 실시간 중계 ----------
const clients = new Set(); // { res, symbols:Set }
const feed = { ws: null, state: 'idle', backoff: 1000, ping: null, retry: null, declareTimer: null, lastMsg: 0 };

function wantedSymbols() {
  const s = new Set();
  for (const c of clients) for (const x of c.symbols) s.add(x);
  return [...s].slice(0, 90); // 연결당 구독 100개 한도
}
function broadcast(symbol, payload) {
  const line = `data: ${JSON.stringify(payload)}\n\n`;
  for (const c of clients) if (c.symbols.has(symbol)) c.res.write(line);
}
function broadcastAll(payload) {
  const line = `data: ${JSON.stringify(payload)}\n\n`;
  for (const c of clients) c.res.write(line);
}
function setState(state, detail) {
  feed.state = state;
  broadcastAll({ type: 'feed', state, detail });
}

function declare() {
  clearTimeout(feed.declareTimer);
  feed.declareTimer = setTimeout(() => {
    if (!feed.ws || feed.ws.readyState !== WebSocket.OPEN) return;
    const kr = []; const us = [];
    for (const s of wantedSymbols()) (marketOf(s) === 'kr' ? kr : us).push(s);
    const msg = [{ id: 'decl' }];
    if (kr.length) msg.push({ type: 'trade:kr', codes: kr });
    if (us.length) msg.push({ type: 'trade:us', codes: us });
    feed.ws.send(JSON.stringify(msg.length > 1 ? msg : []));
  }, 300); // 선언은 초당 5회 한도 → 묶어서 보냄
}

async function connectFeed() {
  if (feed.ws || !clients.size) return;
  setState('connecting');
  let token;
  try { token = await getToken(); } catch (e) { setState('error', e.message); return scheduleRetry(); }
  const ws = new WebSocket(WS_URL, { headers: { Authorization: `Bearer ${token}` } });
  feed.ws = ws;
  ws.on('open', () => {
    feed.backoff = 1000;
    setState('open');
    declare();
    clearInterval(feed.ping);
    feed.ping = setInterval(() => { if (ws.readyState === WebSocket.OPEN) ws.send('PING'); }, 60_000);
  });
  ws.on('message', (buf) => {
    feed.lastMsg = Date.now();
    let m;
    try { m = JSON.parse(buf.toString()); } catch { return; }
    if (m.type === 'message' && typeof m.topic === 'string' && m.topic.startsWith('trade:')) {
      const symbol = m.topic.split(':')[2];
      const d = m.data || {};
      broadcast(symbol, { type: 'trade', symbol, price: num(d.price), volume: num(d.volume), time: Math.floor(Date.parse(d.timestamp) / 1000) || Math.floor(Date.now() / 1000) });
    } else if (m.type === 'subscriptions') {
      if (m.rejected?.length) broadcastAll({ type: 'feed', state: 'rejected', detail: m.rejected.map((r) => `${r.target}: ${r.message}`).join(', ') });
    } else if (m.type === 'error') {
      if (m.error?.code === 'rate-limit-exceeded') setTimeout(declare, 1100);
      else broadcastAll({ type: 'feed', state: 'error', detail: `${m.error?.code}: ${m.error?.message}` });
    }
  });
  const onEnd = (why) => {
    if (feed.ws !== ws) return;
    clearInterval(feed.ping);
    feed.ws = null;
    setState('closed', why);
    scheduleRetry();
  };
  ws.on('close', (code) => onEnd(`close ${code}`));
  ws.on('error', (e) => { try { ws.terminate(); } catch { /* ignore */ } onEnd(e.message); });
  ws.on('unexpected-response', (_req, res) => {
    if (res.statusCode === 401) tok = null; // 다음 연결 때 재발급
    onEnd(`handshake ${res.statusCode}${res.statusCode === 403 ? ' (IP 허용 목록 확인)' : ''}`);
  });
}
function scheduleRetry() {
  clearTimeout(feed.retry);
  if (!clients.size) return;
  const wait = feed.backoff + Math.random() * 500;
  feed.backoff = Math.min(feed.backoff * 2, 30_000);
  feed.retry = setTimeout(connectFeed, wait);
}
function closeFeedIfIdle() {
  if (clients.size) return;
  clearTimeout(feed.retry);
  clearInterval(feed.ping);
  if (feed.ws) { const w = feed.ws; feed.ws = null; try { w.close(); } catch { /* ignore */ } }
  feed.state = 'idle';
}

// ---------- HTTP ----------
const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.json': 'application/json; charset=utf-8' };
const send = (res, status, obj) => {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
  res.end(JSON.stringify(obj));
};
const parseSymbols = (s) => [...new Set(String(s || '').split(',').map((x) => x.trim().toUpperCase()).filter(Boolean))];

export const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://localhost');
    const p = url.pathname;
    if (req.method !== 'GET') return send(res, 405, { error: 'GET 만 지원합니다.' });

    if (p === '/api/candles') {
      const symbol = (url.searchParams.get('symbol') || '').toUpperCase();
      const interval = url.searchParams.get('interval') || '1m';
      const rawCount = url.searchParams.get('count') || 'all';
      if (!SYMBOL_RE.test(symbol)) return send(res, 400, { error: '종목 코드가 올바르지 않습니다.' });
      if (!['1m', '1d'].includes(interval)) return send(res, 400, { error: 'interval 은 1m 또는 1d' });
      const opt = {};
      const before = url.searchParams.get('before');
      if (before) {
        if (!/^[0-9T:.+-Z]{10,40}$/.test(before)) return send(res, 400, { error: 'before 형식이 올바르지 않습니다.' });
        opt.before = before;
      }
      if (rawCount !== 'all') opt.count = Math.min(Math.max(parseInt(rawCount, 10) || 300, 1), 150_000);
      const r = await fetchCandles(symbol, interval, opt);
      // 전송량을 줄이려고 [시각, 시, 고, 저, 종, 거래량] 배열로 보낸다
      return send(res, 200, { symbol, interval, currency: r.currency, truncated: r.truncated, nextBefore: r.nextBefore, rows: r.candles.map((c) => [c.time, c.open, c.high, c.low, c.close, c.volume]) });
    }
    if (p === '/api/index') {
      const symbol = (url.searchParams.get('symbol') || '').toUpperCase();
      if (!/^[A-Z_]{3,20}$/.test(symbol)) return send(res, 400, { error: '지수 코드가 올바르지 않습니다.' });
      const r = await fetchCandles(symbol, '1d', { path: '/api/v1/market-indicators/' + symbol + '/candles' });
      return send(res, 200, { symbol, interval: '1d', truncated: r.truncated, rows: r.candles.map((c) => [c.time, c.open, c.high, c.low, c.close, c.volume]) });
    }
    if (p === '/api/investor') {
      const symbol = (url.searchParams.get('symbol') || '').toUpperCase();
      const until = url.searchParams.get('until');
      if (!SYMBOL_RE.test(symbol) || marketOf(symbol) !== 'kr') return send(res, 400, { error: '국내 종목 코드(6자리)만 지원합니다.' });
      if (until && !/^\d{4}-\d{2}-\d{2}$/.test(until)) return send(res, 400, { error: 'until 은 YYYY-MM-DD' });
      const count = Math.min(Math.max(parseInt(url.searchParams.get('count') || '100', 10) || 100, 1), 100);
      return send(res, 200, { result: await tossGet('/api/v1/stocks/' + symbol + '/investor-trading', { count, until }) });
    }
    if (p === '/api/prices') {
      const symbols = parseSymbols(url.searchParams.get('symbols'));
      if (!symbols.length || symbols.length > 200 || !symbols.every((s) => SYMBOL_RE.test(s))) return send(res, 400, { error: '종목 코드가 올바르지 않습니다.' });
      return send(res, 200, { prices: await tossGet('/api/v1/prices', { symbols: symbols.join(',') }) });
    }
    if (p === '/api/universe') {
      const country = url.searchParams.get('country');
      if (!MARKETS[country]) return send(res, 400, { error: 'country 는 kr 또는 us' });
      const j = await getUniverse(country);
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'private, max-age=3600' });
      return res.end(JSON.stringify(j));
    }
    if (p === '/api/stocks') {
      const symbols = parseSymbols(url.searchParams.get('symbols'));
      if (!symbols.length || symbols.length > 200 || !symbols.every((s) => SYMBOL_RE.test(s))) return send(res, 400, { error: '종목 코드가 올바르지 않습니다.' });
      return send(res, 200, { stocks: await tossGet('/api/v1/stocks', { symbols: symbols.join(',') }) });
    }
    if (p === '/api/caps') {
      if (url.searchParams.get('country') !== 'kr') return send(res, 400, { error: 'country 는 kr 만 지원합니다.' });
      const j = await getCaps();
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'private, max-age=3600' });
      return res.end(JSON.stringify(j));
    }
    if (p === '/api/status') {
      return send(res, 200, { feed: feed.state, clients: clients.size, tokenValid: !!tok && Date.now() < tok.exp, lastMessageAgoSec: feed.lastMsg ? Math.round((Date.now() - feed.lastMsg) / 1000) : null });
    }
    if (p === '/stream') {
      const symbols = parseSymbols(url.searchParams.get('symbols')).filter((s) => SYMBOL_RE.test(s)).slice(0, 20);
      res.writeHead(200, { 'Content-Type': 'text/event-stream; charset=utf-8', 'Cache-Control': 'no-store', Connection: 'keep-alive' });
      res.write(`data: ${JSON.stringify({ type: 'feed', state: feed.state })}\n\n`);
      const c = { res, symbols: new Set(symbols) };
      clients.add(c);
      const hb = setInterval(() => res.write(': hb\n\n'), 20_000);
      req.on('close', () => { clearInterval(hb); clients.delete(c); declare(); closeFeedIfIdle(); });
      if (feed.ws) declare(); else connectFeed();
      return;
    }

    // 정적 파일
    const rel = p === '/' ? '/index.html' : decodeURIComponent(p);
    const file = path.normalize(path.join(PUBLIC, rel));
    if (!file.startsWith(PUBLIC + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) return send(res, 404, { error: '없는 경로' });
    res.writeHead(200, { 'Content-Type': MIME[path.extname(file)] || 'application/octet-stream', 'Cache-Control': 'no-cache' });
    fs.createReadStream(file).pipe(res);
  } catch (e) {
    if (!res.headersSent) send(res, e.status || 500, { error: e.message || '서버 오류' });
    else res.end();
  }
});

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  server.listen(PORT, '127.0.0.1', () => console.log(`http://127.0.0.1:${PORT}  (이 PC에서만 접속 가능)`));
}
