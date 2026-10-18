// 승률 극대화 탐색(2026-10-18): '싸게 사서 비싸게 판다' — 더 싼 자리(낙폭·RSI·하단 이탈)만 사고, 일정 수익이 나면 비싼 자리에서 정리해 승률을 올린다.
// (rotation_improve.mjs 의 공통 부분 복사) 원래 설명: 순환 매매 수익·승률 개선 탐색(2026-10-18): 현재 설정(중형주: 4자리, 변동폭 큰 순, 시간 손절 250일, 손절 −40%)에
// 매수 필터(RSI·낙폭·변동폭·거래량·직전 수익·신호일 양봉·시장 동향·같은 날 신호 수 등)와 매도 보강(이익 잠금·목표 수익)을 하나씩, 그리고 좋은 것끼리 더해
// 연수익·승률(청산 거래 기준)·낙폭을 비교한다. 선택은 2017~2021, 확인은 2022~2026.
// 사용: node tools/rotation_improve.mjs [캔들 json] [large|mid]
import fs from 'node:fs';
import nodePath from 'node:path';
import { computeIndicators } from '../public/js/signals.js';
import { computeStorySignals, storyBuyAt, STORY } from '../public/js/storyrules.js';

const TIER = process.argv[3] === 'large' ? 'large' : 'mid';
const IN = process.argv[2] || nodePath.join(process.env.TEMP || '.', TIER === 'mid' ? 'rotation_candles_mid.json' : 'rotation_candles.json');
const BASE = TIER === 'mid' ? { N: 4, prio: 'atr', tstop: 250, tgain: 0, stop: 0.4, half: 0.25, tp: 0.35 } : { N: 4, prio: 'tag', tstop: 0, tgain: 0, stop: 0.3, half: 0.35, tp: 0.5 };
const COST = 0.006, LIQ = 1e8, MIN_DATE = '2016-01-01', NL = String.fromCharCode(10);
const TAG_RANK = new Proxy({ T1: 0, T6: 1, T3b: 2, T3: 3, 돌파: 4 }, { get: (t, k) => (k in t ? t[k] : 9), has: () => true });
const BASE_TAGS = ['T1', 'T6', 'T3b', 'T3', '돌파'];
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
    // 모든 매수 종류를 후보로 둔다(필터는 시험에서)
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
  let eb = e.sellBar == null ? n - 1 : e.sellBar, closed = e.sellBar != null, peak = 0, jh = -1;
  for (let j = k; j <= eb; j++) {
    const g = c[j].close / ent - 1, held = j - k; peak = Math.max(peak, g);
    if (v.half && jh < 0 && g >= v.half) jh = j;                       // 절반은 목표 수익(half)에서 정리
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
  if (jh >= 0) {                                                       // 분할: jh 다음 날 시가에 절반 정리(비용 반영), 이후 일별 수익은 절반 비중
    const hi = Math.min(jh + 1 - k, rr.length - 1);                      // rr 인덱스 hi = 절반을 판 날
    const out = [];
    for (let q = 0; q < rr.length; q++) {
      if (q < hi) out.push(rr[q]);
      else if (q === hi) { const sold = jh + 1 < n ? c[jh + 1].open / c[jh].close - 1 : 0; out.push(0.5 * sold + 0.5 * (rr[q] + 0) - 0.5 * COST * (eb > jh ? 1 : 0)); }
      else out.push(0.5 * rr[q]);
    }
    rr.length = 0; rr.push(...out);
  }
  const last = sell ? eb + 1 : eb;
  return { ev: e, rr, s: cid.get(dates[k]), end: cid.get(dates[last]), closed };
}
const KEY = { tag: (o) => [TAG_RANK[o.ev.tag], o.ev.dd], atr: (o) => [0, -o.ev.atr], dd: (o) => [0, o.ev.dd] };
const cache = new Map();
function simulate(v) {
  const ck = JSON.stringify([v.stop, v.tstop, v.tgain, v.tp || 0, v.lockG || 0, v.lockF || 0, v.half || 0]);
  if (!cache.has(ck)) cache.set(ck, ev.map((e) => tradePath(e, v)).filter(Boolean));
  const byStart = new Map();
  for (const o of cache.get(ck)) {
    if (v.filter && !v.filter(o.ev)) continue;
    if (v.tags && !v.tags.includes(o.ev.tag)) continue;
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


// 태그별 거래 통계(기본 출구 규칙 기준, 자리 제한 없이)
const byTag = {};
for (const e of ev) { const p = tradePath(e, { ...BASE }); if (!p) continue; const r = p.rr.reduce((a, x) => a * (1 + x), 1) - 1; (byTag[e.tag] ||= []).push({ r, y: S[e.code].dates[e.i].slice(0, 4), closed: p.closed }); }
console.log(NL + '매수 종류별 거래(자리 제한 없이): 건수 / 승률 / 평균 / 중앙 (청산분)');
for (const [t, a] of Object.entries(byTag).sort((x, y) => y[1].length - x[1].length)) { const c = a.filter((x) => x.closed).map((x) => x.r).sort((p, q) => p - q); if (c.length < 5) continue; console.log('  ' + t.padEnd(5) + ' ' + String(c.length).padStart(4) + '건 | 승률 ' + (c.filter((x) => x > 0).length / c.length * 100).toFixed(0) + '% | 평균 ' + (c.reduce((p, q) => p + q, 0) / c.length * 100).toFixed(1) + '% | 중앙 ' + (c[Math.floor(c.length / 2)] * 100).toFixed(1) + '%'); }
const tags = Object.keys(byTag).filter((t) => !BASE_TAGS.includes(t) && byTag[t].filter((x) => x.closed).length >= 5);
const base = simulate({ ...BASE, tags: BASE_TAGS, filter: TIER === 'mid' ? (e) => e.volx <= 3 : null });
console.log(NL + fmt('현재 태그(T1·T6·T3b·T3·돌파)', base));
for (const t of tags) console.log(fmt('+ ' + t, simulate({ ...BASE, tags: [...BASE_TAGS, t], filter: TIER === 'mid' ? (e) => e.volx <= 3 : null })));
console.log(fmt('+ 모든 새 태그', simulate({ ...BASE, tags: [...BASE_TAGS, ...tags], filter: TIER === 'mid' ? (e) => e.volx <= 3 : null })));
console.log(fmt('전체 태그(T4 포함)', simulate({ ...BASE, tags: null, filter: TIER === 'mid' ? (e) => e.volx <= 3 : null })));
