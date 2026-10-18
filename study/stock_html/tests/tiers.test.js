import test from 'node:test';
import assert from 'node:assert/strict';
import { tierOf, fmtCap, TIER } from '../public/js/tiers.js';
import { rankCaps } from '../server.mjs';

test('tierOf: 100위 이내 대형, 5천억 이상 중형, 1천억~5천억 소형, 1천억 미만 잡주, 순위표에 없으면 null', () => {
  assert.equal(tierOf([1e15, 1]), 'large');
  assert.equal(tierOf([5e12, 100]), 'large');
  assert.equal(tierOf([4.9e12, 101]), 'mid');
  assert.equal(tierOf([TIER.midCap, 400]), 'mid'); // 정확히 5천억은 중형
  assert.equal(tierOf([TIER.midCap - 1, 401]), 'small');
  assert.equal(tierOf([TIER.junkCap, 900]), 'small'); // 정확히 1천억은 소형
  assert.equal(tierOf([TIER.junkCap - 1, 1300]), 'junk');
  assert.equal(tierOf(undefined), null);
});

test('tierOf: 순위가 100위 이내면 시총이 작아도 대형 (순위 우선)', () => {
  assert.equal(tierOf([1e10, 50]), 'large');
});

test('tierOf: 규칙을 바꿔 쓸 수 있다', () => {
  const rule = { largeRank: 10, midCap: 1e12, junkCap: 5e11 };
  assert.equal(tierOf([1e12, 50], rule), 'mid');
  assert.equal(tierOf([9e11, 50], rule), 'small');
  assert.equal(tierOf([4e11, 50], rule), 'junk');
});

test('fmtCap: 조·억 단위', () => {
  assert.equal(fmtCap(1.2686e15), '1,268.6조');
  assert.equal(fmtCap(5e11), '5,000억');
  assert.equal(fmtCap(1.47e11), '1,470억');
});

test('rankCaps: 보통주·정상 거래만, 시가총액 내림차순 순위', () => {
  const mk = (symbol, shares, o = {}) => ({ symbol, sharesOutstanding: String(shares), isCommonShare: true, status: 'ACTIVE', koreanMarketDetail: { krxTradingSuspended: false }, ...o });
  const info = [
    mk('A', 100), mk('B', 10), mk('C', 1000),
    mk('P', 9999, { isCommonShare: false }), // 우선주 제외
    mk('D', 5000, { status: 'DELISTED' }), // 상장폐지 제외
    mk('S', 5000, { koreanMarketDetail: { krxTradingSuspended: true } }), // 거래정지 제외
    mk('Z', 0), // 주식 수 없음 제외
    mk('N', 10), // 현재가 없음 제외
  ];
  const prices = new Map([['A', 10], ['B', 1000], ['C', 5], ['P', 1], ['D', 1], ['S', 1], ['Z', 1]]);
  const r = rankCaps(info, prices);
  assert.deepEqual(Object.keys(r).sort(), ['A', 'B', 'C']);
  assert.deepEqual(r.B, [10000, 1]); // 10주 × 1000원
  assert.deepEqual(r.C, [5000, 2]); // 1000주 × 5원
  assert.deepEqual(r.A, [1000, 3]);
});

test('HTTP: /api/caps 는 country=kr 만 허용', async () => {
  const { server } = await import('../server.mjs');
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  try {
    const base = `http://127.0.0.1:${server.address().port}`;
    assert.equal((await fetch(`${base}/api/caps?country=us`)).status, 400);
    assert.equal((await fetch(`${base}/api/caps`)).status, 400);
  } finally {
    server.close();
  }
});

test('tierLists: 구간별로 나누고 각 목록은 시총이 큰 순', async () => {
  const { tierLists, TIER_ORDER } = await import('../public/js/tiers.js');
  const items = { A: [9e14, 1], B: [4e12, 101], C: [6e11, 300], D: [2e11, 450], E: [5e10, 900], F: [8e10, 800], G: [6e11, 250] };
  const r = tierLists(items);
  assert.deepEqual(TIER_ORDER, ['large', 'mid', 'small', 'junk']);
  assert.deepEqual(r.large, ['A']);
  assert.deepEqual(r.mid, ['B', 'G', 'C']); // 순위 101, 250, 300
  assert.deepEqual(r.small, ['D']);
  assert.deepEqual(r.junk, ['F', 'E']); // 순위 800, 900
  assert.deepEqual(tierLists({}), { large: [], mid: [], small: [], junk: [] });
  assert.deepEqual(tierLists(null), { large: [], mid: [], small: [], junk: [] });
});
