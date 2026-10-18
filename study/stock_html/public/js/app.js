import { computeStorySignals, describeStoryState, storyBuys, storySells, storySurges, STORY, EXCLUDE, OPTIONS } from './storyrules.js';
import { manualSignals, manualState, manualEvents, MANUAL, MANUAL_NOTES } from './manual.js';
import { computeIndicators, computeSignals, computeScoreSignals, computeBollingerSignals, describeBollingerState, BB_RULES, runUser, describeUserState, USER_RULES, describeState, describeScoreState, describeContext, aggregate, backtest, MA_LIST, RULES, SQUEEZE, SCORE, SCORE_TABLE } from './signals.js';
import { TIER, TIER_LABEL, TIER_ORDER, tierOf, tierLists, fmtCap } from './tiers.js';
import { searchUniverse, countryOf, MARKET_LABEL, MARKETS, GROUPS } from './universe.js';
import { KST, tradeBelongs, periodsFor, findPeriod, periodStart, startOfLastDays, idxAtOrAfter, needsMore, mergeRows, MAX_RAW } from './periods.js';

const $ = (id) => document.getElementById(id);
const PRESETS = {
  kr: [['005930', '삼성전자'], ['000660', 'SK하이닉스'], ['035420', 'NAVER'], ['005380', '현대차'], ['373220', 'LG에너지솔루션']],
  us: [['AAPL', 'Apple'], ['MSFT', 'Microsoft'], ['NVDA', 'NVIDIA'], ['TSLA', 'Tesla'], ['GOOGL', 'Alphabet']],
};
const PRESET_NAMES = Object.fromEntries([...PRESETS.kr, ...PRESETS.us]); // 종목 목록을 받기 전·실패 시 이름
const PAGE = 100; // 종목 목록에 한 번에 보여줄 개수
const FIRST_CHUNK = { '1m': 3000, '1d': 1500 }; // 첫 화면에 필요한 최근 봉 수 (나머지는 백그라운드로)
const CHUNK = 5000; // 과거를 이어받는 단위
const RENDER_EVERY = 20000; // 이만큼 새로 받을 때마다 다시 그림 (매번 그리면 느림)
const MA_COLORS = { 5: '#f2c94c', 10: '#ff9f43', 20: '#bb86fc', 60: '#2dd4bf', 120: '#f472b6', 200: '#e5e7eb' };

const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* 저장 불가 환경 */ } },
};

const state = {
  symbol: store.get('stock:symbol', '005930'),
  iv: store.get('stock:iv', '5'),
  country: store.get('stock:country', ''),
  currency: 'KRW',
  bars: { key: '', raw: [], cursor: null, done: false, at: 0, loading: false }, // 받아 둔 원본(분봉은 1분봉, 일봉은 일봉)
  candles: [], // 화면 봉 단위로 묶은 것 (1분·일봉이면 raw 와 같은 배열)
  ind: null,
  signals: [],
  loadId: 0,
  lastTradeAt: 0,
  rangeMode: 'period', // period: 선택한 기간에 맞춤 / free: 사용자가 직접 움직임
  pullOlder: false, // 왼쪽 끝에 닿아 과거를 더 받아야 함
  maOn: new Set(JSON.parse(store.get('stock:ma', '[5,20,60,120]'))),
  bbOn: store.get('stock:bb', '1') === '1',
  period: {}, // 봉 단위별로 마지막에 고른 기간 이름
  mode: ['auto', 'story', 'manual', 'user', 'bb', 'score', 'rule', 'off'].includes(store.get('stock:strategy', 'auto')) ? store.get('stock:strategy', 'auto') : 'auto', // 신호 방식 선택
  strategy: 'pending', // 지금 실제로 쓰는 신호 방식 (recompute 에서 정함)
  caps: null, // /api/caps: 국내 보통주 시총 순위 { items: { 종목: [시총, 순위] } }
  capsLoading: true,
  capsError: '',
  tierLists: null, // 구간별 종목 목록(시총 큰 순)
  tierTab: ['large', 'mid', 'small', 'junk'].includes(store.get('stock:tier', 'large')) ? store.get('stock:tier', 'large') : 'large',
  tabSyncedFor: '', // 종목이 바뀔 때 한 번만 구간 탭을 그 종목의 구간에 맞춤
};
const barsCache = new Map(); // 'symbol|1m' → bars (최근 30초 안이면 재사용)

if (!PRESETS[state.country]) state.country = countryOf(state.symbol);

// ---------- 차트 ----------
const L = window.LightweightCharts;
const base = {
  layout: { background: { color: '#0d1117' }, textColor: '#9aa7b4' },
  grid: { vertLines: { color: '#1b222b' }, horzLines: { color: '#1b222b' } },
  rightPriceScale: { borderColor: '#2a313c', minimumWidth: 72 }, // 세 차트의 가로 축을 맞춤
  timeScale: { borderColor: '#2a313c', timeVisible: true, secondsVisible: false, rightOffset: 6, minBarSpacing: 0.002 },
  crosshair: { mode: 0 },
};
const chMain = L.createChart($('chMain'), { ...base, autoSize: true });
const chRsi = L.createChart($('chRsi'), { ...base, autoSize: true });
const chMacd = L.createChart($('chMacd'), { ...base, autoSize: true });

const sCandle = chMain.addCandlestickSeries({ priceLineVisible: true });
const sVol = chMain.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol', lastValueVisible: false, priceLineVisible: false });
chMain.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
const line = (chart, color, w = 1, style = 0) => chart.addLineSeries({ color, lineWidth: w, lineStyle: style, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
const sMa = {};
for (const n of MA_LIST) sMa[n] = line(chMain, MA_COLORS[n], n >= 60 ? 2 : 1);
const sBbU = line(chMain, '#6b7683', 1, 2);
const sBbL = line(chMain, '#6b7683', 1, 2);
const sRsi = line(chRsi, '#4c8dff', 2);
sRsi.createPriceLine({ price: 70, color: '#6b7683', lineStyle: 2, lineWidth: 1, axisLabelVisible: false });
sRsi.createPriceLine({ price: 30, color: '#6b7683', lineStyle: 2, lineWidth: 1, axisLabelVisible: false });
sRsi.applyOptions({ autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }) });
const sHist = chMacd.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
const sMacd = line(chMacd, '#f2c94c');
const sSig = line(chMacd, '#bb86fc');

// 세 차트의 가로 범위를 같이 움직인다
let syncing = false;
for (const src of [chMain, chRsi, chMacd]) {
  src.timeScale().subscribeVisibleLogicalRangeChange((r) => {
    if (syncing || !r) return;
    syncing = true;
    for (const dst of [chMain, chRsi, chMacd]) if (dst !== src) dst.timeScale().setVisibleLogicalRange(r);
    syncing = false;
  });
}
// 사용자가 직접 확대·이동하면 '기간에 맞춤'을 풀고, 보이는 범위가 바뀌면 오른쪽 분석 패널을 다시 계산한다
for (const id of ['chMain', 'chRsi', 'chMacd']) {
  for (const ev of ['wheel', 'pointerdown', 'touchstart']) $(id).addEventListener(ev, () => { if (state.rangeMode !== 'free') { state.rangeMode = 'free'; renderPeriods(); } }, { passive: true });
}
let panelTimer = null;
chMain.timeScale().subscribeVisibleLogicalRangeChange((r) => {
  clearTimeout(panelTimer);
  panelTimer = setTimeout(() => {
    renderPanel();
    renderMarkers(); // 보이는 신호 수에 따라 글자 표시를 바꿈
    if (r && r.from < 30 && !state.bars.done && !state.bars.loading) { state.pullOlder = true; backfill(); } // 왼쪽 끝 → 과거 더 받기
  }, 200);
});

// ---------- 표시 보조 ----------
const isKrw = () => state.currency === 'KRW';
const upColor = () => (isKrw() ? '#e5484d' : '#3fb950');
const downColor = () => (isKrw() ? '#4c8dff' : '#e5484d');
const fmtPrice = (v) => (v == null ? '–' : isKrw() ? Math.round(v).toLocaleString('ko-KR') : v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const fmtTime = (t) => new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(t * 1000));
const fmtDate = (t) => new Date((t + KST) * 1000).toISOString().slice(0, 10);
const fmtPct = (v) => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(2)}%`;
const ct = (t) => t + KST;

function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

function showMsg(text, err = false) {
  const el = $('msg');
  el.hidden = !text;
  el.textContent = text || '';
  el.className = err ? 'msg err' : 'msg';
}
function setLive(kind, text) {
  $('dot').className = `dot ${kind}`;
  $('liveText').textContent = text;
}
function setHist(text) { $('histStatus').textContent = text; }

// ---------- 데이터 ----------
async function api(path) {
  const r = await fetch(path);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || `요청 실패 ${r.status}`);
  return j;
}

const rawInterval = () => (state.iv === '1d' ? '1d' : '1m');

async function fetchChunk(sym, rawIv, cursor, count) {
  const j = await api(`/api/candles?symbol=${encodeURIComponent(sym)}&interval=${rawIv}&count=${count}${cursor ? `&before=${encodeURIComponent(cursor)}` : ''}`);
  return {
    currency: j.currency,
    next: j.nextBefore || null,
    rows: j.rows.map(([time, open, high, low, close, volume]) => ({ time, open, high, low, close, volume })),
  };
}

async function load() {
  const id = ++state.loadId;
  const sym = state.symbol;
  const rawIv = rawInterval();
  const key = `${sym}|${rawIv}`;
  showMsg('');
  setHist('');
  $('nowBody').textContent = '불러오는 중…';
  renderPeriods();
  renderBadge();
  try {
    let bars = barsCache.get(key);
    if (!bars || Date.now() - bars.at > 30_000 || !bars.raw.length) {
      const first = await fetchChunk(sym, rawIv, null, FIRST_CHUNK[rawIv]);
      if (id !== state.loadId) return; // 그 사이 다른 종목을 골랐음
      if (bars && bars.raw.length && first.rows.length && first.rows[0].time <= bars.raw.at(-1).time) {
        // 받아 둔 과거 데이터는 그대로 두고 최근 구간만 새로 합친다 (빈 구간이 생기면 이 분기를 타지 않고 새로 받음)
        bars.raw = mergeRows(bars.raw, first.rows);
        bars.at = Date.now();
      } else {
        bars = { key, raw: first.rows, cursor: first.next, done: !first.next, at: Date.now(), loading: false, currency: first.currency || 'KRW' };
        barsCache.set(key, bars);
        while (barsCache.size > 4) barsCache.delete(barsCache.keys().next().value);
      }
    }
    state.bars = bars;
    state.currency = bars.currency || 'KRW';
    document.body.dataset.cur = state.currency;
    rebuild();
    if (!state.candles.length) showMsg('이 종목의 캔들 데이터가 없습니다. 종목 코드를 확인하세요.', true);
    else store.set('stock:symbol', sym); // 데이터를 받은 종목만 기억 (잘못 입력한 코드는 저장하지 않음)
    const priceFormat = isKrw() ? { type: 'price', precision: 0, minMove: 1 } : { type: 'price', precision: 2, minMove: 0.01 };
    sCandle.applyOptions({ priceFormat, upColor: upColor(), downColor: downColor(), borderUpColor: upColor(), borderDownColor: downColor(), wickUpColor: upColor(), wickDownColor: downColor() });
    state.rangeMode = 'period';
    renderAll();
    applyPeriod();
    startStream();
    backfill();
  } catch (e) {
    if (id !== state.loadId) return;
    state.bars = { key, raw: [], cursor: null, done: true, at: 0, loading: false };
    state.candles = [];
    renderAll();
    showMsg(e.message, true);
  }
}

/** 받아 둔 원본을 화면 봉 단위로 묶는다 */
function rebuild() {
  const raw = state.bars.raw;
  state.candles = state.iv === '1' || state.iv === '1d' ? raw : aggregate(raw, Number(state.iv));
}

const currentPeriod = () => findPeriod(state.iv, state.period[state.iv] || store.get(`stock:p:${state.iv}`, ''));

/** 선택한 기간(과 지표 계산용 여유)이 채워질 때까지, 또는 왼쪽 끝에 닿았을 때 과거를 이어받는다 */
async function backfill() {
  const bars = state.bars;
  if (bars.loading) return; // 이미 돌고 있는 반복이 현재 기간을 보고 계속함
  bars.loading = true;
  const sym = state.symbol;
  const rawIv = rawInterval();
  let sinceRender = 0;
  try {
    while (state.bars === bars && !bars.done && bars.raw.length < MAX_RAW && (state.pullOlder || needsMore(bars.raw, state.iv, currentPeriod(), bars.done))) {
      setHist(`과거 데이터 불러오는 중… ${fmtDate(bars.raw[0].time)} 까지 (${bars.raw.length.toLocaleString('ko-KR')}봉)`);
      const part = await fetchChunk(sym, rawIv, bars.cursor, CHUNK);
      if (state.bars !== bars) return;
      bars.raw = mergeRows(part.rows, bars.raw);
      rebuild(); // state.candles 가 낡은 배열을 가리켜 체결이 이중으로 합쳐지는 일을 막음
      bars.cursor = part.next;
      bars.done = !part.next;
      sinceRender += part.rows.length;
      state.pullOlder = false;
      const covered = !needsMore(bars.raw, state.iv, currentPeriod(), bars.done);
      if (covered || sinceRender >= RENDER_EVERY || bars.done) { sinceRender = 0; rebuildAndRender(); }
    }
    if (state.bars === bars && sinceRender > 0) rebuildAndRender();
  } catch (e) {
    if (state.bars === bars) showMsg(`과거 데이터를 더 불러오지 못했습니다: ${e.message}`, true);
  } finally {
    bars.loading = false;
    if (state.bars === bars) {
      const n = bars.raw.length;
      setHist(n ? `${fmtDate(bars.raw[0].time)} ~ ${fmtDate(bars.raw.at(-1).time)} · ${n.toLocaleString('ko-KR')}봉 보유${bars.done ? ' (전체)' : n >= MAX_RAW ? ' (상한)' : ' · 왼쪽 끝으로 끌면 더 불러옵니다'}` : '');
    }
  }
}

function rebuildAndRender() {
  const vr = state.rangeMode === 'free' ? chMain.timeScale().getVisibleRange() : null; // 직접 움직이는 중이면 보던 위치를 유지
  rebuild();
  renderAll();
  if (vr) {
    const c = state.candles;
    chMain.timeScale().setVisibleLogicalRange({ from: idxAtOrAfter(c, vr.from - KST), to: idxAtOrAfter(c, vr.to - KST) });
  } else applyPeriod();
}

/** 화면에 보일 구간을 선택한 기간으로 맞춘다 (데이터는 그대로 두고 보이는 범위만 좁힘) */
function applyPeriod() {
  const c = state.candles;
  if (!c.length) return;
  const p = currentPeriod();
  const start = periodStart(c, state.iv, p);
  if (start === -Infinity) { chMain.timeScale().fitContent(); return; }
  chMain.timeScale().setVisibleLogicalRange({ from: idxAtOrAfter(c, start) - 0.5, to: c.length - 1 + 6 });
}

// ---------- 그리기 ----------
// 계산 전 구간은 값 없는 점(whitespace)으로 채워서 세 차트의 봉 위치(논리 인덱스)를 맞춘다
const pts = (arr) => arr.map((v, i) => (v == null ? { time: ct(state.candles[i].time) } : { time: ct(state.candles[i].time), value: v }));

function recompute() {
  state.ind = computeIndicators(state.candles);
  state.strategy = activeStrategy();
  const st = state.strategy;
  state.userRun = st === 'user' ? runUser(state.candles, state.ind) : null; // 통합형은 차트 표시용 이벤트(거래량 폭증·보유 유지)도 함께 계산
  state.story = st === 'manual' && state.iv === '1d' ? storyBuys(state.candles, state.ind, manualSignals(state.candles, state.symbol).filter((x) => x.side === 'sell')) : []; // 규칙으로 계산한 매수 후보(사용자 검증용)
  state.storySell = st === 'manual' && state.iv === '1d' ? storySells(state.candles, state.ind, manualSignals(state.candles, state.symbol)) : [];
  state.surge = st === 'story' && state.iv === '1d' ? storySurges(state.candles, state.ind) : []; // 거래량 폭증(참고용)
  state.signals = st === 'story' ? (state.iv === '1d' ? computeStorySignals(state.candles, state.ind, { ...STORY, ...(OPTIONS[state.symbol] || {}), exclude: EXCLUDE[state.symbol] }) : []) : st === 'manual' ? (state.iv === '1d' ? manualSignals(state.candles, state.symbol) : []) : st === 'user' ? state.userRun.signals : st === 'bb' ? computeBollingerSignals(state.candles, state.ind) : st === 'score' ? computeScoreSignals(state.candles, state.ind) : st === 'rule' ? computeSignals(state.candles, state.ind) : [];
}

function applyVisibility() {
  for (const n of MA_LIST) sMa[n].applyOptions({ visible: state.maOn.has(n) });
  sBbU.applyOptions({ visible: state.bbOn });
  sBbL.applyOptions({ visible: state.bbOn });
}

function renderAll() {
  const c = state.candles;
  recompute();
  const { ind } = state;
  sCandle.setData(c.map((x) => ({ time: ct(x.time), open: x.open, high: x.high, low: x.low, close: x.close })));
  sVol.setData(c.map((x) => ({ time: ct(x.time), value: x.volume, color: (x.close >= x.open ? upColor() : downColor()) + '66' })));
  for (const n of MA_LIST) sMa[n].setData(pts(ind.ma[n]));
  sBbU.setData(pts(ind.bb.upper));
  sBbL.setData(pts(ind.bb.lower));
  sRsi.setData(pts(ind.rsi));
  sHist.setData(ind.macd.hist.map((v, i) => (v == null ? { time: ct(c[i].time) } : { time: ct(c[i].time), value: v, color: v >= 0 ? upColor() : downColor() })));
  sMacd.setData(pts(ind.macd.line));
  sSig.setData(pts(ind.macd.signal));
  applyVisibility();
  renderMarkers();
  renderPanel();
  renderQuote();
  renderLegend();
  renderPeriods();
  renderBadge();
  renderRules();
}

/** 틱이 들어올 때: 마지막 봉만 갱신 */
function renderLast() {
  const c = state.candles;
  const n = c.length - 1;
  if (n < 0) return;
  recompute();
  const { ind } = state;
  const x = c[n];
  const t = ct(x.time);
  sCandle.update({ time: t, open: x.open, high: x.high, low: x.low, close: x.close });
  sVol.update({ time: t, value: x.volume, color: (x.close >= x.open ? upColor() : downColor()) + '66' });
  const one = (s, v) => { if (v != null) s.update({ time: t, value: v }); };
  for (const m of MA_LIST) one(sMa[m], ind.ma[m][n]);
  one(sBbU, ind.bb.upper[n]); one(sBbL, ind.bb.lower[n]);
  one(sRsi, ind.rsi[n]); one(sMacd, ind.macd.line[n]); one(sSig, ind.macd.signal[n]);
  if (ind.macd.hist[n] != null) sHist.update({ time: t, value: ind.macd.hist[n], color: ind.macd.hist[n] >= 0 ? upColor() : downColor() });
  renderMarkers();
  renderPanel();
  renderQuote();
}

const MARKER_TEXT_MAX = 40; // 화면에 이보다 신호가 많으면 글자 없이 작은 화살표만
const fmtRet = (v) => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%`;

function renderMarkers() {
  const [i0, i1] = viewIdx();
  const inView = state.signals.reduce((n, s) => n + (s.i >= i0 && s.i <= i1 ? 1 : 0), 0);
  const dense = inView > MARKER_TEXT_MAX;
  const marks = state.signals.map((s) => ({
    time: ct(s.time),
    position: s.side === 'buy' ? 'belowBar' : 'aboveBar',
    shape: s.side === 'buy' ? 'arrowUp' : 'arrowDown',
    color: s.side === 'buy' ? '#ff6b6b' : '#5aa2ff',
    size: dense ? 0.6 : 1,
    text: dense ? '' : (s.side === 'buy' ? (s.tag ? `매수 ${s.tag}` : '매수') : `매도 ${fmtRet(s.ret)}`), // 매도 마커에는 그 거래의 손익을 함께 표시
  }));
  if (state.userRun) {
    // 통합형 확인용: 거래량 폭증 봉(보라 점), 상단을 터치했지만 거래량 동반이라 보유를 유지한 봉(노란 점)
    for (const e of state.userRun.events) {
      if (e.kind === 'surge') marks.push({ time: ct(e.time), position: 'belowBar', shape: 'circle', color: '#b08cff', size: 0.5, text: '' });
      else marks.push({ time: ct(e.time), position: 'aboveBar', shape: 'circle', color: '#ffd98a', size: dense ? 0.6 : 1, text: dense ? '' : '보유' });
    }
    marks.sort((x, y) => x.time - y.time);
  }
  for (const e of state.surge || []) marks.push({ time: ct(e.time), position: 'belowBar', shape: 'circle', color: '#b08cff', size: 0.6, text: '' }); // 거래량 폭증(참고)
  if (state.surge && state.surge.length) marks.sort((x, y) => x.time - y.time);
  if (state.strategy === 'manual' && state.iv === '1d') {
    // 특이 신호: 매수는 아니지만 곧 상승 가능성이 있다고 본 봉
    // 규칙 매수 후보(초안): 사용자가 지정한 매수일과 겹치지 않는 날만 작은 초록 점으로 표시
    const manualBuyTimes = new Set(state.signals.filter((s) => s.side === 'buy').map((s) => s.time));
    for (const e of state.story || []) if (!manualBuyTimes.has(e.time)) marks.push({ time: ct(e.time), position: 'belowBar', shape: 'circle', color: '#3ddc97', size: 0.7, text: dense ? '' : e.kinds.map((k) => k.split(' ')[0]).join('·') });
    for (const e of state.storySell || []) marks.push({ time: ct(e.time), position: 'aboveBar', shape: 'circle', color: '#5aa2ff', size: 0.7, text: dense ? '' : '규칙매도' });
    for (const e of manualEvents(state.candles, state.symbol)) marks.push({ time: ct(e.time), position: 'belowBar', shape: 'circle', color: '#ffd98a', size: 1, text: e.text });
    marks.sort((x, y) => x.time - y.time);
  }
  sCandle.setMarkers(marks);
}

function renderLegend() {
  $('legend').innerHTML = MA_LIST.map((n) => `<button type="button" class="${state.maOn.has(n) ? 'on' : ''}" data-ma="${n}" style="--c:${MA_COLORS[n]}" aria-pressed="${state.maOn.has(n)}"><i></i>MA${n}</button>`).join('')
    + `<button type="button" class="${state.bbOn ? 'on' : ''}" data-bb="1" style="--c:#8b97a5" aria-pressed="${state.bbOn}"><i></i>볼린저(20,2)</button>`;
}

function renderPeriods() {
  const cur = currentPeriod()[0];
  $('periods').innerHTML = periodsFor(state.iv).map(([name]) => `<button type="button" data-p="${name}" class="${state.rangeMode === 'period' && name === cur ? 'on' : ''}">${name}</button>`).join('');
}

function renderQuote() {
  const c = state.candles;
  const nm = nameOf(state.symbol);
  $('qSym').textContent = nm ? `${nm} ${state.symbol}` : state.symbol;
  if (!c.length) { $('qPrice').textContent = '–'; $('qChg').textContent = ''; $('qTime').textContent = ''; return; }
  const last = c[c.length - 1];
  let ref;
  let label;
  if (state.iv === '1d') { ref = c[c.length - 2]?.close; label = '전일 대비'; } else { ref = c[idxAtOrAfter(c, startOfLastDays(c, 1).start)]?.open; label = '당일 시작 대비'; }
  $('qPrice').textContent = fmtPrice(last.close);
  if (ref) {
    const d = last.close - ref;
    const el = $('qChg');
    el.textContent = `${d >= 0 ? '▲' : '▼'} ${fmtPrice(Math.abs(d))} (${fmtPct(d / ref)}) ${label}`;
    el.className = `q-chg ${d >= 0 ? 'up' : 'down'}`;
  } else $('qChg').textContent = '';
  $('qTime').textContent = `${fmtTime(last.time)} 기준`;
}

/**
 * 지금 화면에 보이는 봉 범위 [시작, 끝] 인덱스.
 * 차트의 봉 번호(논리 인덱스)는 과거 데이터를 이어받는 중간에 state.candles 와 어긋날 수 있어서,
 * 보이는 시간 범위를 받아 state.candles 에서 찾는다.
 */
function viewIdx() {
  const c = state.candles;
  const n = c.length;
  const r = n ? chMain.timeScale().getVisibleRange() : null;
  if (!r) return [0, Math.max(0, n - 1)];
  const i0 = idxAtOrAfter(c, r.from - KST);
  const i1 = Math.min(n - 1, idxAtOrAfter(c, r.to - KST));
  return [Math.min(i0, n - 1), Math.max(Math.min(i0, n - 1), i1)];
}

/** 조건 체크리스트 한 줄의 현재 수치 설명 */
function checkDetail(c) {
  const p = (v) => (v == null ? '계산 중' : fmtPrice(v));
  switch (c.key) {
    case 'trend': return `종가 ${p(c.close)} · MA20 ${p(c.ma20)} · MA60 ${p(c.ma60)}`;
    case 'momentum': return c.ok ? `MACD 양전환 ${c.flipAgo === 0 ? '이번 봉' : `${c.flipAgo}봉 전`}` : `최근 ${RULES.momWindow}봉 안에 양전환 없음`;
    case 'heat': return c.rsi == null ? '계산 중' : `RSI ${c.rsi.toFixed(0)}`;
    case 'stop': return `종가 ${p(c.close)} · 손절선 ${p(c.stop)}`;
    case 'trendBreak': return `종가 ${p(c.close)} · MA20 ${p(c.ma20)} · MACD 히스토그램 ${c.hist == null ? '–' : c.hist.toFixed(2)}`;
    case 'heatExit': return c.rsi == null ? '계산 중' : `RSI ${c.rsi.toFixed(0)}`;
    case 'touch': return `저가 ${p(c.low)} · 하단 밴드 ${p(c.lower)}`;
    case 'trendMa': return c.ma == null && BB_RULES.trendMa ? '계산 중' : `종가 ${p(c.close)} · MA${BB_RULES.trendMa} ${p(c.ma)}`;
    case 'align': return c.m.some((v) => v == null) ? '계산 중' : `MA20 ${p(c.m[0])} · MA60 ${p(c.m[1])} · MA120 ${p(c.m[2])} · MA200 ${p(c.m[3])}`;
    case 'accum': return c.maxRatio == null ? '계산 중' : `최근 거래량 최대 ${c.maxRatio.toFixed(1)}배 · 기간 상승률 ${(c.rise * 100).toFixed(0)}%`;
    case 'volUp': return c.ok ? '최근 5봉 종가 상승 + 거래량 평균 이상 → 보유 근거' : '거래량 동반 상승 아님';
    case 'dead': return c.armed ? (c.dead520 ? '5/20 데드크로스 발생' : c.dead560 ? '5/60 데드크로스 발생' : '보유 전환 상태 · 데드크로스 대기') : '보유 전환 전 (상단 터치 + 거래량 동반 시 시작)';
    case 'upper': return `고가 ${p(c.high)} · 상단 밴드 ${p(c.upper)}`;
    default: return '';
  }
}

function checklistHtml(checks) {
  return `<ul class="chk">${checks.map((c) => `<li class="${c.ok ? 'ok' : 'no'}"><span class="t">${c.ok ? '✓' : '✗'} ${esc(c.label)}</span><span class="d">${esc(checkDetail(c))}</span><span class="r">${esc(c.rule)}</span></li>`).join('')}</ul>`;
}

const fmtNum = (v, d = 2) => (v == null ? '–' : v.toFixed(d));

/** 세 지표를 읽는 법 (볼린저 %B·밴드폭, RSI 추세 구간, MACD 0선·시그널) */
function renderContext() {
  const el = $('ctxBody');
  const ctx = state.candles.length ? describeContext(state.candles, state.ind) : null;
  if (!ctx) { el.textContent = '데이터 없음'; return; }
  const { bb, rsi: r, macd: m } = ctx;
  const bbNote = bb.pctB == null ? '계산 중' : bb.pctB > 1 ? '상단 밴드 위 (밴드를 타고 오르는 중일 수 있음)' : bb.pctB < 0 ? '하단 밴드 아래' : bb.pctB >= 0.8 ? '상단 근처' : bb.pctB <= 0.2 ? '하단 근처' : '밴드 중간권';
  const regimeName = { bull: '상승 추세', bear: '하락 추세', neutral: '중립' }[r.regime];
  const zoneName = { high: '구간 상단 이상', low: '구간 하단 미만', mid: '정상 구간' }[r.zone] || '';
  el.innerHTML = `
    <div class="row"><span class="k">볼린저(20, 2)</span> <span class="v">%B ${fmtNum(bb.pctB)} · 밴드폭 ${bb.bandwidth == null ? '–' : (bb.bandwidth * 100).toFixed(1) + '%'}</span>${bb.squeeze ? '<span class="tag hi">수축</span>' : ''}
      <span class="n">${bbNote}${bb.rank != null ? ` · 밴드폭은 최근 ${SQUEEZE.lookback}봉 중 하위 ${Math.max(1, Math.round(bb.rank * 100))}%` : ''}${bb.squeeze ? ' → 변동성 수축(방향은 알 수 없음)' : ''}</span></div>
    <div class="row"><span class="k">RSI(14)</span> <span class="v">${r.value == null ? '–' : r.value.toFixed(0)}</span><span class="tag">${regimeName} ${r.range[0]}~${r.range[1]}</span>
      <span class="n">${zoneName}${r.zone === 'high' ? ' (과열 가능)' : r.zone === 'low' ? ' (지지 약화)' : ''}</span></div>
    <div class="row"><span class="k">MACD(12, 26, 9)</span> <span class="v">${fmtNum(m.line)} / 시그널 ${fmtNum(m.signal)}</span>
      <span class="n">${m.aboveZero == null ? '계산 중' : `0선 ${m.aboveZero ? '위(상승 편향)' : '아래(하락 편향)'} · 시그널선 ${m.aboveSignal ? '위(모멘텀 강화)' : '아래(모멘텀 약화)'}`}</span></div>`;
}

// ---------- 시가총액 구간·신호 방식 ----------
/** 이 종목의 시총 구간 정보. 국내 보통주만 순위표에 있다 */
function tierInfo() {
  const e = state.caps?.items?.[state.symbol];
  return e ? { tier: tierOf(e), cap: e[0], rank: e[1] } : { tier: null };
}

/** 대형주 규칙(사용자 설명)을 자동으로 적용할 종목. 현대차에서 검증이 끝나면 넓힌다 */
const STORY_SYMBOLS = new Set(['005380', '009150', '373220', '196170']);

/** 화면에 쓸 신호 방식: score(점수형) / rule(규칙형) / off(끔) / pending(구간 확인 중) */
function activeStrategy() {
  if (state.mode !== 'auto') return state.mode;
  if (countryOf(state.symbol) === 'kr' && state.capsLoading) return 'pending';
  const { tier } = tierInfo();
  if (tier === 'large') {
    return state.iv === '1d' ? 'story' : 'off'; // 대형주(시총 100위): 사용자 설명 규칙(일봉만). 2026-10-18 전체 대형주로 확대 — 튜닝에 안 쓴 93종목에서 수익 종목 92%·평균 +102%(통합형 73%·+25%)
  }
  if (tier === 'mid' || tier === 'small' || tier === 'junk') return 'off';
  return 'score'; // 순위표에 없는 종목(해외·ETF·우선주 등)은 검증 대상이 아니지만 참고용으로 점수형을 보여준다
}

function tierNote() {
  const { tier, cap, rank } = tierInfo();
  if (tier) return `${TIER_LABEL[tier]} · 시총 ${rank}위 · ${fmtCap(cap)}`;
  if (countryOf(state.symbol) !== 'kr') return '해외 종목 · 구간 분류 대상 아님';
  return state.capsLoading ? '시총 구간 확인 중…' : state.capsError ? '시총 구간을 불러오지 못함' : '구간 미분류 (ETF·우선주 등)';
}

function renderBadge() {
  const { tier } = tierInfo();
  if (tier && state.tabSyncedFor !== state.symbol) {
    state.tabSyncedFor = state.symbol;
    if (state.tierTab !== tier) { state.tierTab = tier; store.set('stock:tier', tier); renderPresets(); }
  }
  const el = $('tierBadge');
  el.textContent = tierNote();
  el.className = `tier ${tier || 'none'}`;
  el.title = `대형주: 시총 ${TIER.largeRank}위 이내 / 중형주: ${TIER.largeRank + 1}위 이하이면서 ${fmtCap(TIER.midCap)} 이상 / 소형주: ${fmtCap(TIER.junkCap)} 이상 ${fmtCap(TIER.midCap)} 미만 / 잡주: ${fmtCap(TIER.junkCap)} 미만 (코스피·코스닥 보통주 합산, 현재 시가총액 기준)`;
}

const STRAT_NAME = { story: '대형주 규칙(사용자 설명)', manual: '사진 기준(내가 지정)', user: '통합형(내 가정)', bb: '볼린저형', score: '점수형', rule: '규칙형', off: '신호 꺼짐', pending: '확인 중' };

function renderRules() {
  const st = state.strategy;
  const common = `<details><summary>시총 구간과 지표 설정</summary><ul>
      <li><b>대형주</b> 시총 ${TIER.largeRank}위 이내 → 현대차·삼성전기·LG에너지솔루션은 대형주 규칙(사용자 설명), 그 외 대형주는 통합형(내 가정)</li>
      <li><b>중형주</b> ${TIER.largeRank + 1}위 이하이면서 ${fmtCap(TIER.midCap)} 이상 → 방식 검토 중(신호 꺼짐)</li>
      <li><b>소형주</b> ${fmtCap(TIER.junkCap)} 이상 ${fmtCap(TIER.midCap)} 미만 → 방식 검토 중(신호 꺼짐)</li>
      <li><b>잡주</b> ${fmtCap(TIER.junkCap)} 미만 → 신호 대상 아님(신호 꺼짐)</li>
      <li>순위는 코스피·코스닥 보통주 합산, 현재 시가총액 기준(하루 1회 갱신).</li>
      <li><b>볼린저(20, 2σ)</b> 20봉 단순이동평균 ± 2×표준편차(모집단). <b>RSI(14)</b> Wilder 평활. <b>MACD(12, 26, 9)</b>. 세 설정 모두 널리 쓰이는 표준 기본값입니다.</li>
    </ul></details>`;
  let html;
  if (st === 'story') {
    html = `<p class="rl-h buy">대형주 규칙(사용자 설명) — 매수는 <b>볼린저 하단 터치</b>가 기본</p>
      <p>하단 터치(T1·T3·T4)가 아니면 매수하지 않습니다. 20일선 재매수(T2)는 하단 터치가 아니어서 뺐고, 예외는 횡보 중 크게 오르는 돌파(주식의 시작) 하나뿐이며, 하락장·역배열에서는 하단 터치가 아니면(골든크로스, 거래량 급증 포함) 매수가 아닙니다.</p>
      <ol><li><b>T1 하단 이탈 + 간격 확대</b> · 20&gt;60&gt;120&gt;200 정배열, 60일-120일·120일-200일 간격이 벌어지는 중, 5일선 하락 중에 <b>종가가 볼린저 하단 아래로 마감하고 RSI(14) ≤ ${STORY.t1Rsi} 또는 20봉 고점 대비 ${STORY.t1Drop * 100}% 이하</b></li><li><b>T3b 정배열로 전환 중</b> · 60일선 &gt; 200일선, 120일선이 오르고 200일선이 내리며 ${STORY.t3bRatio * 100}% 이내로 수렴하는 중에 저가가 하단 밴드와 200일선에 동시에 닿음. 매수 후 ${STORY.deepSkipBars}봉은 첫 돌파 매도와 60일선 하락 매도를 적용하지 않음</li><li><b>횡보 + 등락폭 큰 날의 T3·T3b 매수 금지</b> · 직전 30봉 효율비(순변화/이동거리) &lt; ${STORY.chopEr}(횡보)이면서 ATR(14)이 종가의 ${STORY.chopAtr}% 이상이면 T3·T3b는 사지 않음 (다른 매수에는 적용 안 함)</li><li><b>T6 과대 낙폭</b> · 저가가 하단 밴드의 +${STORY.deepTouch * 100}% 이내 + RSI(14) ≤ ${STORY.deepRsi} + 250봉 고점 대비 ${STORY.deepDrop * 100}% 이하, 또는 RSI ≤ ${STORY.deepRsi2} + 고점 대비 ${STORY.deepDrop2 * 100}% 이하(더 깊은 낙폭은 RSI 완화). 역배열·200일선 아래여도 허용(바닥 반등 초반의 60일선 하락 매도는 매수 후 ${STORY.deepSkipBars}봉 동안 적용 안 함)</li><li><b>T3 200일선 + 하단</b> · 60&gt;120&gt;200 정배열에서 저가가 하단 밴드와 200일선(+${STORY.maTouch * 100}% 이내)에 동시에 닿음</li><li><b>T4 특이신호 후</b> · 최근 ${STORY.alertBars}봉 안에 거래량 ${STORY.alertK}배↑ + 하단 터치가 있었고, 정배열에서 다시 하단 터치(5일선이 20일선보다 ${-STORY.alertGap}% 넘게 아래면 제외)</li><li><b>돌파(시작)</b> · 직전 20봉 고저폭 ≤ ${STORY.surgeMaxRange * 100}%(횡보) + 60일선 상승 + 당일 +${STORY.surgeRet * 100}%↑ + 종가가 20·60·120·200일선 위 + 거래량 ${STORY.surgeStrongK}배↑ (${STORY.surgeK}~${STORY.surgeStrongK}배는 20일선이 안 오르던 바닥 횡보 뒤 첫 돌파만)</li><li><b>T7 상단 매도 뒤 하단 근접 재매수</b> · '상단 대폭 이탈'·'장악형 음봉' 매도 후 ${STORY.nearWithin}봉 안에, 20&gt;60&gt;120&gt;200 정배열(또는 60&gt;120&gt;200) + 60일선 상승 + RSI ≤ ${STORY.nearRsiMax} + 종가가 200일선 위에서 저가가 하단 밴드의 +${STORY.nearTol * 100}% 이내로 다시 내려올 때(급락 금지 조건은 적용 안 함, 한 번 팔 때마다 첫 근접만)</li></ol><p>거래량은 보조 지표이고 차트가 우선입니다. <b style="color:#b08cff">보라 점</b>은 거래량 폭증 + 큰 상승 + 이평선 위 봉(참고 표시)입니다.</p>
      <p>역배열 5/20 골든크로스(T5)는 볼린저 하단 터치가 아니어서 현재 <b>꺼 두었습니다</b>(의도 확인 대기).</p>
      <p>공통 금지: 종가가 ${STORY.maBelow}일선 아래일 때(정배열·T3 터치·돌파 판단은 200일선, 매수 금지만 224일선), 60일선 5봉 변화가 0 이하일 때, 5일-20일 간격이 5봉 사이 ${-STORY.crashGap}%p 이상 급격히 벌어질 때.</p>
      <p class="rl-h sell">매도: 보유 중일 때 위에서부터 판단</p>
      <ol><li>상단 이탈 ≥ ${STORY.hugeOver * 100}% + 거래량 ${STORY.hugeVol}배↑ → 즉시 매도(과열, 보류 근거 무시)</li><li>상단 이탈 ≥ ${STORY.blowOver * 100}% + 거래량 ${STORY.blowVol}배↑ + 음봉(몸통 ${STORY.blowBody * 100}% 이하) + 20일선이 안 오르는 중 → 매도(횡보·하락 중 거래량 터진 뒤 밀림)</li><li>5/60 데드크로스는 60·120·200일선이 ${STORY.deadTight * 100}% 이내로 엉켜 있는(수렴) 구간에서는 보류 · 5/60 데드크로스는 T6·T3b 매수 후 ${STORY.deadSkipBars}봉까지 보류 · T6·T3b 보유 중 20&gt;60&gt;120(60일선이 ${STORY.unalignedStable}봉 넘게 120일선 위) 이지만 200일선 아래일 때 상단 터치 + RSI ≥ ${STORY.unalignedRsi}이면 매도</li><li>RSI 과열 매도(음봉 + RSI ≥ ${STORY.sellRsi} + 거래량 감소 + 20일선 이격 ≥ ${STORY.rsiExt20Min * 100}%)는 60일선이 20봉에 ${STORY.steepSlope * 100}% 넘게 가파르게 오르는 중이면 RSI ≥ ${STORY.steepRsi}일 때만 적용</li><li>추세가 약한 구간의 상단 긴 윗꼬리: 상단 터치 + 윗꼬리 ${STORY.wk2Wick * 100}%↑ + RSI ≤ ${STORY.wk2RsiMax} + 60일선 기울기(20봉) ${STORY.wk2SlopeMin * 100}~${STORY.wk2SlopeMax * 100}% + 종가가 200일선보다 ${STORY.wk2Ext200Min * 100}~${STORY.wk2Ext200Max * 100}% 위 + 거래량 ${STORY.wk2VolMax}배 이하 → 매도</li><li><b>상단 밀림(2단계)</b> · 상단 터치 + 몸통 작은 음봉(−${STORY.tierBBody * 100}% 이내) + 윗꼬리 ${STORY.tierBWick * 100}%↑ + 거래량 감소(전일보다) + 거래량 20일 평균의 ${STORY.tierBVol}배↑ + RSI ≥ ${STORY.tierBRsi} + 20일선 이격 ≥ ${STORY.tierBExt * 100}% → OBV 보류 근거가 있어도 매도</li><li>강력한 5/60 데드크로스 (5일-20일 간격이 5봉 사이 ${-STORY.strongDeadGap}%p 이상 벌어짐)</li><li>상단 터치 + 60일선 하락 중 — 단 상단을 ${STORY.sell60Over * 100}% 이상 넘고 밴드폭이 ${STORY.sell60Bw * 100}% 이상이고 거래량이 20일 평균의 ${STORY.sell60Vol}배 이상이고 윗꼬리가 캔들 길이의 ${STORY.sell60Wick * 100}% 이상일 때만(밴드가 좁아진 구간의 살짝 터치나 고가 부근에서 마감한 강한 양봉으로는 팔지 않음)</li><li>밴드 횡보(전일 밴드폭 ≤ ${STORY.breakBand * 100}%) 중 상단을 ${STORY.breakOver * 100}%↑ 크게 이탈한 거래량 ${STORY.breakVol}배↑ 양봉 (단 최근 10개월 안에 특이 신호가 있었고 그 뒤 첫 돌파면 보유)</li><li>상단 터치한 날 음봉(종가&lt;시가) + RSI(14) ≥ ${STORY.sellRsi} + 거래량이 전일보다 감소 + 20일선 이격 ${STORY.rsiExt20Min * 100}% 이상 (거래량이 계속 늘며 상승 중이면 미룸)</li><li>상단 터치한 날 어제 양봉을 덮는 장악형 음봉, 단 매수 후 수익이 ${STORY.engulfMinGain * 100}% 이상일 때만</li><li>이평선이 정배열이 아닐 때 상단을 터치한 날 RSI가 ${STORY.hotRsi} 이상이면(과열) 매도</li><li>상단을 ${STORY.bigBearOver * 100}% 넘은 뒤 몸통 ${STORY.bigBearBody * 100}% 이하 큰 음봉 — <b>이평선이 정배열(20&gt;60&gt;120&gt;200)이 아닐 때만</b> (정배열 상승 추세에서는 보유)</li><li><b>보류 근거:</b> 위 두 고점 매도(RSI·장악형)는 <b>OBV(거래량 누적)가 60봉 신고가</b>이면 팔지 않고 <b>추세 보유 모드</b>로 들어갑니다(거래량이 가격 상승을 뒷받침하는 중). 이 모드에서는 RSI·장악형 매도를 무시하고, <b>횡보(10봉↑) 뒤 상단을 다시 터치했는데 RSI가 직전 최고보다 20↑ 낮고 종가가 60일선보다 80%↑ 높으면</b> 팝니다. 그 전에 <b>2일 연속 종가가 20일선 아래</b>로 마감해도 팝니다</li><li>상단 터치인데 상승 지속(5봉 상승 + 거래량 동반)이 아님</li></ol>
      <p>한 번에 한 포지션, 체결은 다음 봉 시가, 손절 없음. 5/20 데드크로스는 신규 매수만 막고 보유 중이면 참고용입니다.</p>
      <p class="verify">검증(코스피·코스닥 944종목, 이후 20봉 수익): <b>T1은 대형주 100종목에서만 우위</b>(승률 68%, 기준선 52%, 평균 +5.8%). 단, 2021·2022년은 손실(승률 40%·30%)이었고 2025-08 한 달에 21건이 몰렸습니다. <b>돌파는 우위가 없어 껐고</b>(승률 48%) T2·T3·T4는 약하거나 표본이 적습니다. 규칙 전체의 대형 100종목 5년 결과: 수익 종목 77%, 평균 +26%·중앙값 +11%, 매매 승률 68%(볼린저형 71%·81%·+59%보다 낮음). 중형·소형주에서는 모든 규칙이 우위가 없어 적용하지 않습니다.</p>`;
  } else if (st === 'manual') {
    html = `<p class="rl-h">사진 기준 — 사용자가 차트 사진으로 짚어 준 지점</p>
      <ol>${(MANUAL[state.symbol] || []).map((m) => `<li><b class="${m.side === 'buy' ? 'up' : 'down'}">${m.side === 'buy' ? '매수' : '매도'} ${m.date}</b> · ${esc(m.why)}</li>`).join('')}</ol>
      <p><b style="color:#5aa2ff">파란 점 '규칙매도'</b>는 지정한 매수 뒤 처음 매도 규칙(강력한 5/60 데드크로스 · 상단 터치 + 60일선 하락 · 크게 이탈 + 음봉 · 상단 터치인데 상승 지속 아님)에 걸린 봉입니다. <b style="color:#3ddc97">초록 점</b>은 사용자 설명을 규칙으로 옮긴 매수 후보(초안)입니다. 돌파 · T1 하단 이탈 + 간격 확대 · T2 20일선 지지 · T3 200일선 + 하단 · T4 정배열 시작 직후 + 하단. 지정한 매수일과 겹치지 않는 날만 표시하니, 사지 않을 날이 보이면 알려 주세요. 매수·매도 화살표는 계산한 규칙이 아니라 지정한 날짜를 그대로 표시한 것입니다. 5/20 데드크로스(2026-06-10)는 신규 매수 X, 5/60 데드크로스(2026-06-24)는 강력 매도·매수 X로 봅니다. 2024-07-12~2024-07-29 하단 터치는 이평선이 깨지는 중이라 매수하지 않고, 2026-06-10 이후 하락(상승 없는 계단식 하락)에서도 재매수하지 않습니다. 역배열 구간은 매수하지 않되, 2025-06-16의 특이신호(저가 139,300 아랫꼬리)는 매수는 아니지만 곧 상승이 가능하다는 표시로 노란 점에 '특이신호'를 달았습니다.</p>`;
  } else if (st === 'user') {
    html = `<p class="rl-h buy">통합형(내 가정) — 매수: 아래 3가지를 <b>모두</b> 충족</p>
      <ol><li><b>하단 밴드 터치</b> · 저가 ≤ 볼린저 하단 밴드(20, 2σ)</li><li><b>이평선 정배열</b> · MA20 &gt; MA60 &gt; MA120 &gt; MA200 (역배열이면 매수 안 함)</li><li><b>매집 징후</b> · 최근 ${USER_RULES.accumWindow}봉(약 6개월) 안에 거래량이 직전 20봉 평균의 ${USER_RULES.accumK}배 이상인 봉이 있었고, 그 기간 종가 상승률 ≤ ${Math.round(USER_RULES.accumCap * 100)}%</li></ol>
      <p class="rl-h sell">매도: 보유 중일 때</p>
      <ol><li><b>상단 밴드 터치</b> · 고가 ≥ 볼린저 상단 밴드. 단 <b>거래량 동반 상승</b>(최근 ${USER_RULES.volWindow}봉 종가 상승 + 평균 거래량 ≥ 20봉 평균)이면 팔지 않고 보유</li><li><b>데드크로스</b> · 위처럼 보유로 넘어간 뒤 5/20 또는 5/60 데드크로스가 나오면 매도</li></ol>
      <p>매도한 뒤에는 다음 하단 터치 지점에서 같은 조건으로 다시 매수합니다. 체결은 다음 봉 시가, 손절은 없습니다.</p>
      <p>차트 표시: <b style="color:#b08cff">보라색 점</b> = 거래량 폭증 봉(매집 판단 근거), <b style="color:#ffd98a">노란 점 '보유'</b> = 상단에 닿았지만 거래량 동반이라 팔지 않은 곳.</p>
      <p class="verify">검증(시총 상위 100종목, 5년 일봉): 거래한 종목 97개, 수익 종목 71%, 평균 +23%·중앙값 +14%, 매매 승률 64%, 종목당 4.7회, 미청산 3%. 볼린저형(승률 71%·수익 종목 81%·평균 +59%)보다 성과가 낮으니 차트로 확인하며 비교해 보세요.</p>`;
  } else if (st === 'bb') {
    html = `<p class="rl-h buy">볼린저형 — 매수: 아래 2가지를 <b>모두</b> 충족</p>
      <ol><li><b>하단 밴드 터치</b> · 저가 ≤ 볼린저 하단 밴드(20, 2σ)</li><li><b>장기 추세 위</b> · 종가 &gt; MA${BB_RULES.trendMa}</li></ol>
      <p class="rl-h sell">매도: 보유 중일 때</p>
      <ol><li><b>상단 밴드 터치</b> · 고가 ≥ 볼린저 상단 밴드(20, 2σ)</li></ol>
      <p>한 번에 한 포지션만 잡고, 체결은 신호가 나온 다음 봉 시가로 가정합니다. 손절은 없습니다. 마지막(진행 중) 봉의 신호는 봉이 닫히기 전에는 바뀔 수 있습니다.</p>
      <p class="verify">검증(시총 상위 100종목, 5년 일봉): 매매 승률 <b>71%</b>(청산 거래, 미청산 포함 70%), 수익이 난 종목 <b>81%</b>, 평균 +59%·중앙값 +34%, 거래당 평균 +3.7%, 종목당 11.9회. 단순 보유(평균 +332%)보다는 크게 낮고, 전반 2.5년은 수익 종목 52%·후반 81%로 시기 차이가 큽니다.</p>
      <p class="verify">주의: 5년 끝까지 청산 못 한 종목이 26%이고 그 평가손익은 평균 −11.7%입니다. 하락 추세에서 하단을 계속 건드리며 내려가면 오래 갇힐 수 있습니다.</p>`;
  } else if (st === 'score') {
    html = `<p class="rl-h">점수형 — 이벤트마다 점수를 주고 <b>최근 ${SCORE.window}봉의 합이 ${SCORE.threshold}점 이상</b>이 되는 순간 신호</p>
      <ol>${SCORE_TABLE.map(([, name, pts]) => `<li>${esc(name)} <b>+${pts}</b></li>`).join('')}</ol>
      <p>매수 점수가 더 높으면 매수, 매도 점수가 더 높으면 매도입니다. 신호는 매수 → 매도 순서로만 남기고, 체결은 다음 봉 시가로 가정합니다. 마지막(진행 중) 봉의 신호는 봉이 닫히기 전에는 바뀔 수 있습니다.</p>
      <p class="verify">검증(시총 상위 100종목, 5년 일봉): 수익이 난 종목 <b>75%</b>(전반 70%·후반 72%), 평균 +84%·중앙값 +40%, 매매 승률 47%. 단순 보유(평균 +332%)보다는 크게 낮습니다. 매매 승률 70%는 어떤 방식에서도 나오지 않았습니다.</p>`;
  } else if (st === 'rule') {
    html = `<p class="rl-h buy">규칙형 — 매수: 아래 3가지를 <b>모두</b> 충족</p>
      <ol><li><b>추세 상승</b> · 종가 ≥ MA60 이고 MA20 ≥ MA60</li><li><b>모멘텀 전환</b> · 최근 ${RULES.momWindow}봉 안에 MACD 히스토그램이 음→양</li><li><b>RSI 상승 구간</b> · ${RULES.rsiMin} ≤ RSI(14) &lt; ${RULES.rsiMax}</li></ol>
      <p class="rl-h sell">매도: 보유 중일 때 아래 중 <b>하나라도</b></p>
      <ol><li><b>손절</b> · 종가 &lt; 매수 신호 종가 − ${RULES.atrMult}×ATR(14)</li><li><b>추세 이탈</b> · 종가 &lt; MA20 이고 MACD 히스토그램 &lt; 0</li><li><b>RSI 지지 이탈</b> · RSI(14)가 ${RULES.rsiExit} 위에서 아래로</li></ol>
      <p class="verify">검증(코스피·코스닥 944종목, 5년 일봉): 평균 +9%·중앙값 −11%, 수익이 난 종목 38%. 점수형보다 낮아 기본 방식으로 쓰지 않습니다. 시총 상위 25%에서만 플러스였습니다.</p>`;
  } else if (st === 'off' && tierInfo().tier === 'junk') {
    html = `<p class="rl-h">잡주(시총 ${fmtCap(TIER.junkCap)} 미만)는 신호 대상이 아닙니다</p>
      <p>이 구간은 신호를 표시하지 않도록 분류했습니다. 검증 데이터도 아직 없습니다. 참고용으로 보려면 위의 <b>신호 방식</b>에서 통합형·볼린저형·점수형·규칙형 중 하나를 직접 고르세요(검증되지 않은 신호입니다).</p>`;
  } else if (st === 'off' && tierInfo().tier === 'large') {
    html = `<p class="rl-h">대형주 규칙은 일봉에서만 적용합니다</p>
      <p>분봉에서는 신호가 잦고 거래비용 영향이 커서 대형주 규칙을 적용하지 않습니다. 봉 간격을 <b>일봉</b>으로 바꾸면 매수·매도 신호가 표시됩니다.</p>`;
  } else if (st === 'off') {
    html = `<p class="rl-h">이 구간에는 검증된 신호 방식이 아직 없습니다</p>
      <p>1,000종목 시험에서 두 방식 모두 중형·소형주의 성과가 낮았습니다(점수형: 시총 하위 구간의 수익 종목 비율 41~56%, 규칙형은 31~42%). 방식이 정해질 때까지 매수·매도 신호를 표시하지 않습니다.</p>
      <p>참고용으로 보려면 위의 <b>신호 방식</b>에서 통합형·볼린저형·점수형·규칙형 중 하나를 직접 고르세요(검증되지 않은 신호입니다).</p>`;
  } else html = '<p>시가총액 구간을 확인하는 중입니다…</p>';
  $('rulesTitle').textContent = `매수·매도 기준 · ${STRAT_NAME[st] || ''}`;
  $('rulesBody').innerHTML = html + common;
}

/** 점수표 한 칸: 매수 또는 매도 점수와 이벤트 내역 */
function scoreBlock(title, side, data, threshold) {
  const ok = data.score >= threshold;
  const items = data.items.length ? data.items.map((x) => `<li><span>${esc(x.why)}${x.count > 1 ? ` ×${x.count}` : ''}</span><b>+${x.pts}</b></li>`).join('') : '<li class="none"><span>최근 이벤트 없음</span></li>';
  return `<div class="sc ${side} ${ok ? 'ok' : ''}"><div class="sc-h"><span>${title}</span><b>${data.score} / ${threshold}점</b></div><ul>${items}</ul></div>`;
}

function renderPanel() {
  const c = state.candles;
  const n = c.length;
  const [i0, i1] = viewIdx();
  const sigs = state.signals; // 신호는 전체 데이터로 계산 (지표 워밍업 포함)
  const strat = state.strategy;
  const body = $('nowBody');
  const note = `<p class="tier-note">${esc(tierNote())}</p>`;
  if (strat === 'off' || strat === 'pending' || !n) {
    body.innerHTML = !n ? '데이터 없음' : strat === 'pending'
      ? `<div class="big flat">구간 확인 중</div>${note}<p>시가총액 순위를 계산하는 중입니다. 하루 첫 조회는 30초쯤 걸릴 수 있습니다.</p>`
      : `<div class="big flat">신호 꺼짐</div>${note}<p>${tierInfo().tier === 'junk' ? `잡주(시총 ${fmtCap(TIER.junkCap)} 미만)는 신호 대상이 아니어서 매수·매도 신호를 표시하지 않습니다.` : (tierInfo().tier === 'large' ? '대형주 규칙은 일봉에서만 적용합니다. 위에서 봉 간격을 일봉으로 바꾸세요.' : '이 구간은 검증된 신호 방식이 없어 매수·매도 신호를 표시하지 않습니다.')} 대형주(시총 ${TIER.largeRank}위 이내)에는 대형주 규칙(사용자 설명)을 일봉에서 적용합니다.</p><p>참고용으로 보려면 위의 '신호 방식'을 통합형·볼린저형·점수형·규칙형 중 하나로 바꾸세요.</p>`;
  } else if (strat === 'story') {
    const st = describeStoryState(c, sigs);
    const last = sigs[sigs.length - 1];
    const fresh = last && last.i >= n - 3;
    const head = fresh ? `<div class="big ${last.side === 'buy' ? 'buy' : 'sell'}">${last.side === 'buy' ? `▲ 매수 신호 ${last.tag}` : `▼ 매도 신호 ${fmtRet(last.ret)}`}</div>` : st && st.holding ? '<div class="big buy">보유 구간</div>' : '<div class="big flat">대기</div>';
    const sub = fresh ? `<p>${esc(last.reasons.join(' · '))}</p><p>${last.i === n - 1 ? '진행 중인 봉의 신호입니다. 봉이 닫히기 전에는 바뀔 수 있습니다.' : `${n - 1 - last.i}개 봉 전(${fmtTime(last.time)})에 발생`}</p>` : st && st.holding ? `<p>매수 ${fmtTime(st.entryTime)} · ${fmtPrice(st.entry)} (${esc(st.tag)}) → 현재 <b class="${st.pnl >= 0 ? 'up' : 'down'}">${fmtRet(st.pnl)}</b> · 매도 규칙을 기다리는 중</p>` : `<p>${last ? `마지막 신호: 매도 ${fmtRet(last.ret)} (${fmtTime(last.time)})` : '아직 신호가 없습니다.'}</p>`;
    body.innerHTML = `${head}${note}${sub}`;
  } else if (strat === 'manual') {
    const st = manualState(c, sigs);
    const last = sigs[sigs.length - 1];
    const notes = (MANUAL_NOTES[state.symbol] || []).map((x) => `<li>${esc(x.date)} · ${esc(x.text)}</li>`).join('');
    body.innerHTML = `<div class="big ${st && st.holding ? 'buy' : 'flat'}">${st && st.holding ? '보유 구간' : '대기'}</div>${note}<p>${st && st.holding ? `매수 ${fmtPrice(st.entry)} → 현재 <b class="${st.pnl >= 0 ? 'up' : 'down'}">${fmtRet(st.pnl)}</b>` : last ? `마지막 신호: 매도 ${fmtRet(last.ret)} (${fmtTime(last.time)})` : '표시할 지점이 없습니다.'}</p><p>사진 기준 지점은 규칙 계산이 아니라 지정한 날짜입니다.</p><ul>${notes}</ul>`;
  } else if (strat === 'score') {
    const st = describeScoreState(c, state.ind, sigs);
    const last = sigs[sigs.length - 1];
    const fresh = last && last.i >= n - 3;
    let head;
    let sub;
    if (fresh) {
      const buy = last.side === 'buy';
      head = `<div class="big ${buy ? 'buy' : 'sell'}">${buy ? '▲ 매수 신호' : `▼ 매도 신호 ${fmtRet(last.ret)}`} · ${last.score}점</div>`;
      sub = `<p>${esc(last.reasons.join(' · '))}</p><p>${last.i === n - 1 ? '진행 중인 봉의 신호입니다. 봉이 닫히기 전에는 바뀔 수 있습니다.' : `${n - 1 - last.i}개 봉 전(${fmtTime(last.time)})에 발생`}</p>`;
    } else if (st.holding) {
      head = '<div class="big buy">보유 구간</div>';
      sub = `<p>매수 ${fmtTime(st.entryTime)} · ${fmtPrice(st.entry)} → 현재 <b class="${st.pnl >= 0 ? 'up' : 'down'}">${fmtRet(st.pnl)}</b> · 매도 신호를 기다리는 중</p>`;
    } else {
      head = '<div class="big flat">대기</div>';
      sub = `<p>${last ? `마지막 신호: ${last.side === 'buy' ? '매수' : `매도 ${fmtRet(last.ret)}`} (${fmtTime(last.time)})` : '아직 신호가 없습니다.'}</p>`;
    }
    const sc = st.score;
    body.innerHTML = `${head}${note}${sub}<div class="chk-title">점수표 — 최근 ${sc.window}봉 합계가 ${sc.threshold}점 이상이 되는 순간 신호</div><div class="scores">${scoreBlock('매수 점수', 'buy', sc.buy, sc.threshold)}${scoreBlock('매도 점수', 'sell', sc.sell, sc.threshold)}</div>`;
  } else {
    // 규칙형: 보유 중이면 매도 조건, 대기 중이면 매수 조건을 체크리스트로
    const st = strat === 'user' ? describeUserState(c, state.ind, state.userRun) : strat === 'bb' ? describeBollingerState(c, state.ind, sigs) : describeState(c, state.ind, sigs);
    if (!st) { body.textContent = '봉이 적어 판단하지 않습니다.'; } else {
      const last = sigs[sigs.length - 1];
      const fresh = last && last.i >= n - 3;
      const met = st.checks.filter((x) => x.ok).length;
      let head;
      let sub;
      if (fresh) {
        const buy = last.side === 'buy';
        head = `<div class="big ${buy ? 'buy' : 'sell'}">${buy ? '▲ 매수 신호' : `▼ 매도 신호 ${fmtRet(last.ret)}`}</div>`;
        sub = `<p>${esc(last.reasons.join(' · '))}</p><p>${last.i === n - 1 ? '진행 중인 봉의 신호입니다. 봉이 닫히기 전에는 바뀔 수 있습니다.' : `${n - 1 - last.i}개 봉 전(${fmtTime(last.time)})에 발생`}</p>`;
      } else if (st.holding) {
        head = '<div class="big buy">보유 구간</div>';
        sub = `<p>매수 ${fmtTime(st.entryTime)} · ${fmtPrice(st.entry)} → 현재 <b class="${st.pnl >= 0 ? 'up' : 'down'}">${fmtRet(st.pnl)}</b> · ${strat === 'user' ? `상단 밴드 ${fmtPrice(st.upper)}${st.armed ? ' · 거래량 동반 상승 → 데드크로스까지 보유 중' : ''}` : strat === 'bb' ? `상단 밴드 ${fmtPrice(st.upper)}` : `손절선 ${fmtPrice(st.stop)}`}</p>`;
      } else {
        head = '<div class="big flat">대기</div>';
        sub = `<p>매수 조건 ${met}/${st.checks.length} 충족${last ? ` · 마지막 신호: ${last.side === 'buy' ? '매수' : `매도 ${fmtRet(last.ret)}`} (${fmtTime(last.time)})` : ''}</p>`;
      }
      body.innerHTML = `${head}${note}${sub}<div class="chk-title">${st.title ? `${st.title} (${met}/${st.checks.length} 충족)` : st.holding ? `매도 조건 — 하나라도 충족하면 매도 (${met}/${st.checks.length} 충족)` : `매수 조건 — 모두 충족하면 매수 (${met}/${st.checks.length} 충족)`}</div>${checklistHtml(st.checks)}`;
    }
  }
  renderContext();
  // 신호 목록·백테스트: 화면에 보이는 기간만
  const inView = sigs.filter((s) => s.i >= i0 && s.i <= i1);
  const list = $('sigList');
  if (strat === 'off' || strat === 'pending') list.innerHTML = '<li class="empty">신호가 꺼져 있습니다.</li>';
  else if (!inView.length) list.innerHTML = '<li class="empty">이 기간에 표시할 신호가 없습니다.</li>';
  else {
    list.innerHTML = inView.slice(-15).reverse().map((s) => {
      const buy = s.side === 'buy';
      const right = buy ? (s.stop != null ? `손절선 ${fmtPrice(s.stop)}` : s.score != null ? `${s.score}점` : '') : `<span class="ret ${s.ret >= 0 ? 'up' : 'down'}">${fmtRet(s.ret)}</span>`;
      return `<li><div class="h"><span class="${buy ? 'buy' : 'sell'}"><b>${buy ? '▲ 매수' : '▼ 매도'}</b></span><span>${fmtPrice(s.price)}</span><span>${right}</span><span>${fmtTime(s.time)}</span></div><div class="why">${esc(s.reasons.join(' · '))}</div></li>`;
    }).join('');
  }
  const slice = c.slice(i0, i1 + 1);
  if (strat === 'off' || strat === 'pending') { $('bt').innerHTML = '<p class="note">신호가 꺼져 있어 백테스트를 계산하지 않습니다.</p>'; return; }
  const bt = backtest(slice, inView.map((s) => ({ ...s, i: s.i - i0 })), 0.1);
  const cls = (v) => (v >= 0 ? 'up' : 'down');
  const warn = state.iv === '1d' ? '' : '<p class="note">분봉은 신호가 잦고 거래비용 영향이 커서, 5분봉 시험에서 성과가 낮았습니다. 일봉에서 더 참고할 만합니다.</p>';
  const unverified = tierInfo().tier === 'large' ? '' : '<p class="note">이 종목은 검증된 구간(대형주)이 아니라 참고용입니다.</p>';
  $('bt').innerHTML = slice.length < 30 ? '<p class="note">봉이 적어 계산하지 않습니다.</p>' : `<table>
    <tr><th>기간</th><td>${fmtDate(slice[0].time)} ~ ${fmtDate(slice.at(-1).time)}</td></tr>
    <tr><th>청산한 거래</th><td>${bt.closedCount}회${bt.trades.at(-1)?.open ? ' (+보유 1)' : ''}</td></tr>
    <tr><th>승률</th><td>${bt.winRate == null ? '–' : `${(bt.winRate * 100).toFixed(0)}%`}</td></tr>
    <tr><th>신호 전략 수익률</th><td class="${cls(bt.total)}">${fmtPct(bt.total)}</td></tr>
    <tr><th>그냥 보유(비교)</th><td class="${cls(bt.hold)}">${fmtPct(bt.hold)}</td></tr>
  </table><p class="note">매수 신호 다음 봉 시가에 사고 매도 신호 다음 봉 시가에 파는 롱 전용, 한쪽 수수료 0.1% 가정. 화면에 보이는 기간 기준이며 과거 결과는 미래를 보장하지 않습니다.</p>${warn}${unverified}`;
}

// ---------- 실시간 ----------
let es = null;
function startStream() {
  if (es) es.close();
  setLive('wait', '연결 중…');
  es = new EventSource(`/stream?symbols=${encodeURIComponent(state.symbol)}`);
  es.onmessage = (ev) => {
    let m;
    try { m = JSON.parse(ev.data); } catch { return; }
    if (m.type === 'trade' && m.symbol === state.symbol) { state.lastTradeAt = Date.now(); applyTrade(m); }
    else if (m.type === 'feed') {
      if (m.state === 'open') setLive('on', '실시간 연결됨');
      else if (m.state === 'connecting' || m.state === 'idle') setLive('wait', '연결 중…');
      else if (m.state === 'rejected') { setLive('off', '구독 거부'); showMsg(`실시간 구독이 거부되었습니다: ${m.detail}`, true); }
      else setLive('off', `끊김 · 재연결 중${m.detail ? ` (${m.detail})` : ''}`);
    }
  };
  es.onerror = () => setLive('off', '중계 서버와 끊김');
}

let pending = false;
function schedule() {
  if (pending) return;
  pending = true;
  const run = () => { pending = false; renderLast(); };
  if (state.candles.length > 20000) setTimeout(run, 800); // 봉이 아주 많으면 지표 재계산이 무거워 간격을 둠
  else requestAnimationFrame(run);
}

/** arr 의 마지막 봉에 체결을 합치거나 새 봉을 붙인다. size: 봉 길이(초) 또는 'd'(일봉) */
function bump(arr, t, size) {
  const last = arr[arr.length - 1];
  const vol = t.volume || 0;
  if (size === 'd') {
    if (t.time - last.time < 86400) { last.high = Math.max(last.high, t.price); last.low = Math.min(last.low, t.price); last.close = t.price; last.volume += vol; } else arr.push({ time: Math.floor(t.time / 86400) * 86400, open: t.price, high: t.price, low: t.price, close: t.price, volume: vol });
    return;
  }
  const b = Math.floor(t.time / size) * size;
  if (b < last.time) return; // 늦게 도착한 옛 체결
  if (b === last.time) { last.high = Math.max(last.high, t.price); last.low = Math.min(last.low, t.price); last.close = t.price; last.volume += vol; } else arr.push({ time: b, open: t.price, high: t.price, low: t.price, close: t.price, volume: vol });
}

function applyTrade(t) {
  const raw = state.bars.raw;
  if (!raw.length || !(t.price > 0)) return;
  if (!tradeBelongs(state.bars.key, state.symbol, t.symbol)) return; // 종목 전환 중 다른 종목의 가격이 섞이지 않게
  if (state.iv === '1d') bump(raw, t, 'd');
  else {
    bump(raw, t, 60); // 원본(1분봉)에도 반영해 두면 과거를 더 받아 다시 묶어도 실시간 값이 유지됨
    if (state.candles !== raw) bump(state.candles, t, Number(state.iv) * 60);
  }
  schedule();
}

// 체결이 한동안 없으면(휴장·비활성 종목 등) 현재가를 3초마다 직접 확인해 마지막 봉만 갱신
setInterval(async () => {
  if (document.hidden || !state.candles.length || Date.now() - state.lastTradeAt < 10_000) return;
  const sym = state.symbol;
  try {
    const j = await api(`/api/prices?symbols=${encodeURIComponent(sym)}`);
    const p = j.prices?.find((x) => x.symbol === sym);
    if (p && sym === state.symbol) applyTrade({ symbol: sym, price: Number(p.lastPrice), volume: 0, time: Math.floor(Date.parse(p.timestamp) / 1000) || state.candles.at(-1).time });
  } catch { /* 다음 주기에 재시도 */ }
}, 3000);

// ---------- 기간·이동평균 조작 ----------
$('periods').addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (!b) return;
  state.period[state.iv] = b.dataset.p;
  store.set(`stock:p:${state.iv}`, b.dataset.p);
  state.rangeMode = 'period';
  renderPeriods();
  applyPeriod();
  backfill(); // 더 긴 기간이면 과거를 이어받음
  renderPanel();
});
$('legend').addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (!b) return;
  if (b.dataset.ma) {
    const n = Number(b.dataset.ma);
    if (state.maOn.has(n)) state.maOn.delete(n); else state.maOn.add(n);
    store.set('stock:ma', JSON.stringify([...state.maOn]));
  } else {
    state.bbOn = !state.bbOn;
    store.set('stock:bb', state.bbOn ? '1' : '0');
  }
  applyVisibility();
  renderLegend();
});

// ---------- 종목 목록 (국내/해외) ----------
const uni = { kr: null, us: null }; // { items, names:Map }
const picker = { open: false, query: '', market: 'all', group: 'all', tier: 'all', shown: PAGE, hi: 0, results: [] };

async function loadUniverse(country) {
  if (uni[country]) return;
  try {
    const j = await api(`/api/universe?country=${country}`);
    uni[country] = { items: j.items, names: new Map(j.items.map((x) => [x[0], x[1]])) };
  } catch (e) {
    uni[country] = null;
    picker.error = e.message;
  }
  if (picker.open) renderPicker();
  renderPresets();
  renderQuote();
}
const nameOf = (sym) => uni.kr?.names.get(sym) || uni.us?.names.get(sym) || PRESET_NAMES[sym] || '';

function renderPicker() {
  const u = uni[state.country];
  const list = $('pickList');
  // 거래소·유형 칩
  const mk = ['all', ...MARKETS[state.country]];
  $('mktChips').innerHTML = mk.map((m) => `<button type="button" data-m="${m}" class="${picker.market === m ? 'on' : ''}">${m === 'all' ? '전체' : MARKET_LABEL[m]}</button>`).join('');
  $('grpChips').innerHTML = GROUPS.map(([g, n]) => `<button type="button" data-g="${g}" class="${picker.group === g ? 'on' : ''}">${n}</button>`).join('');
  const krMode = state.country === 'kr';
  $('tierChips').hidden = !krMode;
  $('tierChips').innerHTML = ['all', ...TIER_ORDER].map((t) => `<button type="button" data-t="${t}" class="${picker.tier === t ? 'on' : ''}">${t === 'all' ? '시총 전체' : TIER_LABEL[t]}</button>`).join('');
  if (!u) {
    $('pickCount').textContent = '';
    list.innerHTML = `<li class="state">${picker.error ? esc(picker.error) : '종목 목록을 불러오는 중… (하루 첫 접속은 10초쯤 걸립니다)'}</li>`;
    return;
  }
  let found = searchUniverse(u.items, { query: picker.query, market: picker.market, group: picker.group });
  if (krMode && picker.tier !== 'all') {
    if (!state.caps) {
      $('pickCount').textContent = '';
      list.innerHTML = '<li class="state">시총 구간을 계산하는 중입니다…</li>';
      return;
    }
    found = found.filter((it) => tierOf(state.caps.items[it[0]]) === picker.tier);
    if (!picker.query.trim()) found.sort((x, y) => state.caps.items[x[0]][1] - state.caps.items[y[0]][1]); // 검색어가 없으면 시총 큰 순
  }
  picker.results = found;
  $('pickCount').textContent = `${picker.results.length.toLocaleString('ko-KR')}개`;
  picker.hi = Math.min(picker.hi, Math.max(picker.results.length - 1, 0));
  if (!picker.results.length) {
    list.innerHTML = '<li class="state">검색 결과가 없습니다. 코드를 직접 입력하고 Enter 를 눌러 조회할 수도 있습니다.</li>';
    return;
  }
  list.innerHTML = picker.results.slice(0, picker.shown).map((it, i) => `<li role="option" id="opt${i}" data-sym="${esc(it[0])}" class="${i === picker.hi ? 'hi' : ''}" aria-selected="${it[0] === state.symbol}"><span class="nm">${esc(it[1])}</span><span class="cd">${esc(it[0])}</span>${capText(it[0])}<span class="mk">${MARKET_LABEL[it[2]] || it[2]}</span></li>`).join('')
    + (picker.results.length > picker.shown ? '<li class="state">아래로 스크롤하면 더 보입니다…</li>' : '');
  $('symInput').setAttribute('aria-activedescendant', `opt${picker.hi}`);
}

/** 목록 한 줄에 붙이는 시총 표시 (국내 보통주만) */
function capText(sym) {
  const e = state.caps?.items?.[sym];
  return e ? `<span class="cp">${fmtCap(e[0])}</span>` : '';
}

function openPicker(open) {
  picker.open = open;
  $('panel').hidden = !open;
  $('symInput').setAttribute('aria-expanded', String(open));
  $('browse').setAttribute('aria-expanded', String(open));
  if (open) { picker.shown = PAGE; picker.hi = 0; renderPicker(); loadUniverse(state.country); }
}

const TIER_PRESET_N = 6; // 구간 탭 아래에 보여줄 대표 종목 수 (시총 큰 순)

/** 국내: 시총 구간 탭 + 그 구간의 시총 상위 종목 버튼 / 해외: 고정 목록 */
function renderPresets() {
  const kr = state.country === 'kr';
  $('tierTabs').hidden = !kr;
  const box = $('presets');
  if (!kr) {
    box.innerHTML = PRESETS[state.country].map(([s, n]) => `<button type="button" data-sym="${s}">${esc(nameOf(s) || n)}</button>`).join('');
    markActive();
    return;
  }
  const lists = state.tierLists;
  $('tierTabs').innerHTML = TIER_ORDER.map((t) => `<button type="button" data-t="${t}" class="${state.tierTab === t ? 'on' : ''}">${TIER_LABEL[t]}${lists ? ` <small>${lists[t].length.toLocaleString('ko-KR')}</small>` : ''}</button>`).join('');
  if (!lists) {
    // 순위표를 아직 못 받았거나 실패: 받는 중에는 안내, 실패하면 고정 목록으로 대체
    box.innerHTML = state.capsError
      ? PRESETS.kr.map(([s, n]) => `<button type="button" data-sym="${s}">${esc(nameOf(s) || n)}</button>`).join('')
      : '<span class="hint">시총 구간 계산 중…</span>';
  } else {
    const syms = lists[state.tierTab].slice(0, TIER_PRESET_N);
    box.innerHTML = syms.map((s) => {
      const [cap, rank] = state.caps.items[s];
      return `<button type="button" data-sym="${s}" title="시총 ${rank}위 · ${fmtCap(cap)}">${esc(nameOf(s) || s)}</button>`;
    }).join('') + `<button type="button" class="more" data-more="1" title="${TIER_LABEL[state.tierTab]} 전체를 시총 순으로 보기">전체 보기 ▸</button>`;
  }
  markActive();
}

function setCountry(c, { select = true } = {}) {
  state.country = c;
  store.set('stock:country', c);
  picker.market = 'all'; picker.group = 'all'; picker.tier = 'all'; picker.query = ''; picker.hi = 0; picker.shown = PAGE;
  for (const b of $('country').children) b.classList.toggle('on', b.dataset.c === c);
  renderPresets();
  loadUniverse(c);
  if (picker.open) renderPicker();
  if (select && countryOf(state.symbol) !== c) setSymbol(PRESETS[c][0][0]);
  else markActive();
}

// ---------- 조작 ----------
function setSymbol(s) {
  s = s.trim().toUpperCase();
  if (!/^[A-Z0-9.\-]{1,12}$/.test(s)) { showMsg('종목 코드는 영문·숫자 1~12자입니다. 예: 005930, AAPL (이름은 목록에서 고르세요)', true); return; }
  state.symbol = s;
  if (countryOf(s) !== state.country) setCountry(countryOf(s), { select: false });
  $('symInput').value = '';
  $('symInput').placeholder = `${nameOf(s) || s} (${s})`;
  picker.query = '';
  openPicker(false);
  markActive();
  load();
}
function markActive() {
  for (const b of $('presets').children) b.classList.toggle('on', b.dataset.sym === state.symbol);
  for (const b of $('intervals').children) b.classList.toggle('on', b.dataset.iv === state.iv);
  for (const b of $('country').children) b.classList.toggle('on', b.dataset.c === state.country);
}

$('country').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b && b.dataset.c !== state.country) setCountry(b.dataset.c); });
$('presets').addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (!b) return;
  if (b.dataset.more) { picker.tier = state.tierTab; picker.market = 'all'; picker.group = 'all'; picker.query = ''; $('symInput').value = ''; openPicker(true); return; }
  setSymbol(b.dataset.sym);
});
$('tierTabs').addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (!b || b.dataset.t === state.tierTab) return;
  state.tierTab = b.dataset.t;
  store.set('stock:tier', state.tierTab);
  renderPresets();
});
$('tierChips').addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (b) { picker.tier = b.dataset.t; picker.hi = 0; picker.shown = PAGE; renderPicker(); }
});
$('intervals').addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (!b || b.dataset.iv === state.iv) return;
  state.iv = b.dataset.iv;
  store.set('stock:iv', state.iv);
  markActive();
  load();
});

const input = $('symInput');
input.addEventListener('focus', () => { if (!picker.open) openPicker(true); });
input.addEventListener('input', () => { picker.query = input.value; picker.hi = 0; picker.shown = PAGE; if (!picker.open) openPicker(true); else renderPicker(); });
input.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { openPicker(false); return; }
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    if (!picker.open) { openPicker(true); return; }
    const max = Math.min(picker.results.length, picker.shown) - 1;
    picker.hi = Math.max(0, Math.min(max, picker.hi + (e.key === 'ArrowDown' ? 1 : -1)));
    renderPicker();
    $(`opt${picker.hi}`)?.scrollIntoView({ block: 'nearest' });
  }
});
$('symForm').addEventListener('submit', (e) => {
  e.preventDefault();
  const q = input.value.trim();
  if (!q) { openPicker(!picker.open); return; }
  const hit = picker.results[picker.hi];
  // 목록에 후보가 있으면 그것을, 없으면 입력한 글자를 코드로 간주
  setSymbol(hit && picker.open ? hit[0] : q);
});
$('browse').addEventListener('click', () => openPicker(!picker.open));
$('pickList').addEventListener('click', (e) => { const li = e.target.closest('li[data-sym]'); if (li) setSymbol(li.dataset.sym); });
$('pickList').addEventListener('scroll', (e) => {
  const el = e.target;
  if (el.scrollTop + el.clientHeight > el.scrollHeight - 60 && picker.shown < picker.results.length) { picker.shown += PAGE; renderPicker(); }
});
$('mktChips').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) { picker.market = b.dataset.m; picker.hi = 0; picker.shown = PAGE; renderPicker(); } });
$('grpChips').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) { picker.group = b.dataset.g; picker.hi = 0; picker.shown = PAGE; renderPicker(); } });
document.addEventListener('mousedown', (e) => { if (picker.open && !$('picker').contains(e.target)) openPicker(false); });

if (!['1', '5', '15', '1d'].includes(state.iv)) state.iv = '5';
setCountry(state.country, { select: false });
input.placeholder = `${nameOf(state.symbol) || state.symbol} (${state.symbol})`;
markActive();
load();
// 국내 → 해외 순서로 미리 받아 둔다 (종목 목록 한도가 초당 1회라 동시에 부르지 않음)
loadUniverse('kr').then(() => loadUniverse('us')).then(() => { input.placeholder = `${nameOf(state.symbol) || state.symbol} (${state.symbol})`; });

// ---------- 신호 방식 선택·시총 구간 ----------
$('strategy').value = state.mode;
$('strategy').addEventListener('change', () => {
  state.mode = $('strategy').value;
  store.set('stock:strategy', state.mode);
  renderAll();
});

async function loadCaps() {
  state.capsLoading = true;
  try { state.caps = await api('/api/caps?country=kr'); state.capsError = ''; state.tierLists = tierLists(state.caps.items); } catch (e) { state.capsError = e.message; } finally {
    state.capsLoading = false;
    renderPresets();
    if (picker.open) renderPicker();
    if (state.candles.length) renderAll(); else renderBadge();
  }
}
loadCaps();
