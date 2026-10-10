// static/store.js 검증 — 실제 문항·공식 정답(FIXTURE_DIR 의 catalog.json·answers.json)으로 시험·채점·오답노트·백업을 확인한다.
// 실행: python -m pytest tests -q (test_browser_store_in_node 가 이 파일을 실행)  또는  FIXTURE_DIR=<폴더> node --test tests/store.test.js
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { createStore, memoryAdapter, isLocal, cleanState } = require('../static/store.js');

const dir = process.env.FIXTURE_DIR;
if (!dir) throw new Error('FIXTURE_DIR 이 필요합니다 (pytest 로 실행하세요).');
const read = name => JSON.parse(fs.readFileSync(path.join(dir, name), 'utf-8'));
const catalog = read('catalog.json').items;
const answers = read('answers.json');
const wrongChoice = qid => answers[qid] % 4 + 1;

function setup(extra = {}) {
  let clock = 1_000_000, n = 0;
  const calls = { answers: 0, explain: 0 };
  const adapter = extra.adapter || memoryAdapter();
  const env = { offline: !!extra.offline };
  const store = createStore({
    adapter, now: () => clock, randomId: () => 'a' + String(++n).padStart(3, '0') + (extra.salt || ''),
    catalog: async () => catalog,
    answers: async qids => {
      calls.answers++;
      if (env.offline) throw new Error('Failed to fetch');
      return Object.fromEntries(qids.map(q => [q, answers[q]]));
    },
    explain: async qid => { calls.explain++; return { body: '해설 ' + qid, model: 'gpt-test', created_at: clock, status: 'AI 초안' }; },
  });
  return { store, calls, adapter, env, tick: s => { clock += s; } };
}
const api = (t, method, url, data) => t.store.handle(method, url, data);
const start = async (t, data = {}) => (await api(t, 'POST', '/api/attempts', { round: 59, subject: 0, ...data })).id;
const view = (t, id) => api(t, 'GET', `/api/attempts/${id}`);
const answer = (t, id, qid, choice) => api(t, 'PATCH', `/api/attempts/${id}/answer`, { qid, choice });
const rejects = (promise, status) => assert.rejects(promise, e => { assert.equal(e.status, status, e.message); return true; });

test('28개 회차·과목 시험 모두 공식 정답으로 채점하면 100점', async () => {
  for (let round = 59; round <= 65; round++) for (let subject = 0; subject < 4; subject++) {
    const t = setup();
    const id = await start(t, { round, subject });
    const a = await view(t, id);
    assert.equal(a.questions.length, 30);
    assert.ok(a.questions.every(q => !('correct' in q) && !('is_correct' in q)), '제출 전에는 정답이 없어야 함');
    assert.deepEqual(a.questions.map(q => q.number), [...a.questions.map(q => q.number)].sort((x, y) => x - y));
    for (const q of a.questions) await answer(t, id, q.id, answers[q.id]);
    const r = await api(t, 'POST', `/api/attempts/${id}/submit`, {});
    assert.equal(r.score, 100);
    assert.ok(r.questions.every(q => q.is_correct && q.correct === answers[q.id]));
    assert.deepEqual((await api(t, 'GET', '/api/wrong')).items, []);
  }
});

test('전 과목 120문항, 틀린 답·미응답은 오답노트에 쌓임', async () => {
  const t = setup();
  const id = await start(t, { round: 65, subject: -1 });
  const a = await view(t, id);
  assert.equal(a.questions.length, 120);
  assert.equal(new Set(a.questions.map(q => q.subject)).size, 4);
  const [q0, q1, q2, ...rest] = a.questions;
  for (const q of rest) await answer(t, id, q.id, answers[q.id]);
  await answer(t, id, q1.id, wrongChoice(q1.id));              // q0 은 미응답, q2 도 미응답
  const r = await api(t, 'POST', `/api/attempts/${id}/submit`, {});
  assert.equal(r.score, Math.round(117 * 100 / 120 * 10) / 10);
  const w = await api(t, 'GET', '/api/wrong');
  assert.deepEqual(w.items.map(i => i.id).sort(), [q0.id, q1.id, q2.id].sort());
  assert.equal(w.items.find(i => i.id === q1.id).last_choice, wrongChoice(q1.id));
  assert.equal(w.items.find(i => i.id === q0.id).last_choice, null);
  assert.equal(w.counts.reduce((s, c) => s + c.pending, 0), 3);
  const d = await api(t, 'GET', '/api/dashboard');
  assert.equal(d.wrong, 3); assert.equal(d.completed, 1); assert.equal(d.average, r.score);
});

test('이어 풀기: 선택·나중에 보기·마지막 위치가 저장되고 다시 열어도 같다', async () => {
  const t = setup();
  const id = await start(t);
  const a = await view(t, id);
  await answer(t, id, a.questions[4].id, 2);
  await api(t, 'PATCH', `/api/attempts/${id}/answer`, { qid: a.questions[4].id, flagged: true });
  await api(t, 'PATCH', `/api/attempts/${id}/answer`, { qid: a.questions[7].id });        // 이동만
  const b = await view(t, id);
  assert.equal(b.questions[4].choice, 2); assert.equal(b.questions[4].flagged, true);
  assert.equal(b.last_index, 7); assert.equal(b.submitted_at, null);
  await answer(t, id, a.questions[4].id, null);                                            // 선택 취소
  assert.equal((await view(t, id)).questions[4].choice, null);
  const d = await api(t, 'GET', '/api/dashboard');
  assert.equal(d.history[0].answered, 0); assert.equal(d.history[0].total, 30); assert.equal(d.history[0].submitted_at, null);
});

test('제한 시간이 지나면 저장된 답으로 자동 제출되고 더는 고칠 수 없다', async () => {
  const t = setup();
  const id = await start(t, { minutes: 30 });
  const a = await view(t, id);
  assert.equal(a.deadline, 1_000_000 + 1800);
  await answer(t, id, a.questions[0].id, answers[a.questions[0].id]);
  t.tick(1800);
  await rejects(answer(t, id, a.questions[1].id, 1), 409);
  const done = await view(t, id);
  assert.ok(done.submitted_at !== null);
  assert.equal(done.score, Math.round(100 / 30 * 10) / 10);
  await rejects(answer(t, id, a.questions[1].id, 1), 409);
});

test('시간이 지났는데 서버에 못 닿으면 보류했다가 연결되면 채점', async () => {
  const t = setup();
  const id = await start(t, { minutes: 30 });
  const a = await view(t, id);
  t.tick(2000); t.env.offline = true;
  const waiting = await view(t, id);
  assert.equal(waiting.submitted_at, null);
  await rejects(answer(t, id, a.questions[0].id, 1), 409);                                 // 보류 중에도 고칠 수 없음
  await assert.rejects(api(t, 'POST', `/api/attempts/${id}/submit`, {}), /서버에 연결/);
  t.env.offline = false;
  assert.ok((await view(t, id)).submitted_at !== null);
});

test('잘못된 입력은 거부하고 제출은 한 번만 반영', async () => {
  const t = setup();
  await rejects(api(t, 'POST', '/api/attempts', { round: 58, subject: 0 }), 400);
  await rejects(api(t, 'POST', '/api/attempts', { round: 59, subject: 4 }), 400);
  await rejects(api(t, 'POST', '/api/attempts', { round: 59, subject: 0, mode: 'x' }), 400);
  await rejects(api(t, 'POST', '/api/attempts', { round: 59, subject: 0, minutes: 241 }), 400);
  await rejects(api(t, 'POST', '/api/attempts', { round: 59.5, subject: 0 }), 400);
  await rejects(api(t, 'POST', '/api/attempts', { subject: 0 }), 400);
  await rejects(api(t, 'POST', '/api/attempts', { wrong: true }), 400);                    // 오답이 없으면 풀 문제 없음
  const id = await start(t);
  const a = await view(t, id);
  const q = a.questions[0].id;
  for (const bad of [5, 0, true, '1', 1.5]) await rejects(answer(t, id, q, bad), 400);
  await rejects(api(t, 'PATCH', `/api/attempts/${id}/answer`, { qid: q, flagged: 'yes' }), 400);
  await rejects(answer(t, id, 60000, 1), 400);                                              // 이 시험에 없는 문제
  await rejects(answer(t, id, 1, 1), 400);
  await rejects(view(t, 'nope'), 404);
  await answer(t, id, q, 1);
  const first = await api(t, 'POST', `/api/attempts/${id}/submit`, {});
  t.tick(50);
  const second = await api(t, 'POST', `/api/attempts/${id}/submit`, {});
  assert.equal(second.submitted_at, first.submitted_at); assert.equal(second.score, first.score);
  await rejects(answer(t, id, q, 2), 409);
  assert.equal((await api(t, 'GET', '/api/dashboard')).completed, 1);
});

test('메모·복습 완료·오답 다시 풀기, 다시 틀리면 복습 목록으로 복귀(메모는 유지)', async () => {
  const t = setup();
  const id = await start(t);
  const a = await view(t, id);
  for (const q of a.questions) await answer(t, id, q.id, q.number <= 3 ? wrongChoice(q.id) : answers[q.id]);
  await api(t, 'POST', `/api/attempts/${id}/submit`, {});
  const [w1, w2, w3] = (await api(t, 'GET', '/api/wrong')).items.map(i => i.id);
  await rejects(api(t, 'PUT', `/api/questions/${w1}/note`, { body: 'x'.repeat(20001) }), 400);
  await rejects(api(t, 'PUT', `/api/questions/${w1}/note`, { body: 'x', mastered: 1 }), 400);
  await api(t, 'PUT', `/api/questions/${w1}/note`, { body: '헷갈린 이유', mastered: true });
  await api(t, 'PUT', `/api/questions/${w2}/note`, { body: '', mastered: false });
  let w = await api(t, 'GET', '/api/wrong');
  assert.equal(w.items.find(i => i.id === w1).mastered, 1); assert.equal(w.items.find(i => i.id === w1).note, '헷갈린 이유');
  assert.equal(w.counts.reduce((s, c) => s + c.pending, 0), 2);
  const retry = await start(t, { wrong: true, subject: -1, mode: 'study' });               // 복습 완료한 문제는 빠짐
  const r = await view(t, retry);
  assert.equal(r.title, '전체 오답 복습'); assert.equal(r.round, null);
  assert.deepEqual(r.questions.map(q => q.id).sort(), [w2, w3].sort());
  for (const q of r.questions) await answer(t, retry, q.id, wrongChoice(q.id));            // 또 틀림
  await api(t, 'POST', `/api/attempts/${retry}/submit`, {});
  w = await api(t, 'GET', '/api/wrong');
  assert.equal(w.items.length, 3);
  const again = await start(t, { round: 59, subject: 0 });
  const b = await view(t, again);
  await answer(t, again, w1, wrongChoice(w1));
  for (const q of b.questions.filter(q => q.id !== w1)) await answer(t, again, q.id, answers[q.id]);
  await api(t, 'POST', `/api/attempts/${again}/submit`, {});
  const after = (await api(t, 'GET', '/api/wrong')).items.find(i => i.id === w1);
  assert.equal(after.mastered, 0); assert.equal(after.note, '헷갈린 이유');
  const q = await api(t, 'GET', `/api/questions/${w1}`);
  assert.equal(q.correct, answers[w1]); assert.equal(q.note.body, '헷갈린 이유'); assert.equal(q.explanation, null);
});

test('복습 화면의 정답은 풀어 본 문제는 기억하고 처음 보는 문제만 서버에 물어봄', async () => {
  const t = setup();
  const id = await start(t);
  await api(t, 'POST', `/api/attempts/${id}/submit`, {});
  const before = t.calls.answers;
  const seen = (await view(t, id)).questions[0].id;
  assert.equal((await api(t, 'GET', `/api/questions/${seen}`)).correct, answers[seen]);
  assert.equal(t.calls.answers, before);
  const fresh = 62010;
  assert.equal((await api(t, 'GET', `/api/questions/${fresh}`)).correct, answers[fresh]);
  assert.equal(t.calls.answers, before + 1);
  await rejects(api(t, 'GET', '/api/questions/59999'), 404);
});

test('AI 해설은 처음 한 번만 만들고 이후에는 저장본을 읽음', async () => {
  const t = setup();
  const first = await api(t, 'POST', '/api/questions/59000/explanation', {});
  assert.equal(first.body, '해설 59000'); assert.equal(t.calls.explain, 1);
  t.tick(99);
  const second = await api(t, 'POST', '/api/questions/59000/explanation', {});
  assert.deepEqual(second, first); assert.equal(t.calls.explain, 1);
  assert.deepEqual((await api(t, 'GET', '/api/questions/59000')).explanation, first);
});

test('AI 질문: 실전 연습은 제출 전 금지, 학습 연습은 정답 숨김, 제출 후엔 공개', async () => {
  const t = setup();
  const exam = await start(t), study = await start(t, { mode: 'study' });
  const q = (await view(t, exam)).questions[0].id;
  await rejects(t.store.chatPolicy(exam, q), 409);
  assert.deepEqual(await t.store.chatPolicy(study, q), { reveal: false });
  await rejects(t.store.chatPolicy(study, 62010), 400);
  await api(t, 'POST', `/api/attempts/${exam}/submit`, {});
  assert.deepEqual(await t.store.chatPolicy(exam, q), { reveal: true });
  assert.deepEqual(await t.store.chatPolicy(null, q), { reveal: true });
});

test('기록은 저장 장치에 남아 새로 열어도(서버가 다시 켜져도) 그대로', async () => {
  const t = setup();
  const id = await start(t);
  const a = await view(t, id);
  await answer(t, id, a.questions[0].id, 3);
  await api(t, 'PUT', `/api/questions/${a.questions[0].id}/note`, { body: '남아야 함', mastered: false });
  const reopened = setup({ adapter: t.adapter });                                          // 같은 저장 장치, 새 화면
  const b = await view(reopened, id);
  assert.equal(b.questions[0].choice, 3);
  assert.equal((await api(reopened, 'GET', `/api/questions/${a.questions[0].id}`)).note.body, '남아야 함');
});

test('여러 번 동시에 저장해도 하나도 잃지 않음', async () => {
  const t = setup();
  const id = await start(t);
  const a = await view(t, id);
  await Promise.all(a.questions.map((q, i) => answer(t, id, q.id, (i % 4) + 1)));
  const b = await view(t, id);
  assert.deepEqual(b.questions.map(q => q.choice), a.questions.map((q, i) => (i % 4) + 1));
  const ids = await Promise.all([start(t), start(t), start(t)]);
  assert.equal(new Set(ids).size, 3);
  assert.equal((await api(t, 'GET', '/api/dashboard')).history.length, 4);
});

test('백업 내보내기 → 새 저장소에 복원하면 같은 기록, 복원은 기존 기록을 교체', async () => {
  const t = setup();
  const id = await start(t);
  const a = await view(t, id);
  for (const q of a.questions) await answer(t, id, q.id, q.number === 1 ? wrongChoice(q.id) : answers[q.id]);
  await api(t, 'POST', `/api/attempts/${id}/submit`, {});
  await api(t, 'PUT', `/api/questions/${a.questions[0].id}/note`, { body: '백업 메모', mastered: false });
  await api(t, 'POST', `/api/questions/${a.questions[0].id}/explanation`, {});
  const text = await t.store.exportJson();
  assert.ok(!text.includes('sk-'));

  const fresh = setup({ salt: 'x' });
  await start(fresh, { round: 62, subject: 3 });                                           // 지워질 기록
  assert.deepEqual(await fresh.store.importJson(text), { attempts: 1, notes: 1, explanations: 1 });
  const d = await api(fresh, 'GET', '/api/dashboard');
  assert.equal(d.history.length, 1); assert.equal(d.history[0].id, id); assert.equal(d.wrong, 1);
  assert.equal((await api(fresh, 'GET', `/api/questions/${a.questions[0].id}`)).note.body, '백업 메모');
  const r = await view(fresh, id);
  assert.equal(r.score, Math.round(29 * 100 / 30 * 10) / 10); assert.equal(r.questions[0].is_correct, false);
});

test('이 앱의 백업이 아니거나 고친 흔적이 있는 파일은 거부하고 기존 기록은 그대로', async () => {
  const t = setup();
  const id = await start(t);
  await answer(t, id, (await view(t, id)).questions[0].id, 1);
  const good = JSON.parse(await t.store.exportJson());
  const tamper = fn => { const c = JSON.parse(JSON.stringify(good)); fn(c); return JSON.stringify(c); };
  const cases = {
    '깨진 글자': '{not json',
    '다른 앱': JSON.stringify({ app: 'other', version: 1, attempts: {}, notes: {}, explanations: {} }),
    '버전': tamper(c => { c.version = 2; }),
    '없는 문제 번호': tamper(c => { c.attempts[id].items[0].qid = 1; }),
    '범위 밖 번호': tamper(c => { c.attempts[id].items[0].qid = 59200; }),
    '선택 5번': tamper(c => { c.attempts[id].items[0].choice = 5; }),
    '중복 문제': tamper(c => { c.attempts[id].items[1].qid = c.attempts[id].items[0].qid; }),
    '키와 다른 id': tamper(c => { c.attempts[id].id = 'zzz'; }),
    '위치 범위': tamper(c => { c.attempts[id].last_index = 99; }),
    '제출했는데 정답 없음': tamper(c => { c.attempts[id].submitted_at = 5; c.attempts[id].score = 50; }),
    '메모 길이': tamper(c => { c.notes['59000'] = { body: 'x'.repeat(20001), mastered: 0, updated_at: 1 }; }),
    '메모 번호': tamper(c => { c.notes['5'] = { body: 'x', mastered: 0, updated_at: 1 }; }),
    '모드': tamper(c => { c.attempts[id].mode = 'hack'; }),
  };
  for (const [name, text] of Object.entries(cases)) await assert.rejects(t.store.importJson(text), e => e.status === 400, name);
  assert.equal((await api(t, 'GET', '/api/dashboard')).history.length, 1);                 // 그대로
  assert.equal((await view(t, id)).questions[0].choice, 1);
});

test('예전 서버(sqlite) 백업을 바꾼 기록도 그대로 복원되고 이어서 쓸 수 있음', async () => {
  const converted = read('converted.json');
  const t = setup();
  assert.deepEqual(await t.store.importState(converted), { attempts: 2, notes: 1, explanations: 1 });
  const d = await api(t, 'GET', '/api/dashboard');
  assert.equal(d.completed, 1); assert.equal(d.average, 90);
  assert.equal(d.wrong, 2);                                                                 // 마지막 3문항을 틀렸고 그중 1개는 복습 완료 표시 → 복습할 문제 2
  assert.equal((await api(t, 'GET', '/api/wrong')).items.length, 3);
  const going = d.history.find(h => h.id === 'old2');
  assert.equal(going.submitted_at, null); assert.equal(going.total, 3);
  const r = await view(t, 'old1');
  assert.equal(r.questions[2].flagged, true); assert.equal(r.questions.filter(q => !q.is_correct).length, 3);
  const w = (await api(t, 'GET', '/api/wrong')).items.find(i => i.note);
  assert.equal(w.note, '옛 메모'); assert.equal(w.mastered, 1);
});

test('주소 구분: 기록 요청만 이 저장소가 받음', () => {
  for (const url of ['/api/dashboard', '/api/wrong', '/api/attempts', '/api/attempts/abc/answer', '/api/questions/59000', '/api/questions/59000/note', '/api/questions/59000/explanation', '/api/wrong?x=1'])
    assert.ok(isLocal(url), url);
  for (const url of ['/api/bootstrap', '/api/catalog?v=1', '/api/answers', '/api/ai/chat', '/api/ai/settings', '/api/convert-backup', '/api/questions/abc', '/api/dashboards'])
    assert.ok(!isLocal(url), url);
  assert.throws(() => cleanState(null));
});
