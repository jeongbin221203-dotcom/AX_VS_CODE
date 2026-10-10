// node --test — 토익 시험 기능(점수 환산·통계·계획·오답노트·세션 흐름·백업)이 TS 앱(Python)과 같은 결과인지 확인한다.
// 정답은 tools/make_fixtures.py 가 TS 앱의 실제 코드로 계산해 둔 tests/py_fixtures.json 이다.
const fs = require("fs"), vm = require("vm"), path = require("path"), assert = require("assert");
const test = require("node:test");
const root = path.join(__dirname, "..");
const FX = JSON.parse(fs.readFileSync(path.join(__dirname, "py_fixtures.json"), "utf8"));
const E = FX.expected;

function sandbox({ parts = [], words = true } = {}) {
  const mem = {};
  const win = { WARD_DATA: {} };
  const ctx = vm.createContext({
    window: win, localStorage: { getItem: k => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: k => { delete mem[k]; } },
    document: {}, console, Date, Math, JSON, Set, Map, Object, Array, Number, String, Promise, Error, RegExp, parseInt, isNaN, Infinity, Symbol,
  });
  const run = f => vm.runInContext(fs.readFileSync(path.join(root, f), "utf8"), ctx, { filename: f });
  for (const f of ["js/tsutil.js", "js/scoring.js", "js/store.js", "js/tsstore.js", "js/bank.js", "js/stats.js", "js/planner.js", "js/sessions.js", "data/guide.js", "data/meta.js"]) run(f);
  if (words) run("data/toeic.js");
  for (const p of parts) run(`data/toeic-p${p}.js`);
  return { W: win, run, mem, ctx };
}
function almost(a, b, msg, path0 = "") {
  if (typeof a === "number" && typeof b === "number") return assert.ok(Math.abs(a - b) < 1e-9, `${msg} ${path0}: ${a} != ${b}`);
  if (a === null || b === null || typeof a !== "object" || typeof b !== "object") return assert.strictEqual(a, b, `${msg} ${path0}: ${JSON.stringify(a)} != ${JSON.stringify(b)}`);
  same(Object.keys(a).sort(), Object.keys(b).sort(), `${msg} ${path0}: 키 불일치`);
  for (const k of Object.keys(a)) almost(a[k], b[k], msg, path0 + "." + k);
}
// vm 안에서 만든 객체와 비교할 때 prototype 이 달라 deepStrictEqual 이 실패하므로 JSON 으로 비교한다
const same = (a, b, msg) => assert.strictEqual(JSON.stringify(a), JSON.stringify(b), msg);
const ALL = [1, 2, 3, 4, 5, 6, 7];
function loaded() {                                   // 문제·단어·픽스처 기록을 모두 불러온 환경
  const s = sandbox({ parts: ALL });
  const { W } = s;
  s.run("data/toeic.js");
  for (const p of ALL) W.Bank.setItems(p, W.TS_DATA["p" + p]);
  W.Ward.setClock(() => new Date(2026, 9, 7, 20, 0, 0));
  W.Ward.importJSON(JSON.stringify(FX.backup), "replace");
  W.TSStore.importData(FX.backup.ts, "replace");
  W.Stats.setToday(() => E.today);
  return s;
}
const wardInit = async W => { await W.Ward.init(); };

test("점수 환산: 파이썬과 같은 결과 (구간 보간·원점수·등급 판정)", () => {
  const { W } = sandbox({ words: false });
  for (const c of E.scoring.estimate) almost(W.Score.estimate(c.lc, c.rc), c.out, "estimate");
  for (const c of E.scoring.estimate_raw) almost(W.Score.estimateRaw(...c.args), c.out, "estimateRaw " + c.args);
  for (const c of E.scoring.section) assert.strictEqual(W.Score.sectionScore(c.section, c.ratio), c.out, `section ${c.section} ${c.ratio}`);
  for (const c of E.scoring.grade_for) assert.strictEqual(W.Score.gradeFor(c.score).level, c.level, `grade ${c.score}`);
});

test("문제 데이터: 파트별 문항 수가 TS 앱과 같고, 요약(meta)과도 맞다", () => {
  const { W } = loaded();
  let total = 0;
  for (const [p, c] of Object.entries(E.counts)) {
    assert.strictEqual(W.Bank.countQuestions(Number(p)), c.all, `Part ${p} 문항 수`);
    for (const lv of [1, 2, 3, 4, 5]) assert.strictEqual(W.Bank.countQuestions(Number(p), lv), c.levels[lv], `Part ${p} 등급 ${lv}`);
    for (const [t, n] of Object.entries(c.types)) assert.strictEqual(W.Bank.countQuestions(Number(p), null, t), n, `Part ${p} ${t}`);
    same(W.Bank.types(Number(p)), Object.keys(c.types), `Part ${p} 유형 순서`);
    const m = W.TS_META.toeic.parts[p];
    assert.strictEqual(m.questions, c.all);
    same(m.types.map(t => t[0]), Object.keys(c.types));
    total += c.all;
  }
  assert.strictEqual(W.TS_META.toeic.questions, total);
  assert.ok(total >= 3500, "토익 문항 3,500개 이상: " + total);
  // 모든 문항: 정답 번호가 보기 범위 안, 해설이 비어 있지 않음
  for (const p of ALL) for (const it of W.Bank.itemsOf(p)) for (const q of W.Bank.questions(it)) {
    const n = p === 1 ? it.statements.length : (it.questions ? it.questions[q.qidx].choices.length : it.choices.length);
    assert.ok(q.answer >= 0 && q.answer < n, `${q.qkey} 정답 범위`);
    assert.ok(q.explanation, `${q.qkey} 해설`);
  }
});

test("publicItem 은 정답·해설·번역을 뺀다 / reveal 은 돌려준다", () => {
  const { W } = loaded();
  for (const ref of ["5:p5-001", "3:p3-001", "7:p7-001"]) {
    const pub = W.Bank.publicItem(ref);
    assert.ok(!("answer" in pub) && !("explanation" in pub) && !("translation" in pub), ref);
    for (const q of pub.questions || []) assert.ok(!("answer" in q) && !("explanation" in q));
    const rv = W.Bank.reveal(ref);
    assert.ok(rv.questions.every(q => Number.isInteger(q.answer) && q.explanation));
  }
});

test("통계: 정답률·추이·학습량·연속 학습일·이어서 풀기가 파이썬과 같다", () => {
  const { W } = loaded();
  const S = W.Stats, X = E.part_accuracy;
  almost(S.partAccuracy(), X.all, "part all");
  almost(S.partAccuracy({ days: 7 }), X.d7, "part 7d");
  almost(S.partAccuracy({ lastN: 60 }), X.last60, "part last60");
  almost(S.partAccuracy({ lastN: 100 }), X.last100, "part last100");
  almost(S.levelAccuracy(), E.level_accuracy, "level acc");
  const key = t => `${t.part}|${t.qtype}`;
  const a = S.typeAccuracy().sort((x, y) => (key(x) < key(y) ? -1 : 1)), b = E.type_accuracy.slice().sort((x, y) => (key(x) < key(y) ? -1 : 1));
  almost(a, b, "type acc");
  const rates = S.typeAccuracy().map(t => t.rate);
  same(rates, rates.slice().sort((x, y) => x - y), "약한 유형이 낮은 순");
  almost(S.scoreHistory(30), E.score_history, "score_history");
  almost(S.latestEstimate(), E.latest_estimate, "latest");
  almost(S.dailyCounts(30), E.daily_counts, "daily");
  almost(S.todayCounts(), E.today_counts, "today");
  assert.strictEqual(S.streak(), E.streak, "streak");
  const rec = S.recentSessions(10).map(s => ({ id: s.id, finished_at: s.finished_at, mode: s.mode, variant: s.variant, total: s.total, correct: s.correct, total_est: s.total_est }));
  almost(rec, E.recent_sessions, "recent");
  almost(S.unfinishedSessions().map(s => ({ id: s.id, answered: s.answered, mode: s.mode })), E.unfinished, "unfinished");
  almost(S.timeByPart(), E.time_by_part, "time_by_part");
});

test("학습 계획: 목표·시험일·직접 입력 점수 조합마다 파이썬과 같다", async () => {
  const { W } = loaded();
  await wardInit(W);
  for (const [name, v] of Object.entries(E.plans)) {
    const plan = W.Planner.build(v.settings, { today: E.today });
    const got = { ...plan, grade: plan.grade ? plan.grade.level : null, target_grade: plan.target_grade ? plan.target_grade.level : null, guide: null,
                  tasks: plan.tasks.map(({ href, ...t }) => t) };
    almost(JSON.parse(JSON.stringify(got)), v.plan, "plan " + name);
  }
});

test("단어 진행·오늘 큐가 파이썬과 같다 (Ward 와 TS 앱 srs)", async () => {
  const { W } = loaded();
  await wardInit(W);
  const lp = W.Ward.levelProgress().map(p => ({ level: p.level, tiers: p.tiers, total: p.total, seen: p.seen, mastered: p.mastered, due: p.due }));
  almost(JSON.parse(JSON.stringify(lp)), E.level_progress, "level_progress");
  const q = W.Ward.queue({ startLevel: 3, dailyNew: 20 });
  same(q.due.map(w => w.id), E.vocab_queue_default.due, "due");
  same(q.new.map(w => w.id), E.vocab_queue_default.new, "new");
});

test("오답노트: 풀이를 순서대로 되풀이하면 파이썬과 같은 상태(틀림 수·연속 정답·졸업)", () => {
  const { W } = sandbox({ parts: ALL, words: false });
  for (const p of ALL) W.Bank.setItems(p, W.TS_DATA["p" + p]);
  for (const [qkey, chosen, ts] of FX.events) {
    const q = W.Bank.question(qkey);
    W.TSStore.recordAttempt(1, q, chosen, null, ts);
  }
  for (const status of ["open", "cleared"]) {
    const got = W.TSStore.wrongNotes(status).map(n => { const { memo, ...r } = n; return r; });
    const want = E.notes[status].map(n => { const { memo, ...r } = n; return r; });
    const k = n => n.qkey;
    almost(JSON.parse(JSON.stringify(got.sort((a, b) => (k(a) < k(b) ? -1 : 1)))), want.sort((a, b) => (k(a) < k(b) ? -1 : 1)), "notes " + status);
  }
  assert.ok(E.notes.cleared.length > 0 && E.notes.open.length > 0, "픽스처에 졸업·복습 중이 둘 다 있어야 의미가 있다");
});

test("오답노트 규칙: 같은 날 반복은 한 번으로 세고 다른 날 2번 맞혀야 졸업, 무응답은 기록하지 않음", () => {
  const { W } = sandbox({ parts: [5], words: false });
  W.Bank.setItems(5, W.TS_DATA.p5);
  const q = W.Bank.questions(W.Bank.itemsOf(5)[0])[0];
  const wrong = (q.answer + 1) % 4;
  const T = W.TSStore;
  assert.strictEqual(T.recordAttempt(1, q, -1, null, "2026-10-01T10:00:00"), false);
  assert.strictEqual(Object.keys(T.notes()).length, 0, "무응답은 오답노트에 안 들어감");
  T.recordAttempt(1, q, wrong, null, "2026-10-01T10:00:00");
  assert.strictEqual(T.notes()[q.qkey].status, "open");
  T.recordAttempt(1, q, q.answer, null, "2026-10-02T10:00:00");
  assert.strictEqual(T.notes()[q.qkey].right_streak, 1);
  T.recordAttempt(1, q, q.answer, null, "2026-10-02T11:00:00");              // 같은 날 또 맞힘 → 그대로
  assert.strictEqual(T.notes()[q.qkey].right_streak, 1);
  assert.strictEqual(T.notes()[q.qkey].status, "open");
  T.recordAttempt(1, q, q.answer, null, "2026-10-03T09:00:00");              // 다른 날 → 졸업
  assert.strictEqual(T.notes()[q.qkey].status, "cleared");
  T.recordAttempt(1, q, wrong, null, "2026-10-04T09:00:00");                 // 다시 틀리면 열림
  same([T.notes()[q.qkey].status, T.notes()[q.qkey].right_streak, T.notes()[q.qkey].wrong_count], ["open", 0, 2]);
  T.setNoteStatus(q.qkey, "cleared");
  assert.strictEqual(T.wrongNotes("cleared").length, 1);
  T.setNoteMemo(q.qkey, "x".repeat(3000));
  assert.strictEqual(T.notes()[q.qkey].memo.length, 2000);
  assert.throws(() => T.setNoteStatus(q.qkey, "bad"));
});

test("세션 마감 계산: 저장된 모든 세션을 다시 마감해도 파이썬 결과와 같다 (LC/RC 합계·추정 점수)", () => {
  const { W } = loaded();
  let n = 0;
  for (const s of FX.backup.ts.sessions.filter(x => x.finished_at)) {
    W.TSStore.updateSession(s.id, { finished_at: null });
    const r = W.Sessions.finishSession(s.id, s.duration_sec);
    for (const k of ["total", "correct", "lc_total", "lc_correct", "rc_total", "rc_correct", "lc_est", "rc_est", "total_est", "duration_sec"])
      assert.strictEqual(r[k], s[k], `세션 ${s.id}(${s.mode}/${s.variant}) ${k}`);
    n++;
  }
  assert.ok(n >= 40, "세션 수 " + n);
  assert.ok(FX.backup.ts.sessions.some(s => s.variant === "full" && s.total_est !== null), "실전 모의고사 포함");
});

test("모의고사 구성: 실전 200문항(파트별 실제 구성), 중복·섞임 없음, 미니/하프 크기", async () => {
  const { W } = loaded();
  W.TSStore.reset();
  const rng = W.TSU.makeRng(7);
  const sid = await W.Sessions.startMock("full", rng);
  const s = W.Sessions.getSession(sid);
  const perPart = {};
  let total = 0;
  for (const r of s.items) { const it = W.Bank.item(r); const n = W.Bank.questions(it).length; perPart[it.part] = (perPart[it.part] || 0) + n; total += n; }
  same(perPart, { 1: 6, 2: 25, 3: 39, 4: 30, 5: 30, 6: 16, 7: 54 });
  assert.strictEqual(total, 200);
  assert.strictEqual(new Set(s.items).size, s.items.length, "중복 없음");
  const p7 = s.items.filter(r => W.Bank.item(r).part === 7).map(r => W.Bank.item(r));
  same(["single", "double", "triple"].map(k => p7.filter(i => i.kind === k).reduce((a, i) => a + i.questions.length, 0)), [29, 10, 15]);
  assert.strictEqual(s.time_limit, 75 * 60);
  const g3 = s.items.filter(r => W.Bank.item(r).part === 3).slice(-3).every(r => W.Bank.item(r).graphic);
  assert.ok(g3, "Part 3 마지막 3세트는 시각 자료 문제");
  const g4 = s.items.filter(r => W.Bank.item(r).part === 4).slice(-2).every(r => W.Bank.item(r).graphic);
  assert.ok(g4, "Part 4 마지막 2세트는 시각 자료 문제");
  // 같은 시드면 같은 문제 (재현성)
  const sid2 = await W.Sessions.startMock("full", W.TSU.makeRng(7));
  same(W.Sessions.getSession(sid2).items, s.items);
  for (const [key, want] of [["mini", { 1: 2, 2: 6, 3: 6, 4: 6, 5: 10, 6: 4 }], ["half", { 1: 3, 2: 12, 3: 18, 4: 15, 5: 15, 6: 8 }]]) {
    const sd = W.Sessions.getSession(await W.Sessions.startMock(key, W.TSU.makeRng(3)));
    const pp = {};
    for (const r of sd.items) { const it = W.Bank.item(r); pp[it.part] = (pp[it.part] || 0) + W.Bank.questions(it).length; }
    for (const [p, n] of Object.entries(want)) assert.ok(Math.abs(pp[p] - n) <= 1, `${key} Part ${p}: ${pp[p]} vs ${n}`);
  }
});

test("진단·연습·복습 세션 만들기와 채점 → 마감 흐름", async () => {
  const { W } = loaded();
  W.TSStore.reset();
  const B = W.Bank, Se = W.Sessions;
  // 진단: 구성표대로 (Part 1~5 는 정확히, Part 7 은 묶음 크기만큼 오차)
  const d = Se.getSession(await Se.startDiagnostic(W.TSU.makeRng(1)));
  const dp = {};
  for (const r of d.items) dp[B.item(r).part] = (dp[B.item(r).part] || 0) + B.questions(B.item(r)).length;
  assert.ok(dp[1] === 3 && dp[2] === 10 && dp[5] === 13, JSON.stringify(dp));
  assert.ok(dp[3] >= 9 && dp[4] >= 6 && dp[7] >= 4 && !dp[6], JSON.stringify(dp));
  assert.strictEqual(d.time_limit, 12 * 60);
  // 연습: 조건에 맞는 문제만, 요청 문항 수 이상
  const sid = await Se.startPractice(5, 3, "품사", 6, W.TSU.makeRng(2));
  const s = Se.getSession(sid);
  assert.strictEqual(s.requested, 6);
  assert.ok(s.items.every(r => B.item(r).level === 3 && B.item(r).type === "품사"));
  assert.strictEqual(s.items.length, 6);
  // 채점: 정답을 고르면 정답, 같은 문제 두 번 채점해도 기록은 늘지 않음
  const ref = s.items[0], q = B.questions(B.item(ref))[0];
  const r1 = Se.gradeItem(sid, ref, [{ qidx: 0, chosen: q.answer, elapsed_ms: 1200 }]);
  assert.strictEqual(r1.results[0].correct, true);
  assert.strictEqual(r1.questions[0].answer, q.answer);
  const r2 = Se.gradeItem(sid, ref, [{ qidx: 0, chosen: (q.answer + 1) % 4, elapsed_ms: 99 }]);
  assert.strictEqual(r2.results[0].correct, true, "이미 채점한 문제는 처음 결과를 돌려줌");
  assert.strictEqual(Se.sessionAttempts(sid).length, 1);
  assert.throws(() => Se.gradeItem(sid, "5:없음", []), /이 세션의 문제가 아닙니다/);
  assert.throws(() => Se.gradeItem(sid, ref, "x"), /답안 형식/);
  // 틀린 문제 → 오답노트 → 복습 세션은 그 문제만
  const ref2 = s.items[1], q2 = B.questions(B.item(ref2))[0];
  Se.gradeItem(sid, ref2, [{ qidx: 0, chosen: (q2.answer + 1) % 4 }]);
  assert.strictEqual(W.TSStore.wrongNotes("open").length, 1);
  const done = Se.submitSession(sid, { duration_sec: 90 });
  assert.strictEqual(done.total, 2); assert.strictEqual(done.correct, 1); assert.strictEqual(done.total_est, null, "연습은 점수를 추정하지 않음");
  assert.strictEqual(Se.submitSession(sid, {}).finished_at, done.finished_at, "이미 끝낸 세션은 그대로");
  const rv = Se.getSession(await Se.startReview(null, 10));
  same(rv.items, [ref2]);
  await assert.rejects(() => Se.startPractice(9, null, null, 5), /알 수 없는 파트/);
  await assert.rejects(() => Se.startPractice(5, 3, "없는유형", 5), /조건에 맞는 문제가 없습니다/);
  W.TSStore.reset();
  await assert.rejects(() => Se.startReview(null, 10), /조건에 맞는 문제가 없습니다/);
});

test("모의고사 제출: 답 안 한 문항은 무응답, 절반 미만이면 점수 추정 안 함, 시간 제한은 세션에 저장", async () => {
  const { W } = loaded();
  W.TSStore.reset();
  const B = W.Bank, Se = W.Sessions;
  const sid = await Se.startMock("mini", W.TSU.makeRng(11));
  const s = Se.getSession(sid);
  assert.strictEqual(s.time_limit, 18 * 60);
  const items = {};
  for (const r of s.items.slice(0, 3)) items[r] = B.questions(B.item(r)).map(q => ({ qidx: q.qidx, chosen: q.answer, elapsed_ms: 1000 }));
  const out = Se.submitSession(sid, { items, duration_sec: 600 });
  assert.strictEqual(out.total_est, null, "절반도 안 풀고 낸 시험은 점수 추정 안 함");
  const atts = Se.sessionAttempts(sid);
  assert.ok(atts.some(a => a.c === -1) && atts.some(a => a.c >= 0));
  assert.strictEqual(W.TSStore.wrongNotes("open").length, 0, "무응답은 오답노트에 안 들어감");
  // 전부 정답 → 만점 구간
  const sid2 = await Se.startMock("half", W.TSU.makeRng(12));
  const it2 = {};
  for (const r of Se.getSession(sid2).items) it2[r] = B.questions(B.item(r)).map(q => ({ qidx: q.qidx, chosen: q.answer }));
  const o2 = Se.submitSession(sid2, { items: it2, duration_sec: 5000 });
  assert.ok(o2.total_est >= 900 && o2.lc_est >= 450 && o2.rc_est >= 450, "전부 정답: " + o2.total_est);
  // 이어서 풀기: 시작만 한 모의고사는 목록에 나오고, 끝내면 사라진다
  const sid3 = await Se.startMock("mini", W.TSU.makeRng(13));
  W.Stats.setToday(() => W.TSU.todayStr());
  same(W.Stats.unfinishedSessions().map(x => x.id), [sid3]);
  Se.submitSession(sid3, { items: {}, duration_sec: 10 });
  assert.strictEqual(W.Stats.unfinishedSessions().length, 0);
});

test("안 푼 문제 먼저: 푼 문제는 뒤로 밀리고, 안 푼 모의고사 가능 횟수가 줄어든다", async () => {
  const { W } = loaded();
  W.TSStore.reset();
  W.Stats.setToday(() => W.TSU.todayStr());
  const B = W.Bank, Se = W.Sessions;
  const before = Se.freshMockCapacity("full");
  assert.ok(before.times >= 3, "처음에는 안 푼 문제로 여러 회 가능: " + JSON.stringify(before));
  const sid = await Se.startPractice(5, null, null, 100, W.TSU.makeRng(5));
  const first = Se.getSession(sid).items;
  for (const r of first) Se.gradeItem(sid, r, [{ qidx: 0, chosen: 0 }]);
  const sid2 = await Se.startPractice(5, null, null, 100, W.TSU.makeRng(6));
  const second = Se.getSession(sid2).items;
  assert.strictEqual(second.filter(r => first.includes(r)).length, 0, "100문항을 풀었으면 다음 100문항은 안 푼 것으로");
  const after = Se.freshMockCapacity("full");
  assert.ok(after.times <= before.times);
  const mock = Se.getSession(await Se.startMock("full", W.TSU.makeRng(1)));
  assert.strictEqual(mock.seen_before, mock.items.filter(r => first.includes(r)).length);
});

test("저장소: 백업 내보내기 → 바꾸기/합치기, 세션 번호 다시 매김, 확장 컬렉션", () => {
  const { W } = sandbox({ words: false });
  const T = W.TSStore;
  T.importData(FX.backup.ts, "replace");
  const n0 = { s: T.sessions().length, a: T.attempts().length, n: Object.keys(T.notes()).length };
  assert.strictEqual(n0.s, FX.backup.ts.sessions.length);
  const dump = T.exportData();
  T.importData(dump, "merge");                                    // 같은 기록을 합쳐도 늘지 않음
  same([T.sessions().length, T.attempts().length, Object.keys(T.notes()).length], [n0.s, n0.a, n0.n]);
  // 다른 기록 합치기: 번호가 겹치는 세션은 새 번호를 받고 풀이 기록이 따라간다
  const other = { settings: {}, sessions: [{ id: 1, created_at: "2030-01-01T10:00:00", mode: "practice", variant: null, part: 5, level: null, items: ["5:x"], finished_at: "2030-01-01T10:10:00", total: 1, correct: 1 }],
                  attempts: [{ s: 1, k: "5:x:0", c: 0, o: 1, m: 100, t: "2030-01-01T10:01:00", l: 1, y: "품사" }], notes: {}, ext: { toefl_attempts: [{ id: 1, score: 4 }] } };
  const r = T.importData(other, "merge");
  assert.strictEqual(r.added.sessions, 1); assert.strictEqual(r.added.attempts, 1);
  const added = T.sessions().find(s => s.created_at === "2030-01-01T10:00:00");
  assert.ok(added.id > n0.s && added.id !== 1);
  assert.strictEqual(T.attemptsOf(added.id).length, 1);
  T.importData(other, "merge");                                   // 두 번 합쳐도 그대로
  assert.strictEqual(T.sessions().length, n0.s + 1);
  assert.strictEqual(T.col("toefl_attempts").all().length, 1);
  // 확장 컬렉션
  const c = T.col("opic_attempts");
  const rec = c.add({ answer: "hello" });
  assert.strictEqual(rec.id, 1);
  assert.strictEqual(c.add({ answer: "x" }).id, 2);
  c.update(1, { answer: "hi" });
  assert.strictEqual(c.all()[0].answer, "hi");
  c.remove(2);
  assert.strictEqual(c.all().length, 1);
  assert.ok(T.exportData().ext.opic_attempts.length === 1);
  // 잘못된 설정 키는 무시, 값은 문자열
  T.setSettings({ target_score: 850, nope: "x" });
  assert.strictEqual(T.settings().target_score, "850"); assert.ok(!("nope" in T.settings()));
  T.reset();
  assert.strictEqual(T.sessions().length, 0);
});

test("저장소: 다른 탭이 저장한 내용 위에 이어서 쓴다 (서로 덮어쓰지 않음)", () => {
  const a = sandbox({ words: false });
  // 같은 localStorage(mem)를 쓰는 두 번째 '탭'
  const ctx2 = vm.createContext({ window: { WARD_DATA: {} }, localStorage: { getItem: k => (k in a.mem ? a.mem[k] : null), setItem: (k, v) => { a.mem[k] = String(v); }, removeItem: k => { delete a.mem[k]; } },
    document: {}, console, Date, Math, JSON, Set, Map, Object, Array, Number, String, Promise, Error });
  for (const f of ["js/tsutil.js", "js/tsstore.js"]) vm.runInContext(fs.readFileSync(path.join(root, f), "utf8"), ctx2);
  const T1 = a.W.TSStore, T2 = ctx2.window.TSStore;
  const s1 = T1.createSession({ mode: "practice", items: ["5:p5-001"], part: 5 });
  const s2 = T2.createSession({ mode: "practice", items: ["5:p5-002"], part: 5 });
  assert.notStrictEqual(s1, s2, "서로 다른 번호");
  assert.strictEqual(T1.sessions().length >= 1, true);
  T1.reload();
  assert.strictEqual(T1.sessions().length, 2);
});

test("Planner.currentScore: 직접 입력이 더 최근이면 직접 입력, 아니면 추정 점수", () => {
  const { W } = loaded();
  const est = W.Stats.latestEstimate();
  same(W.Planner.currentScore({ current_score: "", current_score_at: "" }), [est.total_est, "추정 (" + (est.mode === "diagnostic" ? "진단" : "모의고사") + ")"]);
  same(W.Planner.currentScore({ current_score: "555", current_score_at: "2099-01-01T00:00:00" }), [555, "직접 입력"]);
  same(W.Planner.currentScore({ current_score: "abc" }).slice(0, 1), [est.total_est]);
});

test("sw.js 의 캐시 목록: 모든 파일이 실제로 있다, 화면이 읽는 스크립트는 전부 목록에 있다", () => {
  const sw = fs.readFileSync(path.join(root, "sw.js"), "utf8");
  const assets = [...sw.match(/const ASSETS = \[([\s\S]*?)\];/)[1].matchAll(/"([^"]+)"/g)].map(m => m[1]).filter(a => a !== "./");
  for (const a of assets) assert.ok(fs.existsSync(path.join(root, a)), `없는 파일: ${a}`);
  for (const f of fs.readdirSync(root).filter(f => f.endsWith(".html"))) {
    assert.ok(assets.includes(f), `${f} 이 sw.js 목록에 없음`);
    for (const m of fs.readFileSync(path.join(root, f), "utf8").matchAll(/<script src="([^"]+)"/g)) {
      assert.ok(fs.existsSync(path.join(root, m[1])), `${f}: 없는 스크립트 ${m[1]}`);
      assert.ok(assets.includes(m[1]), `${f}: ${m[1]} 이 sw.js 목록에 없음`);
    }
  }
});

test("메뉴 정의: 메뉴 항목의 링크가 실제 파일을 가리키거나 '준비 중'으로 표시된다", () => {
  const { W } = sandbox({ words: false });
  W.TSNav = undefined;
  const nav = (() => { const c = vm.createContext({ window: {}, console }); vm.runInContext(fs.readFileSync(path.join(root, "js/exams.js"), "utf8"), c); return c.window.TSNav; })();
  for (const [key, items] of Object.entries(nav.MENUS)) {
    for (const it of items) {
      const file = it.href.split("#")[0].split("?")[0];
      if (it.ready === false) continue;
      assert.ok(fs.existsSync(path.join(root, file)), `${key} / ${it.label}: ${file} 없음 (ready:false 로 표시해야 함)`);
    }
  }
  for (const e of Object.values(nav.EXAMS)) assert.ok(fs.existsSync(path.join(root, e.href)), e.href);
  assert.ok(nav.matches({ pages: ["solve:practice"] }, "solve:practice") && !nav.matches({ pages: ["solve:practice"] }, "solve:mock"));
  assert.ok(nav.matches({ pages: ["toefl-practice:r_"] }, "toefl-practice:r_reading"));
});

test("백업: 단어+시험 한 파일 왕복, 단어만 든 예전 백업은 시험 기록을 지우지 않는다, 합치기는 중복 없음", async () => {
  const s = sandbox({ parts: [], words: true });
  s.run("js/backup.js");
  const { W } = s;
  await W.Ward.init();
  W.Ward.importJSON(JSON.stringify(FX.backup), "replace");
  W.TSStore.importData(FX.backup.ts, "replace");
  const text = W.Backup.exportText();
  const obj = JSON.parse(text);
  assert.ok(obj.cards && obj.ts && obj.v === 2 && obj.app === "ts-word");
  const sum = W.Backup.summary();
  assert.strictEqual(sum.sessions, FX.backup.ts.sessions.length);
  // 지운 뒤 복원
  W.Ward.reset(); W.TSStore.reset();
  const r = W.Backup.importText(text, "replace");
  assert.strictEqual(r.ts.sessions, FX.backup.ts.sessions.length);
  assert.strictEqual(Object.keys(JSON.parse(W.Ward.exportJSON()).cards).length, Object.keys(FX.backup.cards).length);
  // 단어만 든 예전 백업(replace)은 시험 기록을 건드리지 않는다
  const old = JSON.stringify({ app: "ts-word", v: 1, settings: {}, cards: {}, log: [], quiz: [] });
  W.Backup.importText(old, "replace");
  assert.strictEqual(W.TSStore.sessions().length, FX.backup.ts.sessions.length, "시험 기록 유지");
  assert.strictEqual(Object.keys(JSON.parse(W.Ward.exportJSON()).cards).length, 0, "단어 기록은 파일대로 바뀜");
  // 합치기 두 번 → 그대로
  W.Backup.importText(text, "merge"); W.Backup.importText(text, "merge");
  assert.strictEqual(W.TSStore.attempts().length, FX.backup.ts.attempts.length);
  assert.throws(() => W.Backup.importText("{}", "merge"), /백업 파일 모양/);
  assert.throws(() => W.Backup.importText("[1]", "merge"), /백업 파일 모양/);
});
