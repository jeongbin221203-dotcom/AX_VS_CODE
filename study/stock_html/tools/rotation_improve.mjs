// 순환 매매 수익·승률 개선 탐색(2026-10-18): 현재 설정(중형주: 4자리, 변동폭 큰 순, 시간 손절 250일, 손절 −40%)에
// 매수 필터(RSI·낙폭·변동폭·거래량·직전 수익·신호일 양봉·시장 동향·같은 날 신호 수 등)와 매도 보강(이익 잠금·목표 수익)을 하나씩, 그리고 좋은 것끼리 더해
// 연수익·승률(청산 거래 기준)·낙폭을 비교한다. 선택은 2017~2021, 확인은 2022~2026.
// 사용: node tools/rotation_improve.mjs [캔들 json] [large|mid]
import fs from 'node:fs';
import nodePath from 'node:path';
import { computeIndicators } from '../public/js/signals.js';
import { computeStorySignals, storyBuyAt, STORY } from '../public/js/storyrules.js';

const TIER = process.argv[3] === 'large' ? 'large' : 'mid';
const IN = process.argv[2] || nodePath.join(process.env.TEMP || '.', TIER === 'mid' ? 'rotation_candles_mid.json' : 'rotation_candles.json');
const BASE = TIER === 'mid' ? { N: 4, prio: 'atr', tstop: 250, tgain: 0, stop: 0.4 } : { N: 4, prio: 'tag', tstop: 0, tgain: 0, stop: 0.3 };
const COST = 0.006, LIQ = 1e8, MIN_DATE = '2016-01-01', NL = String.fromCharCode(10);
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
const startIdx = calendar.findIndex((d) => d >= '2017-01-01');
const idx22 = calendar.findIndex((d) => d >= '2022-01-01');

// 시장 동향: 종목군 동일 비중 지수의 200일선
const mret = new Float64Array(calendar.length), mcnt = new Float64Array(calendar.length);
for (const s of Object.values(S)) for (let i = 1; i < s.c.length; i++) { const d = cid.get(s.dates[i]); mret[d] += s.c[i].close / s.c[i - 1].close - 1; mcnt[d]++; }
const lvl = new Float64Array(calendar.length); let L = 1;
for (let i = 0; i < calendar.length; i++) { L *= 1 + (mcnt[i] ? mret[i] / mcnt[i] : 0); lvl[i] = L; }
const mbelow = (d, n) => { if (d < n) return false; let m = 0; for (let j = d - n + 1; j <= d; j++) m += lvl[j]; return lvl[d] < m / n; };

const ev = [];
for (const [code, s] of Object.entries(S)) {
  const { c, ind, dates } = s, n = c.length;
  for (let i = 0; i < n - 1; i++) {
    if (dates[i] < MIN_DATE) continue;
    const kinds = storyBuyAt(c, ind, i, STORY).filter((q) => !q.startsWith('T2'));
    if (!kinds.length) continue;
    const tag = kinds[0].split(' ')[0];
    if (!(tag in TAG_RANK)) continue;
    let liq = 0; for (let j = Math.max(0, i - 19); j <= i; j++) liq += c[j].close * c[j].volume;
    if (liq / Math.min(20, i + 1) < LIQ) continue;
    const sig = computeStorySignals(c, ind, { ...STORY, exclude: i > 0 ? [[dates[0], dates[i - 1]]] : [] });
    const b = sig.find((x) => x.side === 'buy');
    if (!b || b.i !== i) continue;
    const sl = sig.find((x) => x.side === 'sell' && x.i > i);
    let mx = 0; for (let j = Math.max(0, i - 59); j <= i; j++) mx = Math.max(mx, c[j].close);
    let atr = 0, g = 0, l = 0;
    for (let j = Math.max(1, i - 13); j <= i; j++) { atr += (c[j].high - c[j].low) / c[j].close; const d = c[j].close - c[j - 1].close; if (d > 0) g += d; else l -= d; }
    let va = 0; for (let j = Math.max(0, i - 20); j < i; j++) va += c[j].volume; va /= Math.max(1, Math.min(20, i));
    const d0 = cid.get(dates[i]);
    ev.push({ code, i, k: i + 1, tag, dd: c[i].close / mx - 1, atr: atr / 14, rsi: l === 0 ? 100 : 100 - 100 / (1 + g / l), rank: s.rank, sellBar: sl ? sl.i : null,
      volx: va > 0 ? c[i].volume / va : 1, r5: i >= 5 ? c[i].close / c[i - 5].close - 1 : 0, r20: i >= 20 ? c[i].close / c[i - 20].close - 1 : 0, bull: c[i].close > c[i].open ? 1 : 0,
      gapk: c[i + 1].open / c[i].close - 1, mkt200: mbelow(d0, 200) ? 1 : 0, mkt60: mbelow(d0, 60) ? 1 : 0, d0 });
  }
}
const sameDay = new Map(); for (const e of ev) sameDay.set(e.d0, (sameDay.get(e.d0) || 0) + 1);
for (const e of ev) e.nsame = sameDay.get(e.d0);
console.log('종목 ' + Object.keys(S).length + ' 후보 ' + ev.length);

function tradePath(e, v) {
  const { c, dates } = S[e.code], n = c.length, k = e.k, ent = c[k].open;
  if (!(ent > 0)) return null;
  let eb = e.sellBar == null ? n - 1 : e.sellBar, closed = e.sellBar != null, peak = 0;
  for (let j = k; j <= eb; j++) {
    const g = c[j].close / ent - 1, held = j - k; peak = Math.max(peak, g);
    if (v.stop && g <= -v.stop) { eb = j; closed = true; break; }
    if (v.tstop && held >= v.tstop && g < v.tgain) { eb = j; closed = true; break; }
    if (v.tp && g >= v.tp) { eb = j; closed = true; break; }
    if (v.lockG && peak >= v.lockG && g < v.lockF) { eb = j; closed = true; break; }
  }
  const sell = closed && eb + 1 < n;
  const rr = [c[k].close / ent - 1];
  for (let j = k + 1; j <= eb; j++) rr.push(c[j].close / c[j - 1].close - 1);
  if (sell) rr.push(c[eb + 1].open / c[eb].close - 1);
  if (closed) rr[rr.length - 1] -= COST;
  const last = sell ? eb + 1 : eb;
  return { ev: e, rr, s: cid.get(dates[k]), end: cid.get(dates[last]), closed };
}
const KEY = { tag: (o) => [TAG_RANK[o.ev.tag], o.ev.dd], atr: (o) => [0, -o.ev.atr], dd: (o) => [0, o.ev.dd] };
const cache = new Map();
function simulate(v) {
  const ck = JSON.stringify([v.stop, v.tstop, v.tgain, v.tp || 0, v.lockG || 0, v.lockF || 0]);
  if (!cache.has(ck)) cache.set(ck, ev.map((e) => tradePath(e, v)).filter(Boolean));
  const byStart = new Map();
  for (const o of cache.get(ck)) {
    if (v.filter && !v.filter(o.ev)) continue;
    if (!byStart.has(o.s)) byStart.set(o.s, []);
    byStart.get(o.s).push(o);
  }
  const slots = Array(v.N).fill(null), port = new Float64Array(calendar.length); let nt = 0, closedN = 0, win = 0, winA = 0, nA = 0, winB = 0, nB = 0;
  const key = KEY[v.prio];
  for (const d of [...byStart.keys()].sort((a, b) => a - b)) {
    if (d < startIdx) continue;
    const free = []; for (let i = 0; i < v.N; i++) if (!slots[i] || slots[i].end <= d) free.push(i);
    if (!free.length) continue;
    const held = new Set(slots.filter((s) => s && s.end > d).map((s) => s.ev.code));
    const cands = byStart.get(d).filter((o) => !held.has(o.ev.code)).sort((a, b) => { const x = key(a), y = key(b); return x[0] - y[0] || x[1] - y[1]; });
    for (let m = 0; m < Math.min(free.length, cands.length); m++) {
      const o = cands[m]; slots[free[m]] = o; nt++;
      for (let q = 0; q < o.rr.length; q++) port[o.s + q] += o.rr[q] / v.N;
      if (o.closed) { closedN++; const w = o.rr.reduce((a, r) => a * (1 + r), 1) > 1; if (w) win++; if (d < idx22) { nA++; if (w) winA++; } else { nB++; if (w) winB++; } }
    }
  }
  const stat = (a, b) => { let eq = 1, pk = 1, mdd = 0; for (let i = a; i < b; i++) { eq *= 1 + port[i]; pk = Math.max(pk, eq); mdd = Math.min(mdd, eq / pk - 1); } return { cagr: (Math.pow(eq, 245 / (b - a)) - 1) * 100, mdd: mdd * 100 }; };
  const all = stat(startIdx, calendar.length), a = stat(startIdx, idx22), b = stat(idx22, calendar.length);
  let worst = 1e9;
  for (let y = 2017; y <= 2026; y++) {
    const ia = calendar.findIndex((d) => d >= y + '-01-01'), ib = calendar.findIndex((d) => d >= (y + 1) + '-01-01');
    let eq = 1; for (let i = ia; i < (ib < 0 ? calendar.length : ib); i++) eq *= 1 + port[i];
    worst = Math.min(worst, (eq - 1) * 100);
  }
  return { cagr: all.cagr, mdd: all.mdd, a: a.cagr, b: b.cagr, worst, n: nt, win: closedN ? win / closedN * 100 : 0, winA: nA ? winA / nA * 100 : 0, winB: nB ? winB / nB * 100 : 0 };
}
const fmt = (name, r) => name.padEnd(44) + ' | 연 ' + r.cagr.toFixed(1).padStart(5) + '% 승률 ' + r.win.toFixed(0) + '% (17~21 ' + r.winA.toFixed(0) + ' / 22~26 ' + r.winB.toFixed(0) + ') 낙폭 ' + r.mdd.toFixed(0) + '% | 17~21 ' + r.a.toFixed(1) + ' 22~26 ' + r.b.toFixed(1) + ' | 해별 최저 ' + r.worst.toFixed(0) + ' | ' + r.n + '건';
const base = simulate(BASE);
console.log(NL + fmt('현재 설정', base));

// 후보 개선안(하나씩)
const T = {
  'RSI ≤ 35': (e) => e.rsi <= 35, 'RSI ≤ 30': (e) => e.rsi <= 30, 'RSI ≥ 30': (e) => e.rsi >= 30,
  '60일 고점 대비 −25% 이하': (e) => e.dd <= -0.25, '60일 고점 대비 −35% 이하': (e) => e.dd <= -0.35, '60일 고점 대비 −20% 이내': (e) => e.dd > -0.2,
  '변동폭 3% 이상': (e) => e.atr >= 0.03, '변동폭 4% 이상': (e) => e.atr >= 0.04, '변동폭 6% 이하': (e) => e.atr <= 0.06,
  '신호일 거래량 1.5배 이상': (e) => e.volx >= 1.5, '신호일 거래량 3배 이하': (e) => e.volx <= 3,
  '신호일 양봉': (e) => e.bull === 1, '신호일 음봉': (e) => e.bull === 0,
  '5일 수익 −5% 이상(덜 빠짐)': (e) => e.r5 >= -0.05, '5일 수익 ≤ 0': (e) => e.r5 <= 0,
  '20일 수익 ≤ −10%': (e) => e.r20 <= -0.1, '20일 수익 ≥ −20%': (e) => e.r20 >= -0.2,
  '다음 날 갭 하락 아님(시가 ≥ −2%)': (e) => e.gapk >= -0.02, '다음 날 갭 하락(시가 ≤ −1%)': (e) => e.gapk <= -0.01,
  '시장 200일선 위': (e) => e.mkt200 === 0, '시장 60일선 위': (e) => e.mkt60 === 0, '시장 200일선 아래': (e) => e.mkt200 === 1,
  '같은 날 신호 5개 이하': (e) => e.nsame <= 5, '같은 날 신호 3개 이상': (e) => e.nsame >= 3,
  'T4 제외 + T3 제외': (e) => e.tag !== 'T3', '돌파 제외': (e) => e.tag !== '돌파', 'T6 제외': (e) => e.tag !== 'T6', 'T1·T6만': (e) => e.tag === 'T1' || e.tag === 'T6',
};
const rows = [];
for (const [name, f] of Object.entries(T)) { const r = simulate({ ...BASE, filter: f }); rows.push({ name, f, r }); }
const EX = { '이익 잠금: +15%↑ 갔다가 +0% 아래면 정리': { lockG: 0.15, lockF: 0 }, '이익 잠금: +25%↑ 갔다가 +5% 아래면 정리': { lockG: 0.25, lockF: 0.05 }, '이익 잠금: +40%↑ 갔다가 +15% 아래면 정리': { lockG: 0.4, lockF: 0.15 },
  '목표 수익 +50%에 정리': { tp: 0.5 }, '목표 수익 +80%에 정리': { tp: 0.8 }, '시간 손절 120일(수익<0)': { tstop: 120 }, '시간 손절 250일(수익<10%)': { tgain: 0.1 } };
for (const [name, x] of Object.entries(EX)) { const r = simulate({ ...BASE, ...x }); rows.push({ name, x, r }); }
console.log(NL + '하나씩 바꿨을 때(승률·연수익 변화):');
for (const o of rows.sort((p, q) => q.r.cagr - p.r.cagr).slice(0, 0)) console.log('  ' + fmt(o.name, o.r) + ' | Δ연 ' + (o.r.cagr - base.cagr >= 0 ? '+' : '') + (o.r.cagr - base.cagr).toFixed(1) + ' Δ승률 ' + (o.r.win - base.win >= 0 ? '+' : '') + (o.r.win - base.win).toFixed(1));

// 좋은 필터끼리 더하기(수익이 줄지 않으면서 승률이 오르는 조합) — 자리 수도 함께 바꿔 봄
const FIX = ['같은 날 신호 3개 이상', '신호일 거래량 3배 이하', '신호일 양봉', '변동폭 6% 이하', '5일 수익 ≤ 0', '돌파 제외'];
const subsets = []; for (let m = 1; m < (1 << FIX.length); m++) { const sel = FIX.filter((_, i) => m & (1 << i)); if (sel.length <= 3) subsets.push(sel); }
const combos = [];
for (const sel of subsets) for (const N of [4, 5, 6, 8]) {
  const fs_ = sel.map((n) => T[n]); const r = simulate({ ...BASE, N, filter: (e) => fs_.every((f) => f(e)) }); combos.push({ name: 'N' + N + ' ' + sel.join(' + '), r });
}
console.log(NL + '필터 조합(연수익 + 승률 상위 14, 두 구간 모두 +10% 이상):');
for (const o of combos.filter((o) => o.r.a >= 10 && o.r.b >= 10).sort((p, q) => (q.r.cagr + q.r.win) - (p.r.cagr + p.r.win)).slice(0, 14)) console.log('  ' + fmt(o.name, o.r));
console.log(NL + '연수익 상위 6:');
for (const o of combos.filter((o) => o.r.a >= 10 && o.r.b >= 10).sort((p, q) => q.r.cagr - p.r.cagr).slice(0, 6)) console.log('  ' + fmt(o.name, o.r));
