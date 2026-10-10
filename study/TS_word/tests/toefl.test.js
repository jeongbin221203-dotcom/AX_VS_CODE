// node --test — 토플 기능(밴드 추정·반올림·오답 모으기·기록·모의고사 구성·끝내기)이 TS 앱(Python)과 같은 결과인지 확인한다.
// 정답은 tools/make_toefl_fixtures.py 가 TS 앱의 실제 코드(core/toefl.py)로 계산해 둔 tests/toefl_fixtures.json 이다.
const fs = require("fs"), vm = require("vm"), path = require("path"), assert = require("assert");
const test = require("node:test");
const root = path.join(__dirname, "..");
const FX = JSON.parse(fs.readFileSync(path.join(__dirname, "toefl_fixtures.json"), "utf8"));
const E = FX.expected;
const TASKS = Object.keys(E.tasks);

function sandbox() {
  const mem = {};
  const win = { WARD_DATA: {} };
  const ctx = vm.createContext({
    window: win, localStorage: { getItem: k => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: k => { delete mem[k]; } },
    document: {}, console, Date, Math, JSON, Set, Map, Object, Array, Number, String, Promise, Error, RegExp, parseInt, isNaN, Infinity, Symbol,
  });
  const run = f => vm.runInContext(fs.readFileSync(path.join(root, f), "utf8"), ctx, { filename: f });
  for (const f of ["js/tsutil.js", "js/tsstore.js", "js/toefl-core.js", "data/meta.js"]) run(f);
  return { W: win, run, mem };
}
const sorted = o => Object.fromEntries(Object.entries(o).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)));
const same = (a, b, msg) => assert.strictEqual(JSON.stringify(a), JSON.stringify(b), msg);
function almost(a, b, msg, p = "") {
  if (typeof a === "number" && typeof b === "number") return assert.ok(Math.abs(a - b) < 1e-9, `${msg} ${p}: ${a} != ${b}`);
  if (a === null || b === null || typeof a !== "object" || typeof b !== "object") return assert.strictEqual(a, b, `${msg} ${p}: ${JSON.stringify(a)} != ${JSON.stringify(b)}`);
  same(Object.keys(a).sort(), Object.keys(b).sort(), `${msg} ${p}: 키 불일치`);
  for (const k of Object.keys(a)) almost(a[k], b[k], msg, p + "." + k);
}
/** 문제 데이터를 모두 읽은 환경. withHistory: 파이썬이 쌓은 풀이 기록을 불러온다 (모의고사가 남긴 것은 뺌) */
function loaded({ withHistory = true, dropMock = true } = {}) {
  const s = sandbox();
  for (const t of TASKS) s.run(`data/toefl-${t}.js`);
  const { W } = s;
  for (const t of TASKS) W.Toefl.setItems(t, W.TS_DATA["tf_" + t]);
  W.Toefl.setClock(() => "2026-10-10T09:00:00", () => "2026-10-10");
  if (withHistory) {
    const ts = JSON.parse(JSON.stringify(FX.backup_ts));
    const att = ts.ext.toefl_attempts;
    if (dropMock) { att.splice(att.length - FX.n_mock_attempts, FX.n_mock_attempts); delete ts.ext.toefl_mocks; }
    W.TSStore.importData(ts, "replace");
  }
  return s;
}

test("과제·영역·표 상수가 TS 앱과 같다", () => {
  const { W } = sandbox();
  const T = W.Toefl;
  same(Object.keys(T.TASKS), TASKS, "과제 순서");
  for (const t of TASKS) for (const k of Object.keys(E.tasks[t])) assert.strictEqual(T.TASKS[t][k], E.tasks[t][k], `${t}.${k}`);
  same(T.SECTIONS, E.sections, "영역");
  same(T.CEFR, E.cefr, "CEFR");
  same(T.BAND_OLD, E.band_old, "예전 점수 표");
  same(T.LEVEL_BAND, E.level_band, "난이도 → 밴드");
  same(T.RUBRIC, E.rubric, "자기 채점 기준");
  const c = E.consts;
  assert.strictEqual(T.ADAPT_CUT, c.adapt_cut);
  same(T.M1_LEVELS, c.m1); same(T.HARD_LEVELS, c.hard); same(T.EASY_LEVELS, c.easy); same(T.MOCK_ORDER, c.order);
  same(T.READ_MODULE, c.read_module); same(T.LISTEN_MODULE, c.listen_module);
  assert.strictEqual(T.WRONG_CUT_AUTO, c.wrong_auto);
  assert.strictEqual(T.WRONG_CUT_SELF, c.wrong_self);
});

test("반올림: half_up 은 .25 를 올린다 (파이썬 round 와 다름)", () => {
  const { W } = sandbox();
  assert.strictEqual(W.Toefl.halfUp(5.25), 5.5);
  assert.strictEqual(W.Toefl.halfUp(5.24), 5);
  assert.strictEqual(W.Toefl.halfUp(4.75), 5);
  for (const c of E.half_cases) assert.strictEqual(W.Toefl.halfUp(c.x), c.out, `halfUp ${c.x}`);
});

test("난이도별 정답률 → 밴드 (band_from_levels) 무작위 400건이 파이썬과 같다", () => {
  const { W } = sandbox();
  for (const c of E.band_cases) assert.strictEqual(W.Toefl.bandFromLevels(c.acc, c.min_n), c.out, JSON.stringify(c));
  assert.strictEqual(W.Toefl.bandFromLevels({}), null);
  assert.strictEqual(W.Toefl.bandFromLevels({ 3: [3, 1] }), null, "4개 미만이면 추정 안 함");
  assert.strictEqual(W.Toefl.bandFromLevels({ 3: [4, 0.7] }), 4);
  assert.strictEqual(W.Toefl.bandFromLevels({ 3: [4, 0.5] }), 3.5);
});

test("종합 밴드: 네 영역이 모두 있어야 하고 평균을 0.5 단위로 올림 반올림", () => {
  const { W } = sandbox();
  for (const c of E.overall_cases) assert.strictEqual(W.Toefl.overallBand(c.bands), c.out, JSON.stringify(c.bands));
  assert.strictEqual(W.Toefl.overallBand({ R: 4, L: 4, S: 4, W: 4.5 }), 4, "평균 4.125 → 4");
  assert.strictEqual(W.Toefl.overallBand({ R: 4, L: 4, S: 4.5, W: 4.5 }), 4.5, "평균 4.25 → 4.5 (올림)");
  assert.strictEqual(W.Toefl.overallBand({ R: 4, L: 4, S: null, W: 5 }), null, "한 영역이라도 없으면 계산 안 함");
});

test("문제 데이터: 과제별 묶음 수가 TS 앱과 같고, 요약(meta)과도 맞다", () => {
  const { W } = loaded({ withHistory: false });
  const T = W.Toefl;
  let total = 0;
  for (const t of TASKS) {
    assert.strictEqual(T.itemsOf(t).length, E.counts[t].all, `${t} 묶음 수`);
    assert.strictEqual(W.TS_META.toefl.tasks[t].items, E.counts[t].all, `${t} meta`);
    assert.strictEqual(T.count(t), E.counts[t].all);
    for (const lv of [1, 2, 3, 4, 5]) {
      assert.strictEqual(T.count(t, lv), E.counts[t].levels[lv], `${t} 난이도 ${lv}`);
      assert.strictEqual(T.itemsOf(t).filter(it => it.level === lv).length, E.counts[t].levels[lv]);
    }
    total += E.counts[t].all;
  }
  assert.strictEqual(T.totalItems(), total);
  assert.strictEqual(W.TS_META.toefl.items, total);
  assert.ok(new Set(TASKS.flatMap(t => T.itemsOf(t).map(it => t + it.id))).size === total, "id 중복 없음");
});

test("모의고사 기록까지 모두 불러오면: 최종 밴드·종합·통계·최근 기록이 파이썬과 같다", () => {
  const full = loaded({ dropMock: false });
  const F = full.W.Toefl;
  almost(F.sectionBands(), E.mock.bands_after, "영역별 밴드");
  assert.strictEqual(F.overallBand(F.sectionBands()), E.mock.overall_after);
  almost(F.taskStats(), E.mock.task_stats_after, "과제별 통계");
  almost(F.recent(5), E.mock.recent_after, "최근 기록(모의고사 포함)");
  const mocks = F.listMocks();
  assert.strictEqual(mocks.length, 1, "TS 앱에서 가져온 끝난 모의고사 1번");
  almost(mocks[0].result, E.mock.result, "가져온 모의고사 결과");
  assert.strictEqual(mocks[0].plan, null);
});

test("모의고사 이전 상태: 밴드·통계·최근·오답·날짜별·마지막으로 푼 때가 파이썬과 같다", () => {
  const { W } = loaded();
  const T = W.Toefl;
  // 파이썬 expected 의 이 값들은 모의고사 끝내기 '이전'에 계산한 것이다
  almost(T.sectionBands(), E.bands, "영역별 밴드");
  assert.strictEqual(T.overallBand(T.sectionBands()), E.overall);
  almost(T.taskStats(), E.task_stats, "과제별 통계");
  almost(T.recent(12), E.recent, "최근 12");
  assert.strictEqual(T.recent(100000).length, E.recent_all.length);
  almost(T.recent(100000), E.recent_all, "최근 전체");
  almost(T.history(36500), E.history, "날짜별 기록");
  assert.strictEqual(T.lastDate(), E.last_date);
  for (const t of TASKS) {
    almost(sorted(T.lastSeen(t)), sorted(E.last_seen[t]), `마지막으로 푼 때 ${t}`);
    almost(sorted(T.wrongItems(t)), sorted(E.wrong[t]), `오답 ${t}`);
  }
  assert.ok(Object.values(E.wrong).some(w => Object.keys(w).length), "오답이 하나도 없으면 시험이 의미 없음");
  for (const [k, label] of Object.entries(E.labels)) {
    const [t, id] = k.split("|");
    assert.strictEqual(T.itemLabel(t, T.get(t, id)), label, `라벨 ${k}`);
  }
});

test("오래 안 푼 순 뽑기: 문제를 모두 서로 다른 시각에 푼 과제는 파이썬과 같은 순서", () => {
  const { W } = loaded();
  for (const [k, ids] of Object.entries(E.pick_oldest)) {
    const [level, n] = k.split("|");
    const got = W.Toefl.pick("s_interview", level === "null" ? null : Number(level), Number(n), W.TSU.makeRng(5));
    same(got.map(it => it.id), ids, `pick ${k}`);
  }
  // 안 푼 문제가 먼저 나온다
  const fresh = loaded({ withHistory: false });
  const T = fresh.W.Toefl;
  const items = T.itemsOf("w_email");
  T.record("w_email", items[0].id, items[0].level, [{ qidx: 0, score: 1 }]);
  const got = T.pick("w_email", null, items.length - 1, fresh.W.TSU.makeRng(1));
  assert.ok(!got.some(it => it.id === items[0].id), "푼 문제는 뒤로 밀린다");
});

test("모의고사 끝내기: 같은 답안이면 파이썬과 같은 밴드·세부, 한 번만 기록", () => {
  const s = loaded();
  const T = s.W.Toefl;
  let n = 0;                                                              // 파이썬처럼 기록할 때마다 7초씩 흐르는 시계
  const base = new Date(2026, 9, 9, 20, 0, 0).getTime();
  T.setClock(() => s.W.TSU.nowStr(new Date(base + 7000 * ++n)), () => "2026-10-10");
  const mid = T.createMock(4.5, s.W.TSU.makeRng(11));
  n = 0;
  const before = s.W.TSStore.col("toefl_attempts").all().length;
  const m = T.finishMock(mid, E.mock.payload);
  almost(m.result, E.mock.result, "결과");
  assert.strictEqual(m.r, E.mock.r); assert.strictEqual(m.l, E.mock.l); assert.strictEqual(m.s, E.mock.s);
  assert.strictEqual(m.w, E.mock.w); assert.strictEqual(m.total, E.mock.total);
  assert.strictEqual(m.plan, null, "끝난 시험은 계획을 버림");
  assert.ok(m.finished_at);
  assert.strictEqual(s.W.TSStore.col("toefl_attempts").all().length, before + FX.n_mock_attempts, "풀이 기록 개수");
  almost(T.sectionBands(), E.mock.bands_after, "끝낸 뒤 연습 밴드에도 반영");
  almost(T.taskStats(), E.mock.task_stats_after, "끝낸 뒤 통계");
  almost(T.recent(5), E.mock.recent_after, "끝낸 뒤 최근 기록");
  const again = T.finishMock(mid, { items: [] });                    // 다시 제출해도 그대로
  assert.strictEqual(again.total, E.mock.again_total);
  assert.strictEqual(s.W.TSStore.col("toefl_attempts").all().length, before + FX.n_mock_attempts, "중복 기록 없음");
  assert.strictEqual(T.listMocks().length, 1);
  assert.throws(() => T.finishMock(9999, { items: [] }), /모의고사가 없습니다/);
});

test("모의고사 끝내기: 잘못된 답안 모양은 아무것도 저장하지 않고 거부", () => {
  const s = loaded({ withHistory: false });
  const T = s.W.Toefl;
  const id = T.itemsOf("r_daily")[0].id;
  const mid = T.createMock(4, s.W.TSU.makeRng(2));
  assert.throws(() => T.finishMock(mid, { items: [{ task: "r_daily", item_id: id, results: [{ qidx: 0, score: 1 }] }, null] }), /답안 형식/);
  assert.throws(() => T.finishMock(mid, { items: [{ task: "r_daily", item_id: id, results: [5] }] }), /답안 형식/);
  assert.strictEqual(s.W.TSStore.col("toefl_attempts").all().length, 0);
  assert.strictEqual(T.getMock(mid).finished_at, null);
});

test("모의고사 구성: 모양·난이도·중복 없음·읽은 적 없는 문제 우선", () => {
  const s = loaded({ withHistory: false });
  const T = s.W.Toefl;
  for (const seed of [1, 2, 3, 4, 5]) {
    const plan = T.buildMock(4.5, s.W.TSU.makeRng(seed));
    same(plan.order, ["R", "L", "W", "S"]);
    const ids = [];
    const shape = {};
    const lv = (task, id) => T.get(task, id).level;
    for (const [k, sec] of Object.entries(plan.sections)) {
      if (sec.modules) {
        shape[k] = { modules: [sec.modules[0].length, { hard: sec.modules[1].hard.length, easy: sec.modules[1].easy.length }] };
        sec.modules[0].forEach(e => assert.strictEqual(lv(e.task, e.id), 3, `${k} 1모듈은 밴드 4`));
        sec.modules[1].hard.forEach(e => assert.ok([4, 5].includes(lv(e.task, e.id)), `${k} 어려운 모듈`));
        sec.modules[1].easy.forEach(e => assert.ok([1, 2].includes(lv(e.task, e.id)), `${k} 쉬운 모듈`));
        ids.push(...sec.modules[0], ...sec.modules[1].hard, ...sec.modules[1].easy);
      } else { shape[k] = { items: sec.items.length }; ids.push(...sec.items); }
    }
    same(shape, FX.plan_shape, "구성 개수가 TS 앱과 같다");
    assert.strictEqual(new Set(ids.map(e => e.task + "|" + e.id)).size, ids.length, "문제 중복 없음");
    assert.strictEqual(plan.level, 4, "목표 4.5 → 밴드 5 난이도(4)");
    // 안내(announcement)·강의(academic)가 한 개씩
    const talks = plan.sections.L.modules[0].filter(e => e.task === "l_talk").map(e => T.get("l_talk", e.id).kind).sort();
    same(talks, ["academic", "announcement"]);
    // 쓰기: 문장 만들기 10 + 이메일 1 + 토론 1, 말하기: 따라 말하기 + 인터뷰
    same(plan.sections.W.items.map(e => e.task).filter(t => t !== "w_sentence"), ["w_email", "w_discussion"]);
    same(plan.sections.S.items.map(e => e.task), ["s_repeat", "s_interview"]);
    // 목표별 난이도
    assert.strictEqual(T.buildMock(2, s.W.TSU.makeRng(1)).level, 1);
    assert.strictEqual(T.buildMock(6, s.W.TSU.makeRng(1)).level, 5);
  }
  // 거의 다 푼 과제에서는 안 푼 문제를 먼저 고른다
  const arts = T.itemsOf("r_academic").filter(it => it.level === 3);
  arts.slice(0, -1).forEach(it => T.record("r_academic", it.id, 3, [{ qidx: 0, score: 1 }]));
  const plan = T.buildMock(4.5, s.W.TSU.makeRng(9));
  assert.ok(plan.sections.R.modules[0].some(e => e.task === "r_academic" && e.id === arts[arts.length - 1].id), "안 푼 문제가 1모듈에 들어감");
});

test("모의고사 구성: 문제가 모자라면 가까운 난이도로 채움", () => {
  const s = loaded({ withHistory: false });
  const T = s.W.Toefl;
  T.setItems("r_words", T.itemsOf("r_words").filter(it => it.level === 3).slice(0, 1));         // 밴드 4 빈칸 문제 1개뿐
  const plan = T.buildMock(4.5, s.W.TSU.makeRng(3));
  assert.strictEqual(plan.sections.R.modules[0].filter(e => e.task === "r_words").length, 1);
  assert.strictEqual(plan.sections.R.modules[1].hard.filter(e => e.task === "r_words").length, 0, "더 채울 문제가 없으면 모자란 채로");
});

test("기록 저장: 점수 범위 보정·긴 답 자르기·시각이 같은 한 묶음, 용량 보호", () => {
  const s = loaded({ withHistory: false });
  const T = s.W.Toefl, col = s.W.TSStore.col("toefl_attempts");
  assert.strictEqual(T.record("r_daily", "x1", 2, [{ qidx: 0, score: 5, response: "a".repeat(6000) }, { qidx: 1, score: -1 }, { qidx: 2, score: "0.5" }]), 3);
  const rows = col.all();
  same(rows.map(r => r.score), [1, 0, 0.5]);
  assert.strictEqual(rows[0].response.length, 5000);
  assert.strictEqual(new Set(rows.map(r => r.created_at)).size, 1);
  assert.strictEqual(rows[0].created_at, "2026-10-10T09:00:00");
  const many = Array.from({ length: T.ATTEMPT_CAP + 300 }, (_, i) => ({ qidx: i, score: 1 }));
  T.record("r_daily", "x2", 2, many);
  assert.strictEqual(col.all().length, T.ATTEMPT_CAP, "넘치면 오래된 것부터 정리");
  assert.ok(col.all().every(r => r.item_id === "x2" || r.qidx >= 0));
});

test("날짜별 기록은 최근 days 일만", () => {
  const s = loaded({ withHistory: false });
  const T = s.W.Toefl;
  T.setClock(() => "2026-07-01T10:00:00", () => "2026-10-10");
  T.record("r_daily", "a", 2, [{ qidx: 0, score: 1 }]);
  T.setClock(() => "2026-10-05T10:00:00", () => "2026-10-10");
  T.record("r_daily", "a", 2, [{ qidx: 0, score: 0 }]);
  same(T.history(90).map(r => r.d), ["2026-10-05"]);
  same(T.history(120).map(r => r.d), ["2026-10-05", "2026-07-01"]);
});

test("백업·합치기: 토플 기록이 같은 레코드는 한 번만, 모의고사는 따로 보존", () => {
  const s = loaded();
  const S = s.W.TSStore;
  const n = S.col("toefl_attempts").all().length;
  const backup = S.exportData();
  S.importData(backup, "merge");
  assert.strictEqual(S.col("toefl_attempts").all().length, n, "합쳐도 늘지 않음");
  const t2 = loaded({ withHistory: false });
  t2.W.TSStore.importData(backup, "merge");
  assert.strictEqual(t2.W.TSStore.col("toefl_attempts").all().length, n);
  almost(t2.W.Toefl.sectionBands(), s.W.Toefl.sectionBands(), "합친 뒤에도 같은 밴드");
});

test("sw.js 의 캐시 목록에 토플 화면·스크립트·데이터가 모두 있고, HTML 이 읽는 스크립트가 실제로 있다", () => {
  const sw = fs.readFileSync(path.join(root, "sw.js"), "utf8");
  const assets = [...sw.match(/const ASSETS = \[([\s\S]*?)\];/)[1].matchAll(/"([^"]+)"/g)].map(m => m[1]);
  for (const t of TASKS) assert.ok(assets.includes(`data/toefl-${t}.js`), t);
  for (const f of fs.readdirSync(root).filter(f => /^toefl.*\.html$/.test(f))) {
    assert.ok(assets.includes(f), f + " 이 sw.js 목록에 없음");
    const html = fs.readFileSync(path.join(root, f), "utf8");
    for (const m of html.matchAll(/<script src="([^"]+)"/g)) {
      assert.ok(fs.existsSync(path.join(root, m[1])), `${f}: ${m[1]} 없음`);
      assert.ok(assets.includes(m[1]), `${f}: ${m[1]} 이 sw.js 목록에 없음`);
    }
  }
  for (const j of fs.readdirSync(path.join(root, "js")).filter(j => /^toefl/.test(j))) assert.ok(assets.includes("js/" + j), j);
});

test("화면 표시: 정답률 %·자기 평가 점수·밴드 글자가 TS 앱 템플릿(Jinja)과 같다 (62.5% → 62, 2.25점 → 2.2, 4 → 4.0)", () => {
  const D = JSON.parse(fs.readFileSync(path.join(__dirname, "toefl_display_fixtures.json"), "utf8"));
  const { W } = sandbox();
  const T = W.Toefl;
  for (const c of D.cases) {
    assert.strictEqual(String(T.pct(c.a)), c.pct, `정답률 ${c.a}`);
    assert.strictEqual(T.self5(c.a), c.self, `자기 평가 ${c.a}`);
  }
  for (const c of D.bands) assert.strictEqual(T.fmtBand(c.x), c.out, `밴드 ${c.x}`);
  assert.strictEqual(T.pct(0.125), 12, "Math.round 라면 13");
  assert.strictEqual(T.pct(0.625), 62);
  assert.strictEqual(T.self5(0.45), "2.2");
  assert.strictEqual(T.fmtBand(null), "–");
  assert.strictEqual(T.scoreText("r_daily", 0.625), "62%");
  assert.strictEqual(T.scoreText("w_email", 0.6), "3.0/5");
  assert.strictEqual(T.scoreText("w_email", 0.6, " / "), "3.0 / 5");
});
