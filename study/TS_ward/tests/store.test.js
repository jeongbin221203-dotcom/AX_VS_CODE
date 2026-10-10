// node tests/store.test.js — 복습 일정 계산이 TS 앱(core/srs.py)과 같은지, 저장·큐 동작이 맞는지 확인한다
const fs = require("fs"), vm = require("vm"), path = require("path"), assert = require("assert");
const root = path.join(__dirname, "..");
function sandbox() {
  const mem = {};
  const win = { WARD_DATA: {} };
  const ctx = vm.createContext({
    window: win, localStorage: { getItem: k => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); } },
    document: {}, console, Date, Math, JSON, Set, Map, Object, Array, Number, String, Promise,
  });
  vm.runInContext(fs.readFileSync(path.join(root, "js/store.js"), "utf8"), ctx);
  for (const k of ["toeic", "toefl"]) vm.runInContext(fs.readFileSync(path.join(root, `data/${k}.js`), "utf8"), ctx);
  return { Ward: win.Ward, ctx, mem, win };
}
let ok = 0;
const t = (name, fn) => { fn(); ok++; console.log("  ✓", name); };

const { Ward } = sandbox();
const cases = JSON.parse(fs.readFileSync(path.join(__dirname, "schedule_cases.json"), "utf8"));
t(`schedule: 파이썬과 ${cases.length}개 경우 일치`, () => {
  for (const [ef, iv, reps, g, exp] of cases) {
    const got = Ward.schedule(ef, iv, reps, g);
    assert.strictEqual(JSON.stringify(got), JSON.stringify(exp), `ef=${ef} iv=${iv} reps=${reps} g=${g}: ${got} != ${exp}`);
  }
});
t("schedule: 잘못된 평가는 거부", () => assert.throws(() => Ward.schedule(2.5, 1, 1, 2)));

(async () => {
  const s = sandbox();
  const W = s.Ward;
  await W.init();                     // data/toeic.js 는 이미 올라가 있어 스크립트 태그 없이 읽는다
  t("단어 1,800개·등급 5개", () => { assert.strictEqual(W.words().length, 1800); assert.strictEqual(W.grades().length, 5); });
  t("새 단어는 하루 한도만큼, 복습 없음", () => { const q = W.queue({ dailyNew: 7 }); assert.strictEqual(q.new.length, 7); assert.strictEqual(q.due.length, 0); });
  t("첫 학습에서 '다시'를 눌러도 본 카드(lapses 1)로 세어진다", () => {
    const id = W.queue({ dailyNew: 1 }).new[0].id;
    const c = W.review(id, 0);
    assert.strictEqual(c.lapses, 1); assert.strictEqual(W.stateOf(c), "learning");
  });
  t("오늘 본 새 단어는 한도에서 빠진다", () => assert.strictEqual(W.queue({ dailyNew: 5 }).new.length, 4));
  t("내일이 되면 '다시' 단어가 복습에 나온다", () => {
    const id = Object.keys(JSON.parse(W.exportJSON()).cards)[0];
    const dump = JSON.parse(W.exportJSON()); dump.cards[id].due = "2000-01-01"; W.importJSON(JSON.stringify(dump));
    assert.ok(W.queue({ dailyNew: 0 }).due.some(w => w.id === id));
  });
  t("퀴즈 오답은 복습 카드에 들어가고 '최근 틀린 단어'에 모인다", () => {
    const w = W.words()[500];
    const r = W.quizAnswer(w.id, false);
    assert.ok(r.scheduled); assert.ok(W.recentlyMissed().has(w.id));
    W.quizAnswer(w.id, true); assert.ok(!W.recentlyMissed().has(w.id));
  });
  t("2번 틀리면 자주 잊는 단어", () => {
    const w = W.words()[700]; W.review(w.id, 0); W.review(w.id, 0);
    assert.ok(W.failCounts()[w.id] >= 2);
  });
  t("별표 토글", () => { const id = W.words()[10].id; assert.strictEqual(W.toggleStar(id), true); assert.strictEqual(W.toggleStar(id), false); });
  t("백업 내보내기·불러오기가 그대로 돌아온다", () => {
    const a = W.exportJSON(); W.reset(); assert.strictEqual(Object.keys(JSON.parse(W.exportJSON()).cards).length, 0);
    W.importJSON(a); assert.strictEqual(W.exportJSON(), a);
  });
  t("잘못된 백업 파일은 거부", () => { assert.throws(() => W.importJSON("{}")); assert.throws(() => W.importJSON("not json")); });
  t("저장은 새로고침해도 남는다(같은 localStorage 로 다시 읽기)", () => {
    const id = W.words()[20].id; W.review(id, 4);
    const raw = s.mem["ward:v1"];
    const s2 = sandbox(); s2.mem["ward:v1"] = raw;
    // 같은 저장소 내용으로 새로 읽으면 카드가 있어야 한다
    const s3 = (() => { const m = { "ward:v1": raw }; const w = { WARD_DATA: {} };
      const c = vm.createContext({ window: w, localStorage: { getItem: k => m[k] ?? null, setItem: (k, v) => { m[k] = v; } }, document: {}, console, Date, Math, JSON, Set, Map, Object, Array, Number, String, Promise });
      vm.runInContext(fs.readFileSync(path.join(root, "js/store.js"), "utf8"), c); return w.Ward; })();
    assert.ok(s3.cardOf(id) && s3.cardOf(id).reps === 1);
  });
  console.log(`\n${ok}개 통과`);
})().catch(e => { console.error("실패:", e.message); process.exit(1); });
