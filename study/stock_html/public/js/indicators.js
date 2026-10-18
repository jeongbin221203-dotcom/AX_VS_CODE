// 기술적 지표 — 순수 함수 (브라우저·Node 공용). 계산 불가 구간은 null.

export function sma(values, n) {
  const out = new Array(values.length).fill(null);
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= n) sum -= values[i - n];
    if (i >= n - 1) out[i] = sum / n;
  }
  return out;
}

// 앞쪽 null 을 건너뛰고 첫 n개의 평균으로 시작하는 EMA
export function ema(values, n) {
  const out = new Array(values.length).fill(null);
  const k = 2 / (n + 1);
  let start = values.findIndex((v) => v != null);
  if (start < 0 || values.length - start < n) return out;
  let sum = 0;
  for (let i = start; i < start + n; i++) sum += values[i];
  let prev = sum / n;
  out[start + n - 1] = prev;
  for (let i = start + n; i < values.length; i++) {
    prev = values[i] * k + prev * (1 - k);
    out[i] = prev;
  }
  return out;
}

// Wilder RSI
export function rsi(closes, n = 14) {
  const out = new Array(closes.length).fill(null);
  if (closes.length <= n) return out;
  let gain = 0;
  let loss = 0;
  for (let i = 1; i <= n; i++) {
    const d = closes[i] - closes[i - 1];
    if (d >= 0) gain += d; else loss -= d;
  }
  gain /= n;
  loss /= n;
  const calc = () => (loss === 0 ? (gain === 0 ? 50 : 100) : 100 - 100 / (1 + gain / loss));
  out[n] = calc();
  for (let i = n + 1; i < closes.length; i++) {
    const d = closes[i] - closes[i - 1];
    gain = (gain * (n - 1) + Math.max(d, 0)) / n;
    loss = (loss * (n - 1) + Math.max(-d, 0)) / n;
    out[i] = calc();
  }
  return out;
}

export function macd(closes, fast = 12, slow = 26, sig = 9) {
  const f = ema(closes, fast);
  const s = ema(closes, slow);
  const line = closes.map((_, i) => (f[i] != null && s[i] != null ? f[i] - s[i] : null));
  const signal = ema(line, sig);
  const hist = line.map((v, i) => (v != null && signal[i] != null ? v - signal[i] : null));
  return { line, signal, hist };
}

export function bollinger(closes, n = 20, k = 2) {
  const mid = sma(closes, n);
  const upper = new Array(closes.length).fill(null);
  const lower = new Array(closes.length).fill(null);
  for (let i = n - 1; i < closes.length; i++) {
    let v = 0;
    for (let j = i - n + 1; j <= i; j++) v += (closes[j] - mid[i]) ** 2;
    const sd = Math.sqrt(v / n);
    upper[i] = mid[i] + k * sd;
    lower[i] = mid[i] - k * sd;
  }
  return { mid, upper, lower };
}

/** Wilder ATR(평균 진폭). 변동성 기준 손절선에 사용 */
export function atr(candles, n = 14) {
  const out = new Array(candles.length).fill(null);
  if (candles.length <= n) return out;
  const tr = (i) => {
    const c = candles[i];
    const pc = candles[i - 1].close;
    return Math.max(c.high - c.low, Math.abs(c.high - pc), Math.abs(c.low - pc));
  };
  let sum = 0;
  for (let i = 1; i <= n; i++) sum += tr(i);
  let prev = sum / n;
  out[n] = prev;
  for (let i = n + 1; i < candles.length; i++) {
    prev = (prev * (n - 1) + tr(i)) / n;
    out[i] = prev;
  }
  return out;
}

/** 볼린저 %B = (종가−하단)/(상단−하단), 밴드폭 = (상단−하단)/중단. 밴드가 없는 구간은 null */
export function bollingerExtras(closes, bb) {
  const pctB = closes.map((c, i) => (bb.upper[i] == null || bb.upper[i] === bb.lower[i] ? null : (c - bb.lower[i]) / (bb.upper[i] - bb.lower[i])));
  const bandwidth = closes.map((_, i) => (bb.mid[i] ? (bb.upper[i] - bb.lower[i]) / bb.mid[i] : null));
  return { pctB, bandwidth };
}

/** arr[i] 가 직전 lookback 개 값(자신 제외) 중 몇 %보다 작거나 같은가 (0~1). 값이 모자라면 null */
export function percentRank(arr, i, lookback) {
  if (arr[i] == null) return null;
  let n = 0;
  let le = 0;
  for (let j = Math.max(0, i - lookback); j < i; j++) {
    if (arr[j] == null) continue;
    n++;
    if (arr[j] <= arr[i]) le++;
  }
  return n >= Math.min(lookback, 30) ? le / n : null;
}
