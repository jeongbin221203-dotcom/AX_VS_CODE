// static/ai.js 검증 — OpenAI 를 부르는 요청 모양, 키 보관, 오류 안내, 정답 숨김을 가짜 fetch 로 확인한다(실제 API 호출·비용 없음).
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('node:vm');
const { create, TUTOR, HINT_ONLY } = require('../static/ai.js');

const loadData = name => {
  const sandbox = { window: {} };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'data', name), 'utf-8'), sandbox);
  return sandbox.window;
};
const catalog = loadData('catalog.js').TRADE_CATALOG;
const answers = loadData('answers.js').TRADE_ANSWERS;
const byId = new Map(catalog.map(q => [q.id, q]));
const KEY = 'sk-fake-testing-only-1234567890';

const reply = text => ({ ok: true, status: 200, json: async () => ({ status: 'completed', output: [{ type: 'message', content: [{ type: 'output_text', text }] }] }) });
const failing = status => ({ ok: false, status, json: async () => ({}) });

function setup(extra = {}) {
  let clock = 5_000_000;
  const calls = [];
  const data = new Map();
  const storage = extra.storage || { getItem: k => (data.has(k) ? data.get(k) : null), setItem: (k, v) => data.set(k, String(v)), removeItem: k => data.delete(k) };
  const ai = create({
    storage, now: () => clock,
    fetch: async (url, init) => {
      calls.push({ url, init, body: JSON.parse(init.body) });
      if (extra.fetch) return extra.fetch(calls.length);
      return reply('핵심 개념: 테스트 해설입니다.');
    },
    catalog: async () => catalog,
    answers: async qids => Object.fromEntries(qids.map(q => [q, answers[q]])),
    image: extra.image || (async src => 'data:image/webp;base64,' + src),
  });
  return { ai, calls, data, storage, tick: s => { clock += s; } };
}
const rejects = (promise, status, message) => assert.rejects(promise, e => { assert.equal(e.status, status, e.message); if (message) assert.match(e.message, message); return true; });
const lastText = t => t.calls.at(-1).body.input[0].content[0].text;

test('연결: 검사 요청 모양, 키는 헤더에만, 이 탭 저장소에만 보관', async () => {
  const t = setup();
  assert.deepEqual(t.ai.status(), { connected: false, model: 'gpt-4.1-mini' });
  assert.deepEqual(await t.ai.connect(KEY, 'gpt-4.1-mini'), { connected: true, model: 'gpt-4.1-mini' });
  const c = t.calls[0];
  assert.equal(c.url, 'https://api.openai.com/v1/responses');
  assert.equal(c.init.method, 'POST');
  assert.equal(c.init.headers.Authorization, 'Bearer ' + KEY);
  assert.equal(c.body.model, 'gpt-4.1-mini'); assert.equal(c.body.store, false); assert.equal(c.body.max_output_tokens, 128);
  assert.ok(!JSON.stringify(c.body).includes(KEY), '키가 요청 본문에 들어가면 안 됨');
  assert.deepEqual(t.ai.status(), { connected: true, model: 'gpt-4.1-mini' });
  assert.deepEqual([...t.data.keys()], ['trade-study-ai']);
  assert.deepEqual(t.ai.disconnect(), { connected: false });
  assert.equal(t.data.size, 0); assert.equal(t.ai.status().connected, false);
});

test('연결 입력 검사: 짧은 키·공백·긴 키·이상한 모델 이름', async () => {
  const t = setup();
  for (const bad of ['short', 'sk-fake testing 12345678', 'x'.repeat(501), '', null, 123]) await rejects(t.ai.connect(bad, 'gpt-4.1-mini'), 400, /API 키/);
  for (const bad of ['', 'model name', 'a'.repeat(101), '모델', null]) await rejects(t.ai.connect(KEY, bad), 400, /모델 이름/);
  assert.equal(t.calls.length, 0);
  assert.equal(t.ai.status().connected, false);
});

test('키는 8시간 뒤 만료되고 저장소에서도 지워짐', async () => {
  const t = setup();
  await t.ai.connect(KEY, 'gpt-4.1-mini');
  t.tick(8 * 3600 - 1); assert.equal(t.ai.status().connected, true);
  t.tick(2); assert.equal(t.ai.status().connected, false);
  assert.equal(t.data.size, 0);
  await rejects(t.ai.chat({ message: '질문' }), 409, /API 키/);
});

test('연결 실패는 키를 저장하지 않고 원인별 안내를 보여 줌', async () => {
  for (const [status, text] of [[401, /API 키를 확인/], [403, /사용 권한/], [404, /모델 이름/], [429, /한도나 잔액/], [400, /이미지 입력/], [500, /응답에 실패/]]) {
    const t = setup({ fetch: () => failing(status) });
    await rejects(t.ai.connect(KEY, 'gpt-4.1-mini'), 502, text);
    assert.equal(t.ai.status().connected, false); assert.equal(t.data.size, 0);
  }
  const net = setup({ fetch: () => { throw new TypeError('Failed to fetch'); } });
  await rejects(net.ai.connect(KEY, 'gpt-4.1-mini'), 502, /연결하지 못했습니다/);
  await rejects(setup({ fetch: () => ({ ok: true, status: 200, json: async () => { throw new Error('x'); } }) }).ai.connect(KEY, 'm1'), 502, /형식을 읽지/);
  await rejects(setup({ fetch: () => ({ ok: true, status: 200, json: async () => ({ status: 'incomplete', output: [] }) }) }).ai.connect(KEY, 'm1'), 502, /끝내지 못했/);
  await rejects(setup({ fetch: () => ({ ok: true, status: 200, json: async () => ({ status: 'completed', output: [] }) }) }).ai.connect(KEY, 'm1'), 502, /비어 있습니다/);
});

test('질문: 공개 연습은 정답 포함·그림 순서(공통 지문 먼저)·대화 기록 전달', async () => {
  const t = setup();
  await t.ai.connect(KEY, 'gpt-4.1-mini');
  const q = byId.get(64090);
  assert.ok(q.context_images.length && q.images.length);
  const out = await t.ai.chat({ qid: 64090, message: '  이 문제 설명해 줘  ', history: [{ role: 'user', content: '앞 질문' }, { role: 'assistant', content: '앞 답' }], reveal: true });
  assert.equal(out.text, '핵심 개념: 테스트 해설입니다.');
  const body = t.calls.at(-1).body;
  assert.equal(body.instructions, TUTOR);
  const [first, h1, h2, ask] = body.input;
  assert.match(first.content[0].text, new RegExp(`첨부된 공식 정답표의 정답: ${answers[64090]}번`));
  assert.ok(first.content[0].text.includes(q.body) && first.content[0].text.includes('제64회'));
  const urls = first.content.slice(1).map(c => { assert.equal(c.type, 'input_image'); return c.image_url; });
  assert.deepEqual(urls, [...q.context_images, ...q.images].map(s => 'data:image/webp;base64,' + s));
  assert.deepEqual([h1, h2, ask], [{ role: 'user', content: '앞 질문' }, { role: 'assistant', content: '앞 답' }, { role: 'user', content: '이 문제 설명해 줘' }]);
  assert.ok(!JSON.stringify(body).includes(KEY));
});

test('제출 전 학습 연습(reveal 아님·미지정)은 정답을 빼고 힌트만 요청', async () => {
  const t = setup();
  await t.ai.connect(KEY, 'gpt-4.1-mini');
  for (const data of [{ reveal: false }, {}, { reveal: 'true' }]) {
    await t.ai.chat({ qid: 59000, message: '힌트', ...data });
    assert.ok(!lastText(t).includes('공식 정답표의 정답'), JSON.stringify(data));
    assert.ok(t.calls.at(-1).body.instructions.endsWith(HINT_ONLY));
  }
  await t.ai.chat({ qid: 59000, message: '설명', reveal: true });
  assert.match(lastText(t), /정답: 3번/);
});

test('질문 입력 검사', async () => {
  const t = setup();
  await t.ai.connect(KEY, 'gpt-4.1-mini');
  const before = t.calls.length;
  await rejects(t.ai.chat({ message: '' }), 400, /질문은/);
  await rejects(t.ai.chat({ message: '   ' }), 400);
  await rejects(t.ai.chat({ message: 'x'.repeat(4001) }), 400);
  await rejects(t.ai.chat({ message: 5 }), 400);
  await rejects(t.ai.chat({ message: 'x', history: Array(13).fill({ role: 'user', content: 'a' }) }), 400, /새 대화/);
  await rejects(t.ai.chat({ message: 'x', history: [{ role: 'system', content: 'a' }] }), 400, /대화 형식/);
  await rejects(t.ai.chat({ message: 'x', history: [{ role: 'user', content: 'a'.repeat(12001) }] }), 400);
  await rejects(t.ai.chat({ message: 'x', qid: 1 }), 400, /범위/);
  await rejects(t.ai.chat({ message: 'x', qid: 59120 }), 400);
  await rejects(t.ai.chat({ message: 'x', qid: '59000' }), 400);
  assert.equal(t.calls.length, before, '검사에 걸리면 OpenAI 로 보내지 않음');
});

test('해설 생성: 정답·그림 포함, 결과는 저장하지 않고 돌려줌', async () => {
  const t = setup();
  await rejects(t.ai.explain(59000), 409, /API 키/);
  await t.ai.connect(KEY, 'gpt-4.1-mini');
  const exp = await t.ai.explain(59000);
  assert.equal(exp.status, 'AI 초안'); assert.equal(exp.model, 'gpt-4.1-mini'); assert.equal(exp.created_at, 5_000_000);
  assert.equal(exp.body, '핵심 개념: 테스트 해설입니다.');
  const [a, b] = t.calls.at(-1).body.input;
  assert.match(a.content[0].text, /정답: 3번/);
  assert.ok(a.content.slice(1).every(c => c.type === 'input_image'));
  assert.match(b.content, /핵심 개념.*정답이 맞는 이유.*틀린 이유.*기억할 한 줄/);
  await rejects(t.ai.explain(1), 404);
});

test('그림을 읽지 못해도(파일로 직접 연 경우 등) 글자만으로 질문', async () => {
  const t = setup({ image: async () => { throw new Error('blocked'); } });
  await t.ai.connect(KEY, 'gpt-4.1-mini');
  assert.equal((await t.ai.chat({ qid: 64090, message: '질문', reveal: true })).text.length > 0, true);
  const content = t.calls.at(-1).body.input[0].content;
  assert.equal(content.length, 1); assert.equal(content[0].type, 'input_text');
});

test('탭 저장소를 쓸 수 없어도 이 탭 메모리에서 동작', async () => {
  const broken = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); }, removeItem() { throw new Error('denied'); } };
  const t = setup({ storage: broken });
  await t.ai.connect(KEY, 'gpt-4.1-mini');
  assert.equal(t.ai.status().connected, true);
  t.ai.disconnect();
  assert.equal(t.ai.status().connected, false);
});
