import test from 'node:test';
import assert from 'node:assert/strict';
import { startOfLastDays, periodStart, idxAtOrAfter, needsMore, mergeRows, findPeriod, MAX_RAW, KST } from '../public/js/periods.js';

const DAY = 86400;
const D0 = 1_700_000_000 - ((1_700_000_000 + KST) % DAY); // 어떤 날의 KST 0시(UTC 초)
// d 일째 분봉 m 개 (09:00 부터 1분 간격)
const mk = (d, m) => Array.from({ length: m }, (_, i) => {
  const time = D0 + d * DAY + 9 * 3600 + i * 60;
  return { time, open: 1, high: 1, low: 1, close: 1, volume: 1 };
});
const days = (list) => list.flatMap(([d, m]) => mk(d, m));

test('startOfLastDays: 최근 n 개 거래일의 첫 봉', () => {
  const bars = days([[0, 3], [1, 3], [2, 3], [4, 3]]); // 3일째는 휴장
  assert.equal(startOfLastDays(bars, 1).start, D0 + 4 * DAY + 9 * 3600);
  assert.equal(startOfLastDays(bars, 2).start, D0 + 2 * DAY + 9 * 3600);
  assert.equal(startOfLastDays(bars, 4).start, bars[0].time);
  assert.deepEqual(startOfLastDays(bars, 10), { start: bars[0].time, days: 4 });
  assert.deepEqual(startOfLastDays([], 3), { start: -Infinity, days: 0 });
});

test('startOfLastDays: 자정을 넘는 해외 장도 KST 날짜로 센다', () => {
  // KST 22:30 ~ 다음 날 05:00 은 서로 다른 KST 날짜
  const a = { time: D0 + 22.5 * 3600, open: 1, high: 1, low: 1, close: 1, volume: 1 };
  const b = { time: D0 + DAY + 1 * 3600, open: 1, high: 1, low: 1, close: 1, volume: 1 };
  assert.equal(startOfLastDays([a, b], 1).start, b.time);
  assert.equal(startOfLastDays([a, b], 2).start, a.time);
});

test('periodStart: 분봉은 거래일, 일봉은 달력 일수, 전체는 -Infinity', () => {
  const m = days([[0, 2], [1, 2], [2, 2]]);
  assert.equal(periodStart(m, '5', ['3일', 3]), m[0].time);
  assert.equal(periodStart(m, '5', ['1일', 1]), m[4].time);
  assert.equal(periodStart(m, '5', ['전체', 0]), -Infinity);
  const d = [0, 100, 200, 300].map((x) => ({ time: D0 + x * DAY, open: 1, high: 1, low: 1, close: 1, volume: 1 }));
  assert.equal(periodStart(d, '1d', ['3개월', 92]), D0 + (300 - 92) * DAY);
});

test('idxAtOrAfter: 이분 탐색', () => {
  const bars = [10, 20, 30].map((time) => ({ time }));
  assert.equal(idxAtOrAfter(bars, 5), 0);
  assert.equal(idxAtOrAfter(bars, 20), 1);
  assert.equal(idxAtOrAfter(bars, 21), 2);
  assert.equal(idxAtOrAfter(bars, 99), 3);
  assert.equal(idxAtOrAfter([], 1), 0);
});

test('needsMore: 기간+2 거래일이 모이면 멈춤, 일봉·전체는 끝까지, done·상한이면 멈춤', () => {
  const three = days([[0, 2], [1, 2], [2, 2]]);
  assert.equal(needsMore(three, '5', ['1일', 1], false), false); // 1+2 = 3일 있음
  assert.equal(needsMore(three, '5', ['3일', 3], false), true); // 5일 필요
  assert.equal(needsMore(three, '5', ['전체', 0], false), true);
  assert.equal(needsMore(three, '1d', ['1년', 365], false), true);
  assert.equal(needsMore(three, '5', ['전체', 0], true), false);
  assert.equal(needsMore({ length: MAX_RAW, [MAX_RAW - 1]: { time: 1 } }, '5', ['전체', 0], false), false);
});

test('mergeRows: 겹치는 시각은 뒤 배열 우선, 오름차순 유지', () => {
  const a = [{ time: 1, v: 'a1' }, { time: 2, v: 'a2' }];
  const b = [{ time: 0, v: 'b0' }, { time: 1, v: 'b1' }];
  const m = mergeRows(a, b);
  assert.deepEqual(m.map((x) => [x.time, x.v]), [[0, 'b0'], [1, 'b1'], [2, 'a2']]);
});

test('findPeriod: 모르는 이름은 기본 기간', () => {
  assert.equal(findPeriod('5', '3일')[1], 3);
  assert.equal(findPeriod('5', '없음')[0], '3일');
  assert.equal(findPeriod('1d', '없음')[0], '1년');
});

test('tradeBelongs: 봉과 화면 종목과 체결 종목이 모두 같을 때만 반영', async () => {
  const { tradeBelongs } = await import('../public/js/periods.js');
  assert.equal(tradeBelongs('005930|1d', '005930', '005930'), true);
  assert.equal(tradeBelongs('AAPL|1m', 'AAPL', 'AAPL'), true);
  assert.equal(tradeBelongs('005930|1d', 'AAPL', 'AAPL'), false); // 종목 전환 중: 봉은 아직 삼성전자
  assert.equal(tradeBelongs('AAPL|1m', 'AAPL', '005930'), false); // 다른 종목의 체결
  assert.equal(tradeBelongs('', '005930', '005930'), false); // 아직 봉이 없음
  assert.equal(tradeBelongs(undefined, 'A', 'A'), false);
});
