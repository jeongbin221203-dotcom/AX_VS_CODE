import test from 'node:test';
import assert from 'node:assert/strict';
import { sma, ema, rsi, macd, bollinger, bollingerExtras, percentRank, atr } from '../public/js/indicators.js';
import { runUser, computeUserSignals, userBuyChecks, userSellChecks, describeUserState, USER_RULES, computeBollingerSignals, bollingerBuyChecks, bollingerSellChecks, describeBollingerState, BB_RULES, computeScoreSignals, scoreState, describeScoreState, SCORE, computeIndicators, computeSignals, buyChecks, sellChecks, describeState, describeContext, aggregate, backtest, RULES } from '../public/js/signals.js';

const near = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);
const bar = (i, c, spread = 0.5, volume = 100) => ({ time: 1_700_000_000 + i * 60, open: c, high: c + spread, low: c - spread, close: c, volume });

// 상승(0~139) → 얕은 조정(140~149) → 재상승(150~209) → 급락(210~269). 잡음을 넣어 RSI 가 100 에 붙지 않게 함
function scenario() {
  const p = [];
  for (let i = 0; i < 140; i++) p.push(100 + i * 0.5);
  for (let i = 0; i < 10; i++) p.push(169.5 - i * 0.8);
  const base = p.at(-1);
  for (let i = 0; i < 60; i++) p.push(base + i * 0.9);
  const top = p.at(-1);
  for (let i = 0; i < 60; i++) p.push(top - i * 1.3);
  return p.map((x, i) => bar(i, x + 0.9 * Math.sin(i * 1.3), 0.6));
}

test('sma', () => {
  assert.deepEqual(sma([1, 2, 3, 4, 5], 3), [null, null, 2, 3, 4]);
});

test('ema: 처음 n개 평균에서 시작', () => {
  const r = ema([1, 2, 3, 4, 5], 3);
  assert.deepEqual(r.slice(0, 2), [null, null]);
  near(r[2], 2); near(r[3], 3); near(r[4], 4);
});

test('rsi: 계속 오르면 100, 계속 내리면 0, 길이 부족은 null', () => {
  const up = Array.from({ length: 30 }, (_, i) => i);
  assert.equal(rsi(up)[29], 100);
  assert.equal(rsi(up.map((v) => 100 - v))[29], 0);
  assert.equal(rsi([1, 2, 3])[2], null);
});

test('macd: 길이와 hist = line - signal', () => {
  const c = scenario().map((x) => x.close);
  const m = macd(c);
  assert.equal(m.line.length, c.length);
  assert.equal(m.line[24], null);
  assert.notEqual(m.line[25], null);
  near(m.hist[60], m.line[60] - m.signal[60]);
});

test('bollinger: 값이 같으면 밴드 폭 0, 상단 ≥ 중단 ≥ 하단', () => {
  const flat = bollinger(new Array(25).fill(10));
  near(flat.upper[24], 10); near(flat.lower[24], 10);
  const b = bollinger(scenario().map((x) => x.close));
  assert.ok(b.upper[50] >= b.mid[50] && b.mid[50] >= b.lower[50]);
});

test('bollinger: 모표준편차(÷n) 사용 — [1..20] 의 σ = √33.25', () => {
  const closes = Array.from({ length: 20 }, (_, i) => i + 1);
  const b = bollinger(closes, 20, 2);
  near(b.mid[19], 10.5);
  near(b.upper[19], 10.5 + 2 * Math.sqrt(33.25), 1e-9);
});

test('bollingerExtras: %B 는 하단 0 · 중단 0.5 · 상단 1, 폭이 0이면 null', () => {
  const closes = Array.from({ length: 20 }, (_, i) => i + 1);
  const bb = bollinger(closes, 20, 2);
  const x = bollingerExtras(closes, bb);
  near(x.pctB[19], (20 - bb.lower[19]) / (bb.upper[19] - bb.lower[19]));
  near(x.bandwidth[19], (bb.upper[19] - bb.lower[19]) / bb.mid[19]);
  const flat = new Array(25).fill(10);
  assert.equal(bollingerExtras(flat, bollinger(flat)).pctB[24], null);
});

test('percentRank: 직전 값들 중 작거나 같은 비율', () => {
  const a = Array.from({ length: 40 }, (_, i) => i);
  near(percentRank(a, 39, 126), 1);
  near(percentRank([...a.slice(0, 39).map((v) => v + 10), 0], 39, 126), 0);
  assert.equal(percentRank([1, 2, 3], 2, 126), null); // 표본 부족
});

test('atr: 매 봉 진폭이 같으면 그 값', () => {
  const c = Array.from({ length: 30 }, (_, i) => bar(i, 100, 1)); // high-low = 2
  near(atr(c, 14)[29], 2);
  assert.equal(atr(c, 14)[13], null);
});

test('신호: 매수와 매도가 번갈아 나오고, 매수 뒤에만 매도', () => {
  const c = scenario();
  const sig = computeSignals(c);
  assert.ok(sig.length >= 2, `신호 ${sig.length}개`);
  assert.equal(sig[0].side, 'buy');
  for (let k = 1; k < sig.length; k++) assert.notEqual(sig[k].side, sig[k - 1].side, `연속 ${sig[k].side}`);
  for (const s of sig) {
    assert.ok(s.reasons.length > 0);
    if (s.side === 'buy') assert.ok(s.stop < s.price);
    else assert.equal(typeof s.ret, 'number');
  }
});

test('신호: 재상승 구간에서 매수, 급락 구간에서 매도', () => {
  const sig = computeSignals(scenario());
  assert.ok(sig.some((s) => s.side === 'buy' && s.i >= 140 && s.i < 210), JSON.stringify(sig.map((s) => [s.i, s.side])));
  assert.ok(sig.some((s) => s.side === 'sell' && s.i >= 205), JSON.stringify(sig.map((s) => [s.i, s.side])));
});

test('신호: 봉이 MA60 계산에 모자라면 신호 없음', () => {
  assert.deepEqual(computeSignals(scenario().slice(0, 50)), []);
});

test('buyChecks: 세 조건이 각각 규칙대로 평가된다', () => {
  const c = scenario();
  const ind = computeIndicators(c);
  // 급락이 끝난 구간 끝: 추세 상승 아님
  const down = buyChecks(c, ind, c.length - 1);
  assert.deepEqual(down.map((x) => x.key), ['trend', 'momentum', 'heat']);
  assert.equal(down[0].ok, false);
  // 신호가 난 봉에서는 3개 모두 충족
  const s = computeSignals(c, ind).find((x) => x.side === 'buy');
  assert.ok(buyChecks(c, ind, s.i).every((x) => x.ok));
  // RSI 기준 경계: rsiMin 을 100 으로 올리면 heat 는 항상 실패
  assert.equal(buyChecks(c, ind, s.i, { ...RULES, rsiMin: 100 })[2].ok, false);
});

test('sellChecks: 손절선 아래면 stop 이 충족', () => {
  const c = scenario();
  const ind = computeIndicators(c);
  const i = c.length - 1;
  const r = sellChecks(c, ind, i, { stop: c[i].close + 1 });
  assert.equal(r[0].key, 'stop');
  assert.equal(r[0].ok, true);
  assert.equal(sellChecks(c, ind, i, { stop: c[i].close - 100 })[0].ok, false);
});

test('describeState: 매수 신호 뒤에는 보유(매도 조건), 매도 뒤에는 대기(매수 조건)', () => {
  const c = scenario();
  const ind = computeIndicators(c);
  const sig = computeSignals(c, ind);
  const buy = sig.find((s) => s.side === 'buy');
  const holding = describeState(c.slice(0, buy.i + 5), computeIndicators(c.slice(0, buy.i + 5)), computeSignals(c.slice(0, buy.i + 5)));
  assert.equal(holding.holding, true);
  assert.deepEqual(holding.checks.map((x) => x.key), ['stop', 'trendBreak', 'heatExit']);
  const waiting = describeState(c, ind, sig);
  assert.equal(waiting.holding, sig.at(-1).side === 'buy');
  assert.equal(describeState([bar(0, 1)], computeIndicators([bar(0, 1)]), []), null);
});

test('describeContext: 상승 추세는 RSI 40~80, 하락 추세는 20~60, MACD 0선·시그널 위치', () => {
  const c = scenario();
  const up = c.slice(0, 140);
  const ctxUp = describeContext(up, computeIndicators(up));
  assert.equal(ctxUp.rsi.regime, 'bull');
  assert.deepEqual(ctxUp.rsi.range, [40, 80]);
  assert.equal(ctxUp.macd.aboveZero, true);
  const ctxDown = describeContext(c, computeIndicators(c));
  assert.equal(ctxDown.rsi.regime, 'bear');
  assert.deepEqual(ctxDown.rsi.range, [20, 60]);
  assert.equal(ctxDown.macd.aboveZero, false);
  assert.ok(ctxDown.bb.pctB != null && ctxDown.bb.bandwidth > 0);
});

test('aggregate: 5분봉 묶기', () => {
  const base = 1_700_000_100; // 300 의 배수
  const c = Array.from({ length: 7 }, (_, i) => ({ time: base + i * 60, open: i + 1, high: i + 2, low: i, close: i + 1.5, volume: 10 }));
  const a = aggregate(c, 5);
  assert.equal(a.length, 2);
  assert.deepEqual(a[0], { time: base, open: 1, high: 6, low: 0, close: 5.5, volume: 50 });
  assert.equal(a[1].volume, 20);
  assert.equal(aggregate(c, 1).length, 7);
});

test('backtest: 다음 봉 시가 체결, 수수료 반영, 마지막 봉 신호 무시', () => {
  const c = [100, 110, 120, 130, 140].map((p, i) => ({ time: i, open: p, high: p, low: p, close: p, volume: 1 }));
  const sig = [{ i: 0, side: 'buy' }, { i: 2, side: 'sell' }, { i: 4, side: 'buy' }];
  const r = backtest(c, sig, 0);
  assert.equal(r.closedCount, 1);
  near(r.trades[0].ret, 130 / 110 - 1);
  assert.equal(r.winRate, 1);
  assert.ok(backtest(c, sig, 0.1).trades[0].ret < r.trades[0].ret);
});

test('backtest: 보유 중이면 마지막 종가로 평가, 신호 없으면 거래 0', () => {
  const c = [100, 105, 110].map((p, i) => ({ time: i, open: p, high: p, low: p, close: p, volume: 1 }));
  const r = backtest(c, [{ i: 0, side: 'buy' }], 0);
  assert.equal(r.closedCount, 0);
  assert.equal(r.trades[0].open, true);
  near(r.total, 110 / 105 - 1);
  assert.equal(backtest(c, [], 0).trades.length, 0);
});

test('신호의 매도 수익률(ret)은 백테스트의 체결 규칙(다음 봉 시가)과 일치', () => {
  const c = scenario();
  const sig = computeSignals(c);
  const bt = backtest(c, sig, 0);
  const closed = bt.trades.filter((t) => !t.open);
  const sells = sig.filter((s) => s.side === 'sell');
  assert.equal(closed.length, sells.length);
  closed.forEach((t, k) => near(t.ret, sells[k].ret, 1e-9));
});

test('점수형: 매수와 매도가 번갈아 나오고 점수는 문턱 이상, 매도에는 수익률', () => {
  const c = scenario();
  const sig = computeScoreSignals(c);
  assert.ok(sig.length >= 2, `신호 ${sig.length}개`);
  assert.equal(sig[0].side, 'buy');
  for (let k = 1; k < sig.length; k++) assert.notEqual(sig[k].side, sig[k - 1].side);
  for (const x of sig) {
    assert.ok(x.score >= SCORE.threshold);
    assert.ok(x.reasons.length > 0);
    if (x.side === 'sell') assert.equal(typeof x.ret, 'number');
  }
});

test('점수형: 신호의 수익률은 백테스트의 체결 규칙과 일치', () => {
  const c = scenario();
  const sig = computeScoreSignals(c);
  const closed = backtest(c, sig, 0).trades.filter((t) => !t.open);
  const sells = sig.filter((x) => x.side === 'sell');
  assert.equal(closed.length, sells.length);
  closed.forEach((t, k) => near(t.ret, sells[k].ret, 1e-9));
});

test('점수형: 점수표는 문턱·창 크기와 매수/매도 합계를 돌려준다', () => {
  const c = scenario();
  const ind = computeIndicators(c);
  const sc = scoreState(c, ind);
  assert.equal(sc.threshold, SCORE.threshold);
  assert.equal(sc.window, SCORE.window);
  assert.equal(sc.buy.score, sc.buy.items.reduce((a, x) => a + x.pts, 0));
  assert.equal(sc.sell.score, sc.sell.items.reduce((a, x) => a + x.pts, 0));
  assert.equal(scoreState([bar(0, 1)], computeIndicators([bar(0, 1)])), null);
});

test('점수형: describeScoreState — 매수 뒤에는 보유, 매도 뒤에는 대기', () => {
  const c = scenario();
  const sig = computeScoreSignals(c);
  const buy = sig.find((x) => x.side === 'buy');
  const part = c.slice(0, buy.i + 3);
  const hold = describeScoreState(part, computeIndicators(part), computeScoreSignals(part));
  assert.equal(hold.holding, true);
  assert.equal(typeof hold.pnl, 'number');
  const last = describeScoreState(c, computeIndicators(c), sig);
  assert.equal(last.holding, sig.at(-1).side === 'buy');
});

// 완만한 상승 추세(MA200 위)에서 가끔 급락하는 시리즈, 또는 같은 모양의 하락 추세
function bbScenario(drift) {
  return Array.from({ length: 360 }, (_, i) => {
    let p = 100 + i * drift + 1.2 * Math.sin(i * 0.5);
    if (i % 40 >= 30 && i % 40 < 33) p -= 8; // 3봉짜리 급락
    return { time: 1_700_000_000 + i * 86400, open: p + 0.3, high: p + 1.5, low: p - 1.5, close: p, volume: 100 };
  });
}

test('볼린저형: 하단 터치 + 종가>MA200 에서 매수, 상단 터치에서 매도, 번갈아 나옴', () => {
  const c = bbScenario(0.4);
  const ind = computeIndicators(c);
  const sig = computeBollingerSignals(c, ind);
  assert.ok(sig.length >= 4, `신호 ${sig.length}개`);
  assert.equal(sig[0].side, 'buy');
  for (let k = 1; k < sig.length; k++) assert.notEqual(sig[k].side, sig[k - 1].side);
  for (const x of sig) {
    if (x.side === 'buy') {
      assert.ok(c[x.i].low <= ind.bb.lower[x.i]);
      assert.ok(c[x.i].close > ind.ma[200][x.i]);
    } else {
      assert.ok(c[x.i].high >= ind.bb.upper[x.i]);
      assert.equal(typeof x.ret, 'number');
    }
  }
});

test('볼린저형: 하락 추세(종가<MA200)에서는 하단을 건드려도 매수하지 않음', () => {
  const c = bbScenario(-0.4);
  const ind = computeIndicators(c);
  assert.ok(c.some((x, i) => ind.bb.lower[i] != null && x.low <= ind.bb.lower[i])); // 하단 터치는 있었음
  assert.deepEqual(computeBollingerSignals(c, ind), []);
  // 장기 이동평균 조건을 끄면(0) 신호가 생긴다
  assert.ok(computeBollingerSignals(c, ind, { trendMa: 0 }).length > 0);
});

test('볼린저형: 신호의 수익률은 백테스트의 체결 규칙과 일치', () => {
  const c = bbScenario(0.4);
  const sig = computeBollingerSignals(c);
  const closed = backtest(c, sig, 0).trades.filter((t) => !t.open);
  const sells = sig.filter((x) => x.side === 'sell');
  assert.equal(closed.length, sells.length);
  closed.forEach((t, k) => near(t.ret, sells[k].ret, 1e-9));
});

test('볼린저형: 조건 평가와 현재 상태', () => {
  const c = bbScenario(0.4);
  const ind = computeIndicators(c);
  const sig = computeBollingerSignals(c, ind);
  const buy = sig.find((x) => x.side === 'buy');
  const chk = bollingerBuyChecks(c, ind, buy.i);
  assert.deepEqual(chk.map((x) => x.key), ['touch', 'trendMa']);
  assert.ok(chk.every((x) => x.ok));
  assert.equal(chk[1].rule, `종가 > MA${BB_RULES.trendMa}`);
  assert.equal(bollingerSellChecks(c, ind, buy.i)[0].key, 'upper');
  const part = c.slice(0, buy.i + 2);
  const hold = describeBollingerState(part, computeIndicators(part), computeBollingerSignals(part));
  assert.equal(hold.holding, true);
  assert.equal(typeof hold.pnl, 'number');
  assert.equal(typeof hold.upper, 'number');
  const wait = describeBollingerState(c.slice(0, buy.i), computeIndicators(c.slice(0, buy.i)), []);
  assert.equal(wait.holding, false);
  assert.equal(wait.checks.length, 2);
  assert.equal(describeBollingerState([bar(0, 1)], computeIndicators([bar(0, 1)]), []), null);
});

// 완만한 상승(정배열 유지) + 40봉마다 3봉짜리 급락(하단 터치) + 300번째 봉에 거래량 폭증
function userScenario(drift = 0.12) {
  return Array.from({ length: 520 }, (_, i) => {
    let p = 100 + i * drift + 1.0 * Math.sin(i * 0.5);
    if (i % 40 >= 30 && i % 40 < 33) p -= 7;
    return { time: 1_700_000_000 + i * 86400, open: p + 0.2, high: p + 1.2, low: p - 1.2, close: p, volume: i === 300 ? 600 : 100 };
  });
}

test('통합형: 거래량 폭증(매집) 이후에만 매수하고, 매수·매도가 번갈아 나옴', () => {
  const c = userScenario();
  const ind = computeIndicators(c);
  const { signals, events } = runUser(c, ind);
  const buys = signals.filter((x) => x.side === 'buy');
  assert.ok(buys.length >= 1, '매수 신호 없음');
  assert.ok(buys.every((x) => x.i > 300 && x.i <= 300 + USER_RULES.accumWindow), JSON.stringify(buys.map((x) => x.i)));
  assert.equal(signals[0].side, 'buy');
  for (let k = 1; k < signals.length; k++) assert.notEqual(signals[k].side, signals[k - 1].side);
  assert.ok(signals.filter((x) => x.side === 'sell').every((x) => typeof x.ret === 'number'));
  assert.ok(events.some((e) => e.kind === 'surge' && e.i === 300));
});

test('통합형: 매수 봉에서는 3가지 조건이 모두 충족, 기준을 못 맞추면 신호 없음', () => {
  const c = userScenario();
  const ind = computeIndicators(c);
  const buy = runUser(c, ind).signals.find((x) => x.side === 'buy');
  const chk = userBuyChecks(c, ind, buy.i);
  assert.deepEqual(chk.map((x) => x.key), ['touch', 'align', 'accum']);
  assert.ok(chk.every((x) => x.ok));
  assert.deepEqual(computeUserSignals(c, ind, { accumK: 1e9 }), []); // 거래량 폭증 기준을 못 넘김
  assert.deepEqual(computeUserSignals(c, ind, { accumCap: -0.99 }), []); // 상승 한도를 못 맞춤
});

test('통합형: 정배열이 아니면(하락 추세) 매수하지 않음', () => {
  const c = userScenario(-0.12);
  const ind = computeIndicators(c);
  assert.deepEqual(computeUserSignals(c, ind), []);
});

test('통합형: 거래량 동반 상단 터치는 팔지 않고 보유(hold), 아니면 즉시 매도', () => {
  const c = userScenario();
  const ind = computeIndicators(c);
  // 거래량이 계속 크면(동반 상승) 상단 터치에서 팔지 않고 데드크로스까지 보유 → 매도 사유가 데드크로스
  const heavy = c.map((x, i) => ({ ...x, volume: i > 300 ? 400 : x.volume }));
  const r1 = runUser(heavy, computeIndicators(heavy));
  assert.ok(r1.events.some((e) => e.kind === 'hold') || r1.signals.some((x) => x.side === 'sell' && /데드크로스/.test(x.reasons[0])) || r1.state.armed);
  // 거래량이 그대로면 상단 터치 즉시 매도
  const r2 = runUser(c, ind);
  assert.ok(r2.signals.some((x) => x.side === 'sell' && /상단 밴드 터치/.test(x.reasons[0])));
});

test('통합형: 현재 상태 설명 (대기: 매수 조건 3개 / 보유: 매도 조건 3개)', () => {
  const c = userScenario();
  const ind = computeIndicators(c);
  const run = runUser(c, ind);
  const buy = run.signals.find((x) => x.side === 'buy');
  const part = c.slice(0, buy.i + 2);
  const pind = computeIndicators(part);
  const hold = describeUserState(part, pind, runUser(part, pind));
  assert.equal(hold.holding, true);
  assert.deepEqual(hold.checks.map((x) => x.key), ['upper', 'volUp', 'dead']);
  assert.equal(typeof hold.pnl, 'number');
  const wait = describeUserState(c.slice(0, 250), computeIndicators(c.slice(0, 250)), runUser(c.slice(0, 250)));
  assert.equal(wait.holding, false);
  assert.equal(wait.checks.length, 3);
  assert.equal(describeUserState([bar(0, 1)], computeIndicators([bar(0, 1)]), { signals: [], state: { holding: false, armed: false } }), null);
});

test('통합형: 신호의 수익률은 백테스트의 체결 규칙과 일치', () => {
  const c = userScenario();
  const sig = computeUserSignals(c);
  const closed = backtest(c, sig, 0).trades.filter((t) => !t.open);
  const sells = sig.filter((x) => x.side === 'sell');
  assert.equal(closed.length, sells.length);
  closed.forEach((t, k) => near(t.ret, sells[k].ret, 1e-9));
});
