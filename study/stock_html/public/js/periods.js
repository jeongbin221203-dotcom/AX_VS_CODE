// 보기 기간·과거 데이터 이어받기 계산 — 순수 함수 (브라우저·Node 공용)
// bars: 오래된 순 [{time(초), open, high, low, close, volume}]

export const KST = 9 * 3600;

/** [이름, 값]. 분봉은 값=거래일 수(0=전체), 일봉은 값=달력 일수(0=전체) */
export const PERIODS = {
  intraday: [['1일', 1], ['3일', 3], ['1주', 5], ['2주', 10], ['1개월', 22], ['3개월', 66], ['전체', 0]],
  daily: [['3개월', 92], ['6개월', 183], ['1년', 365], ['3년', 1095], ['5년', 1826], ['10년', 3652], ['전체', 0]],
};
export const DEFAULT_PERIOD = { 1: '1일', 5: '3일', 15: '1주', '1d': '1년' };
export const MAX_RAW = 200_000; // 메모리·그리기 속도를 위한 상한(봉)

export const periodsFor = (iv) => (iv === '1d' ? PERIODS.daily : PERIODS.intraday);
export function findPeriod(iv, name) {
  const list = periodsFor(iv);
  return list.find((p) => p[0] === name) || list.find((p) => p[0] === DEFAULT_PERIOD[iv]) || list[0];
}

export const kstDay = (t) => Math.floor((t + KST) / 86400);

/** 가장 최근 n 개 거래일(날짜)의 첫 봉 시각. days = 실제로 찾은 날짜 수(≤ n) */
export function startOfLastDays(bars, n) {
  if (!bars.length) return { start: -Infinity, days: 0 };
  let days = 0;
  let prev = null;
  let start = bars[0].time;
  for (let i = bars.length - 1; i >= 0; i--) {
    const d = kstDay(bars[i].time);
    if (d !== prev) {
      days++;
      if (days > n) break;
      prev = d;
    }
    start = bars[i].time;
  }
  return { start, days: Math.min(days, n) };
}

/** 선택한 기간이 시작되는 시각 (전체면 -Infinity) */
export function periodStart(bars, iv, period) {
  const v = period[1];
  if (!bars.length) return -Infinity;
  if (!v) return -Infinity;
  if (iv === '1d') return bars[bars.length - 1].time - v * 86400;
  return startOfLastDays(bars, v).start;
}

/** time 이상인 첫 봉의 인덱스 (없으면 bars.length) */
export function idxAtOrAfter(bars, time) {
  let lo = 0;
  let hi = bars.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (bars[mid].time < time) lo = mid + 1; else hi = mid;
  }
  return lo;
}

/**
 * 과거 데이터를 더 받아야 하는가?
 * - 일봉: 전부(수천 봉이라 가벼움)
 * - 분봉: 전체면 상한까지, 아니면 (기간 거래일 + 2일)치가 모일 때까지 (지표 계산용 여유 포함)
 */
export function needsMore(bars, iv, period, done) {
  if (done || bars.length >= MAX_RAW) return false;
  if (iv === '1d' || !period[1]) return true;
  const want = period[1] + 2;
  return startOfLastDays(bars, want).days < want;
}

/** 두 오름차순 배열을 시각 기준으로 합침(같은 시각은 b 가 우선) */
export function mergeRows(a, b) {
  const map = new Map();
  for (const x of a) map.set(x.time, x);
  for (const x of b) map.set(x.time, x);
  return [...map.values()].sort((p, q) => p.time - q.time);
}

/**
 * 체결·현재가를 지금 받아 둔 봉(barsKey = '종목|1m' 또는 '종목|1d')에 합쳐도 되는가?
 * 종목을 바꾸는 중간에는 화면의 봉이 아직 이전 종목의 것이라, 새 종목의 가격이 섞이는 것을 막는다.
 */
export function tradeBelongs(barsKey, symbol, tradeSymbol) {
  return !!barsKey && barsKey.split('|')[0] === symbol && tradeSymbol === symbol;
}
