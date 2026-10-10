/* 학습 통계 (window.Stats) — TS 앱 core/stats.py. 정답률·풀이 시간·점수 추이·연속 학습일.
   풀이 기록은 TSStore, 단어 복습 기록(연속 학습일·하루 학습량)은 Ward.logEntries() 에서 읽는다. */
(function (root) {
  "use strict";
  const U = root.TSU || require("./tsutil.js");
  const S = root.Score || require("./scoring.js");
  const T = () => root.TSStore;
  let todayFn = () => U.todayStr();
  let vocabLogFn = () => (root.Ward && root.Ward.logEntries ? root.Ward.logEntries() : []);

  const answered = a => a.c >= 0;
  const part = a => T().partOf(a);

  /** 파트별 정답률. lastN 이면 파트마다 최근 n문항만, days 이면 최근 며칠만. → {part: {n, correct, rate, avg_sec}} */
  function partAccuracy({ days = null, lastN = null } = {}) {
    const out = {};
    const all = T().attempts();
    const since = days ? U.addDays(todayFn(), -(days - 1)) : null;
    for (const p of Object.keys(S.PART_INFO).map(Number)) {
      let rows = [];
      for (let i = all.length - 1; i >= 0; i--) {            // id 내림차순 (가장 최근 먼저)
        const a = all[i];
        if (part(a) !== p || !answered(a)) continue;
        if (since && !(a.t >= since)) continue;
        rows.push(a);
        if (lastN && rows.length >= lastN) break;
      }
      const n = rows.length;
      const times = rows.filter(r => r.m).map(r => r.m);
      const c = rows.reduce((s, r) => s + r.o, 0);
      out[p] = { n, correct: c, rate: n ? c / n : null, avg_sec: times.length ? times.reduce((s, x) => s + x, 0) / times.length / 1000 : null };
    }
    return out;
  }

  /** (파트, 등급)별 정답률 → {"파트,등급": {n, rate}} */
  function levelAccuracy() {
    const g = {};
    for (const a of T().attempts()) {
      if (!answered(a)) continue;
      const k = `${part(a)},${a.l}`;
      const x = g[k] || (g[k] = { n: 0, c: 0 });
      x.n++; x.c += a.o;
    }
    const out = {};
    for (const [k, x] of Object.entries(g)) out[k] = { n: x.n, rate: x.c / x.n };
    return out;
  }
  const levelAcc = (acc, p, lv) => acc[`${p},${lv}`] || null;

  /** 유형별 정답률 (낮은 순, minN 문항 이상 푼 유형만) */
  function typeAccuracy(minN = 3) {
    const g = new Map();
    for (const a of T().attempts()) {
      if (!answered(a)) continue;
      const k = `${part(a)}|${a.y}`;
      const x = g.get(k) || { part: part(a), qtype: a.y, n: 0, c: 0 };
      x.n++; x.c += a.o;
      g.set(k, x);
    }
    const out = [...g.values()].filter(x => x.n >= minN).map(x => ({ part: x.part, qtype: x.qtype, n: x.n, rate: x.c / x.n }));
    out.sort((a, b) => (a.rate - b.rate) || (b.n - a.n));
    return out;
  }

  /** 점수가 나온 세션들 (오래된 것 → 최근). limit 개까지 최근 것만. */
  function scoreHistory(limit = 30) {
    const rows = T().sessions().filter(s => s.total_est !== null && s.total_est !== undefined && s.finished_at);
    rows.sort((a, b) => (a.finished_at < b.finished_at ? 1 : a.finished_at > b.finished_at ? -1 : b.id - a.id));
    return rows.slice(0, limit).reverse().map(s => ({ id: s.id, finished_at: s.finished_at, mode: s.mode, variant: s.variant, lc_est: s.lc_est, rc_est: s.rc_est, total_est: s.total_est }));
  }
  function latestEstimate() { const h = scoreHistory(1); return h.length ? h[h.length - 1] : null; }

  function dailyCounts(days = 30) {
    const start = U.addDays(todayFn(), -(days - 1));
    const q = {}, v = {};
    for (const a of T().attempts()) {
      if (!answered(a) || a.t < start) continue;
      const d = a.t.slice(0, 10);
      const x = q[d] || (q[d] = [0, 0]);
      x[0]++; x[1] += a.o;
    }
    for (const l of vocabLogFn()) if (l.t >= start) v[l.t.slice(0, 10)] = (v[l.t.slice(0, 10)] || 0) + 1;
    const out = [];
    for (let i = 0; i < days; i++) {
      const d = U.addDays(start, i);
      const [n, c] = q[d] || [0, 0];
      out.push({ date: d, questions: n, correct: c, words: v[d] || 0 });
    }
    return out;
  }

  function todayCounts() {
    const d = todayFn();
    const per = {};
    let reviewed = 0;
    const modeOf = new Map(T().sessions().map(s => [s.id, s.mode]));
    for (const a of T().attempts()) {
      if (!answered(a) || a.t.slice(0, 10) !== d) continue;
      per[part(a)] = (per[part(a)] || 0) + 1;
      if (modeOf.get(a.s) === "review") reviewed++;
    }
    const words = vocabLogFn().filter(l => l.t.slice(0, 10) === d).length;
    return { per_part: per, questions: Object.values(per).reduce((s, x) => s + x, 0), words, reviewed };
  }

  /** 오늘(또는 어제)까지 이어진 연속 학습일 */
  function streak() {
    const days = new Set();
    for (const a of T().attempts()) days.add(a.t.slice(0, 10));
    for (const l of vocabLogFn()) days.add(l.t.slice(0, 10));
    let d = todayFn(), n = 0;
    if (!days.has(d)) d = U.addDays(d, -1);
    while (days.has(d)) { n++; d = U.addDays(d, -1); }
    return n;
  }

  function recentSessions(limit = 10) {
    const rows = T().sessions().filter(s => s.finished_at);
    rows.sort((a, b) => (a.finished_at < b.finished_at ? 1 : a.finished_at > b.finished_at ? -1 : b.id - a.id));
    return rows.slice(0, limit);
  }

  /** 끝내지 않은 세션 (이어서 풀기) — 모의고사·진단은 시작만 해도, 연습은 한 문제라도 푼 것만. 최근 며칠 것. */
  function unfinishedSessions(limit = 5, days = 7) {
    const since = U.addDays(todayFn(), -days);
    const count = {};
    for (const a of T().attempts()) count[a.s] = (count[a.s] || 0) + 1;
    const rows = T().sessions().filter(s => !s.finished_at && s.created_at >= since && (s.mode === "mock" || s.mode === "diagnostic" || count[s.id] > 0));
    rows.sort((a, b) => b.id - a.id);
    return rows.slice(0, limit).map(s => ({ ...s, answered: count[s.id] || 0 }));
  }

  /** RC 파트별 문항당 평균 풀이 시간(초), 최근 100문항 */
  function timeByPart() {
    const acc = partAccuracy({ lastN: 100 });
    return { 5: acc[5].avg_sec, 6: acc[6].avg_sec, 7: acc[7].avg_sec };
  }

  root.Stats = { partAccuracy, levelAccuracy, levelAcc, typeAccuracy, scoreHistory, latestEstimate, dailyCounts, todayCounts,
                 streak, recentSessions, unfinishedSessions, timeByPart,
                 setToday: fn => { todayFn = fn; }, setVocabLog: fn => { vocabLogFn = fn; } };
  if (typeof module !== "undefined") module.exports = root.Stats;
})(typeof window !== "undefined" ? window : globalThis);
