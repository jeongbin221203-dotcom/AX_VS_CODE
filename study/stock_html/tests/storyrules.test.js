import test from 'node:test';
import assert from 'node:assert/strict';
import { computeIndicators } from '../public/js/signals.js';
import { storyBuys, storyBuyAt, storySellAt, STORY, OPTIONS } from '../public/js/storyrules.js';

const flat = (n, price = 100) => Array.from({ length: n }, (_, i) => ({ time: 86400 * i, open: price, high: price * 1.005, low: price * 0.995, close: price, volume: 1000 }));

test('봉이 적으면 후보가 없다', () => {
  const c = flat(50);
  assert.deepEqual(storyBuys(c, computeIndicators(c)), []);
});

test('거래량 폭증 장대양봉이 모든 이평선을 넘으면 돌파 후보', () => {
  const c = flat(460);
  const last = c[c.length - 1];
  c.push({ time: last.time + 86400, open: 100, high: 112, low: 100, close: 110, volume: 8000 });
  const ind = computeIndicators(c);
  assert.ok(storyBuyAt(c, ind, c.length - 1, { ...STORY, useSurge: true }).includes('돌파'));
  assert.ok(!storyBuyAt(c, ind, c.length - 1, { ...STORY, useSurge: false }).includes('돌파')); // 끄면 나오지 않음
});

test('조용한 횡보에서는 후보가 없다', () => {
  const c = flat(300);
  assert.deepEqual(storyBuys(c, computeIndicators(c)), []);
});

import { computeStorySignals, describeStoryState } from '../public/js/storyrules.js';

test('신호는 매수 → 매도 순서로만 나오고 매도에는 손익이 붙는다', () => {
  const c = flat(460);
  const last = c[c.length - 1];
  c.push({ time: last.time + 86400, open: 100, high: 112, low: 100, close: 110, volume: 8000 });
  for (let k = 1; k <= 12; k++) { const p = c[c.length - 1]; c.push({ time: p.time + 86400, open: p.close, high: p.close * 1.01, low: p.close * 0.995, close: p.close * 1.005, volume: 1000 }); }
  const sig = computeStorySignals(c, computeIndicators(c), { ...STORY, useSurge: true });
  assert.equal(sig[0].side, 'buy');
  for (let k = 1; k < sig.length; k++) assert.notEqual(sig[k].side, sig[k - 1].side);
  for (const s of sig) if (s.side === 'sell') assert.equal(typeof s.ret, 'number');
  const st = describeStoryState(c, sig);
  assert.equal(typeof st.holding, 'boolean');
});

test('조용한 횡보에서는 신호가 없다', () => {
  const c = flat(300);
  assert.deepEqual(computeStorySignals(c, computeIndicators(c)), []);
});

test('과대 낙폭: 250봉 고점에서 크게 무너진 과매도 하단은 T6 후보', () => {
  const c = flat(460, 100);
  const push = (ret, lowF = 0.99) => { const p = c[c.length - 1]; const px = p.close * (1 + ret); c.push({ time: p.time + 86400, open: p.close, high: p.close, low: px * lowF, close: px, volume: 1000 }); };
  for (let k = 0; k < 60; k++) push(-0.01);
  for (let k = 0; k < 8; k++) push(-0.03, 0.97);
  push(-0.12, 0.95);
  const ind = computeIndicators(c);
  assert.ok(storyBuyAt(c, ind, c.length - 1).some((q) => q.startsWith('T6')));
  const quiet = flat(500);
  const qi = computeIndicators(quiet);
  for (let i = 260; i < quiet.length; i++) assert.ok(!storyBuyAt(quiet, qi, i).some((q) => q.startsWith('T6')));
});

test('제외 구간 안의 매수 신호는 나오지 않는다', () => {
  const c = flat(460);
  const last = c[c.length - 1];
  c.push({ time: last.time + 86400, open: 100, high: 112, low: 100, close: 110, volume: 8000 });
  const ind = computeIndicators(c);
  const ymd = new Date((c[c.length - 1].time + 9 * 3600) * 1000).toISOString().slice(0, 10);
  assert.ok(computeStorySignals(c, ind, { ...STORY, useSurge: true }).length > 0);
  assert.equal(computeStorySignals(c, ind, { ...STORY, useSurge: true, exclude: [[ymd, ymd]] }).length, 0);
});

test('돌파: 거래량 5~7배는 20일선이 안 오르던 바닥 횡보 뒤에만 인정, 7배↑는 항상 인정', () => {
  const mk = (vol, rising) => {
    const c = flat(460);
    if (rising) for (let k = 1; k <= 3; k++) { const p = c[c.length - 1]; c.push({ time: p.time + 86400, open: p.close, high: p.close * 1.03, low: p.close, close: p.close * 1.02, volume: 1000 }); }
    const p = c[c.length - 1];
    c.push({ time: p.time + 86400, open: p.close, high: p.close * 1.12, low: p.close, close: p.close * 1.1, volume: vol });
    return c;
  };
  const has = (c) => storyBuyAt(c, computeIndicators(c), c.length - 1, { ...STORY, useSurge: true }).includes('돌파');
  assert.ok(has(mk(8000, true))); // 7배↑
  assert.ok(!has(mk(6000, true))); // 약한 돌파 + 20일선 상승 중
  assert.ok(!has(mk(4000, false))); // 5배 미만
});

test('매도: 거래량 폭증 + 상단 대폭 이탈은 보류 근거와 관계없이 팔고, 폭증 음봉은 거래량 3배↑일 때만 판다', () => {
  const mk = (open, high, low, close, volume) => { const c = flat(460); const p = c[c.length - 1]; c.push({ time: p.time + 86400, open, high, low, close, volume }); return c; };
  const sell = (c, entryTag = 'T1') => storySellAt(c, computeIndicators(c), c.length - 1, STORY, { entryTag, barsHeld: 100, gain: 0 });
  assert.match(sell(mk(130, 150, 100, 125, 8000)) || '', /대폭 이탈/); // 상단 +20%↑ · 거래량 7배↑
  assert.match(sell(mk(100, 112, 96, 98, 4000)) || '', /음봉/); // 상단 +3%↑ · 3배↑ · 몸통 −1%↓ · 20일선 안 오름
  assert.equal(sell(mk(100, 112, 96, 98, 1500)), null); // 거래량 3배 미만은 안 판다
});

test('T7: 대폭 이탈·장악형 매도 뒤에서만 하단 근접 재매수가 나온다', () => {
  assert.equal(STORY.nearAfter, '대폭|장악형'); // 상단 대폭 이탈·장악형 음봉 매도 뒤에만
  const c = flat(460);
  const ind = computeIndicators(c);
  assert.ok(!storyBuyAt(c, ind, c.length - 1, { ...STORY, nearBuy: false }).some((q) => q.startsWith('T7')));
});

test('상단 밀림(2단계): 몸통이 작고 윗꼬리가 긴 음봉만 판다', () => {
  assert.equal(STORY.tierB, true);
  assert.ok(STORY.tierBBody > 0 && STORY.tierBBody <= 0.02); // 큰 음봉(현대차 2026-01-08 −3.1%)은 대상이 아님
});

test('T6 2단계: 더 깊은 낙폭(−50%)은 RSI 31까지 허용하고, 5/60 데드크로스는 T6 보유 120봉까지 보류한다', () => {
  assert.equal(STORY.deepRsi2, 31);
  assert.ok(STORY.deepDrop2 < STORY.deepDrop);
  assert.equal(STORY.deadSkipBars, 120);
  assert.equal(STORY.unalignedUpper, true);
});

test('RSI 과열 매도: 60일선이 가파르게 오르는 중에는 RSI 80 이상일 때만', () => {
  assert.equal(STORY.steepSlope, 0.12);
  assert.ok(STORY.steepRsi > STORY.sellRsi);
});

test('5/60 데드크로스: 60·120·200일선이 엉킨(수렴) 구간에서는 보류', () => {
  assert.equal(STORY.deadTight, 0.09);
});

test('T9 지지선 재테스트(엄격: RSI 30 이하)와 T6c 투매 바닥·상단 대폭 이탈 2단계는 기본 켜짐, 효성중공업은 T9를 더 느슨하게 쓴다', () => {
  assert.equal(STORY.supBuy, true);
  assert.equal(STORY.supRsi, 30);
  assert.equal(STORY.capBuy, true);
  assert.equal(STORY.blow2Over, 0.12);
  assert.ok(OPTIONS['298040'].supRsi > STORY.supRsi);
});

test('종목별 지정 매매(manualTrades): 지정한 날짜의 매수·매도가 신호로 나오고 매도에 손익이 붙는다', () => {
  const c = flat(500);
  const ymd = (i) => new Date((c[i].time + 9 * 3600) * 1000).toISOString().slice(0, 10);
  const ind = computeIndicators(c);
  const sig = computeStorySignals(c, ind, { ...STORY, manualTrades: { from: ymd(450), trades: [{ date: ymd(460), side: 'buy', tag: '저점' }, { date: ymd(480), side: 'sell' }] } });
  const m = sig.filter((s) => s.reasons[0] === '사용자 지정');
  assert.deepEqual(m.map((s) => [s.i, s.side]), [[460, 'buy'], [480, 'sell']]);
  assert.equal(m[0].tag, '저점');
  assert.ok(Math.abs(m[1].ret) < 1e-9); // 가격이 그대로라 0%
});

test('추세가 약한 구간의 상단 긴 윗꼬리 매도는 기본 켜짐(RSI 60 이하·60일선 기울기 0~2%·200일선 이격 5~14%·거래량 1.2배 이하)', () => {
  assert.equal(STORY.wickTop2, true);
  assert.equal(STORY.wk2RsiMax, 60);
  assert.equal(STORY.wickTop, false); // 조건 없는 긴 윗꼬리 매도는 꺼짐
});

test('시험용 규칙 옵션(RULE_OPTIONS)은 세 종목에만 있고 T10 은 기본 꺼짐', async () => {
  const m = await import('../public/js/storyrules.js');
  assert.deepEqual(Object.keys(m.RULE_OPTIONS).sort(), ['006400', '035720', '207940']);
  assert.equal(STORY.hlBuy, false);
});
