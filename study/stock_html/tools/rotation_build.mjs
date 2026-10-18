// 대형주·중형주 순환 매매 계산(2026-10-18): 시총 1~100위(large) 또는 101~300위·5,000억 이상(mid) 종목의 대형주 규칙(storyrules.js) 신호로
//  - 4자리만 보유, 비는 자리에 그 날의 매수 신호 종목(T1 → T6 → T3b → T3 → 돌파 순, 같은 종류는 60일 고점 대비 낙폭이 깊은 순; T4 제외)을 사고,
//  - 보유 종목은 그 종목의 매도 신호가 나면 정리 — 이를 반복.
// 손절: 종가가 매수가보다 30% 이상 아래로 마감하면 다음 날 시가에 정리(회복 못 하는 종목이 자리를 막는 것을 방지).
// 체결은 신호 다음 날 시가, 비용 0.6%(왕복), 자리마다 동일 비중, 빈 자리는 현금, 거래대금(20일 평균) 1억 원 미만 종목은 사지 않음.
// 과거 시험(대형주 표본 90종목, 2017~2026, tools/research/q40.py): 4자리 + 손절 −30% → 연 +24.9%, 낙폭 −43%, 2017~21 +21% · 2022~26 +29%, 해별 최저 −9%. 수익 보장 아님.
// 사용: node tools/rotation_build.mjs [캔들 json] [출력 json] [large|mid]
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { computeIndicators } from '../public/js/signals.js';
import { computeStorySignals, storyBuyAt, STORY } from '../public/js/storyrules.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const TIER = process.argv[4] === 'mid' ? 'mid' : 'large';
const IN = process.argv[2] || path.join(process.env.TEMP || '.', TIER === 'mid' ? 'rotation_candles_mid.json' : 'rotation_candles.json');
const OUT = process.argv[3] || path.join(HERE, '..', 'public', TIER === 'mid' ? 'rotation_mid.json' : 'rotation.json');
// 구간별 설정(tools/research/q40.py 시험 결과): 대형주 = 4자리 + 손절 −30%, 중형주 = 5자리 + 시간 손절(250일이 지나도 손실이면 정리; 중형주는 가격 손절이 오히려 수익을 깎았음)
const CFG = {
  large: { N: 4, stop: 0.30, timeStop: null, half: 0.35, tp: 0.50, prio: 'tag', label: '대형주', study: { cagr: 32.2, mdd: -43, y1: 34.8, y2: 29.5, worstYear: -1, win: 70, note: '대형주 99종목에서 4자리 + 손절 −30% + 수익 +35%에서 절반 정리 + 나머지는 +50% 목표 또는 규칙 매도를 고른 시험(같은 종목군에서 고른 설정이라 낙관적). 이전 설정(연 +28.7%, 승률 64%)보다 연수익과 승률이 함께 올랐습니다. 대형주 표본 90종목 시험은 장기 +24.9%였습니다'} },
  mid: { N: 4, stop: 0.40, timeStop: 250, half: 0.25, tp: 0.35, prio: 'atr', minSame: process.env.ROT_MINSAME !== undefined ? Number(process.env.ROT_MINSAME) : 0, maxVolx: process.env.ROT_MAXVOLX ? Number(process.env.ROT_MAXVOLX) : 3, label: '중형주', study: { cagr: 29.4, mdd: -49, y1: 28.8, y2: 30.0, worstYear: -10, win: 76, note: '중형주 196종목에서 4자리 + 변동폭 큰 순 + 신호일 거래량 3배 이하 + 수익 +25%에서 절반 정리 + 나머지는 +35% 목표 또는 규칙 매도 + 시간 손절 250일 + 손절 −40%를 고른 시험(같은 종목군에서 고른 설정이라 낙관적). 이전 설정(연 +26.6%, 승률 71%)보다 연수익과 승률이 함께 올랐습니다. 화면의 숫자와 ±2%p 차이가 나는 것은 후보 정렬 방식 차이입니다' } },
}[TIER];
const HALF = process.env.ROT_HALF !== undefined ? (Number(process.env.ROT_HALF) || 0) : (CFG.half || 0), TP = process.env.ROT_TP !== undefined ? (Number(process.env.ROT_TP) || 0) : (CFG.tp || 0);
const N = process.env.ROT_N ? Number(process.env.ROT_N) : CFG.N, STOP = process.env.ROT_STOP !== undefined ? (Number(process.env.ROT_STOP) || null) : CFG.stop, TSTOP = process.env.ROT_TSTOP !== undefined ? (Number(process.env.ROT_TSTOP) || null) : CFG.timeStop, COST = 0.006, LIQ = 1e8, MIN_DATE = '2016-01-01';
const TAG_RANK = { T1: 0, T6: 1, T3b: 2, T3: 3, 돌파: 4 };
const ymd = (t) => new Date((t + 9 * 3600) * 1000).toISOString().slice(0, 10);

const src = JSON.parse(fs.readFileSync(IN, 'utf8'));
const S = {};
for (const [code, v] of Object.entries(src.stocks)) {
  const c = v.c.map(([time, open, high, low, close, volume]) => ({ time, open, high, low, close, volume }));
  S[code] = { name: v.name, rank: v.rank, c, ind: computeIndicators(c), dates: c.map((x) => ymd(x.time)) };
}
const calendar = [...new Set(Object.values(S).flatMap((s) => s.dates))].sort();
const cid = new Map(calendar.map((d, i) => [d, i]));
const lastDate = calendar[calendar.length - 1];

// 후보 거래: 매 봉의 매수 신호(보유 중이어도 독립 계산) → 그 봉에서 들어갔다고 보고 매도 신호까지 이어서 계산
const ev = [];
for (const [code, s] of Object.entries(S)) {
  const { c, ind, dates } = s;
  const first = dates[0];
  const closes = c.map((x) => x.close), vols = c.map((x) => x.volume);
  for (let i = 0; i < c.length; i++) {
    if (dates[i] < MIN_DATE) continue;
    const kinds = storyBuyAt(c, ind, i, STORY).filter((q) => !q.startsWith('T2'));
    if (!kinds.length) continue;
    const tag = kinds[0].split(' ')[0];
    if (!(tag in TAG_RANK)) continue;                         // T4 등 제외
    let liq = 0;
    for (let j = Math.max(0, i - 19); j <= i; j++) liq += closes[j] * vols[j];
    if (liq / Math.min(20, i + 1) < LIQ) continue;
    let mx = 0;
    for (let j = Math.max(0, i - 59); j <= i; j++) mx = Math.max(mx, closes[j]);
    const dd = closes[i] / mx - 1;
    let sellBar = null, why = '', jh = -1;
    if (i < c.length - 1) {
      const ex = i > 0 ? [[first, dates[i - 1]]] : [];
      const sig = computeStorySignals(c, ind, { ...STORY, exclude: ex });
      const b = sig.find((x) => x.side === 'buy');
      if (!b || b.i !== i) continue;
      const sl = sig.find((x) => x.side === 'sell' && x.i > i);
      if (sl) { sellBar = sl.i; why = (sl.reasons[0] || ''); }
      const entPx = c[i + 1].open, stopTo = sellBar == null ? c.length - 1 : sellBar;
      for (let j = i + 1; j <= stopTo; j++) {
        if (STOP && c[j].close <= entPx * (1 - STOP)) { sellBar = j; why = `손절(매수가 대비 −${STOP * 100}%)`; break; }
        if (TSTOP && j - (i + 1) >= TSTOP && c[j].close < entPx) { sellBar = j; why = `시간 손절(${TSTOP}거래일이 지나도 손실)`; break; }
        if (TP && c[j].close >= entPx * (1 + TP)) { sellBar = j; why = `목표 수익 +${TP * 100}% 도달`; break; }
      }
      if (HALF) { const lim = sellBar == null ? c.length - 1 : sellBar; for (let j = i + 1; j <= lim; j++) if (c[j].close >= entPx * (1 + HALF)) { jh = j; break; } }
    }
    let atr = 0;
    for (let j = Math.max(0, i - 13); j <= i; j++) atr += (c[j].high - c[j].low) / c[j].close;
    atr /= Math.min(14, i + 1);
    let va = 0; for (let j = Math.max(0, i - 20); j < i; j++) va += c[j].volume;
    va /= Math.max(1, Math.min(20, i));
    ev.push({ code, i, tag, kinds, dd, atr, volx: va > 0 ? c[i].volume / va : 1, sellBar, jh, why });
  }
}

// 같은 날 매수 신호가 난 종목 수(시장 전체가 같이 눌린 날 = 반등이 강했던 날): 중형주는 3개 이상일 때만 산다
const sameDay = new Map();
for (const e of ev) { const d = S[e.code].dates[e.i]; sameDay.set(d, (sameDay.get(d) || 0) + 1); }
for (const e of ev) e.nsame = sameDay.get(S[e.code].dates[e.i]);

// 거래 → 시가 체결 수익 경로
const trades = [];
for (const e of ev) {
  const s = S[e.code], { c, dates } = s, n = c.length;
  const sigDate = dates[e.i];
  if (e.i >= n - 1) { trades.push({ ...e, today: true, start: cid.get(sigDate), sigDate }); continue; } // 오늘 신호(체결은 다음 거래일 시가)
  const k = e.i + 1, ent = c[k].open;
  const eb = e.sellBar == null ? n - 1 : e.sellBar;
  if (eb < k || !(ent > 0)) continue;
  const closed = e.sellBar != null, sell = closed && eb + 1 < n;
  const rr = [c[k].close / ent - 1];
  for (let j = k + 1; j <= eb; j++) rr.push(c[j].close / c[j - 1].close - 1);
  let exitPrice = c[eb].close, exitDate = dates[eb];
  if (sell) { rr.push(c[eb + 1].open / c[eb].close - 1); exitPrice = c[eb + 1].open; exitDate = dates[eb + 1]; }
  if (closed) rr[rr.length - 1] -= COST;
  if (e.jh >= 0) {                                                       // 분할: 목표(HALF)에 닿은 다음 거래일 시가에 절반 정리(비용 반영), 이후는 절반 비중으로 이어감
    const hi = Math.min(e.jh + 1 - k, rr.length - 1);
    const out = [];
    for (let q = 0; q < rr.length; q++) {
      if (q < hi) out.push(rr[q]);
      else if (q === hi) { const sold = e.jh + 1 < n ? c[e.jh + 1].open / c[e.jh].close - 1 : 0; out.push(0.5 * sold + 0.5 * rr[q] - 0.5 * COST * (eb > e.jh ? 1 : 0)); }
      else out.push(0.5 * rr[q]);
    }
    rr.length = 0; rr.push(...out);
  }
  const last = sell ? eb + 1 : eb;
  trades.push({ ...e, today: false, entryDate: dates[k], entryPrice: ent, exitDate, exitPrice, closed, ret: rr.reduce((a, r) => a * (1 + r), 1) - 1, days: rr.length, rr, startIdx: cid.get(dates[k]), endIdx: cid.get(dates[last]), sigDate });
}

// 순환: 날짜 순으로 빈 자리에 우선순위 높은 후보(대형주: 매수 종류 → 낙폭 깊은 순, 중형주: 변동폭(ATR%) 큰 순 — 가벼운 종목이 더 크게 움직여서)
const byPrio = CFG.prio === 'atr' ? (a, b) => b.atr - a.atr : (a, b) => TAG_RANK[a.tag] - TAG_RANK[b.tag] || a.dd - b.dd;
const byStart = new Map();
for (const t of trades) if (!t.today) { const k = t.startIdx; if (!byStart.has(k)) byStart.set(k, []); byStart.get(k).push(t); }
const slots = Array(N).fill(null);
const port = new Float64Array(calendar.length);
const taken = [];
for (const d of [...byStart.keys()].sort((a, b) => a - b)) {
  const free = [];
  for (let i = 0; i < N; i++) if (!slots[i] || slots[i].endIdx <= d) free.push(i);
  if (!free.length) continue;
  const held = new Set(slots.filter((s) => s && s.endIdx > d).map((s) => s.code));
  const cands = byStart.get(d).filter((t) => !held.has(t.code) && (!CFG.minSame || t.nsame >= CFG.minSame) && (!CFG.maxVolx || t.volx <= CFG.maxVolx)).sort(byPrio);
  for (let m = 0; m < Math.min(free.length, cands.length); m++) {
    const t = cands[m]; slots[free[m]] = t; taken.push(t);
    for (let q = 0; q < t.rr.length; q++) port[t.startIdx + q] += t.rr[q] / N;
  }
}

// 성과
const eq = new Float64Array(calendar.length); let v = 1, peak = 1, mdd = 0;
const startIdx = calendar.findIndex((d) => d >= '2017-01-01');
for (let i = startIdx; i < calendar.length; i++) { v *= 1 + port[i]; eq[i] = v; peak = Math.max(peak, v); mdd = Math.min(mdd, v / peak - 1); }
const years = (calendar.length - startIdx) / 245;
const yearly = {};
for (let y = 2017; y <= Number(lastDate.slice(0, 4)); y++) {
  let p = 1, cnt = 0;
  for (let i = 0; i < calendar.length; i++) if (calendar[i].startsWith(String(y))) { p *= 1 + port[i]; cnt++; }
  yearly[y] = { ret: (p - 1) * 100, days: cnt };
}
const closedTr = taken.filter((t) => t.closed);
const stats = { cagr: (Math.pow(v, 1 / years) - 1) * 100, mdd: mdd * 100, trades: taken.length, closed: closedTr.length, win: closedTr.length ? closedTr.filter((t) => t.ret > 0).length / closedTr.length * 100 : null, avgDays: taken.reduce((a, t) => a + t.days, 0) / (taken.length || 1) };

// 지금 상태
const holdNow = slots.filter((t) => t && !t.closed).map((t) => {
  const s = S[t.code], n = s.c.length, lastClose = s.c[n - 1].close;
  const sellToday = (() => { // 마지막 봉에서 매도 신호가 났는지(체결은 다음 거래일 시가)
    const ex = [[s.dates[0], s.dates[t.i - 1] || s.dates[0]]];
    const sig = computeStorySignals(s.c, s.ind, { ...STORY, exclude: ex });
    const sl = sig.find((x) => x.side === 'sell' && x.i > t.i);
    if (STOP && lastClose <= t.entryPrice * (1 - STOP)) return `손절(매수가 대비 −${STOP * 100}%)`;
    if (TSTOP && n - 1 - (t.i + 1) >= TSTOP && lastClose < t.entryPrice) return `시간 손절(${TSTOP}거래일이 지나도 손실)`;
    return sl && sl.i === n - 1 ? (sl.reasons[0] || '') : null;
  })();
  return { code: t.code, name: s.name, tag: t.tag, entryDate: t.entryDate, entryPrice: t.entryPrice, last: lastClose, pnl: (lastClose / t.entryPrice - 1) * 100, sellSignal: sellToday, half: t.jh >= 0 ? (t.jh + 1 <= n - 1 ? 'done' : 'tomorrow') : null };
});
const heldCodes = new Set(holdNow.map((h) => h.code));
const freeNow = N - holdNow.length;
const todayCands = trades.filter((t) => t.today && !heldCodes.has(t.code) && (!CFG.minSame || t.nsame >= CFG.minSame) && (!CFG.maxVolx || t.volx <= CFG.maxVolx)).sort(byPrio)
  .map((t, i) => ({ rank: i + 1, code: t.code, name: S[t.code].name, tag: t.tag, kinds: t.kinds, dd: t.dd * 100, atr: t.atr * 100, close: S[t.code].c[S[t.code].c.length - 1].close, buy: i < freeNow }));
const recent = taken.filter((t) => t.closed).slice(-25).reverse().map((t) => ({ code: t.code, name: S[t.code].name, tag: t.tag, entryDate: t.entryDate, exitDate: t.exitDate, ret: t.ret * 100, days: t.days, why: t.why, half: t.jh >= 0 }));
const equity = [];
for (let i = startIdx; i < calendar.length; i += 5) equity.push([calendar[i], Number(eq[i].toFixed(4))]);
equity.push([lastDate, Number(eq[calendar.length - 1].toFixed(4))]);

const outObj = { generated: new Date().toISOString(), asof: lastDate, capsDate: src.caps_date, tier: TIER, label: CFG.label, stop: STOP, timeStop: TSTOP, study: CFG.study, universe: Object.keys(S).length, slots: N, cost: COST, stats, yearly, holdings: holdNow, freeSlots: freeNow, candidates: todayCands, recent, equity,
  note: '신호는 종가 기준으로 계산하고 체결은 다음 거래일 시가로 가정합니다. 과거 시뮬레이션이며 수익을 보장하지 않습니다.' };
fs.writeFileSync(OUT, JSON.stringify(outObj));
console.log(`종목 ${outObj.universe}, 후보 거래 ${ev.length}, 선택 ${taken.length}건 | 연 ${stats.cagr.toFixed(1)}% 낙폭 ${stats.mdd.toFixed(0)}% 승률 ${stats.win?.toFixed(0)}% | 기준일 ${lastDate} | 보유 ${holdNow.length} 후보 ${todayCands.length} → ${OUT}`);
console.log('해별:', Object.entries(yearly).map(([y, o]) => `${y} ${o.ret >= 0 ? '+' : ''}${o.ret.toFixed(0)}%`).join(' '));
