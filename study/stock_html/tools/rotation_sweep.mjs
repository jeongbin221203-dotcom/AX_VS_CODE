// 순환 매매 설정 탐색(2026-10-18): 후보 거래를 한 번 만든 뒤 자리 수·시간 손절(일수·수익 기준)·가격 손절·후보 우선순위·매수 종류를 바꿔 가며
// 연수익·낙폭·구간별 성과를 비교한다. 선택 구간 2017~2021, 확인 구간 2022~2026.
// 사용: node tools/rotation_sweep.mjs [캔들 json] [large|mid]
import fs from 'node:fs';
import nodePath from 'node:path';
import { computeIndicators } from '../public/js/signals.js';
import { computeStorySignals, storyBuyAt, STORY } from '../public/js/storyrules.js';

const TIER = process.argv[3] === 'large' ? 'large' : 'mid';
const IN = process.argv[2] || nodePath.join(process.env.TEMP || '.', TIER === 'mid' ? 'rotation_candles_mid.json' : 'rotation_candles.json');
const COST = 0.006, LIQ = 1e8, MIN_DATE = '2016-01-01', NL = String.fromCharCode(10);
const TAG_RANK = { T1: 0, T6: 1, T3b: 2, T3: 3, 돌파: 4, T4: 5 };
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
    ev.push({ code, i, k: i + 1, tag, dd: c[i].close / mx - 1, atr: atr / 14, rsi: l === 0 ? 100 : 100 - 100 / (1 + g / l), rank: s.rank, sellBar: sl ? sl.i : null });
  }
}
console.log('종목 ' + Object.keys(S).length + ' 후보 ' + ev.length);

function tradePath(e, v) {
  const { c, dates } = S[e.code], n = c.length, k = e.k, ent = c[k].open;
  if (!(ent > 0)) return null;
  let eb = e.sellBar == null ? n - 1 : e.sellBar, closed = e.sellBar != null;
  for (let j = k; j <= eb; j++) {
    const g = c[j].close / ent - 1, held = j - k;
    if (v.stop && g <= -v.stop) { eb = j; closed = true; break; }
    if (v.tstop && held >= v.tstop && g < v.tgain) { eb = j; closed = true; break; }
  }
  const sell = closed && eb + 1 < n;
  const rr = [c[k].close / ent - 1];
  for (let j = k + 1; j <= eb; j++) rr.push(c[j].close / c[j - 1].close - 1);
  if (sell) rr.push(c[eb + 1].open / c[eb].close - 1);
  if (closed) rr[rr.length - 1] -= COST;
  const last = sell ? eb + 1 : eb;
  return { ev: e, rr, s: cid.get(dates[k]), end: cid.get(dates[last]), closed };
}

const KEY = { tag: (o) => [TAG_RANK[o.ev.tag], o.ev.dd], dd: (o) => [0, o.ev.dd], rsi: (o) => [0, o.ev.rsi], atr: (o) => [0, -o.ev.atr], small: (o) => [0, -o.ev.rank] };
const cache = new Map();
function simulate(v) {
  const ck = JSON.stringify([v.stop, v.tstop, v.tgain]);
  if (!cache.has(ck)) cache.set(ck, ev.map((e) => tradePath(e, v)).filter(Boolean));
  const byStart = new Map();
  for (const o of cache.get(ck)) {
    if (v.tags && !v.tags.includes(o.ev.tag)) continue;
    if (!byStart.has(o.s)) byStart.set(o.s, []);
    byStart.get(o.s).push(o);
  }
  const slots = Array(v.N).fill(null), port = new Float64Array(calendar.length); let nt = 0;
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
  return { ...v, tags: v.tags ? v.tags.join('') : 'all', cagr: all.cagr, mdd: all.mdd, a: a.cagr, b: b.cagr, worst, n: nt };
}

const rows = [];
const TAGSETS = [null, ['T1', 'T6', 'T3b', 'T3', '돌파']];
for (const N of [3, 4, 5, 6, 8]) for (const prio of ['tag', 'dd', 'rsi', 'atr', 'small']) for (const tags of TAGSETS)
  for (const [tstop, tgain] of [[0, 0], [120, 0], [250, 0], [250, 0.1], [400, 0], [250, 0.3]]) for (const stop of [0, 0.4])
    rows.push(simulate({ N, prio, tags, tstop, tgain, stop }));
const fmt = (r) => 'N' + r.N + ' ' + r.prio.padEnd(5) + ' ' + String(r.tags).padEnd(8) + ' 시간' + String(r.tstop).padStart(3) + '/' + r.tgain + ' 손절' + r.stop + ' | 연 ' + r.cagr.toFixed(1) + '% 낙폭 ' + r.mdd.toFixed(0) + '% | 17~21 ' + r.a.toFixed(1) + ' 22~26 ' + r.b.toFixed(1) + ' | 해별 최저 ' + r.worst.toFixed(0) + ' | ' + r.n + '건';
const ok = rows.filter((r) => r.a > 5 && r.b > 5);
console.log(NL + '전체 연수익 상위 12 (두 구간 모두 +5% 초과):');
for (const r of ok.sort((x, y) => y.cagr - x.cagr).slice(0, 12)) console.log('  ' + fmt(r));
const cur = rows.find((r) => r.N === 5 && r.prio === 'tag' && r.tags === 'T1T6T3bT3돌파' && r.tstop === 250 && r.tgain === 0 && r.stop === 0);
console.log(NL + '현재 설정(N5 tag 시간250/0 손절없음 T4제외):'); console.log('  ' + fmt(cur));
const avg = (key) => { const m = {}; for (const r of rows) (m[String(r[key])] ||= []).push(r); return Object.entries(m).map(([k, a]) => k + ': 연 ' + (a.reduce((s, r) => s + r.cagr, 0) / a.length).toFixed(1) + ' 낙폭 ' + (a.reduce((s, r) => s + r.mdd, 0) / a.length).toFixed(0) + ' (17~21 ' + (a.reduce((s, r) => s + r.a, 0) / a.length).toFixed(1) + ' / 22~26 ' + (a.reduce((s, r) => s + r.b, 0) / a.length).toFixed(1) + ')').join(' | '); };
for (const k of ['N', 'prio', 'tags', 'tstop', 'tgain', 'stop']) console.log('평균[' + k + '] ' + avg(k));
fs.writeFileSync(nodePath.join(process.env.TEMP || '.', 'rotation_sweep_' + TIER + '.json'), JSON.stringify(rows));
