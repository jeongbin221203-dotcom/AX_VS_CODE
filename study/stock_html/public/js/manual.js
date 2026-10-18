// 사용자가 차트 사진으로 직접 짚어 준 매수·매도 지점 (규칙으로 계산한 신호가 아님)
// 현대차(005380) 일봉 기준. 날짜는 한국 시간 기준 봉 날짜.
// 2025-06-16(저가 139,300 아랫꼬리 이상 신호)은 역배열 구간이라 표시하지 않고, 상승 전환 여부는 차트를 보고 나중에 판단한다.
export const MANUAL = {
  '005380': [
    { date: '2024-02-01', side: 'buy', why: '거래량 동반 상승 (거래량 5.7배, +6.9%, 20·60·120·200일선 위로 돌파)' },
    { date: '2024-02-13', side: 'sell', why: '2024-02-02~02-08 볼린저 상단 이탈이 이어졌지만 계속 상승 중이라 팔지 않고(2024-02-06은 전일 종가보다 낮아도 양봉), 처음 음봉(종가<시가)이 나온 2024-02-13에 매도' },
    { date: '2024-04-03', side: 'buy', why: '계속 하락하다 볼린저 하단 터치. 5/20 데드크로스 상태지만 이평선들이 계속 상승 중이고 서로의 간격이 커지는 중 (2024-04-23·04-24 상단 터치는 큰 폭 상승이 아니라 보유)' },
    { date: '2024-05-22', side: 'sell', why: '2024-04-03 하단 터치로 산 주식을 볼린저 상단 터치(횡보 상승 중의 장대양봉, 거래량 4.2배)에서 매도' },
    { date: '2024-08-05', side: 'buy', why: '5일선이 계속 하락해 120일선까지 내려왔고 200일선과 가까운 위치에서 반등 가능성 + 볼린저 하단·200일선 터치 (60>120>200 정배열, 종가는 224일선 위)' },
    { date: '2024-08-29', side: 'sell', why: '볼린저 상단 터치, 60일선이 하락 중이라 매도 · 이후 역배열로 바뀌는 중이라 매수 X' },
    { date: '2025-10-13', side: 'buy', why: '정배열(20>60>120>200) + 볼린저 하단 터치' },
    { date: '2026-01-22', side: 'sell', why: '볼린저 밴드를 크게 이탈한 상단 터치 (상단보다 10.2% 위, 음봉 마감)' },
    { date: '2026-03-31', side: 'buy', why: '60·120·200일선 정배열, 20일선은 약간 하락(강력한 하락 아님) + 볼린저 하단 터치 (5/60 데드크로스가 같은 날이지만 강력한 하락 데드크로스가 아님)' },
    { date: '2026-05-14', side: 'sell', why: '볼린저 상단 터치 (상단보다 8.1% 위)' },
  ],
};

// 특이 신호: 매수는 아니지만 곧 상승이 가능하다는 표시 (거래 신호 아님)
export const MANUAL_EVENTS = {
  '005380': [{ date: '2025-06-16', text: '특이신호' }],
};

// 표시만 하는 메모 (신호 아님): 매수 금지 확인용
export const MANUAL_NOTES = {
  '005380': [
    { date: '2024-05-31', text: '2024-05-31 20일선에 닿았지만 볼린저 하단 터치가 아니라 매수 아님 (2026-05-19도 같은 이유로 매수에서 뺌)' },
    { date: '2024-07-12', text: '2024-06-27 이후 상승 없는 하락(2024-07-12~2024-07-29 하단 터치)은 매수 X, 2024-08-05 200일선·하단 터치에서 재매수 (이평선이 깨지며 역배열로 바뀌는 중)' },
    { date: '2026-06-10', text: '2026-06-10 5/20 데드크로스 → 신규 매수 X' },
    { date: '2026-06-24', text: '2026-06-24 5/60 데드크로스(강력): 5일선이 20일선 아래로 강하게 내려가며 60일선도 뚫고 내려감, 60일선이 하락하며 120일선으로 향함, 상승 없는 계단식 하락이라 매수 X' },
  ],
};

const kstDate = (t) => new Date((t + 9 * 3600) * 1000).toISOString().slice(0, 10);

/** 일봉 candles 에서 날짜가 맞는 봉에 신호를 붙인다. 체결은 다음 봉 시가로 계산해 다른 방식과 같게 한다. */
export function manualSignals(candles, symbol) {
  const list = MANUAL[symbol];
  if (!list || !candles.length) return [];
  const idx = new Map(candles.map((x, i) => [kstDate(x.time), i]));
  const exec = (i) => (candles[i + 1] ? candles[i + 1].open : candles[i].close);
  const out = [];
  let entry = null;
  for (const m of list) {
    const i = idx.get(m.date);
    if (i == null) continue;
    if (m.side === 'buy') {
      entry = exec(i);
      out.push({ i, time: candles[i].time, side: 'buy', price: candles[i].close, reasons: [m.why] });
    } else if (entry != null) {
      out.push({ i, time: candles[i].time, side: 'sell', price: candles[i].close, ret: exec(i) / entry - 1, reasons: [m.why] });
      entry = null;
    }
  }
  return out;
}

/** 특이 신호를 봉 시간으로 바꾼다 */
export function manualEvents(candles, symbol) {
  const list = MANUAL_EVENTS[symbol] || [];
  const idx = new Map(candles.map((x, i) => [kstDate(x.time), i]));
  return list.filter((e) => idx.has(e.date)).map((e) => ({ time: candles[idx.get(e.date)].time, text: e.text }));
}

export function manualState(candles, signals) {
  const last = signals[signals.length - 1];
  if (!candles.length) return null;
  if (last && last.side === 'buy') {
    const i = last.i;
    const entry = candles[i + 1] ? candles[i + 1].open : last.price;
    return { holding: true, entry, pnl: candles[candles.length - 1].close / entry - 1 };
  }
  return { holding: false };
}
