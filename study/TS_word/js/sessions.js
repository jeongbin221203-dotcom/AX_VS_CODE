/* 풀이 세션 (window.Sessions) — TS 앱 core/study.py: 문제 구성 → 채점 → 기록(풀이·오답노트) → 결과 집계.
   start* 는 필요한 파트 데이터를 읽어 오므로 async 이고, 세션 번호(sid)를 돌려준다. 나머지는 동기 함수. */
(function (root) {
  "use strict";
  const U = root.TSU || require("./tsutil.js");
  const S = root.Score || require("./scoring.js");
  const B = () => root.Bank;
  const T = () => root.TSStore;

  class StudyError extends Error {}

  /** {ref: 마지막으로 푼 시각} — 안 푼 문제·오래전에 푼 문제를 먼저 내기 위해. */
  function lastSeen() {
    const out = {};
    for (const a of T().attempts()) {
      const [p, id] = a.k.split(":");
      const r = `${p}:${id}`;
      if (!(r in out) || a.t > out[r]) out[r] = a.t;
    }
    return out;
  }

  function create(mode, refs, f = {}) {
    if (!refs.length) throw new StudyError("조건에 맞는 문제가 없습니다. 등급이나 유형 조건을 바꿔 보세요.");
    return T().createSession({ mode, items: refs, ...f });
  }

  async function startPractice(part, level, qtype, n, rng) {
    if (!S.PART_INFO[part]) throw new StudyError("알 수 없는 파트입니다.");
    await B().load([part]);
    n = Math.max(1, Math.min(n, 100));
    const refs = B().pick(part, n, { levels: level ? [level] : null, qtype: qtype || null, rng, lastSeen: lastSeen() });
    return create("practice", refs, { variant: qtype || null, part, level: level || null, requested: n });
  }

  /** 등급 분포(MOCK_LEVEL_MIX)대로 n문항 가까이 뽑는다. 모자라면 다른 등급으로 채운다. */
  function pickMixed(part, n, rng, used, seen, graphic) {
    if (n <= 0) return [];
    const perItem = { 3: 3, 4: 3, 6: 4 }[part] || 1;           // 세트형 파트는 세트 단위로 나눈다
    const units = Math.max(1, U.pyRound(n / perItem));
    const raw = {}, alloc = {};
    for (const [lv, share] of Object.entries(S.MOCK_LEVEL_MIX)) { raw[lv] = units * share; alloc[lv] = Math.trunc(raw[lv]); }
    const rest = units - Object.values(alloc).reduce((s, x) => s + x, 0);
    // 최대 나머지 방식 (나머지가 같으면 낮은 등급 먼저 — 파이썬 sorted(reverse=True) 의 안정 정렬과 같다)
    const order = Object.keys(raw).sort((a, b) => (raw[b] - alloc[b]) - (raw[a] - alloc[a]));
    for (const lv of order.slice(0, rest)) alloc[lv] += 1;
    let out = [], got = 0;
    for (const [lv, k] of Object.entries(alloc)) {
      if (k <= 0) continue;
      const refs = B().pick(part, k * perItem, { levels: [Number(lv)], exclude: used, rng, graphic, lastSeen: seen });
      for (const r of refs) { used.add(r); got += B().questions(B().item(r)).length; }
      out = out.concat(refs);
    }
    if (got < n) {
      const more = B().pick(part, n - got, { exclude: used, rng, graphic, lastSeen: seen });
      for (const r of more) used.add(r);
      out = out.concat(more);
    }
    out.sort((a, b) => B().item(a).level - B().item(b).level);        // 실제 시험처럼 쉬운 문제부터 (안정 정렬)
    return out;
  }

  async function startMock(formKey, rng) {
    const form = S.MOCK_FORMS[formKey];
    if (!form) throw new StudyError("알 수 없는 모의고사 종류입니다.");
    await B().load();
    rng = rng || U.makeRng();
    const seen = lastSeen();
    const used = new Set();
    let refs = [];
    for (const part of [1, 2, 3, 4, 5, 6]) {
      const n = form[`p${part}`];
      const g = form[`p${part}_graphic`] || 0;                  // 실제 시험: Part 3·4 마지막 세트들은 시각 자료 문제
      if (g) {
        const plain = pickMixed(part, n - g * 3, rng, used, seen, false);
        refs = refs.concat(plain, pickMixed(part, g * 3, rng, used, seen, true));
      } else refs = refs.concat(pickMixed(part, n, rng, used, seen, null));
    }
    const p7 = B().pickP7(form.p7_single, form.p7_double, form.p7_triple, { rng, lastSeen: seen });
    const rank = { single: 0, double: 1, triple: 2 };
    p7.sort((a, b) => (rank[B().item(a).kind] - rank[B().item(b).kind]) || (B().item(a).level - B().item(b).level));
    refs = refs.concat(p7);
    return create("mock", refs, { variant: formKey, time_limit: form.rc_minutes * 60, seen_before: refs.filter(r => r in seen).length });
  }

  /** 안 푼 문제만으로 실전 모의고사를 몇 회 더 볼 수 있는지 (가장 모자란 구성 기준). 모든 파트가 읽혀 있어야 한다. */
  function freshMockCapacity(formKey = "full") {
    const form = S.MOCK_FORMS[formKey];
    const seen = lastSeen();
    const fresh = {};
    for (const p of S.PARTS) fresh[p] = B().itemsOf(p).filter(it => !(B().ref(it) in seen));
    const need = {
      "Part 1": [fresh[1].length, form.p1],
      "Part 2": [fresh[2].length, form.p2],
      "Part 3": [fresh[3].length, Math.trunc(form.p3 / 3)],
      "Part 4": [fresh[4].length, Math.trunc(form.p4 / 3)],
      "Part 5": [fresh[5].length, form.p5],
      "Part 6": [fresh[6].length, Math.trunc(form.p6 / 4)],
      "Part 7 단일": [fresh[7].filter(it => it.kind === "single").reduce((s, it) => s + it.questions.length, 0), form.p7_single],
      "Part 7 이중": [fresh[7].filter(it => it.kind === "double").length, form.p7_double],
      "Part 7 삼중": [fresh[7].filter(it => it.kind === "triple").length, form.p7_triple],
    };
    const times = {};
    for (const [k, [have, want]] of Object.entries(need)) times[k] = want ? Math.floor(have / want) : 99;
    let short = null;
    for (const k of Object.keys(times)) if (short === null || times[k] < times[short]) short = k;
    return { times: times[short], limit: short };
  }

  async function startDiagnostic(rng) {
    await B().load();
    rng = rng || U.makeRng();
    let refs = [];
    const used = new Set();
    for (const [part, perLevel] of Object.entries(S.DIAGNOSTIC_FORM)) {
      for (const [lv, n] of Object.entries(perLevel)) {
        const picked = B().pick(Number(part), n, { levels: [Number(lv)], exclude: used, rng });
        for (const r of picked) used.add(r);
        refs = refs.concat(picked);
      }
    }
    return create("diagnostic", refs, { time_limit: 12 * 60 });
  }

  /** 오답노트에서 열려 있는 문항이 속한 문제를 다시 낸다 (오래 안 본 것부터). */
  async function startReview(part, n) {
    const groups = new Map();                                    // "파트:item_id" -> 가장 오래된 last_seen_at
    for (const [qkey, nt] of Object.entries(T().notes())) {
      if (nt.status !== "open" || (part && nt.part !== part)) continue;
      const r = `${nt.part}:${nt.item_id}`;
      if (!groups.has(r) || nt.last_seen_at < groups.get(r)) groups.set(r, nt.last_seen_at);
    }
    const rows = [...groups.entries()].sort((a, b) => (a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0));
    await B().load([...new Set(rows.map(r => parseInt(r[0], 10)))]);
    const refs = [];
    let count = 0;
    for (const [r] of rows) {
      const it = B().item(r);
      if (!it) continue;
      refs.push(r);
      count += B().questions(it).length;
      if (count >= n) break;
    }
    refs.sort((a, b) => parseInt(a, 10) - parseInt(b, 10));
    return create("review", refs, { part: part || null });
  }

  // ---- 세션 조회 ---------------------------------------------------------------------
  const getSession = sid => T().getSession(sid);
  const sessionAttempts = sid => T().attemptsOf(sid);

  // ---- 채점 -------------------------------------------------------------------------
  /** raw: [{qidx, chosen, elapsed_ms}] → {qidx: [chosen, ms]} */
  function parseAnswers(raw) {
    if (!Array.isArray(raw)) throw new StudyError("답안 형식이 올바르지 않습니다.");
    const out = {};
    for (const a of raw) {
      const qidx = Number(a && a.qidx), chosen = a && a.chosen !== undefined && a.chosen !== null ? Number(a.chosen) : -1;
      if (!Number.isInteger(qidx) || !Number.isInteger(chosen)) throw new StudyError("답안 형식이 올바르지 않습니다.");
      const ms = a.elapsed_ms !== undefined && a.elapsed_ms !== null ? Math.round(Number(a.elapsed_ms)) : null;
      out[qidx] = [chosen, Number.isFinite(ms) ? ms : null];
    }
    return out;
  }

  /** 연습·복습: 문제 하나(세트)를 채점하고 정답·해설을 돌려준다. 같은 문제를 두 번 채점하지 않는다. */
  function gradeItem(sid, ref, rawAnswers) {
    const s = getSession(sid);
    if (!s) throw new StudyError("세션이 없습니다.");
    if (!s.items.includes(ref)) throw new StudyError("이 세션의 문제가 아닙니다.");
    const item = B().item(ref);
    if (!item) throw new StudyError("문제 데이터가 없습니다.");
    const answers = parseAnswers(rawAnswers);
    const [part, iid] = B().splitRef(ref);
    const ts = U.nowStr();
    let results = [];
    T().batch(() => {
      const done = sessionAttempts(sid).filter(a => T().partOf(a) === part && T().itemIdOf(a) === iid);
      if (done.length) {
        const prev = {};
        for (const a of done) prev[T().qidxOf(a)] = a;
        results = B().questions(item).filter(q => q.qidx in prev).map(q => ({ qidx: q.qidx, chosen: prev[q.qidx].c, correct: !!prev[q.qidx].o }));
      } else {
        for (const q of B().questions(item)) {
          const [chosen, ms] = answers[q.qidx] || [-1, null];
          results.push({ qidx: q.qidx, chosen, correct: T().recordAttempt(sid, q, chosen, ms, ts) });
        }
      }
    });
    return { ...B().reveal(ref), results };
  }

  /** 모의고사·진단: 전체 답안을 한 번에 받아 채점. 연습·복습은 남은 문제만 마감한다.
      payload: {items: {ref: [{qidx, chosen, elapsed_ms}]}, duration_sec} */
  function submitSession(sid, payload) {
    const s = getSession(sid);
    if (!s) throw new StudyError("세션이 없습니다.");
    if (s.finished_at) return s;
    const items = (payload && payload.items) || {};
    if (typeof items !== "object" || Array.isArray(items)) throw new StudyError("답안 형식이 올바르지 않습니다.");
    const ts = U.nowStr();
    T().batch(() => {
      const graded = new Set(sessionAttempts(sid).map(a => `${T().partOf(a)}:${T().itemIdOf(a)}`));
      if (s.mode === "mock" || s.mode === "diagnostic") {
        for (const ref of s.items) {
          const item = B().item(ref);
          if (!item || graded.has(ref)) continue;
          const answers = parseAnswers(items[ref] || []);
          for (const q of B().questions(item)) {
            const [chosen, ms] = answers[q.qidx] || [-1, null];
            T().recordAttempt(sid, q, chosen, ms, ts);
          }
        }
      }
    });
    return finishSession(sid, payload && payload.duration_sec);
  }

  function finishSession(sid, durationSec) {
    const rows = sessionAttempts(sid);
    const s = getSession(sid);
    const isLC = a => S.LC_PARTS.includes(T().partOf(a));
    const lc = rows.filter(isLC).map(a => [a.l, !!a.o]);
    const rc = rows.filter(a => !isLC(a)).map(a => [a.l, !!a.o]);
    let est = { lc_est: null, rc_est: null, total_est: null };
    const ans = rows.filter(a => a.c >= 0).length;
    const real = s.mode === "mock" && (S.MOCK_FORMS[s.variant || ""] || {}).real;
    if (rows.length && ans * 2 < rows.length) { /* 절반도 안 풀고 낸 시험은 점수로 추정하지 않는다 (현재 점수·등급이 0점으로 바뀌지 않게) */ }
    else if (real) est = S.estimateRaw(lc.filter(x => x[1]).length, lc.length, rc.filter(x => x[1]).length, rc.length);
    else if (s.mode === "mock" || s.mode === "diagnostic") est = S.estimate(lc, rc);
    let dur = null;
    if (durationSec !== undefined && durationSec !== null && Number.isFinite(Number(durationSec))) dur = Math.trunc(Number(durationSec));
    if (dur !== null && !(dur >= 0 && dur <= 24 * 3600)) dur = null;
    if (dur === null) dur = Math.trunc((Date.now() - new Date(s.created_at).getTime()) / 1000);
    return T().updateSession(sid, {
      finished_at: U.nowStr(), total: rows.length, correct: rows.reduce((x, a) => x + a.o, 0),
      lc_total: lc.length, lc_correct: lc.filter(x => x[1]).length, rc_total: rc.length, rc_correct: rc.filter(x => x[1]).length,
      lc_est: est.lc_est, rc_est: est.rc_est, total_est: est.total_est, duration_sec: dur });
  }

  // ---- 화면용 도우미 --------------------------------------------------------------------
  const MODE_LABEL = { practice: "파트 연습", diagnostic: "진단 테스트", mock: "모의고사", review: "오답 복습" };
  function sessionTitle(s) {
    if (s.mode === "mock") return (S.MOCK_FORMS[s.variant || ""] || {}).name || "모의고사";
    if (s.mode === "practice" && s.part) {
      let t = `Part ${s.part} ${S.PART_INFO[s.part].name}`;
      if (s.level) t += ` · ${S.GRADE_BY_LEVEL[s.level].name}`;
      if (s.variant) t += ` · ${s.variant}`;
      return t;
    }
    if (s.mode === "review" && s.part) return `오답 복습 · Part ${s.part}`;
    return MODE_LABEL[s.mode];
  }
  /** 오답노트 목록에 보일 문제 한 줄 */
  function preview(item, qidx) {
    const p = item.part;
    if (p === 1) return item.scene;
    if (p === 2 || p === 5) return item.question;
    if (p === 3 || p === 4 || p === 7) return item.questions[qidx].q || item.topic || "";
    if (p === 6) return `${item.title.split("\n")[0]} — 빈칸 (${qidx + 1})`;
    return "";
  }

  root.Sessions = { StudyError, lastSeen, startPractice, startMock, startDiagnostic, startReview, freshMockCapacity, pickMixed,
                    getSession, sessionAttempts, gradeItem, submitSession, finishSession, parseAnswers, MODE_LABEL, sessionTitle, preview };
  if (typeof module !== "undefined") module.exports = root.Sessions;
})(typeof window !== "undefined" ? window : globalThis);
