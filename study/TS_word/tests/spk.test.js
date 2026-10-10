// node --test — 토익스피킹·오픽(js/spk-core.js)이 TS 앱(Python core/speaking.py)과 같은 결과인지 확인한다.
// 정답은 tools/make_spk_fixtures.py 가 TS 앱의 실제 코드로 계산해 둔 tests/spk_fixtures.json 이다.
const fs = require("fs"), vm = require("vm"), path = require("path"), assert = require("assert"), crypto = require("crypto");
const test = require("node:test");
const root = path.join(__dirname, "..");
const FX = JSON.parse(fs.readFileSync(path.join(__dirname, "spk_fixtures.json"), "utf8"));

function sandbox() {
  const mem = {};
  const win = { WARD_DATA: {} };
  const ctx = vm.createContext({
    window: win, localStorage: { getItem: k => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: k => { delete mem[k]; } },
    document: {}, console, Date, Math, JSON, Set, Map, Object, Array, Number, String, Promise, Error, RegExp, parseInt, isNaN, Infinity, Symbol,
  });
  const run = f => vm.runInContext(fs.readFileSync(path.join(root, f), "utf8"), ctx, { filename: f });
  for (const f of ["js/tsutil.js", "js/tsstore.js", "js/spk-core.js", "data/speaking-tsp.js", "data/speaking-opic.js"]) run(f);
  win.Spk.setTsp(win.SPK_DATA.tsp);
  win.Spk.setOpic(win.SPK_DATA.opic);
  return win;
}
const same = (a, b, msg) => assert.strictEqual(JSON.stringify(a), JSON.stringify(b), msg);
function canon(o) {
  if (Array.isArray(o)) return "[" + o.map(canon).join(",") + "]";
  if (o && typeof o === "object") return "{" + Object.keys(o).sort().filter(k => o[k] !== undefined).map(k => JSON.stringify(k) + ":" + canon(o[k])).join(",") + "}";
  return JSON.stringify(o);
}
const sha = o => crypto.createHash("sha1").update(canon(o), "utf8").digest("hex");
function almost(a, b, msg, p = "") {
  if (typeof a === "number" && typeof b === "number") return assert.ok(Math.abs(a - b) < 1e-9, `${msg} ${p}: ${a} != ${b}`);
  if (a === null || b === null || typeof a !== "object" || typeof b !== "object") return assert.strictEqual(a, b, `${msg} ${p}: ${JSON.stringify(a)} != ${JSON.stringify(b)}`);
  assert.deepStrictEqual(Object.keys(a).sort(), Object.keys(b).sort(), `${msg} ${p}: 키 불일치`);
  for (const k of Object.keys(a)) almost(a[k], b[k], msg, p + "." + k);
}
const pick = (o, keys) => Object.fromEntries(keys.map(k => [k, o[k] === undefined ? null : o[k]]));

test("상수: 과제 정의·채점 기준·레벨/등급 표가 TS 앱과 같다", () => {
  const W = sandbox(), S = W.Spk, C = FX.consts;
  same(S.TSP_TASKS, C.TSP_TASKS); same(S.TSP_LEVELS, C.TSP_LEVELS); same(S.TSP_TARGETS, C.TSP_TARGETS);
  same(S.TSP_RUBRIC, C.TSP_RUBRIC); same(S.TSP_DIRECTIONS, C.TSP_DIRECTIONS); assert.strictEqual(S.TSP_RAW_MAX, C.TSP_RAW_MAX);
  same(S.OPIC_TOPICS, C.OPIC_TOPICS); same(S.SURVEY_GROUPS, C.SURVEY_GROUPS); same(S.OPIC_KINDS, C.OPIC_KINDS);
  same(S.OPIC_GRADES, C.OPIC_GRADES); same(S.OPIC_GRADE_NAME, C.OPIC_GRADE_NAME); same(S.OPIC_RUBRIC, C.OPIC_RUBRIC);
  same(S.OPIC_LEVELS, C.OPIC_LEVELS); same(S.OPIC_TARGETS, C.OPIC_TARGETS); assert.strictEqual(S.OPIC_MINUTES, C.OPIC_MINUTES);
  assert.strictEqual(S.TSP_RAW_MAX, 35);
});

test("점수·레벨·등급 환산표: 경계값까지 TS 앱과 같다 (반올림은 파이썬 방식)", () => {
  const S = sandbox().Spk;
  for (const [raw, exp] of FX.tables.score) assert.strictEqual(S.tspScore(raw), exp, `raw ${raw}`);
  for (const [sc, exp] of FX.tables.level) same(S.tspLevel(sc), exp, `score ${sc}`);
  for (const [p, w, exp] of FX.tables.grade) assert.strictEqual(S.opicGrade(p, w), exp, `avg ${p} words ${w}`);
  assert.strictEqual(S.tspScore(17.5), 100, "17.5/35*20 = 10 → 100점");
  assert.strictEqual(S.tspScore(35), 200);
});

test("문제 개수: 은행이 TS 앱과 같다", () => {
  const S = sandbox().Spk;
  for (const [t, n] of Object.entries(FX.counts.tsp)) assert.strictEqual(S.tspItems(t).length, n, t);
  assert.strictEqual(S.opicQ().length, FX.counts.opic_q);
  assert.strictEqual(S.opicRp().length, FX.counts.opic_rp);
});

test("단계(steps): 모든 문제(토익스피킹·오픽·롤플레이)에서 TS 앱과 완전히 같다 (정규화 JSON 해시)", () => {
  const S = sandbox().Spk;
  let n = 0;
  for (const task of S.TSP_ORDER) for (const it of S.tspItems(task)) {
    const exp = FX.digests.tsp[`${task}|${it.id}`];
    assert.ok(exp, `정답표에 없음 ${task} ${it.id}`);
    assert.strictEqual(sha(S.tspSteps(task, it)), exp[0], `${task} ${it.id} (연습)`);
    assert.strictEqual(sha(S.tspSteps(task, it, 4)), exp[1], `${task} ${it.id} (모의고사 4번)`);
    n++;
  }
  for (const q of S.opicQ()) {
    const exp = FX.digests.opic_q[q.id];
    assert.strictEqual(sha(S.opicQStep(q, "라벨", true)), exp[0], q.id);
    assert.strictEqual(sha(S.opicQStep(q, "Question 3", false)), exp[1], q.id);
    n++;
  }
  for (const r of S.opicRp()) {
    const exp = FX.digests.opic_rp[r.id];
    assert.strictEqual(sha(S.opicRpSteps(r, null, true)), exp[0], r.id);
    assert.strictEqual(sha(S.opicRpSteps(r, 5, false)), exp[1], r.id);
    n++;
  }
  assert.strictEqual(n, Object.values(FX.digests).reduce((a, d) => a + Object.keys(d).length, 0));
});

test("모의고사 구성: 토익스피킹 11문항(읽기2·사진2·질문3·표3·의견1)이고 모양이 TS 앱이 만들 수 있는 것뿐이다", () => {
  const W = sandbox(), S = W.Spk;
  const allowed = new Set(FX.shapes.tsp);
  for (let seed = 0; seed < 60; seed++) {
    const plan = S.tspMockPlan(W.TSU.makeRng(seed));
    const steps = plan.flatMap(u => u.steps);
    assert.strictEqual(steps.length, 11);
    assert.ok(allowed.has(steps.map(s => `${s.ref.task}:${s.ref.qidx}`).join("|")));
    steps.forEach((s, i) => assert.strictEqual(s.label, `Question ${i + 1} of 11`));
  }
  assert.strictEqual(S.TSP_RAW_MAX, 35);
});

test("모의고사 구성: 오픽 난이도별 문항 수(15/13)와 유형 순서가 TS 앱이 만들 수 있는 모양이다", () => {
  const W = sandbox(), S = W.Spk;
  const surveys = [[], ["home", "movie", "park", "jogging", "cooking", "travel_dom"], ["pets", "gym", "beach", "tv"]];
  for (let level = 1; level <= 6; level++) {
    const allowed = new Set(FX.shapes.opic[String(level)]);
    for (const sv of surveys) for (let seed = 0; seed < 40; seed++) {
      const plan = S.opicMockPlan(sv, level, W.TSU.makeRng(seed));
      const steps = plan.flatMap(u => u.steps);
      const kinds = plan.flatMap(u => { const it = S.item(u.task, u.item_id); return u.steps.map(s => (u.task === "opic_q" ? it.kind : it.steps[s.ref.qidx].kind)); });
      assert.ok(allowed.has(kinds.join("|")), `난이도 ${level}: ${kinds.join("|")}`);
      assert.strictEqual(steps.length, level >= 3 ? 15 : 13, `난이도 ${level} 문항 수`);
      assert.strictEqual(kinds[0], "intro");
      steps.forEach((s, i) => assert.strictEqual(s.label, `Question ${i + 1}`));
      const rp = plan.find(u => u.task === "opic_rp");
      assert.strictEqual(rp.steps.length, 3);
      const ids = plan.map(u => u.item_id);
      assert.strictEqual(new Set(ids).size, ids.length, "같은 문제가 두 번 나오지 않는다");
    }
  }
  // 설문 주제가 설정돼 있으면 설문 콤보는 그 주제에서만 (돌발·거주 제외)
  const sv = ["pets", "gym", "beach", "tv"];
  for (let seed = 0; seed < 40; seed++) {
    const plan = S.opicMockPlan(sv, 4, W.TSU.makeRng(seed));
    const topics = plan.filter(u => u.task === "opic_q").map(u => S.item("opic_q", u.item_id).topic);
    const survey = topics.filter(t => S.OPIC_TOPICS[t][2]);
    assert.ok(survey.every(t => sv.includes(t)), survey.join());
  }
});

test("기록 다시 재생: 통계·약한 문항·추세·모의고사 결과가 TS 앱과 같다", () => {
  const W = sandbox(), S = W.Spk, E = FX.expected;
  const seq = FX.now_seq.slice();
  let i = 0;
  S.setNow(() => { assert.ok(i < seq.length, "시각 목록이 모자람"); return seq[i++]; });
  for (const ev of FX.events) {
    if (ev.kind === "record") {
      const saved = S.record(ev.exam, ev.rows, ev.mock_id);
      assert.strictEqual(saved.length, ev.saved, "저장된 행 수 (검증)");
    } else if (ev.kind === "create_mock") {
      const mid = S.createMock(ev.exam, ev.units.map(u => ({ ...u, steps: [] })), ev.settings);
      assert.ok(mid >= 1);
      assert.strictEqual(sha(S.expandPlan(ev.exam, ev.units)), ev.expected_plan_sha, "모의고사 계획(단계 포함)이 TS 앱과 같다");
    } else if (ev.kind === "finish_mock") {
      S.finishMock(ev.mid, ev.rows, ev.duration);
    }
  }
  assert.strictEqual(i, seq.length, "시각을 정확히 같은 횟수만큼 받았다");
  almost(S.tspTaskStats(), E.tsp_stats, "tsp_stats");
  assert.strictEqual(S.tspEstimate(S.tspTaskStats()), E.tsp_estimate);
  almost(S.opicStats(), E.opic_stats, "opic_stats");
  for (const t of S.TSP_ORDER) almost(S.weakItems("tsp", "tsp:" + t), E.weak.tsp[t], "weak " + t);
  almost(S.weakItems("opic", "opic_q"), E.weak.opic, "weak opic");
  for (const [k, v] of Object.entries(E.last_seen)) same(Object.fromEntries(Object.entries(S.lastSeen(k)).sort()), Object.fromEntries(Object.entries(v).sort()), "last_seen " + k);
  const keys = ["created_at", "task", "item_id", "qidx", "points", "max_points", "words", "seconds", "accuracy", "response", "mock_id"];
  same(S.recent("tsp", 12).map(r => pick(r, keys)), E.recent.tsp, "recent tsp");
  same(S.recent("opic", 12).map(r => pick(r, keys)), E.recent.opic, "recent opic");
  const hk = ["id", "created_at", "exam", "task", "item_id", "qidx", "points", "max_points", "words", "seconds", "accuracy", "mock_id", "response"];
  const [h1, n1] = S.history("tsp", 40, 0), [h2, n2] = S.history("opic", 15, 15);
  same(h1.map(r => pick(r, hk)), E.history.tsp[0], "history tsp"); assert.strictEqual(n1, E.history.tsp[1]);
  same(h2.map(r => pick(r, hk)), E.history.opic_page2[0], "history opic p2"); assert.strictEqual(n2, E.history.opic_page2[1]);
  almost(S.trend("tsp", null, 60, E.as_of), E.trend.tsp, "trend tsp");
  almost(S.trend("opic", null, 60, E.as_of), E.trend.opic, "trend opic");
  almost(S.trend("tsp", "tsp:read_aloud", 60, E.as_of), E.trend.tsp_read, "trend read");
  assert.ok(E.trend.tsp.length > 0 && S.trend("tsp", null, 365, E.as_of).length > E.trend.tsp.length, "60일 밖 기록은 추세에서 빠진다");
  const byKey = (a, b) => (a.task + a.item_id + a.points < b.task + b.item_id + b.points ? -1 : 1);       // SQL 은 순서를 정하지 않음
  almost(S.topicProgress().map(r => ({ task: r.task, item_id: r.item_id, points: r.points })).sort(byKey), E.topic_progress.slice().sort(byKey), "topic_progress");
  almost(S.listMocks("tsp"), E.mocks.tsp, "mocks tsp");
  almost(S.listMocks("opic"), E.mocks.opic, "mocks opic");
  for (const [mid, exp] of Object.entries(E.mock_full)) {
    const m = S.getMock(Number(mid));
    almost(pick(m, ["id", "exam", "created_at", "finished_at", "settings", "result", "score"]), exp, "mock " + mid);
  }
  for (const [t, id, k, exp] of E.question_of) assert.strictEqual(S.questionOf(t, id, k), exp, `question_of ${t} ${id}`);
  same(S.tspPractice("respond_questions", 5, null, true).map(u => u.item_id), E.practice_weak.tsp_respond_questions, "약한 문항 연습(토익스피킹)");
  same(S.opicPractice(null, null, 10, true, null, true).map(u => u.item_id), E.practice_weak.opic, "약한 문항 연습(오픽)");
  // 약한 문항은 마지막 회차 기준: 같은 문제를 다시 풀어 만점을 받으면 빠진다
  const w0 = Object.keys(S.weakItems("opic", "opic_q"));
  assert.ok(w0.length > 0);
  S.setNow(() => "2026-10-11T09:00:00");
  S.record("opic", [{ task: "opic_q", item_id: w0[0], qidx: 0, points: 5 }]);
  assert.ok(!(w0[0] in S.weakItems("opic", "opic_q")));
});

test("모의고사 채점 규칙: 안 한 문항은 토익스피킹 0점 / 오픽 1점, 오픽 자기소개는 평균에서 제외, 두 번 제출해도 한 번만", () => {
  const W = sandbox(), S = W.Spk;
  S.setNow(() => "2026-10-10T10:00:00");
  const plan = S.opicMockPlan([], 4, W.TSU.makeRng(3));
  const mid = S.createMock("opic", plan, { level: 4 });
  const steps = plan.flatMap(u => u.steps);
  const rows = steps.slice(0, 4).map(s => ({ ...s.ref, points: 5, words: 150 }));          // 첫 문항은 자기소개
  let m = S.finishMock(mid, rows, 600);
  const planned = steps.length - 1;
  assert.strictEqual(m.result.answered, 4); assert.strictEqual(m.result.total, steps.length);
  almost(m.result.avg, (3 * 5 + (planned - 3) * 1) / planned, "avg");          // 자기소개 제외, 나머지 3문항 5점 + 안 한 문항 1점
  assert.strictEqual(m.result.grade, S.opicGrade(m.result.avg, 150));
  const before = S.history("opic", 100, 0)[1];
  m = S.finishMock(mid, rows, 600);                                            // 다시 제출해도 그대로
  assert.strictEqual(S.history("opic", 100, 0)[1], before);
  assert.throws(() => S.finishMock(999, rows, 1), /모의고사가 없습니다/);
  const tp = S.tspMockPlan(W.TSU.makeRng(1));
  const tid = S.createMock("tsp", tp, { target: 140 });
  const ts = tp.flatMap(u => u.steps);
  assert.throws(() => S.finishMock(tid, [{ task: "opinion", item_id: "없는문제", qidx: 0, points: 3 }], 1), /채점한 답변이 없습니다/);
  const tm = S.finishMock(tid, ts.map(s => ({ ...s.ref, points: s.max })), 1100);
  assert.strictEqual(tm.result.score, 200); assert.strictEqual(tm.result.raw, 35); assert.strictEqual(tm.score, "200");
  const t2 = S.createMock("tsp", tp, {});
  const tm2 = S.finishMock(t2, ts.slice(0, 5).map(s => ({ ...s.ref, points: s.max })), 1);      // 5문항만 → 나머지 0점
  assert.strictEqual(tm2.result.raw, ts.slice(0, 5).reduce((a, s) => a + s.max, 0));
  assert.strictEqual(tm2.result.answered, 5);
});

test("추정 점수는 다섯 유형을 모두 연습해야 나온다, 오픽 등급은 답변 5개부터", () => {
  const W = sandbox(), S = W.Spk;
  S.setNow(() => "2026-10-10T10:00:00");
  assert.strictEqual(S.tspEstimate(S.tspTaskStats()), null);
  for (const t of S.TSP_ORDER) {
    const it = S.tspItems(t)[0];
    S.record("tsp", S.tspSteps(t, it).map(s => ({ ...s.ref, points: s.max })));
  }
  assert.strictEqual(S.tspEstimate(S.tspTaskStats()), 200);
  const q = S.opicQ().filter(x => x.kind === "describe").slice(0, 5);
  for (let i = 0; i < 4; i++) S.record("opic", [{ task: "opic_q", item_id: q[i].id, qidx: 0, points: 4, words: 120 }]);
  assert.strictEqual(S.opicStats().grade, null);
  S.record("opic", [{ task: "opic_q", item_id: q[4].id, qidx: 0, points: 4, words: 120 }]);
  assert.strictEqual(S.opicStats().grade, "IH");
});

test("백업: 말하기 기록(speaking_*)이 백업에 들어가고 합치기는 중복 없이 합친다", () => {
  const W = sandbox(), S = W.Spk;
  S.setNow(() => "2026-10-10T10:00:00");
  const it = S.tspItems("opinion")[0];
  S.record("tsp", [{ task: "opinion", item_id: it.id, qidx: 0, points: 4, words: 100 }]);
  const mid = S.createMock("tsp", S.tspMockPlan(W.TSU.makeRng(2)), {});
  const dump = W.TSStore.exportData();
  assert.strictEqual(dump.ext.speaking_attempts.length, 1);
  assert.strictEqual(dump.ext.speaking_mocks.length, 1);
  assert.ok(JSON.stringify(dump.ext.speaking_mocks[0]).length < 2500, "모의고사 계획은 문제 번호만 저장");
  W.TSStore.importData(dump, "merge"); W.TSStore.importData(dump, "merge");
  assert.strictEqual(W.TSStore.exportData().ext.speaking_attempts.length, 1);
  assert.ok(S.getMock(mid).plan.length === 7);
});
