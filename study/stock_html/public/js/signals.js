// 매수·매도 신호 계산, 봉 묶기, 백테스트 — 순수 함수 (브라우저·Node 공용)
import { sma, rsi, macd, bollinger, bollingerExtras, percentRank, atr } from './indicators.js';

export const MA_LIST = [5, 10, 20, 60, 120, 200]; // 화면에 그리는 이동평균 기간

/** 신호 규칙의 숫자들. 화면의 '신호 기준' 설명도 이 값을 그대로 보여준다 */
export const RULES = {
  momWindow: 3, // 매수 ②: 최근 몇 개 봉 안에 MACD 가 양전환했는지
  rsiMin: 40, // 매수 ③: 상승 추세의 RSI 구간(Cardwell: 40~80)의 아래쪽 — 이 값 이상이어야 지지가 유지된 것
  rsiMax: 80, // 매수 ③: 같은 구간의 위쪽 — 이 값 미만이어야 과열이 아님
  rsiExit: 40, // 매도 ③: RSI 가 이 값(상승 구간의 지지선) 위에서 아래로 내려옴
  atrMult: 2, // 매도 ①: 손절선 = 매수 신호 종가 − atrMult × ATR(14)
};

/** 지표 한 번에 계산 */
export function computeIndicators(candles) {
  const closes = candles.map((c) => c.close);
  const vols = candles.map((c) => c.volume);
  const ma = {};
  for (const n of MA_LIST) ma[n] = sma(closes, n);
  const bb = bollinger(closes, 20, 2);
  return {
    ma,
    ma5: ma[5],
    ma20: ma[20],
    ma60: ma[60],
    rsi: rsi(closes, 14),
    macd: macd(closes),
    bb,
    bbx: bollingerExtras(closes, bb),
    volAvg: sma(vols, 20),
    atr: atr(candles, 14),
  };
}

const has = (...v) => v.every((x) => x != null);

/** i 번째 봉에서 MACD 히스토그램이 0 이하 → 0 초과로 바뀌었는가 */
const macdUp = (ind, i) => i > 0 && has(ind.macd.hist[i - 1], ind.macd.hist[i]) && ind.macd.hist[i - 1] <= 0 && ind.macd.hist[i] > 0;

/** 매수 조건 3가지를 i 번째 봉 기준으로 평가 */
export function buyChecks(candles, ind, i, o = RULES) {
  const c = candles[i];
  const ma20 = ind.ma20[i];
  const ma60 = ind.ma60[i];
  const trend = has(ma20, ma60) && c.close >= ma60 && ma20 >= ma60;
  let flipAgo = null; // 몇 봉 전에 양전환했는지 (없으면 null)
  for (let k = 0; k < o.momWindow && i - k > 0; k++) if (macdUp(ind, i - k)) { flipAgo = k; break; }
  const r = ind.rsi[i];
  return [
    { key: 'trend', label: '추세 상승', rule: '종가 ≥ MA60 이고 MA20 ≥ MA60', ok: trend, close: c.close, ma20, ma60 },
    { key: 'momentum', label: '모멘텀 전환', rule: `최근 ${o.momWindow}봉 안에 MACD 히스토그램이 음→양`, ok: flipAgo != null, flipAgo },
    { key: 'heat', label: 'RSI 상승 구간', rule: `${o.rsiMin} ≤ RSI(14) < ${o.rsiMax}`, ok: r != null && r >= o.rsiMin && r < o.rsiMax, rsi: r },
  ];
}

/** 매도 조건 3가지를 i 번째 봉 기준으로 평가 (pos: 보유 정보 {stop}) */
export function sellChecks(candles, ind, i, pos, o = RULES) {
  const c = candles[i];
  const ma20 = ind.ma20[i];
  const hist = ind.macd.hist[i];
  const r = ind.rsi[i];
  const rPrev = ind.rsi[i - 1];
  return [
    { key: 'stop', label: '손절', rule: `종가 < 매수가 − ${o.atrMult}×ATR(14)`, ok: c.close < pos.stop, close: c.close, stop: pos.stop },
    { key: 'trendBreak', label: '추세 이탈', rule: '종가 < MA20 이고 MACD 히스토그램 < 0', ok: has(ma20, hist) && c.close < ma20 && hist < 0, close: c.close, ma20, hist },
    { key: 'heatExit', label: 'RSI 지지 이탈', rule: `RSI(14)가 ${o.rsiExit} 위에서 아래로`, ok: has(r, rPrev) && rPrev > o.rsiExit && r <= o.rsiExit, rsi: r },
  ];
}

const SELL_REASON = {
  stop: '손절: 종가가 손절선(매수가−2×ATR) 아래',
  trendBreak: '추세 이탈: 종가<MA20 & MACD<0',
  heatExit: 'RSI 지지 이탈(40 아래로)',
};

/**
 * 규칙 기반 신호. 한 번에 한 포지션만 잡아서 매수 → 매도 → 매수 순서로만 나온다.
 *   매수(3가지 모두): 추세 상승 + 모멘텀 전환 + RSI 상승 구간(40~80)
 *   매도(하나라도, 보유 중일 때만): 손절 / 추세 이탈 / RSI 지지 이탈(40 아래로)
 * 반환: [{ i, time, side, reasons, price(신호 봉 종가), stop(매수만), ret(매도만: 실제 체결가 기준 수익률) }]
 *  - 체결가는 다음 봉 시가(없으면 종가)로 가정한다. 백테스트와 같은 규칙.
 */
export function computeSignals(candles, ind = computeIndicators(candles), opt = {}) {
  const o = { ...RULES, ...opt };
  const out = [];
  let pos = null; // { entryExec, stop }
  const exec = (i) => (candles[i + 1] ? candles[i + 1].open : candles[i].close);
  for (let i = 1; i < candles.length; i++) {
    const c = candles[i];
    if (!pos) {
      const chk = buyChecks(candles, ind, i, o);
      if (chk.every((x) => x.ok) && ind.atr[i] != null) {
        const stop = c.close - o.atrMult * ind.atr[i];
        pos = { entryExec: exec(i), stop };
        const r = ind.rsi[i];
        out.push({
          i, time: c.time, side: 'buy', price: c.close, stop,
          reasons: [`추세 상승(종가≥MA60, MA20≥MA60)`, `MACD 상향 전환`, `RSI ${r.toFixed(0)} (${o.rsiMin}~${o.rsiMax} 상승 구간)`],
        });
      }
    } else {
      const hit = sellChecks(candles, ind, i, pos, o).find((x) => x.ok);
      if (hit) {
        const ex = exec(i);
        out.push({ i, time: c.time, side: 'sell', price: c.close, ret: ex / pos.entryExec - 1, reasons: [SELL_REASON[hit.key]] });
        pos = null;
      }
    }
  }
  return out;
}

export const SQUEEZE = { lookback: 126, rank: 0.1 }; // 밴드폭이 최근 126봉 중 하위 10% 이하면 '수축'(상대 기준)

/**
 * 마지막 봉 기준 세 지표의 읽는 법 (표시용).
 *  - 볼린저(20,2): %B, 밴드폭, 수축 여부. 밴드 터치는 신호가 아니라 맥락으로만 본다
 *  - RSI(14): 추세에 따라 정상 구간이 달라진다 (상승 40~80, 하락 20~60, 그 외 30~70)
 *  - MACD(12,26,9): 0선 위/아래(추세 편향)와 시그널선 위/아래(모멘텀)
 */
export function describeContext(candles, ind) {
  const i = candles.length - 1;
  if (i < 1) return null;
  const c = candles[i];
  const ma20 = ind.ma20[i];
  const ma60 = ind.ma60[i];
  const regime = has(ma20, ma60) ? (c.close >= ma60 && ma20 >= ma60 ? 'bull' : c.close < ma60 && ma20 < ma60 ? 'bear' : 'neutral') : 'neutral';
  const range = { bull: [40, 80], bear: [20, 60], neutral: [30, 70] }[regime];
  const r = ind.rsi[i];
  const rsiZone = r == null ? null : r >= range[1] ? 'high' : r < range[0] ? 'low' : 'mid';
  const bw = ind.bbx.bandwidth[i];
  const rank = percentRank(ind.bbx.bandwidth, i, SQUEEZE.lookback);
  return {
    bb: { pctB: ind.bbx.pctB[i], bandwidth: bw, rank, squeeze: rank != null && rank <= SQUEEZE.rank },
    rsi: { value: r, regime, range, zone: rsiZone },
    macd: { line: ind.macd.line[i], signal: ind.macd.signal[i], hist: ind.macd.hist[i], aboveZero: ind.macd.line[i] == null ? null : ind.macd.line[i] > 0, aboveSignal: ind.macd.hist[i] == null ? null : ind.macd.hist[i] > 0 },
  };
}

/** 마지막 봉 기준 현재 상태: 보유 중이면 보유 정보와 매도 조건, 아니면 매수 조건 */
export function describeState(candles, ind, signals, o = RULES) {
  const n = candles.length;
  if (n < 2) return null;
  const lastSig = signals[signals.length - 1];
  const i = n - 1;
  if (lastSig && lastSig.side === 'buy') {
    const entryBar = candles[lastSig.i + 1] || candles[lastSig.i];
    const entry = candles[lastSig.i + 1] ? candles[lastSig.i + 1].open : lastSig.price;
    return {
      holding: true,
      entryTime: entryBar.time, entry, stop: lastSig.stop,
      pnl: candles[i].close / entry - 1,
      checks: sellChecks(candles, ind, i, { stop: lastSig.stop }, o),
    };
  }
  return { holding: false, checks: buyChecks(candles, ind, i, o) };
}

// ---------------------------------------------------------------------------
// 점수형 신호 (대형주용). 지표별 이벤트에 점수를 주고, 최근 window 개 봉의 점수 합이
// threshold 를 처음 넘는 순간에 신호를 낸다. 매수 → 매도 → 매수 순서로만 남긴다.
// ---------------------------------------------------------------------------
export const SCORE = {
  threshold: 3, // 이 점수 이상이면 신호
  window: 5, // 최근 몇 개 봉의 이벤트를 합산할지
  volMult: 1.5, // 평균 거래량의 몇 배면 가산할지
  rsiLow: 30,
  rsiHigh: 70,
};
/** 이벤트 목록(화면의 점수표에도 그대로 쓴다): [키, 이름, 점수] */
export const SCORE_TABLE = [
  ['cross', '골든크로스(MA5↑MA20) / 데드크로스(MA5↓MA20)', 2],
  ['rsi', 'RSI 30 위로 탈출 / 70 아래로 이탈', 2],
  ['macd', 'MACD 히스토그램 양전환 / 음전환', 2],
  ['bb', '볼린저 하단 반등 / 상단 하락(캔들 방향 확인)', 1],
  ['vol', '거래량이 20봉 평균의 1.5배 이상(위 이벤트와 같은 봉일 때만)', 1],
];

/** 봉마다 매수·매도 이벤트 [{ pts, why }] 를 찾는다 */
function scoreEvents(candles, ind, o) {
  const n = candles.length;
  const buyEv = Array.from({ length: n }, () => []);
  const sellEv = Array.from({ length: n }, () => []);
  const sum = (ev) => ev.reduce((a, e) => a + e.pts, 0);
  for (let i = 1; i < n; i++) {
    const c = candles[i];
    const { ma5, ma20, rsi: r, macd: m, bb, volAvg } = ind;
    if (has(ma5[i - 1], ma20[i - 1], ma5[i], ma20[i])) {
      if (ma5[i - 1] <= ma20[i - 1] && ma5[i] > ma20[i]) buyEv[i].push({ pts: 2, why: '골든크로스(MA5↑MA20)' });
      if (ma5[i - 1] >= ma20[i - 1] && ma5[i] < ma20[i]) sellEv[i].push({ pts: 2, why: '데드크로스(MA5↓MA20)' });
    }
    if (has(r[i - 1], r[i])) {
      if (r[i - 1] < o.rsiLow && r[i] >= o.rsiLow) buyEv[i].push({ pts: 2, why: `RSI 과매도 탈출(${r[i].toFixed(0)})` });
      if (r[i - 1] > o.rsiHigh && r[i] <= o.rsiHigh) sellEv[i].push({ pts: 2, why: `RSI 과매수 이탈(${r[i].toFixed(0)})` });
    }
    if (has(m.hist[i - 1], m.hist[i])) {
      if (m.hist[i - 1] <= 0 && m.hist[i] > 0) buyEv[i].push({ pts: 2, why: 'MACD 상향 돌파' });
      if (m.hist[i - 1] >= 0 && m.hist[i] < 0) sellEv[i].push({ pts: 2, why: 'MACD 하향 돌파' });
    }
    if (has(bb.lower[i], bb.upper[i])) {
      if (c.low <= bb.lower[i] && c.close > bb.lower[i] && c.close > c.open) buyEv[i].push({ pts: 1, why: '볼린저 하단 반등' });
      if (c.high >= bb.upper[i] && c.close < bb.upper[i] && c.close < c.open) sellEv[i].push({ pts: 1, why: '볼린저 상단 하락' });
    }
    if (has(volAvg[i]) && volAvg[i] > 0 && c.volume >= volAvg[i] * o.volMult) {
      const x = (c.volume / volAvg[i]).toFixed(1);
      if (sum(buyEv[i]) > 0) buyEv[i].push({ pts: 1, why: `거래량 ${x}배` });
      if (sum(sellEv[i]) > 0) sellEv[i].push({ pts: 1, why: `거래량 ${x}배` });
    }
  }
  return { buyEv, sellEv };
}

/** i 봉 기준 최근 window 개 봉의 이벤트 합. items: 이름별로 묶은 [{ why, pts, count }] */
function windowOf(ev, i, o) {
  let score = 0;
  const map = new Map();
  for (let j = Math.max(0, i - o.window + 1); j <= i; j++) {
    for (const e of ev[j]) {
      score += e.pts;
      const key = e.why.replace(/[\d.]+/g, '#'); // 같은 종류(숫자만 다른 것)는 묶음
      const cur = map.get(key) || { why: e.why, pts: 0, count: 0 };
      cur.pts += e.pts; cur.count += 1; cur.why = e.why;
      map.set(key, cur);
    }
  }
  return { score, items: [...map.values()] };
}

/**
 * 점수형 신호. 반환: [{ i, time, side, price, reasons, score, ret(매도만) }]
 * 이미 보유 중일 때의 매수, 보유가 없을 때의 매도는 버린다(백테스트가 무시하는 신호와 같음).
 * ret 는 다음 봉 시가 체결 기준 거래 수익률(수수료 제외).
 */
export function computeScoreSignals(candles, ind = computeIndicators(candles), opt = {}) {
  const o = { ...SCORE, ...opt };
  const { buyEv, sellEv } = scoreEvents(candles, ind, o);
  const raw = [];
  for (let i = 1; i < candles.length; i++) {
    const b = windowOf(buyEv, i, o);
    const s = windowOf(sellEv, i, o);
    const bPrev = windowOf(buyEv, i - 1, o).score;
    const sPrev = windowOf(sellEv, i - 1, o).score;
    if (b.score >= o.threshold && bPrev < o.threshold && b.score > s.score) raw.push({ i, side: 'buy', score: b.score, reasons: b.items.map((x) => x.why) });
    else if (s.score >= o.threshold && sPrev < o.threshold && s.score > b.score) raw.push({ i, side: 'sell', score: s.score, reasons: s.items.map((x) => x.why) });
  }
  const exec = (i) => (candles[i + 1] ? candles[i + 1].open : candles[i].close);
  const out = [];
  let entry = null;
  for (const r of raw) {
    const sig = { ...r, time: candles[r.i].time, price: candles[r.i].close };
    if (r.side === 'buy' && entry == null) { entry = exec(r.i); out.push(sig); } else if (r.side === 'sell' && entry != null) { sig.ret = exec(r.i) / entry - 1; entry = null; out.push(sig); }
  }
  return out;
}

/** 마지막 봉 기준 매수·매도 점수표 (화면 표시용) */
export function scoreState(candles, ind, opt = {}) {
  const o = { ...SCORE, ...opt };
  const i = candles.length - 1;
  if (i < 1) return null;
  const { buyEv, sellEv } = scoreEvents(candles, ind, o);
  const b = windowOf(buyEv, i, o);
  const s = windowOf(sellEv, i, o);
  return { threshold: o.threshold, window: o.window, buy: b, sell: s };
}

/** 점수형의 현재 상태: 마지막 신호가 매수면 보유 중 */
export function describeScoreState(candles, ind, signals, opt = {}) {
  const n = candles.length;
  if (n < 2) return null;
  const last = signals[signals.length - 1];
  const sc = scoreState(candles, ind, opt);
  if (last && last.side === 'buy') {
    const entry = candles[last.i + 1] ? candles[last.i + 1].open : last.price;
    const entryTime = candles[last.i + 1] ? candles[last.i + 1].time : last.time;
    return { holding: true, entry, entryTime, pnl: candles[n - 1].close / entry - 1, score: sc };
  }
  return { holding: false, score: sc };
}

// ---------------------------------------------------------------------------
// 볼린저형 신호 (대형주용): 저가가 하단 밴드를 터치하고 종가가 장기 이동평균 위일 때 매수,
// 고가가 상단 밴드를 터치하면 매도. 한 번에 한 포지션, 체결은 다음 봉 시가.
// ---------------------------------------------------------------------------
export const BB_RULES = { trendMa: 200 }; // 매수 ②: 종가가 이 이동평균 위여야 함 (MA_LIST 에 있는 기간, 0 이면 조건 없음)

/** i 봉의 매수 조건 2가지 */
export function bollingerBuyChecks(candles, ind, i, o = BB_RULES) {
  const c = candles[i];
  const lo = ind.bb.lower[i];
  const ma = o.trendMa && ind.ma[o.trendMa] ? ind.ma[o.trendMa][i] : null;
  return [
    { key: 'touch', label: '하단 밴드 터치', rule: '저가 ≤ 볼린저 하단 밴드(20, 2σ)', ok: lo != null && c.low <= lo, low: c.low, lower: lo },
    { key: 'trendMa', label: '장기 추세 위', rule: o.trendMa ? `종가 > MA${o.trendMa}` : '(조건 없음)', ok: !o.trendMa || (ma != null && c.close > ma), close: c.close, ma },
  ];
}

/** i 봉의 매도 조건 (보유 중일 때) */
export function bollingerSellChecks(candles, ind, i) {
  const c = candles[i];
  const up = ind.bb.upper[i];
  return [{ key: 'upper', label: '상단 밴드 터치', rule: '고가 ≥ 볼린저 상단 밴드(20, 2σ)', ok: up != null && c.high >= up, high: c.high, upper: up }];
}

/** 반환: [{ i, time, side, price, reasons, ret(매도만: 다음 봉 시가 체결 기준 수익률) }] */
export function computeBollingerSignals(candles, ind = computeIndicators(candles), opt = {}) {
  const o = { ...BB_RULES, ...opt };
  const out = [];
  const exec = (i) => (candles[i + 1] ? candles[i + 1].open : candles[i].close);
  let entry = null;
  for (let i = 1; i < candles.length; i++) {
    if (ind.bb.lower[i] == null) continue;
    if (entry == null) {
      if (bollingerBuyChecks(candles, ind, i, o).every((x) => x.ok)) {
        entry = exec(i);
        out.push({ i, time: candles[i].time, side: 'buy', price: candles[i].close, reasons: ['저가가 볼린저 하단 밴드를 터치', `종가가 MA${o.trendMa} 위(장기 상승 추세)`] });
      }
    } else if (bollingerSellChecks(candles, ind, i)[0].ok) {
      out.push({ i, time: candles[i].time, side: 'sell', price: candles[i].close, ret: exec(i) / entry - 1, reasons: ['고가가 볼린저 상단 밴드를 터치'] });
      entry = null;
    }
  }
  return out;
}

/** 마지막 봉 기준 현재 상태: 보유 중이면 매도 조건과 상단 밴드, 아니면 매수 조건 */
export function describeBollingerState(candles, ind, signals, o = BB_RULES) {
  const n = candles.length;
  if (n < 2) return null;
  const last = signals[signals.length - 1];
  const i = n - 1;
  if (last && last.side === 'buy') {
    const entry = candles[last.i + 1] ? candles[last.i + 1].open : last.price;
    const entryTime = candles[last.i + 1] ? candles[last.i + 1].time : last.time;
    return { holding: true, entry, entryTime, pnl: candles[i].close / entry - 1, upper: ind.bb.upper[i], checks: bollingerSellChecks(candles, ind, i) };
  }
  return { holding: false, checks: bollingerBuyChecks(candles, ind, i, o) };
}

// ---------------------------------------------------------------------------
// 통합형(사용자 가정) 신호 — 대형주용
//  매수: 저가가 볼린저 하단 밴드 터치 + 정배열(MA20>MA60>MA120>MA200) + 매집 징후
//        (최근 accumWindow 봉 안에 거래량이 직전 20봉 평균의 accumK 배 이상인 봉이 있었고, 그 기간 종가 상승률 ≤ accumCap)
//  매도: 고가가 상단 밴드 터치. 단 '거래량 동반 상승'(최근 volWindow 봉 종가 상승 + 평균 거래량 ≥ 20봉 평균)이면
//        팔지 않고 보유하다가 5/20 또는 5/60 데드크로스에서 매도. 매도 후에는 다음 하단 터치에서 같은 조건으로 다시 매수.
// ---------------------------------------------------------------------------
export const USER_RULES = { accumWindow: 126, accumK: 3, accumCap: 0.3, volWindow: 5 };

const crossDownAt = (a, b, i) => i > 0 && a[i - 1] != null && b[i - 1] != null && a[i] != null && b[i] != null && a[i - 1] >= b[i - 1] && a[i] < b[i];

/** 봉별 거래량 / 직전 20봉 평균 */
function surgeRatios(candles, ind) {
  return candles.map((x, i) => (i >= 20 && ind.volAvg[i - 1] > 0 ? x.volume / ind.volAvg[i - 1] : 0));
}

function volBackedAt(candles, ind, i, o) {
  const n = o.volWindow;
  if (i < n || !(ind.volAvg[i] > 0)) return false;
  let v = 0;
  for (let j = i - n + 1; j <= i; j++) v += candles[j].volume;
  return candles[i].close > candles[i - n].close && v / n >= ind.volAvg[i];
}

function accumAt(candles, ratio, i, o) {
  const W = o.accumWindow;
  if (i < W) return { ok: false, maxRatio: null, rise: null };
  let mx = 0;
  for (let j = i - W + 1; j <= i; j++) if (ratio[j] > mx) mx = ratio[j];
  const rise = candles[i].close / candles[i - W].close - 1;
  return { ok: mx >= o.accumK && rise <= o.accumCap, maxRatio: mx, rise };
}

/** i 봉의 매수 조건 3가지 (ratio 를 이미 계산해 뒀으면 넘겨서 재계산을 피한다) */
export function userBuyChecks(candles, ind, i, o = USER_RULES, ratio = surgeRatios(candles, ind)) {
  const c = candles[i];
  const lo = ind.bb.lower[i];
  const m = [ind.ma20[i], ind.ma60[i], ind.ma[120][i], ind.ma[200][i]];
  const aligned = m.every((v) => v != null) && m[0] > m[1] && m[1] > m[2] && m[2] > m[3];
  const ac = accumAt(candles, ratio, i, o);
  return [
    { key: 'touch', label: '하단 밴드 터치', rule: '저가 ≤ 볼린저 하단 밴드(20, 2σ)', ok: lo != null && c.low <= lo, low: c.low, lower: lo },
    { key: 'align', label: '이평선 정배열', rule: 'MA20 > MA60 > MA120 > MA200', ok: aligned, m },
    { key: 'accum', label: '매집 징후', rule: `최근 ${o.accumWindow}봉 안에 거래량 ${o.accumK}배↑ 봉이 있고, 그 기간 상승률 ≤ ${Math.round(o.accumCap * 100)}%`, ok: ac.ok, maxRatio: ac.maxRatio, rise: ac.rise },
  ];
}

/** 보유 중일 때 i 봉의 매도 관련 상태 3가지 (armed: 거래량 동반이라 보유 전환한 상태) */
export function userSellChecks(candles, ind, i, armed, o = USER_RULES) {
  const c = candles[i];
  const up = ind.bb.upper[i];
  const dead520 = crossDownAt(ind.ma5, ind.ma20, i);
  const dead560 = crossDownAt(ind.ma5, ind.ma60, i);
  return [
    { key: 'upper', label: '상단 밴드 터치', rule: '고가 ≥ 볼린저 상단 밴드(20, 2σ)', ok: up != null && c.high >= up, high: c.high, upper: up },
    { key: 'volUp', label: '거래량 동반 상승', rule: `최근 ${o.volWindow}봉 종가 상승 + 평균 거래량 ≥ 20봉 평균 (상단 터치 때 이면 팔지 않고 보유)`, ok: volBackedAt(candles, ind, i, o), info: true },
    { key: 'dead', label: '데드크로스(5/20 또는 5/60)', rule: '거래량 동반으로 보유 전환한 뒤 발생하면 매도', ok: !!armed && (dead520 || dead560), armed: !!armed, dead520, dead560 },
  ];
}

/**
 * 신호 계산. 반환: { signals, events, state }
 *  signals: [{ i, time, side, price, reasons, ret(매도만) }]
 *  events: 차트 확인용 표시 [{ i, time, kind: 'surge'|'hold' }]  (surge: 거래량 폭증 봉, hold: 상단 터치했지만 거래량 동반이라 보유 유지)
 *  state: { holding, armed }  — 마지막 봉 기준
 */
export function runUser(candles, ind = computeIndicators(candles), opt = {}) {
  const o = { ...USER_RULES, ...opt };
  const ratio = surgeRatios(candles, ind);
  const signals = [];
  const events = [];
  const exec = (i) => (candles[i + 1] ? candles[i + 1].open : candles[i].close);
  let entry = null;
  let armed = false;
  for (let i = 1; i < candles.length; i++) {
    if (ratio[i] >= o.accumK) events.push({ i, time: candles[i].time, kind: 'surge' });
    if (ind.bb.lower[i] == null || ind.ma[200][i] == null) continue;
    const c = candles[i];
    if (entry == null) {
      if (userBuyChecks(candles, ind, i, o, ratio).every((x) => x.ok)) {
        entry = exec(i);
        armed = false;
        signals.push({ i, time: c.time, side: 'buy', price: c.close, reasons: ['저가가 볼린저 하단 밴드를 터치', '이평선 정배열(MA20>60>120>200)', `최근 ${o.accumWindow}봉 안에 거래량 폭증(${o.accumK}배↑) + 상승 적음`] });
      }
    } else {
      const upT = c.high >= ind.bb.upper[i];
      let why = null;
      if (upT) {
        if (volBackedAt(candles, ind, i, o)) { if (!armed) events.push({ i, time: c.time, kind: 'hold' }); armed = true; } else why = '상단 밴드 터치 (거래량 동반 상승 아님)';
      } else if (armed) {
        if (crossDownAt(ind.ma5, ind.ma20, i)) why = '상단 터치 후 5/20 데드크로스';
        else if (crossDownAt(ind.ma5, ind.ma60, i)) why = '상단 터치 후 5/60 데드크로스';
      }
      if (why) {
        signals.push({ i, time: c.time, side: 'sell', price: c.close, ret: exec(i) / entry - 1, reasons: [why] });
        entry = null;
        armed = false;
      }
    }
  }
  return { signals, events, state: { holding: entry != null, armed } };
}

export const computeUserSignals = (candles, ind, opt) => runUser(candles, ind, opt).signals;

/** 마지막 봉 기준 현재 상태. run 은 runUser 의 반환값 */
export function describeUserState(candles, ind, run, o = USER_RULES) {
  const n = candles.length;
  if (n < 2) return null;
  const i = n - 1;
  const last = run.signals[run.signals.length - 1];
  if (run.state.holding && last && last.side === 'buy') {
    const entry = candles[last.i + 1] ? candles[last.i + 1].open : last.price;
    const entryTime = candles[last.i + 1] ? candles[last.i + 1].time : last.time;
    return { holding: true, armed: run.state.armed, entry, entryTime, pnl: candles[i].close / entry - 1, upper: ind.bb.upper[i], checks: userSellChecks(candles, ind, i, run.state.armed, o), title: '매도 — 상단 터치 시 거래량 동반이 아니면 매도, 동반이면 보유하다 데드크로스에서 매도' };
  }
  return { holding: false, armed: false, checks: userBuyChecks(candles, ind, i, o), title: '매수 — 3가지를 모두 충족하면 매수' };
}

/** 1분봉(오름차순) → N분봉. time 은 초 단위, bucket 은 time 기준 */
export function aggregate(candles, minutes) {
  if (minutes <= 1) return candles.slice();
  const size = minutes * 60;
  const out = [];
  for (const c of candles) {
    const t = Math.floor(c.time / size) * size;
    const last = out[out.length - 1];
    if (last && last.time === t) {
      last.high = Math.max(last.high, c.high);
      last.low = Math.min(last.low, c.low);
      last.close = c.close;
      last.volume += c.volume;
    } else {
      out.push({ time: t, open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume });
    }
  }
  return out;
}

/**
 * 롱 전용 백테스트: 매수 신호 다음 봉 시가에 사고, 매도 신호 다음 봉 시가에 판다.
 * 마지막 봉의 신호는 체결할 다음 봉이 없어 무시. 끝까지 보유 중이면 마지막 종가로 평가.
 * feePct: 한쪽(매수 또는 매도) 비용 %.
 */
export function backtest(candles, signals, feePct = 0.1) {
  const fee = feePct / 100;
  const trades = [];
  let pos = null;
  for (const s of signals) {
    const next = candles[s.i + 1];
    if (!next) continue;
    if (s.side === 'buy' && !pos) pos = { entryIdx: s.i + 1, entry: next.open, entryTime: next.time };
    else if (s.side === 'sell' && pos) {
      trades.push(close(pos, next.open, next.time, false));
      pos = null;
    }
  }
  let open = null;
  if (pos && candles.length) {
    const last = candles[candles.length - 1];
    open = close(pos, last.close, last.time, true);
  }
  function close(p, exit, time, isOpen) {
    const ret = (exit * (1 - fee)) / (p.entry * (1 + fee)) - 1;
    return { entryTime: p.entryTime, exitTime: time, entry: p.entry, exit, ret, open: isOpen };
  }
  const all = open ? [...trades, open] : trades;
  const wins = trades.filter((t) => t.ret > 0).length;
  const total = all.reduce((acc, t) => acc * (1 + t.ret), 1) - 1;
  const first = candles[0];
  const last = candles[candles.length - 1];
  const hold = first && last ? last.close / first.open - 1 : 0;
  return {
    trades: all,
    closedCount: trades.length,
    winRate: trades.length ? wins / trades.length : null,
    total,
    hold,
  };
}
