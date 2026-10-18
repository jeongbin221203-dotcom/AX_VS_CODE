// 사용자 설명(2026-10-17~18, 현대차 차트 기준)을 규칙으로 옮긴 매수·매도 후보 계산.
// 사용자가 차트로 검증 중인 초안이다. 숫자 기준은 사용자가 말한 것이 아니라 현대차 날짜에 맞춰 정한 값이다.
export const STORY = {
  skipBreakEarly: true,              // T6·T3b 매수 후 60봉은 '횡보 중 크게 이탈' 매도도 적용하지 않음
  skip60ForDeep: true, deepSkipBars: 60, // T6 매수 후 60봉 동안은 '상단 터치 + 60일선 하락' 매도를 적용하지 않음(바닥 반등 초반엔 60일선이 아직 내려가는 중)
  sell60Wick: 0.4, sell60Vol: 2, sell60Over: 0.008, sell60Bw: 0.12, // '상단 터치 + 60일선 하락' 매도: 상단을 0.8% 이상 넘고 밴드폭이 12% 이상일 때만(밴드가 좁아진 구간의 살짝 터치로는 팔지 않음, LGES 2026-01-20)
  chopBlock: 'T3', chopEr: 0.1, chopAtr: 4, // 횡보(30봉 효율비<0.1)이면서 등락폭이 큰(ATR≥4%) 날의 매수 금지 — 'T3'=T3·T3b만, 'all'=모든 매수, null=끔
  useHot: true, hotRsi: 85,          // 이평선이 정배열이 아닐 때 상단을 터치한 날 RSI가 85 이상이면 과열로 매도
  useBigBear: true, bigBearBody: -0.04, bigBearOver: 0.03, bigBearUnaligned: true, bigBearRange: null, // 상단을 3%↑ 넘은 뒤 몸통 -4% 이하 큰 음봉이면 매도 — 정배열(20>60>120>200)이 아닐 때만(+횡보 상한)
  useStar: false, starOver: 0.03, starWick: 0.4, // 시험: 상단을 3%↑ 넘은 뒤 윗꼬리 40%↑ 음봉(유성형)이면 매도
  deepIsAlert: true,                 // 과대 낙폭 바닥(T6 조건)도 '특이 신호'로 보아, 그 뒤 첫 돌파는 팔지 않는다
  useDeep: true, deepRsi: 25, deepDrop: -0.35, deepTouch: 0.01, // T6 과대 낙폭: 하단 근처(저가 ≤ 하단×1.01) + RSI ≤ 25 + 250봉 고점 대비 -35% 이하 (LG에너지솔루션 2025-05-23)
  t3bConverge: true, t3bRatio: 0.95, // T3b: 120일선이 오르고 200일선이 내리며 서로 95% 이내로 수렴 중(정배열로 바뀌는 중)일 때만
  t3Relax: true,                     // T3b: T3 정배열 조건을 완화 — 60일선 > 200일선이고 120일선이 200일선에 수렴하며 정배열로 바뀌는 중(LGES 2025-09-29)
  stopLoss: null, stopTags: ['T6'], timeStopBars: null, // 시험: 매수 후 손실이 stopLoss(예 0.2)를 넘으면 매도(대상 매수 종류만), timeStopBars 봉이 지나도 손실이면 매도
  maAlign: 200, maBelow: 224, maTouchLine: 200, maBreak: 200, // 장기 이동평균선 길이를 용도별로 지정(200 또는 224): 정배열·간격 / 매수 금지(종가<선) / T3 터치선 / 돌파의 '모든 이평선 위'
  warmup: 400,                       // 신규상장 종목은 안정된 뒤에 진입: 200일선이 처음 생긴 날(200봉째)부터 다시 200봉이 지난 400봉째부터 신호를 낸다 (사용자 지시 2026-10-18, LG에너지솔루션)
  useT2: false,                      // T2(20일선 재매수)는 볼린저 하단 터치가 아니라 2026-10-18 사용자 지시('하단 터치가 아니어서 매수 아니야')로 끔
  useGolden: false,                  // T5: 역배열(20<60<120<200)에서 5/20 골든크로스 매수. 사용자가 '볼린저 하단 터치도 아닌데 왜 매수냐'고 해 2026-10-18 끔(의도 확인 대기)
  useSurge: true,                    // 돌파(주식의 시작): 횡보 중 이평선 위로 크게 오르는 봉. 사용자 지시(2026-10-18): 돌파가 있어야 시작 매수가 생김. 거래량은 보조 확인(5배)
  blow2Over: 0.12, blow2Vol: 5, blow2Wick: 2, blow2Body: -0.01, blowOver: 0.03, blowVol: 3, extMap: null, extBuy: null, extSell: null, wickTop2: true, wk2Wick: 0.4, wk2RsiMax: 60, wk2SlopeMin: 0, wk2SlopeMax: 0.02, wk2Ext200Min: 0.05, wk2Ext200Max: 0.14, wk2VolMax: 1.2, wickTop: false, wickMin: 0.5, wickRsi: 55, sqTop: false, sqBw: 0.09, sqNear: 0.01, sqRsi: 55, wideTop: false, wideUnaligned: true, wideBw: 0.2, wideNear: 0.005, wideBody: -0.025, wideRsi: 60, firstBreakStrict: false, ma20Exit: false, ma20ExitExt: 0.1, capBuy: true, capRsi: 30, capBelow: 0.03, capVol: 5, capDd: -0.25, supMa200Slope: 0, supBuy: true, supLook: 120, supMinAge: 20, supBelow: 0.02, supAbove: 0.02, supRsi: 30, supMa200Floor: 0.94, supMa200Cap: 1.0, reMode: null, t8Trend: true, reAfter: '', reWithin: 400, reExt200: 0, reTol: 0.01, reBelow: 0.02, reRsiMax: 60, reHigh: 120, deepRsi2: 31, deepDrop2: -0.5, deepExt2: -0.1, deadTight: 0.09, deadTightM200Above: false, deadNeedBelow60: false, deadNeedM60Down: false, deadSkipBars: 120, sellOverMin: null, steepSlope: 0.12, steepRsi: 80, tierB: true, tierBRsi: 68, tierBExt: 0.14, tierBWick: 0.35, tierBBody: 0.01, tierBVol: 1.3, sellBody: null, sellWick: null, sellVolMin: null, unalignedUpper: true, unalignedNeed3: true, unalignedStable: 20, unalignedRsi: 74, unalignedTags: ['T6'], blowBody: -0.01, earlyTop: true, blowM20Flat: true, hugeOver: 0.2, hugeVol: 7, // 시험: 상단 이탈 + 거래량 폭증 + 음봉(몸통 blowBody 이하) 매도 / 상단 대폭 이탈(과열) 매도
  nearBuy: true, nearTol: 0.01, nearBelow20: false, nearM20Up: false, nearA3: true, nearRsiMax: 45, nearWithin: 90, nearAfter: '대폭|장악형', // nearAfter: 직전 매도 이유가 이 정규식에 맞을 때만(빈 값=모든 매도) // 시험: 매도 뒤 nearWithin 봉 안에 정배열에서 저가가 하단의 +nearTol 이내로 처음 근접하면 재매수(T7)
  surgeTightRange: null, // 시험: 거래량이 surgeStrongK배 이상이면 기존 조건, 그보다 약하면(surgeK~) 직전 20봉 고저폭이 surgeTightRange 이하일 때만 돌파
  surgeBaseFlat: false, surgeNeedHigh20: false, surgeM20Above60: false, // 시험: 돌파 종가가 직전 20봉 고가 위 / 20일선 > 60일선
  surgeNeedM60Up: true, surgeMaxRange: 0.2, // 돌파 추가 조건: 60일선 상승 중 / 직전 20봉 고저폭(횡보) 상한
  surgeK: 5, surgeStrongK: 7, surgeBaseFlat: true, surgeRet: 0.05,        // 돌파: 거래량 5배↑ + 당일 +5%↑ + 종가가 20·60·120·200일선 위 (4.2배인 현대차 2025-07-23은 제외)
  slopeBars: 5,                      // 이평선 변화를 재는 기준(5봉 전 대비)
  crashRsi: null, crashGap: -6,                      // 5일-20일 간격이 5봉 사이 이만큼(%p) 이하로 벌어지면 매수 금지
  t1Rsi: 40, t1Drop: -0.15, t1GapTol: -0.1, // T1: RSI 40 이하이거나 20봉 고점 대비 -15% 이하(삼성전기 2025-11-18은 RSI 41.5·-18.2%), 간격 변화는 -0.1%p까지 허용         // T1: RSI 42 이하(삼성전기 2025-11-18 RSI 41.5), 간격 변화는 -0.1%p까지 허용(같은 날 60-120 간격 -0.00)
  t1RsiOld: 40,                         // T1: 하단 밴드 아래로 마감하면서 RSI(14)가 이 값 이하(과매도 = 크게 이탈한 하락)
  touchBelow: -0.01, touchAbove: 0.005, // T2: 저가가 20일선 대비 이 범위(-1%~+0.5%)
  upperLookback: 10,                 // T2: 최근 이 봉 수 안에 상단 터치가 있어야 함(상승 직후의 눌림 = 재매수)
  maTouch: 0.005,                    // T3: 200일선 터치 허용 오차
  resellBars: 10,                    // T2: 직전 매도(규칙 또는 지정) 후 이 봉 수 이내여야 재매수로 본다
  alertK: 3, alertBars: 40,          // T4: 최근 40봉 안에 거래량 3배↑ + 하단 터치 봉(특이신호)이 있었음
  alertGap: -2,                      // T4: 5일선이 20일선보다 이 %보다 더 아래면 '계속 하락 중'이라 제외
  breakOver: 0.05, breakVol: 3, breakBand: 0.09, // 매도: 밴드가 횡보(전일 밴드폭 ≤9%)하다 상단을 5%↑ 크게 이탈한 양봉(거래량 3배↑)이면 즉시 매도 (현대차 2024-05-22)
  alertHold: 210,                    // 단, 최근 210봉(약 10개월) 안에 특이 신호(거래량 3배↑+하단 터치)가 있었고 그 뒤 첫 상승 돌파라면 팔지 않음 (현대차 2025-10-16)
  retouch: true, retouchGap: 10, retouchRsiDrop: 20, retouchLook: 30, retouchExt60: 0.8, // 추세 보유 중 횡보(직전 상단 터치와 10봉↑ 간격) 후 상단을 다시 터치했는데 RSI가 직전 30봉 최고보다 20↑ 낮고 종가가 60일선보다 80%↑ 높으면(과열 뒤 모멘텀 약화) 매도 (삼성전기 2026-06-19에 맞춘 값)
  trendExit: 'ma20x2',               // 보류 근거가 나온 뒤엔 추세 보유 모드 — 고점 매도(RSI·장악형)는 무시하고 2일 연속 종가<20일선에서 매도 ('ma20' | 'dead520' | 'ma60' | null=그날만 보류)
  holdMode: 'obv',                   // 고점 매도 신호(RSI·장악형)를 보류할 근거: 'obv'(OBV=거래량 누적이 60봉 신고가) | 'cmf' | 'macd' (조합은 'obv+macd')
  holdCmf: 0.4, holdMacd: 0.036,
  useRebreak: false, rebreakBars: 90, rebreakRet: 0.03, // 시험: 매도 후 90봉 안에 매도한 날 고가를 종가로 뚫는 +3%↑ 양봉(정배열, 20·60일선 상승)이면 재매수
  useEngulf: true, engulfMinGain: 0.5, // 상단 터치한 날 장악형 음봉(어제 양봉을 덮음)이면 매도, 단 매수 후 수익이 50% 이상일 때만(삼성전기 2025-11-04에 맞춘 규칙)                  // 시험: 상단 터치한 날 '어제 양봉을 덮는 음봉'(장악형)이면 매도
  rsiExt20Min: 0.2,                  // RSI 매도 규칙은 20일선 이격 20% 이상일 때만(삼성전기 2025-09-05 보유, 현대차 지정 매도일은 22·43·25%)
  useTouchSell: false,               // 예전 보조 매도('상단 터치인데 상승 지속 아님'): 살짝 터치에도 팔아서 끔
  sellRsi: 73,                       // 매도: 상단 터치한 날 음봉(종가<시가)이고 RSI(14)가 이 값 이상이면 매도(계속 상승 중에는 음봉이 나올 때까지 미룸, 현대차 2024-02-13·2026-05-14에 맞춘 값)
  sellOver: 0.08,                    // (사용 안 함) 예전 '크게 이탈 + 음봉' 기준
  strongDeadGap: -6,                 // 매도: 5/60 데드크로스가 '강력'하려면 5일-20일 간격이 5봉 사이 이만큼(%p) 이하로 벌어짐
  volBars: 5,                        // 매도: 거래량 동반 상승 판단 봉 수
};

/** 사용자가 직접 지정한 매매 제외 구간(종목 코드 → [시작일, 종료일] 목록, 한국 날짜). 이 구간 안의 매수 신호는 내지 않는다.
 *  규칙으로 가를 수 없는 경우(같은 조건인데 결과가 다른 경우)에만 쓴다. */
/** 종목별로 따로 켜는 옵션(사용자 지정). 효성중공업: 이전 저점 지지선 재테스트 + 200일선 부근이면 매수(T9) — 일반 규칙으로는 현대차·삼성전기 지정 매매가 깨져 종목 한정 */
export const OPTIONS = {
  // 엉킨 이평선 데드크로스 보류(deadTight)를 끈 종목: 사용자가 비교샷으로 '변경 전'이 저점 매수·고점 매도에 맞다고 지정(2026-10-18)
  // 아래 5종목은 사용자가 비교샷을 보고 지정(2026-10-18). manualTrades: from 날짜부터 지정한 매매만, 마지막 지정 뒤에는 규칙이 이어받음
  '096770': { // SK이노베이션: 2022-06-24 매도 · 2022-09-27 T6 매수 유지, 매도는 가장 높은 고점(거래량 5.0배·상단 +15.5%·RSI 75)인 2023-07-26
    deadTight: null,
    manualTrades: { from: '2022-06-24', trades: [{ date: '2022-06-24', side: 'sell' }, { date: '2022-09-27', side: 'buy', tag: 'T6' }, { date: '2023-07-26', side: 'sell' }] },
  },
  '016360': { // 삼성증권: 2023-06 중순 고점(2023-06-13) 매도 → 35,100원대 쌍바닥 매수 → 12월 고점 매도 → 2024-01 지지선 매수 → 3월 고점 매도
    deadTight: null,
    manualTrades: { from: '2023-06-01', trades: [{ date: '2023-06-13', side: 'sell' }, { date: '2023-10-30', side: 'buy', tag: '쌍바닥' }, { date: '2023-12-20', side: 'sell' }, { date: '2024-01-19', side: 'buy', tag: '지지선' }, { date: '2024-03-22', side: 'sell' }] },
  },
  '034020': { // 두산에너빌리티: 2022-08-26 고점 매도, 2022-10-13 저점 매수
    deadTight: null,
    manualTrades: { from: '2022-08-26', trades: [{ date: '2022-08-26', side: 'sell' }, { date: '2022-10-13', side: 'buy', tag: 'T6' }] },
  },
  '329180': { // HD현대중공업: 4월 저점 매수 → 5월 말 고점(2026-05-27, 앞쪽 봉 기준) 매도 → 4월 저점 지지선(428,000) 터치 2026-07-28 매수 → 8월 중순 고점 매도
    deadTight: null,
    manualTrades: { from: '2026-03-04', trades: [{ date: '2026-04-01', side: 'buy', tag: '저점' }, { date: '2026-05-27', side: 'sell' }, { date: '2026-07-28', side: 'buy', tag: '지지선' }, { date: '2026-08-10', side: 'sell' }] },
  },
  '066570': { deadTight: null, unalignedUpper: false, breakBand: 0 }, // LG전자: 2025-10-20 상단 터치 매도·2026-01-16 밴드 이탈 매도를 끄고 더 뒤의 고점(2026-02-26~27)까지 보유
  '298040': { supBuy: true, supMa200Cap: 1.0, supRsi: 40, supBelow: 0.01, supAbove: 0.01 },
};

export const EXCLUDE = {
  '373220': [['2023-11-01', '2024-07-03']], // LG에너지솔루션: 2023-11-01 T6 매수 → 2024-07-03 매도(−5.3%) 거래 제외(사용자 지정 2026-10-18)
  '196170': [['2022-01-19', '2022-01-26'], ['2022-09-23', '2022-10-12']], // 알테오젠: 과대 낙폭(T6) 매수를 바닥(2022-01-27·2022-10-13)까지 미룸(사용자 지정 2026-10-18)
};

const kstYmd = (t) => new Date((t + 9 * 3600) * 1000).toISOString().slice(0, 10);

const pct = (a, b) => (a / b - 1) * 100;

/** 종가의 최근 20봉 고점 대비 등락률(음수=하락) */
function drop20(candles, i) {
  let hi = 0;
  for (let j = i - 19; j <= i; j++) hi = Math.max(hi, candles[j].high);
  return candles[i].close / hi - 1;
}

/** 직전 n봉 최고가 */
function prevHigh(candles, i, n) {
  let h = 0;
  for (let j = i - n; j < i; j++) h = Math.max(h, candles[j].high);
  return h;
}

/** 돌파 직전 20봉 고저폭이 상한 이내(횡보)인지. 상한이 없으면 항상 true */
function surgeRangeOk(candles, i, o) {
  if (o.surgeMaxRange == null) return true;
  let hi = 0;
  let lo = Infinity;
  for (let j = i - 20; j < i; j++) { hi = Math.max(hi, candles[j].high); lo = Math.min(lo, candles[j].low); }
  return hi / lo - 1 <= o.surgeMaxRange;
}

/** 봉 i 에서 걸리는 매수 후보 이름 목록 (없으면 빈 배열). ind 는 computeIndicators 결과 */
export function storyBuyAt(candles, ind, i, o = STORY) {
  const n = o.slopeBars;
  const need = Math.max(o.maAlign, o.maBelow, o.maTouchLine, o.maBreak);
  if (i < Math.max(need + n, o.warmup) || ind.ma[200][i - n] == null || ind.bb.lower[i] == null) return [];
  const c = candles[i];
  const LA = maLine(candles, ind, o.maAlign), LB = maLine(candles, ind, o.maBelow), LT = maLine(candles, ind, o.maTouchLine), LK = maLine(candles, ind, o.maBreak);
  const m = (k) => ({ m5: ind.ma5[k], m20: ind.ma20[k], m60: ind.ma60[k], m120: ind.ma[120][k], m200: LA[k] });
  const x = m(i);
  const y = m(i - n);
  const gap = (a, b) => pct(x[a], x[b]) - pct(y[a], y[b]); // 두 이평선 사이 간격(%)의 5봉 변화(%p)
  const a3 = x.m60 > x.m120 && x.m120 > x.m200;
  const a4 = a3 && x.m20 > x.m60;
  const ratio = ind.volAvg[i - 1] > 0 ? c.volume / ind.volAvg[i - 1] : 0;
  const ret = c.close / candles[i - 1].close - 1;
  const out = [];
  if (o.useSurge && ratio >= o.surgeK && ret >= o.surgeRet && c.close > Math.max(x.m20, x.m60, x.m120, LK[i]) && (!o.surgeNeedM60Up || x.m60 > y.m60) && (!o.surgeM20Above60 || x.m20 > x.m60) && (!o.surgeNeedHigh20 || prevHigh(candles, i, 20) < c.close) && surgeRangeOk(candles, i, o) && (o.surgeStrongK == null || ratio >= o.surgeStrongK || (o.surgeBaseFlat ? x.m20 <= y.m20 : surgeRangeOk(candles, i, { ...o, surgeMaxRange: o.surgeTightRange })))) out.push('돌파');
  // T5: 역배열에서도 5/20 골든크로스는 매수 (200일선 아래·60일선 하락 금지 조건의 예외)
  if (o.useGolden && x.m20 < x.m60 && x.m60 < x.m120 && x.m120 < x.m200 && ind.ma5[i - 1] != null && ind.ma20[i - 1] != null && ind.ma5[i - 1] <= ind.ma20[i - 1] && x.m5 > x.m20) out.push('T5 역배열 5/20 골든크로스');
  // T6: 250봉 고점 대비 크게 무너진 과매도 하단 — 역배열·200일선 아래여도 허용(급락 금지 조건의 예외)
  if (o.useDeep && ind.rsi[i] != null && (ind.rsi[i] <= o.deepRsi || (o.deepRsi2 != null && ind.rsi[i] <= o.deepRsi2)) && c.low <= ind.bb.lower[i] * (1 + o.deepTouch)) {
    let h250 = 0;
    for (let j = Math.max(0, i - 250); j <= i; j++) h250 = Math.max(h250, candles[j].high);
    const dd = c.close / h250 - 1;
    if ((ind.rsi[i] <= o.deepRsi && dd <= o.deepDrop) || (o.deepRsi2 != null && ind.rsi[i] <= o.deepRsi2 && dd <= o.deepDrop2 && (o.deepExt2 == null || c.close / x.m20 - 1 <= o.deepExt2))) out.push('T6 과대 낙폭 하단');
  }
  // T6c(시험): 투매 거래량 바닥 — 저가가 하단을 capBelow 넘게 하회 + RSI ≤ capRsi + 거래량 capVol배↑ + 250봉 고점 대비 capDd 이하 (역배열·200일선 아래 허용)
  if (o.capBuy && ind.rsi[i] != null && ind.rsi[i] <= o.capRsi && c.low <= ind.bb.lower[i] * (1 - o.capBelow) && ind.volAvg[i - 1] > 0 && c.volume / ind.volAvg[i - 1] >= o.capVol) {
    let hc = 0;
    for (let j = Math.max(0, i - 250); j <= i; j++) hc = Math.max(hc, candles[j].high);
    if (c.close / hc - 1 <= o.capDd) out.push('T6c 투매 거래량 바닥');
  }
  // T9(시험): 지지선 재테스트 — 이전 피벗 저점(좌우 5봉)을 다시 찍고(−supBelow ~ +supAbove) 지키며, 200일선이 오르는 중이고 종가가 200일선 부근(−6% 이내)일 때
  if (o.supBuy && ind.rsi[i] != null && ind.rsi[i] <= o.supRsi && (x.m200 > y.m200 || (o.supMa200Slope < 0 && ind.ma[200][i - 20] != null && x.m200 / ind.ma[200][i - 20] - 1 >= o.supMa200Slope)) && c.close >= x.m200 * o.supMa200Floor && (o.supMa200Cap == null || c.close <= x.m200 * o.supMa200Cap)) {
    for (let j = i - o.supMinAge; j >= Math.max(5, i - o.supLook); j--) {
      let piv = true;
      for (let k2 = j - 5; k2 <= j + 5; k2++) if (k2 !== j && candles[k2].low < candles[j].low) { piv = false; break; }
      if (!piv) continue;
      const S0 = candles[j].low;
      if (c.low >= S0 * (1 - o.supBelow) && c.low <= S0 * (1 + o.supAbove) && c.close >= S0 * 0.98) { out.push('T9 지지선 재테스트'); break; }
    }
  }
  // T7(시험): 정배열에서 저가가 볼린저 하단에 근접(+nearTol 이내)
  if (o.nearBuy && (a4 || (o.nearA3 && a3 && x.m120 > y.m120)) && x.m60 > y.m60 && c.close >= LB[i] && (!o.nearM20Up || x.m20 > y.m20) && c.low <= ind.bb.lower[i] * (1 + o.nearTol) && (!o.nearBelow20 || c.close < x.m20) && (o.nearRsiMax == null || (ind.rsi[i] != null && ind.rsi[i] <= o.nearRsiMax))) out.push('T7 정배열 하단 근접');
  const crash = gap('m5', 'm20') <= o.crashGap && !(o.crashRsi != null && a4 && ind.rsi[i] != null && ind.rsi[i] <= o.crashRsi); // crashRsi: 정배열에서 RSI 가 이 값 이하(투매 바닥)면 급락 금지 해제     // 5일선이 20일선에서 급격히 멀어지며 하락
  const ma60Down = x.m60 <= y.m60;                  // 60일선 하락 중이면 매수 금지
  const belowMa200 = c.close < LB[i];              // 종가가 200일선 아래로 내려가 있으면 매수 금지
  if (crash || ma60Down || belowMa200) return out;
  const lowerTouch = c.low <= ind.bb.lower[i];
  // T1: 60·120·200일선 정배열로 간격이 커지는 상승 추세에서 5일선만 하락 → 볼린저 하단을 크게 이탈한 하락(과매도)
  if (a4 && c.close < ind.bb.lower[i] && ind.rsi[i] != null && (ind.rsi[i] <= o.t1Rsi || (o.t1Drop != null && drop20(candles, i) <= o.t1Drop)) && gap('m60', 'm120') >= o.t1GapTol && gap('m120', 'm200') >= o.t1GapTol) out.push('T1 하단 이탈 + 간격 확대');
  // T4: 이전에 거래량 폭증 + 하단 터치(특이신호)가 있었고, 지금 정배열에서 다시 하단 터치
  if (a4 && lowerTouch && pct(x.m5, x.m20) >= o.alertGap) {
    for (let k = Math.max(1, i - o.alertBars); k < i; k++) {
      const rk = ind.volAvg[k - 1] > 0 ? candles[k].volume / ind.volAvg[k - 1] : 0;
      if (rk >= o.alertK && candles[k].low <= ind.bb.lower[k]) { out.push('T4 특이신호 후 정배열 하단 터치'); break; }
    }
  }
  const tl = LT[i];
  if (lowerTouch && c.low <= tl * (1 + o.maTouch) && c.high >= tl) {
    if (a3) out.push('T3 200일선 + 하단');
    else if (o.t3Relax && x.m60 > tl && (!o.t3bConverge || (x.m120 > y.m120 && tl <= LT[i - n] && x.m120 / tl >= o.t3bRatio))) out.push('T3b 200일선 + 하단(정배열로 전환 중)');
  }
  // T8(시험): 강한 상승 추세 재진입 — 정배열·20/60일선 상승 중 눌림(20일선 터치) 또는 신고가 돌파
  if (o.reMode && a4 && c.close / x.m200 - 1 >= o.reExt200 && x.m20 > y.m20 && x.m60 > y.m60 && x.m120 > y.m120) {
    if (o.reMode === 'ma20' && c.low <= x.m20 * (1 + o.reTol) && c.close >= x.m20 * (1 - o.reBelow) && ind.rsi[i] != null && ind.rsi[i] <= o.reRsiMax) out.push('T8 추세 재진입(20일선 눌림)');
    if (o.reMode === 'high') {
      let hh = 0;
      for (let j = i - o.reHigh; j < i; j++) hh = Math.max(hh, candles[j].close);
      if (c.close > hh && c.close > candles[i - 1].close) out.push('T8 추세 재진입(신고가)');
    }
  }
  // T2: 상단 터치 직후 눌림에서 5일선만 하락하고 20일선에 닿음(재매수)
  if (o.useT2 && a4 && ind.ma5[i] < ind.ma5[i - 1] && c.close >= x.m20 && c.low / x.m20 - 1 >= o.touchBelow && c.low / x.m20 - 1 <= o.touchAbove) {
    let up = false;
    for (let k = i - o.upperLookback; k < i; k++) if (k > 0 && candles[k].high >= ind.bb.upper[k]) { up = true; break; }
    if (up) out.push('T2 20일선 지지');
  }
  return chopFilter(candles, ind, i, out, o);
}

/** 횡보 중 등락폭이 큰 날의 매수를 거른다 */
function chopFilter(candles, ind, i, out, o) {
  if (!o.chopBlock || !out.length || i < 31) return out;
  const keep = out.filter((q) => !(o.chopBlock === 'all' ? !q.startsWith('돌파') || true : q.startsWith('T3')));
  if (keep.length === out.length) return out;
  let path = 0;
  for (let j = i - 29; j <= i; j++) path += Math.abs(candles[j].close - candles[j - 1].close);
  const er = path > 0 ? Math.abs(candles[i].close - candles[i - 30].close) / path : 1;
  const atr = ind.atr[i] != null ? (ind.atr[i] / candles[i].close) * 100 : 0;
  return er < o.chopEr && atr >= o.chopAtr ? keep : out;
}

/** 전체 봉에서 매수 후보를 모은다: [{ i, time, kinds }] */
export function storyBuys(candles, ind, sells = null, o = STORY) {
  const out = [];
  for (let i = 0; i < candles.length; i++) {
    let kinds = storyBuyAt(candles, ind, i, o);
    if (sells && kinds.some((q) => q.startsWith('T2'))) { // T2 는 상승 뒤 매도한 직후의 재매수만
      const last = sells.filter((e) => e.i < i).at(-1);
      if (!last || i - last.i > o.resellBars) kinds = kinds.filter((q) => !q.startsWith('T2'));
    }
    if (kinds.length) out.push({ i, time: candles[i].time, kinds });
  }
  return out;
}

const crossDown = (a, b, i) => i > 0 && a[i - 1] != null && b[i - 1] != null && a[i] != null && b[i] != null && a[i - 1] >= b[i - 1] && a[i] < b[i];

/** 큰 음봉 매도를 적용할 환경인가: 정배열이 아니고(옵션) 직전 20봉이 횡보(옵션) */
function bigBearOk(candles, ind, i, o) {
  if (o.bigBearUnaligned) {
    const aligned = ind.ma20[i] > ind.ma60[i] && ind.ma60[i] > ind.ma[120][i] && ind.ma[120][i] > maLine(candles, ind, o.maAlign)[i];
    if (aligned) return false;
  }
  if (o.bigBearRange != null) {
    let hi = 0;
    let lo = Infinity;
    for (let j = i - 20; j < i; j++) { hi = Math.max(hi, candles[j].high); lo = Math.min(lo, candles[j].low); }
    if (hi / lo - 1 > o.bigBearRange) return false;
  }
  return true;
}

/** 과대 낙폭 바닥(T6 조건)인가 */
function isDeepLow(candles, ind, k, o) {
  if (k < 250 || ind.bb.lower[k] == null || ind.rsi[k] == null) return false;
  if (!(candles[k].low <= ind.bb.lower[k] * (1 + o.deepTouch))) return false;
  let h = 0;
  for (let j = k - 250; j <= k; j++) h = Math.max(h, candles[j].high);
  const dd = candles[k].close / h - 1;
  return (ind.rsi[k] <= o.deepRsi && dd <= o.deepDrop) || (o.deepRsi2 != null && ind.rsi[k] <= o.deepRsi2 && dd <= o.deepDrop2 && (o.deepExt2 == null || candles[k].close / ind.ma20[k] - 1 <= o.deepExt2));
}

/** 밴드 횡보 중 상단을 크게 이탈한 장대양봉인가 */
function isBandBreak(candles, ind, i, o) {
  const c = candles[i];
  if (ind.bb.upper[i - 1] == null || !(c.close > c.open) || c.high / ind.bb.upper[i] - 1 < o.breakOver) return false;
  const ratio = ind.volAvg[i - 1] > 0 ? c.volume / ind.volAvg[i - 1] : 0;
  const band = (ind.bb.upper[i - 1] - ind.bb.lower[i - 1]) / ind.ma20[i - 1];
  return ratio >= o.breakVol && band <= o.breakBand;
}

/** 최근 alertHold 봉 안에 특이 신호(거래량 3배↑ + 하단 터치)가 있었고, 그 뒤 처음 나온 상승 돌파인가 */
function firstBreakAfterAlert(candles, ind, i, o) {
  let s = -1;
  for (let k = i - 1; k >= Math.max(1, i - o.alertHold); k--) {
    const rk = ind.volAvg[k - 1] > 0 ? candles[k].volume / ind.volAvg[k - 1] : 0;
    if (rk >= o.alertK && ind.bb.lower[k] != null && candles[k].low <= ind.bb.lower[k]) { s = k; break; }
    if (o.deepIsAlert && isDeepLow(candles, ind, k, o)) { s = k; break; } // 과대 낙폭 바닥도 특이 신호로 본다
  }
  if (s < 0) return false;
  for (let k = s + 1; k < i; k++) {
    const rk = ind.volAvg[k - 1] > 0 ? candles[k].volume / ind.volAvg[k - 1] : 0;
    if (candles[k].close > candles[k].open && ind.bb.upper[k] != null && candles[k].high / ind.bb.upper[k] - 1 >= o.breakOver && rk >= o.breakVol && (!o.firstBreakStrict || isBandBreak(candles, ind, k, o))) return false;
  }
  return true;
}

const _maCache = new WeakMap();
/** 장기 이동평균선: 200은 지표 계산 결과를 그대로, 그 밖의 길이는 단순이동평균을 직접 계산해 캐시한다 */
export function maLine(candles, ind, n) {
  if (n === 200) return ind.ma[200];
  let m = _maCache.get(candles);
  if (!m) { m = new Map(); _maCache.set(candles, m); }
  let v = m.get(n);
  if (v && v.length === candles.length) return v;
  v = new Array(candles.length).fill(null);
  let sum = 0;
  for (let i = 0; i < candles.length; i++) { sum += candles[i].close; if (i >= n) sum -= candles[i - n].close; if (i >= n - 1) v[i] = sum / n; }
  m.set(n, v);
  return v;
}

const _obvCache = new WeakMap();
/** OBV(거래량 누적) 시리즈 */
function obvSeries(candles) {
  let v = _obvCache.get(candles);
  if (v && v.length === candles.length) return v;
  v = new Array(candles.length).fill(0);
  for (let i = 1; i < candles.length; i++) v[i] = v[i - 1] + (candles[i].close > candles[i - 1].close ? candles[i].volume : candles[i].close < candles[i - 1].close ? -candles[i].volume : 0);
  _obvCache.set(candles, v);
  return v;
}

/** 고점 매도 신호를 보류할 근거(보조지표). 근거가 있으면 이름을, 없으면 null */
export function holdReason(candles, ind, i, o = STORY) {
  if (!o.holdMode || i < 60) return null;
  const mode = o.holdMode;
  if (mode.includes('obv')) {
    const obv = obvSeries(candles);
    let mx = -Infinity;
    for (let j = i - 60; j < i; j++) mx = Math.max(mx, obv[j]);
    if (obv[i] >= mx) return 'OBV(거래량 누적)가 60봉 신고가';
  }
  if (mode.includes('cmf')) {
    let mf = 0;
    let vv = 0;
    for (let j = i - 19; j <= i; j++) { const rng = candles[j].high - candles[j].low; const m = rng === 0 ? 0 : ((candles[j].close - candles[j].low) - (candles[j].high - candles[j].close)) / rng; mf += m * candles[j].volume; vv += candles[j].volume; }
    if (vv && mf / vv >= o.holdCmf) return 'CMF(자금 유입) ≥ ' + o.holdCmf;
  }
  if (mode.includes('macd') && ind.macd.hist[i] != null && ind.macd.hist[i] / candles[i].close >= o.holdMacd && ind.macd.hist[i] > ind.macd.hist[i - 3]) return 'MACD 히스토그램이 강하게 확대';
  return null;
}

/** 보유 중인 봉 i 에서 걸리는 매도 이유 (없으면 null). 사용자가 설명한 순서대로 판단한다 */
export function storySellAt(candles, ind, i, o = STORY, opt = {}) {
  const n = o.slopeBars;
  if (i < n || ind.ma60[i] == null || ind.ma60[i - n] == null || ind.bb.upper[i] == null) return null;
  const c = candles[i];
  const gap520 = pct(ind.ma5[i], ind.ma20[i]) - pct(ind.ma5[i - n], ind.ma20[i - n]);
  const deadSkip = o.deadSkipBars != null && (opt.entryTag === 'T6' || opt.entryTag === 'T3b') && opt.barsHeld <= o.deadSkipBars; // 시험: 바닥 반등 매수 직후의 5/60 데드크로스는 보류
  const tight = o.deadTight != null && ind.ma[120][i] != null && ind.ma[200][i] != null && Math.max(ind.ma60[i], ind.ma[120][i], ind.ma[200][i]) / Math.min(ind.ma60[i], ind.ma[120][i], ind.ma[200][i]) - 1 <= o.deadTight && (!o.deadTightM200Above || ind.ma[200][i] >= ind.ma60[i]); // 시험: 60·120·200일선이 엉켜 있는(수렴) 구간의 데드크로스는 보류
  if (!deadSkip && !tight && crossDown(ind.ma5, ind.ma60, i) && gap520 <= o.strongDeadGap && (!o.deadNeedBelow60 || c.close < ind.ma60[i]) && (!o.deadNeedM60Down || ind.ma60[i] <= ind.ma60[i - n])) return '강력한 5/60 데드크로스';
  if ((o.sqTop || o.wideTop || o.wickTop || o.wickTop2) && !opt.skipTop && ind.bb.lower[i] != null && !(o.skip60ForDeep && (opt.entryTag === 'T6' || opt.entryTag === 'T3b') && opt.barsHeld <= o.deepSkipBars)) {
    const bw = (ind.bb.upper[i] - ind.bb.lower[i]) / ind.ma20[i];
    if (o.wickTop2 && c.high >= ind.bb.upper[i] * 0.998 && (c.high - Math.max(c.open, c.close)) / (c.high - c.low || 1) >= o.wk2Wick && ind.rsi[i] <= o.wk2RsiMax && i >= 20) {
      const sl = ind.ma60[i] / ind.ma60[i - 20] - 1;
      const e200 = ind.ma[200][i] != null ? c.close / ind.ma[200][i] - 1 : null;
      const vr = ind.volAvg[i - 1] > 0 ? c.volume / ind.volAvg[i - 1] : 0;
      if (sl >= o.wk2SlopeMin && sl <= o.wk2SlopeMax && e200 != null && e200 >= o.wk2Ext200Min && e200 <= o.wk2Ext200Max && vr <= o.wk2VolMax) return '추세가 약한 구간의 상단 긴 윗꼬리';
    }
    if (o.wickTop && c.high >= ind.bb.upper[i] * 0.998 && (c.high - Math.max(c.open, c.close)) / (c.high - c.low || 1) >= o.wickMin && ind.rsi[i] >= o.wickRsi) return '상단 터치 + 긴 윗꼬리';
    if (o.sqTop && bw <= o.sqBw && c.high >= ind.bb.upper[i] * (1 - o.sqNear) && ind.rsi[i] >= o.sqRsi) {
      let h = 0;
      for (let j = i - 20; j < i; j++) h = Math.max(h, candles[j].high);
      if (c.high >= h * 0.995) return '밴드가 좁은 종목의 상단 근접 20봉 신고가';
    }
    const aligned4 = ind.ma20[i] > ind.ma60[i] && ind.ma60[i] > ind.ma[120][i] && ind.ma[120][i] > ind.ma[200][i];
    if (o.wideTop && !(o.wideUnaligned && aligned4) && bw >= o.wideBw && c.high >= ind.bb.upper[i] * (1 - o.wideNear) && c.close / c.open - 1 <= o.wideBody && ind.rsi[i] >= o.wideRsi) return '밴드가 넓은 구간 상단 터치 + 큰 음봉';
  }
  if (o.ma20Exit && i >= 2 && c.close < ind.ma20[i] && candles[i - 1].close < ind.ma20[i - 1] && !opt.skipTop) {
    let pk = -1;
    for (let j = i - 15; j < i; j++) pk = Math.max(pk, candles[j].close / ind.ma20[j] - 1);
    if (pk >= o.ma20ExitExt) return '20일선 아래 2일 연속(상승 뒤 꺾임)';
  }
  if (c.high < ind.bb.upper[i]) return null;
  const early = o.skip60ForDeep && (opt.entryTag === 'T6' || opt.entryTag === 'T3b') && opt.barsHeld <= o.deepSkipBars && o.skipBreakEarly;
  const earlyTop = o.earlyTop && early; // 시험: T6·T3b 매수 직후 60봉은 장악형·RSI 고점 매도도 보류(큰 음봉 매도는 유지)
  if (isBandBreak(candles, ind, i, o) && !early && !firstBreakAfterAlert(candles, ind, i, o)) return '횡보 중 볼린저 크게 이탈 (장대양봉)';
  const volRatio = ind.volAvg[i - 1] > 0 ? c.volume / ind.volAvg[i - 1] : 0;
  if (o.blowOver != null && !early && c.close / c.open - 1 <= o.blowBody && (!o.blowM20Flat || ind.ma20[i] <= ind.ma20[i - n]) && c.high / ind.bb.upper[i] - 1 >= o.blowOver && volRatio >= o.blowVol) return '상단 이탈 + 거래량 폭증 + 음봉';
  if (o.blow2Over != null && !early && c.high / ind.bb.upper[i] - 1 >= o.blow2Over && volRatio >= o.blow2Vol && (c.close / c.open - 1 <= o.blow2Body || (c.high - Math.max(c.open, c.close)) / (c.high - c.low || 1) >= o.blow2Wick)) return '상단 대폭 이탈(2단계) + 거래량 + 음봉·긴 윗꼬리';
  if (o.hugeOver != null && !early && c.high / ind.bb.upper[i] - 1 >= o.hugeOver && volRatio >= o.hugeVol) return '상단 대폭 이탈 + 거래량 폭증(과열)';
  if (o.unalignedUpper && o.unalignedTags.includes(opt.entryTag) && ind.rsi[i] >= o.unalignedRsi && (!o.unalignedNeed3 || (ind.ma20[i] > ind.ma60[i] && ind.ma60[i] > ind.ma[120][i] && (!o.unalignedStable || ind.ma60[i - o.unalignedStable] > ind.ma[120][i - o.unalignedStable]))) && !(ind.ma20[i] > ind.ma60[i] && ind.ma60[i] > ind.ma[120][i] && ind.ma[120][i] > maLine(candles, ind, o.maAlign)[i])) return '상단 터치 (정배열 아님, 바닥 반등 매수)';
  if (opt.upperOnly) return '상단 터치 (역배열 골든크로스 매수는 상단 터치에서 매도)';
  const bwNow = (ind.bb.upper[i] - ind.bb.lower[i]) / ind.ma20[i];
  if (ind.ma60[i] <= ind.ma60[i - n] && c.high / ind.bb.upper[i] - 1 >= o.sell60Over && bwNow >= o.sell60Bw && (o.sell60Wick == null || (c.high - Math.max(c.open, c.close)) / (c.high - c.low || 1) >= o.sell60Wick) && (o.sell60Vol == null || (ind.volAvg[i - 1] > 0 && c.volume / ind.volAvg[i - 1] >= o.sell60Vol)) && !(o.skip60ForDeep && (opt.entryTag === 'T6' || opt.entryTag === 'T3b') && opt.barsHeld <= o.deepSkipBars)) return '상단 터치 + 60일선 하락';
  if (o.useHot && ind.rsi[i] >= o.hotRsi && !(ind.ma20[i] > ind.ma60[i] && ind.ma60[i] > ind.ma[120][i] && ind.ma[120][i] > maLine(candles, ind, o.maAlign)[i]) && !(o.skipBreakEarly && (opt.entryTag === 'T6' || opt.entryTag === 'T3b') && opt.barsHeld <= 0)) return '상단 터치 + RSI 과열(≥' + o.hotRsi + ') (정배열 아님)';
  if (o.useBigBear && bigBearOk(candles, ind, i, o) && c.close / c.open - 1 <= o.bigBearBody && c.high / ind.bb.upper[i] - 1 >= o.bigBearOver && !(o.holdMode && holdReason(candles, ind, i, o))) return '상단 이탈 후 큰 음봉';
  if (o.useStar && c.close < c.open && c.high / ind.bb.upper[i] - 1 >= o.starOver && (c.high - Math.max(c.open, c.close)) / (c.high - c.low || 1) >= o.starWick && !(o.holdMode && holdReason(candles, ind, i, o))) return '상단 이탈 후 윗꼬리 큰 음봉(유성형)';
  const topSell = !opt.skipTop && (c.close < c.open) && !(o.holdMode && !o.trendExit && holdReason(candles, ind, i, o));
  if (o.useEngulf && topSell && !earlyTop) {
    const p = candles[i - 1];
    if (p.close > p.open && c.close < c.open && c.open >= p.close * 0.995 && c.close <= p.open && (o.engulfMinGain == null || (opt.gain != null && opt.gain >= o.engulfMinGain))) return '상단 터치 + 장악형 음봉(어제 양봉을 덮음)';
  }
  // 거래량이 전일보다 늘어나는 중이면(계속 상승) 음봉이어도 미루고, 거래량이 줄어든 음봉에서 판다
  if (topSell && !earlyTop && ind.rsi[i] != null && c.volume < candles[i - 1].volume) {
    const ext20 = c.close / ind.ma20[i] - 1;
    const wick = (c.high - Math.max(c.open, c.close)) / (c.high - c.low || 1);
    const tierA = ind.rsi[i] >= o.sellRsi && (o.rsiExt20Min == null || ext20 >= o.rsiExt20Min) && (o.sellOverMin == null || c.high / ind.bb.upper[i] - 1 >= o.sellOverMin) && !(o.steepSlope != null && i >= 20 && ind.ma60[i] / ind.ma60[i - 20] - 1 >= o.steepSlope && ind.rsi[i] < o.steepRsi); // steep: 60일선이 20봉에 steepSlope 이상 가파르게 오르는 중에는 RSI steepRsi 이상일 때만
    // 2단계(완화): RSI·이격은 낮아도 몸통이 작고 윗꼬리가 길며 거래량(20일 평균 대비)이 살아 있는 상단 밀림(유성형·도지형)
    const tierB = o.tierB && (o.tierBBody == null || c.close / c.open - 1 >= -o.tierBBody) && ind.rsi[i] >= o.tierBRsi && ext20 >= o.tierBExt && wick >= o.tierBWick && ind.volAvg[i - 1] > 0 && c.volume / ind.volAvg[i - 1] >= o.tierBVol;
    if (tierA) return '상단 터치 + 음봉 + RSI ≥ ' + o.sellRsi + ' + 거래량 감소';
    if (tierB) return '상단 밀림(2단계): 음봉 + 윗꼬리 ' + o.tierBWick * 100 + '%↑ + 거래량 ' + o.tierBVol + '배↑ (OBV 보류 무시)'; // 'RSI' 글자를 빼서 추세 보유 모드로 넘어가지 않음
  }
  if (!o.useTouchSell) return null; // 횡보 중 상단을 살짝 터치한 정도로는 팔지 않음(사용자 지시, 2025-09-01 삼성전기)
  let v = 0;
  for (let k = i - o.volBars + 1; k <= i; k++) v += candles[k].volume;
  const momentum = c.close > candles[i - n].close && v / o.volBars >= ind.volAvg[i];
  return momentum ? null : '상단 터치 (상승 지속 아님)';
}

/** 사용자가 지정한 매수~매도 구간마다 규칙이 처음 매도하는 봉을 찾는다: [{ i, time, why }] */
export function storySells(candles, ind, signals) {
  const out = [];
  let from = null;
  for (const s of signals) {
    if (s.side === 'buy') { from = s.i; continue; }
    if (from == null) continue;
    for (let i = from + 1; i <= s.i; i++) {
      const why = storySellAt(candles, ind, i);
      if (why) { out.push({ i, time: candles[i].time, why }); break; }
    }
    from = null;
  }
  if (from != null) {
    for (let i = from + 1; i < candles.length; i++) {
      const why = storySellAt(candles, ind, i);
      if (why) { out.push({ i, time: candles[i].time, why }); break; }
    }
  }
  return out;
}

/**
 * 사용자 설명 규칙으로 매수·매도 신호를 만든다(한 번에 한 포지션, 체결은 다음 봉 시가).
 * 반환: [{ i, time, side, price, tag, reasons, ret(매도만) }]
 */
export function computeStorySignals(candles, ind, o = STORY) {
  if (!o.manualTrades) return runStory(candles, ind, o, null);
  // 사용자 지정 매매(종목별): from 날짜 전까지는 규칙, 그 뒤는 지정한 날짜의 매수·매도만, 마지막 지정 뒤에는 규칙이 이어받는다
  const { from, trades } = o.manualTrades;
  const ymd = (i) => kstYmd(candles[i].time);
  const idxOf = (date) => candles.findIndex((x) => kstYmd(x.time) === date);
  const exec = (i) => (candles[i + 1] ? candles[i + 1].open : candles[i].close);
  const pre = runStory(candles, ind, { ...o, manualTrades: null }, null).filter((s) => ymd(s.i) < from);
  let entry = null;
  let entryTag = null;
  const last = pre[pre.length - 1];
  if (last && last.side === 'buy') { entry = exec(last.i); entryTag = last.tag; }
  const mid = [];
  for (const t of trades) {
    const i = idxOf(t.date);
    if (i < 0) continue;
    if (t.side === 'buy') {
      if (entry != null && mid.length === 0 && pre.length && pre[pre.length - 1].side === 'buy') pre.pop(); // 규칙의 열린 매수는 취소하고 지정 매수로 바꾼다
      entry = exec(i);
      entryTag = t.tag || '지정';
      mid.push({ i, time: candles[i].time, side: 'buy', price: candles[i].close, tag: entryTag, reasons: ['사용자 지정'] });
    } else if (entry != null) {
      mid.push({ i, time: candles[i].time, side: 'sell', price: candles[i].close, ret: exec(i) / entry - 1, reasons: ['사용자 지정'] });
      entry = null;
    }
  }
  if (!mid.length) return runStory(candles, ind, { ...o, manualTrades: null }, null);
  const lastM = mid[mid.length - 1];
  const init = lastM.side === 'buy' ? { i: lastM.i, entry, tag: lastM.tag } : { i: lastM.i, entry: null, tag: null };
  const post = runStory(candles, ind, { ...o, manualTrades: null }, init);
  return [...pre, ...mid, ...post];
}

function runStory(candles, ind, o, init) {
  const sig = [];
  const exec = (i) => (candles[i + 1] ? candles[i + 1].open : candles[i].close);
  let entry = init ? init.entry : null;
  let entryTag = init ? init.tag : null;
  let entryIdx = init ? init.i : 0;
  let trendHold = false;
  let lastSell = init && init.entry == null ? init.i : -999;
  let lastSellHigh = 0;
  let nearTouched = false;
  let lastSellWhy = '';
  let lastHugeSell = -999;
  for (let i = init ? init.i + 1 : 0; i < candles.length; i++) {
    if (entry == null) {
      if (o.exclude && o.exclude.some(([a, b]) => { const ymd = kstYmd(candles[i].time); return ymd >= a && ymd <= b; })) continue; // 제외 구간
      const nearArmed = o.nearBuy && lastSell >= 0 && i - (o.nearAfter ? lastHugeSell : lastSell) <= o.nearWithin && !nearTouched;
      const reArmed = o.reMode && lastSell >= 0 && i - lastSell <= o.reWithin && (!o.reAfter || new RegExp(o.reAfter).test(lastSellWhy));
      const kinds = storyBuyAt(candles, ind, i, { ...o, nearBuy: nearArmed, reMode: reArmed ? o.reMode : null })
        .filter((q) => !q.startsWith('T2') || i - lastSell <= o.resellBars)
        .filter((q) => !o.only || o.only.some((p) => q.startsWith(p))); // only: 시험용으로 특정 매수 종류만 허용
      if (nearArmed && ind.bb.lower[i] != null && candles[i].low <= ind.bb.lower[i] * (1 + o.nearTol)) nearTouched = true; // 첫 근접만 인정
      if (o.extBuy != null && o.extMap && o.extMap[kstYmd(candles[i].time)] && o.extMap[kstYmd(candles[i].time)][1] >= o.extBuy) kinds.push('ML 저점 확률'); // 시험용: 학습한 저점 확률
      if (o.forceBuys && o.forceBuys[kstYmd(candles[i].time)]) kinds.push(o.forceBuys[kstYmd(candles[i].time)]); // 시험용: 지정한 날짜에 지정한 종류로 매수
      if (!kinds.length && o.useRebreak && lastSell >= 0 && i - lastSell <= o.rebreakBars && isRebreak(candles, ind, i, lastSellHigh, o)) kinds.push('재돌파 매도 고가를 뚫고 재상승');
      if (kinds.length) {
        entry = exec(i);
        entryTag = kinds[0].split(' ')[0];
        entryIdx = i;
        trendHold = o.t8Trend && entryTag === 'T8'; // T8(추세 재진입)은 처음부터 추세 보유 모드: 고점 매도는 무시하고 추세가 꺾일 때만 판다
        sig.push({ i, time: candles[i].time, side: 'buy', price: candles[i].close, tag: entryTag, reasons: kinds });
      }
    } else {
      if (o.stopLoss != null && o.stopTags.includes(entryTag) && candles[i].close / entry - 1 <= -o.stopLoss) {
        sig.push({ i, time: candles[i].time, side: 'sell', price: candles[i].close, ret: exec(i) / entry - 1, reasons: ['손절(매수가 대비 −' + Math.round(o.stopLoss * 100) + '%)'] });
        entry = null;
        lastSell = i;
        nearTouched = false;
        lastSellHigh = candles[i].high;
        continue;
      }
      if (o.timeStopBars != null && o.stopTags.includes(entryTag) && i - entryIdx >= o.timeStopBars && candles[i].close < entry) {
        sig.push({ i, time: candles[i].time, side: 'sell', price: candles[i].close, ret: exec(i) / entry - 1, reasons: ['시간 손절(' + o.timeStopBars + '봉 지나도 손실)'] });
        entry = null;
        lastSell = i;
        nearTouched = false;
        lastSellHigh = candles[i].high;
        continue;
      }
      const base = { upperOnly: entryTag === 'T5', gain: candles[i].close / entry - 1, entryTag, barsHeld: i - entryIdx };
      let why;
      if (o.extSell != null && o.extMap && o.extMap[kstYmd(candles[i].time)] && o.extMap[kstYmd(candles[i].time)][0] >= o.extSell) why = 'ML 고점 확률'; // 시험용: 학습한 고점 확률
      if (!why && o.trendExit && o.holdMode && !trendHold) {
        // 고점 매도 신호가 났는데 보류 근거가 있으면 추세 보유 모드로 들어간다
        const raw = storySellAt(candles, ind, i, { ...o, holdMode: null }, base);
        if (raw && /RSI|장악형/.test(raw) && holdReason(candles, ind, i, o)) trendHold = true;
      }
      if (why) { /* ML 매도가 이미 정해짐 */ } else if (trendHold) why = storySellAt(candles, ind, i, o, { ...base, skipTop: true }) || trendExitReason(candles, ind, i, o);
      else why = storySellAt(candles, ind, i, o, base); // T5 매수는 상단 터치에서 바로 매도(사용자 지시)
      if (why) {
        lastSellWhy = why;
        if (o.nearAfter && new RegExp(o.nearAfter).test(why)) lastHugeSell = i;
        sig.push({ i, time: candles[i].time, side: 'sell', price: candles[i].close, ret: exec(i) / entry - 1, reasons: [why] });
        entry = null;
        lastSell = i;
        nearTouched = false;
        lastSellHigh = candles[i].high;
      }
    }
  }
  return sig;
}

/** 재돌파: 정배열 + 20·60일선 상승 중에 매도한 날 고가를 종가로 뚫는 양봉 */
function isRebreak(candles, ind, i, sellHigh, o) {
  const c = candles[i];
  if (!(c.close > sellHigh && c.close > c.open && c.close / candles[i - 1].close - 1 >= o.rebreakRet)) return false;
  const m20 = ind.ma20[i], m60 = ind.ma60[i], m120 = ind.ma[120][i], m200 = ind.ma[200][i];
  if (m20 == null || m200 == null || ind.ma20[i - 5] == null) return false;
  return m20 > m60 && m60 > m120 && m120 > m200 && m20 > ind.ma20[i - 5] && m60 > ind.ma60[i - 5];
}

/** 추세 보유 모드의 매도: 추세가 꺾이면 판다 */
function trendExitReason(candles, ind, i, o) {
  const c = candles[i];
  if (o.retouch && c.high >= ind.bb.upper[i]) {
    let gap = 0;
    for (let j = i - 1; j >= 1 && candles[j].high < ind.bb.upper[j]; j--) gap++; // 직전 상단 터치 이후 밴드 안에 머문 봉 수
    let mx = 0;
    for (let j = i - o.retouchLook; j < i; j++) mx = Math.max(mx, ind.rsi[j] ?? 0);
    if (gap >= o.retouchGap && ind.rsi[i] <= mx - o.retouchRsiDrop && c.close / ind.ma60[i] - 1 >= o.retouchExt60) return '추세 보유 중 횡보 후 상단 재터치 + RSI 약화(다이버전스)';
  }
  const m = o.trendExit;
  if (m === 'ma20' && c.close < ind.ma20[i]) return '추세 보유 중 종가가 20일선 아래로 마감';
  if (m === 'ma20x2' && c.close < ind.ma20[i] && candles[i - 1].close < ind.ma20[i - 1]) return '추세 보유 중 2일 연속 종가가 20일선 아래';
  if (m === 'ma60' && c.close < ind.ma60[i]) return '추세 보유 중 종가가 60일선 아래로 마감';
  if (m === 'dead520' && ind.ma5[i - 1] >= ind.ma20[i - 1] && ind.ma5[i] < ind.ma20[i]) return '추세 보유 중 5/20 데드크로스';
  return null;
}

/** 마지막 봉 기준 보유 상태 */
export function describeStoryState(candles, signals) {
  const last = signals[signals.length - 1];
  if (!candles.length) return null;
  if (last && last.side === 'buy') {
    const entry = candles[last.i + 1] ? candles[last.i + 1].open : last.price;
    return { holding: true, entry, entryTime: candles[last.i + 1] ? candles[last.i + 1].time : last.time, pnl: candles[candles.length - 1].close / entry - 1, tag: last.tag };
  }
  return { holding: false };
}

/** 참고용 표시: 거래량 폭증 + 큰 상승(+5%↑) + 종가가 모든 이평선 위인 봉 (매매 신호 아님) */
export function storySurges(candles, ind, o = STORY) {
  const out = [];
  for (let i = 210; i < candles.length; i++) {
    const ratio = ind.volAvg[i - 1] > 0 ? candles[i].volume / ind.volAvg[i - 1] : 0;
    const ret = candles[i].close / candles[i - 1].close - 1;
    const top = Math.max(ind.ma20[i], ind.ma60[i], ind.ma[120][i], ind.ma[200][i]);
    if (ratio >= o.surgeK && ret >= o.surgeRet && candles[i].close > top) out.push({ i, time: candles[i].time, ratio });
  }
  return out;
}
