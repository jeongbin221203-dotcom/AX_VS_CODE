/* 공통 작은 도구 (window.TSU) — 날짜 문자열, 파이썬과 같은 반올림, 난수, 시간 표시.
   서버·화면 없이 node 에서도 돌아가야 하므로 document 를 쓰지 않는다. */
(function (root) {
  "use strict";
  const pad = n => String(n).padStart(2, "0");
  const todayStr = (d = new Date()) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const nowStr = (d = new Date()) => `${todayStr(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  function addDays(dateStr, n) {
    const [y, m, d] = dateStr.slice(0, 10).split("-").map(Number);
    return todayStr(new Date(y, m - 1, d + n));
  }
  /** 두 날짜(YYYY-MM-DD)의 차이 (b - a), 일 */
  function daysBetween(a, b) {
    const f = s => { const [y, m, d] = s.slice(0, 10).split("-").map(Number); return Date.UTC(y, m - 1, d); };
    return Math.round((f(b) - f(a)) / 86400000);
  }
  /** 파이썬 round() — .5 는 짝수 쪽 (Math.round 는 6.5 → 7 이라 결과가 어긋난다) */
  function pyRound(x) {
    const f = Math.floor(x), d = x - f;
    if (d < 0.5) return f;
    if (d > 0.5) return f + 1;
    return f % 2 === 0 ? f : f + 1;
  }
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = v => (v === null || v === undefined ? "–" : pyRound(v * 100) + "%");
  function fmtTime(sec) {
    sec = Math.max(0, Math.round(sec));
    return `${Math.floor(sec / 60)}:${pad(sec % 60)}`;
  }
  /** 파이썬 helpers.mmss: 1시간 미만 m:ss, 이상은 'N시간 M분' */
  function mmss(sec) {
    if (sec === null || sec === undefined) return "–";
    sec = Math.trunc(sec);
    return sec < 3600 ? `${Math.floor(sec / 60)}:${pad(sec % 60)}` : `${Math.floor(sec / 3600)}시간 ${Math.floor(sec % 3600 / 60)}분`;
  }
  /** 시드를 줄 수 있는 난수 (테스트에서 같은 결과가 나오게). 시드가 없으면 Math.random */
  function makeRng(seed) {
    if (seed === undefined || seed === null) return { random: Math.random };
    let a = seed >>> 0;
    return { random() { a = (a + 0x6D2B79F5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; } };
  }
  function shuffle(arr, rng) {
    rng = rng || { random: Math.random };
    for (let k = arr.length - 1; k > 0; k--) { const j = Math.floor(rng.random() * (k + 1)); [arr[k], arr[j]] = [arr[j], arr[k]]; }
    return arr;
  }
  root.TSU = { pad, todayStr, nowStr, addDays, daysBetween, pyRound, esc, pct, fmtTime, mmss, makeRng, shuffle };
  if (typeof module !== "undefined") module.exports = root.TSU;
})(typeof window !== "undefined" ? window : globalThis);
