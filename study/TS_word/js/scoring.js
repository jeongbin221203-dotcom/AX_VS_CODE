/* 등급 판정과 점수 추정 (window.Score) — TS 앱 core/scoring.py + core/content.py 상수를 그대로 옮겼다.
   토익 환산표는 시험마다 달라 공개되지 않는다. 여기서는 정답률(난이도 가중)을 흔히 알려진 환산 구간에 맞춘
   구간별 직선으로 바꾼 '추정 점수'를 쓴다. 실제 점수와는 차이가 있다. */
(function (root) {
  "use strict";
  const U = root.TSU || require("./tsutil.js");

  const LC_PARTS = [1, 2, 3, 4], RC_PARTS = [5, 6, 7], PARTS = [1, 2, 3, 4, 5, 6, 7], SET_PARTS = [3, 4, 6, 7];
  const PART_INFO = {
    1: { name: "사진 묘사", section: "LC", real_count: 6, sec_per_q: null },
    2: { name: "질의응답", section: "LC", real_count: 25, sec_per_q: null },
    3: { name: "짧은 대화", section: "LC", real_count: 39, sec_per_q: null },
    4: { name: "짧은 담화", section: "LC", real_count: 30, sec_per_q: null },
    5: { name: "단문 빈칸", section: "RC", real_count: 30, sec_per_q: 20 },
    6: { name: "장문 빈칸", section: "RC", real_count: 16, sec_per_q: 30 },
    7: { name: "독해", section: "RC", real_count: 54, sec_per_q: 60 },
  };
  const GRADES = [
    { level: 1, name: "Orange", ko: "입문", low: 10, high: 215, color: "#E07B24" },
    { level: 2, name: "Brown", ko: "기초", low: 220, high: 465, color: "#8A5A3B" },
    { level: 3, name: "Green", ko: "중급", low: 470, high: 725, color: "#2E8B57" },
    { level: 4, name: "Blue", ko: "중상급", low: 730, high: 855, color: "#2F6DB5" },
    { level: 5, name: "Gold", ko: "고급", low: 860, high: 990, color: "#B8912F" },
  ];
  const GRADE_BY_LEVEL = Object.fromEntries(GRADES.map(g => [g.level, g]));
  const rangeText = g => `${g.low}~${g.high}점`;

  function gradeFor(score) {
    if (score === null || score === undefined) return null;
    for (let i = GRADES.length - 1; i >= 0; i--) if (score >= GRADES[i].low) return GRADES[i];
    return GRADES[0];
  }

  // 정답률(0~1) → 섹션 점수(5~495). 구간 사이는 직선 보간.
  const LC_TABLE = [[0.0, 5], [0.2, 60], [0.4, 165], [0.6, 285], [0.75, 375], [0.85, 430], [0.93, 475], [0.97, 495], [1.0, 495]];
  const RC_TABLE = [[0.0, 5], [0.2, 45], [0.4, 140], [0.6, 255], [0.75, 345], [0.85, 405], [0.93, 455], [0.98, 495], [1.0, 495]];

  function bisectRight(xs, v) { let lo = 0, hi = xs.length; while (lo < hi) { const mid = (lo + hi) >> 1; if (v < xs[mid]) hi = mid; else lo = mid + 1; } return lo; }
  function interp(table, ratio) {
    ratio = Math.min(Math.max(ratio, 0.0), 1.0);
    const xs = table.map(t => t[0]);
    const i = Math.max(1, Math.min(bisectRight(xs, ratio), table.length - 1));
    const [x0, y0] = table[i - 1], [x1, y1] = table[i];
    const y = x1 === x0 ? y0 : y0 + (y1 - y0) * (ratio - x0) / (x1 - x0);
    return U.pyRound(y / 5.0) * 5;
  }
  const sectionScore = (section, ratio) => interp(section === "LC" ? LC_TABLE : RC_TABLE, ratio);

  // 모의고사 등급 구성과 같은 난이도 분포를 기준(1.0)으로 난이도 가중 정답률을 보정한다.
  const LEVEL_WEIGHT = { 1: 0.6, 2: 0.8, 3: 1.0, 4: 1.25, 5: 1.5 };
  /** results: [[level, correct]] */
  function weightedRatio(results) {
    if (!results.length) return 0.0;
    let total = 0, got = 0;
    for (const [lv, ok] of results) total += LEVEL_WEIGHT[lv];
    for (const [lv, ok] of results) if (ok) got += LEVEL_WEIGHT[lv];
    return got / total;
  }
  function estimate(lc, rc) {
    const lcEst = lc.length ? sectionScore("LC", weightedRatio(lc)) : null;
    const rcEst = rc.length ? sectionScore("RC", weightedRatio(rc)) : null;
    return { lc_est: lcEst, rc_est: rcEst, total_est: lcEst !== null && rcEst !== null ? lcEst + rcEst : null };
  }

  const MOCK_LEVEL_MIX = { 1: 0.10, 2: 0.20, 3: 0.35, 4: 0.25, 5: 0.10 };
  const MOCK_FORMS = {
    mini: { name: "미니 모의고사", desc: "LC 약 20문항 + RC 약 20문항 · 약 30분",
            p1: 2, p2: 6, p3: 6, p4: 6, p5: 10, p6: 4, p7_single: 6, p7_double: 0, p7_triple: 0, rc_minutes: 18 },
    half: { name: "하프 모의고사", desc: "LC 약 50문항 + RC 약 50문항 · 약 65분",
            p1: 3, p2: 12, p3: 18, p4: 15, p5: 15, p6: 8, p7_single: 14, p7_double: 1, p7_triple: 1, rc_minutes: 38 },
    // 실제 시험과 같은 구성·진행: LC 는 음성 흐름대로 자동 진행(다시 듣기·되돌아가기 없음), RC 75분
    full: { name: "실전 모의고사", desc: "실제 토익과 같은 200문항 · LC 약 45분 자동 진행 + RC 75분",
            p1: 6, p2: 25, p3: 39, p4: 30, p5: 30, p6: 16, p7_single: 29, p7_double: 2, p7_triple: 3,
            p3_graphic: 3, p4_graphic: 2, rc_minutes: 75, real: true },
  };

  // 실전 모의고사: 원점수(맞힌 개수, 100문항 기준) → 환산 점수. 비공식 구간이라 ±30점 정도 오차가 있다.
  const LC_RAW = [[0, 5], [5, 10], [10, 25], [15, 45], [20, 65], [25, 85], [30, 105], [35, 125], [40, 150], [45, 175],
                  [50, 200], [55, 230], [60, 260], [65, 290], [70, 320], [75, 350], [80, 380], [85, 410], [90, 445],
                  [95, 475], [97, 495], [100, 495]];
  const RC_RAW = [[0, 5], [5, 5], [10, 20], [15, 35], [20, 55], [25, 75], [30, 95], [35, 115], [40, 140], [45, 165],
                  [50, 190], [55, 220], [60, 250], [65, 280], [70, 310], [75, 340], [80, 370], [85, 400], [90, 435],
                  [95, 465], [98, 495], [100, 495]];
  function rawSectionScore(section, correct, total) {
    const raw = total ? 100 * correct / total : 0;
    const table = (section === "LC" ? LC_RAW : RC_RAW).map(([x, y]) => [x / 100, y]);
    return interp(table, raw / 100);
  }
  function estimateRaw(lcCorrect, lcTotal, rcCorrect, rcTotal) {
    const lc = lcTotal ? rawSectionScore("LC", lcCorrect, lcTotal) : null;
    const rc = rcTotal ? rawSectionScore("RC", rcCorrect, rcTotal) : null;
    return { lc_est: lc, rc_est: rc, total_est: lc !== null && rc !== null ? lc + rc : null };
  }

  // 진단 테스트: 등급을 고르게 섞은 짧은 세트 (약 20분). part -> {level: 문항 수}
  const DIAGNOSTIC_FORM = {
    1: { 1: 1, 2: 1, 3: 1 },
    2: { 1: 2, 2: 2, 3: 2, 4: 2, 5: 2 },
    3: { 2: 3, 4: 3, 5: 3 },
    4: { 3: 3, 5: 3 },
    5: { 1: 2, 2: 3, 3: 3, 4: 3, 5: 2 },
    7: { 2: 2, 4: 2 },
  };

  root.Score = { LC_PARTS, RC_PARTS, PARTS, SET_PARTS, PART_INFO, GRADES, GRADE_BY_LEVEL, rangeText, gradeFor,
                 sectionScore, LEVEL_WEIGHT, weightedRatio, estimate, MOCK_LEVEL_MIX, MOCK_FORMS,
                 rawSectionScore, estimateRaw, DIAGNOSTIC_FORM };
  if (typeof module !== "undefined") module.exports = root.Score;
})(typeof window !== "undefined" ? window : globalThis);
