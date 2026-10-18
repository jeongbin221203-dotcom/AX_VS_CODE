// 시가총액 구간 — 순수 함수 (브라우저·Node 공용)
// 대형주: 시총 순위 100위 이내
// 중형주: 101위 이하이면서 시총 5,000억원 이상
// 소형주: 시총 1,000억원 이상 5,000억원 미만
// 잡주:   시총 1,000억원 미만 (신호 대상에서 제외)
export const TIER = { largeRank: 100, midCap: 5e11, junkCap: 1e11 };
export const TIER_LABEL = { large: '대형주', mid: '중형주', small: '소형주', junk: '잡주' };

/** entry 는 /api/caps 항목 [시가총액, 순위]. 순위표에 없으면(ETF·우선주·해외 등) null */
export function tierOf(entry, rule = TIER) {
  if (!entry) return null;
  const [cap, rank] = entry;
  if (rank <= rule.largeRank) return 'large';
  if (cap >= rule.midCap) return 'mid';
  if (cap >= rule.junkCap) return 'small';
  return 'junk';
}

/** 시가총액(원)을 읽기 쉬운 글자로 */
export function fmtCap(cap) {
  if (cap >= 1e12) return `${(cap / 1e12).toLocaleString('ko-KR', { maximumFractionDigits: 1 })}조`;
  return `${Math.round(cap / 1e8).toLocaleString('ko-KR')}억`;
}

export const TIER_ORDER = ['large', 'mid', 'small', 'junk'];

/**
 * 순위표 {종목: [시총, 순위]} 를 구간별 종목 목록으로 나눈다. 각 목록은 시가총액이 큰 순(순위 오름차순).
 */
export function tierLists(items, rule = TIER) {
  const out = { large: [], mid: [], small: [], junk: [] };
  for (const [sym, entry] of Object.entries(items || {})) out[tierOf(entry, rule)].push(sym);
  for (const t of TIER_ORDER) out[t].sort((a, b) => items[a][1] - items[b][1]);
  return out;
}
