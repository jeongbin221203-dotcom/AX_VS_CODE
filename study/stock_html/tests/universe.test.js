import test from 'node:test';
import assert from 'node:assert/strict';
import { searchUniverse, countryOf, groupOf } from '../public/js/universe.js';

const items = [
  ['000020', '동화약품', 'KOSPI', 'STOCK'],
  ['005930', '삼성전자', 'KOSPI', 'STOCK'],
  ['005935', '삼성전자우', 'KOSPI', 'STOCK'],
  ['069500', 'KODEX 200', 'KOSPI', 'ETF'],
  ['0001A0', '덕양에너젠', 'KOSDAQ', 'STOCK'],
  ['AAPL', '애플', 'NASDAQ', 'STOCK'],
  ['AAAP', 'AAAP', 'NASDAQ', 'STOCK'],
  ['QQQ', '인베스코 QQQ', 'NASDAQ', 'ETF'],
];

test('빈 검색은 필터만 적용하고 순서 유지', () => {
  assert.equal(searchUniverse(items).length, 8);
  assert.deepEqual(searchUniverse(items, { market: 'KOSDAQ' }).map((x) => x[0]), ['0001A0']);
  assert.deepEqual(searchUniverse(items, { group: 'etf' }).map((x) => x[0]), ['069500', 'QQQ']);
  assert.deepEqual(searchUniverse(items, { market: 'KOSPI', group: 'etf' }).map((x) => x[0]), ['069500']);
});

test('검색 순위: 코드 일치 > 코드 시작 > 이름 시작 > 포함', () => {
  assert.deepEqual(searchUniverse(items, { query: '005930' }).map((x) => x[0]), ['005930']);
  assert.deepEqual(searchUniverse(items, { query: '삼성' }).map((x) => x[0]), ['005930', '005935']);
  assert.deepEqual(searchUniverse(items, { query: 'qqq' }).map((x) => x[0]), ['QQQ']);
  assert.deepEqual(searchUniverse(items, { query: 'aap' }).map((x) => x[0]), ['AAPL', 'AAAP']); // 시작 일치 → 포함
  assert.deepEqual(searchUniverse(items, { query: '코덱스' }), []);
});

test('검색: 대소문자·공백 무시, 이름 중간 일치', () => {
  assert.deepEqual(searchUniverse(items, { query: ' kodex200 ' }).map((x) => x[0]), ['069500']);
  assert.deepEqual(searchUniverse(items, { query: '전자우' }).map((x) => x[0]), ['005935']);
});

test('countryOf / groupOf', () => {
  assert.equal(countryOf('005930'), 'kr');
  assert.equal(countryOf('0001A0'), 'kr');
  assert.equal(countryOf('AAPL'), 'us');
  assert.equal(countryOf('BRK.B'), 'us');
  assert.equal(groupOf('FOREIGN_ETF'), 'etf');
  assert.equal(groupOf('REIT'), 'etc');
  assert.equal(groupOf('DEPOSITARY_RECEIPT'), 'stock');
});
