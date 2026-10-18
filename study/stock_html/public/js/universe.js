// 종목 목록 검색·필터 — 순수 함수 (브라우저·Node 공용)
// 항목: [symbol, name, market, securityType]

export const MARKET_LABEL = { KOSPI: '코스피', KOSDAQ: '코스닥', KR_ETC: '국내 기타', NYSE: 'NYSE', NASDAQ: '나스닥', AMEX: 'AMEX', US_ETC: '해외 기타' };
export const MARKETS = { kr: ['KOSPI', 'KOSDAQ', 'KR_ETC'], us: ['NYSE', 'NASDAQ', 'AMEX', 'US_ETC'] };

const TYPE_GROUP = {
  STOCK: 'stock', FOREIGN_STOCK: 'stock', DEPOSITARY_RECEIPT: 'stock',
  ETF: 'etf', FOREIGN_ETF: 'etf', ETN: 'etf',
};
export const GROUPS = [['all', '전체'], ['stock', '주식'], ['etf', 'ETF·ETN'], ['etc', '리츠·기타']];
export const groupOf = (type) => TYPE_GROUP[type] || 'etc';

/** 국내 6자리 코드(영문 섞인 코드 포함)면 kr, 아니면 us — 서버의 marketOf 와 같은 규칙 */
export const countryOf = (symbol) => (/^\d[0-9A-Z]{5}$/.test(symbol) ? 'kr' : 'us');

const norm = (s) => String(s).toLowerCase().replace(/\s+/g, '');

/**
 * 검색 + 필터. 순위: 코드 일치 > 코드 시작 > 이름 시작 > 포함.
 * 반환: 점수순(같으면 원래 순서) 배열
 */
export function searchUniverse(items, { query = '', market = 'all', group = 'all' } = {}) {
  const q = norm(query);
  const out = [];
  for (let i = 0; i < items.length; i++) {
    const it = items[i];
    if (market !== 'all' && it[2] !== market) continue;
    if (group !== 'all' && groupOf(it[3]) !== group) continue;
    if (!q) { out.push([0, i, it]); continue; }
    const sym = it[0].toLowerCase();
    const name = norm(it[1]);
    let score;
    if (sym === q) score = 0;
    else if (sym.startsWith(q)) score = 1;
    else if (name.startsWith(q)) score = 2;
    else if (name.includes(q) || sym.includes(q)) score = 3;
    else continue;
    out.push([score, i, it]);
  }
  if (q) out.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  return out.map((x) => x[2]);
}
