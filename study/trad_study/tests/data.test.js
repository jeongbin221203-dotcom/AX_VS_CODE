// 만들어 둔 정적 데이터(data/·img/·글꼴)와 index.html 의 연결이 온전한지 확인한다.
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('node:vm');

const root = path.join(__dirname, '..');
const at = (...p) => path.join(root, ...p);
const loadData = name => {
  const sandbox = { window: {} };
  vm.runInNewContext(fs.readFileSync(at('data', name), 'utf-8'), sandbox);
  return sandbox.window;
};
const catalog = loadData('catalog.js').TRADE_CATALOG;
const answers = loadData('answers.js').TRADE_ANSWERS;
const head = (file, n) => { const fd = fs.openSync(file, 'r'); const b = Buffer.alloc(n); fs.readSync(fd, b, 0, n, 0); fs.closeSync(fd); return b; };

test('문항 840개, 28개 회차·과목 묶음이 각각 30문항이고 정답은 들어 있지 않음', () => {
  assert.equal(catalog.length, 840);
  assert.equal(new Set(catalog.map(q => q.id)).size, 840);
  const groups = new Map();
  for (const q of catalog) groups.set(`${q.round}-${q.subject}`, (groups.get(`${q.round}-${q.subject}`) || 0) + 1);
  assert.equal(groups.size, 28);
  assert.ok([...groups.values()].every(n => n === 30));
  for (const q of catalog) {
    assert.ok(!('correct' in q) && !('parts' in q), q.id);
    assert.ok(q.round >= 59 && q.round <= 65 && q.subject >= 0 && q.subject <= 3);
    assert.ok([...'①②③④'].every(c => q.body.includes(c)), `${q.id} 보기`);
    assert.ok(q.images.length >= 1, `${q.id} 이미지`);
  }
});

test('공식 정답 840개가 문항과 1:1로 맞음', () => {
  assert.deepEqual(Object.keys(answers).map(Number).sort(), [...catalog.map(q => q.id)].sort((a, b) => a - b));
  assert.ok(Object.values(answers).every(v => [1, 2, 3, 4].includes(v)));
  assert.equal(answers[59000], 3);
});

test('이미지 파일이 모두 있고 WebP 이며, 같은 그림은 한 파일을 함께 씀', () => {
  const urls = new Set(catalog.flatMap(q => [...q.images, ...q.context_images]));
  for (const url of urls) {
    assert.match(url, /^img\/[0-9a-f]{16}\.webp$/);
    const file = at(url);
    assert.ok(fs.existsSync(file), url);
    const h = head(file, 12);
    assert.equal(h.subarray(0, 4).toString(), 'RIFF'); assert.equal(h.subarray(8, 12).toString(), 'WEBP');
  }
  const onDisk = fs.readdirSync(at('img')).filter(f => f.endsWith('.webp'));
  assert.equal(onDisk.length, urls.size, '쓰이지 않는 이미지 파일이 없어야 함');
  const contextUsers = new Map();
  for (const q of catalog) for (const u of q.context_images) contextUsers.set(u, (contextUsers.get(u) || 0) + 1);
  assert.ok([...contextUsers.values()].some(n => n > 1), '공통 지문 그림은 여러 문항이 같은 파일을 가리킴');
});

test('원본 시험지·정답표 PDF 14개와 문항의 시험지 링크', () => {
  for (let round = 59; round <= 65; round++) for (const kind of ['questions', 'answers']) {
    const file = at('data', 'sources', `${round}_${kind}.pdf`);
    assert.ok(fs.existsSync(file), file);
    assert.equal(head(file, 4).toString(), '%PDF');
  }
  for (const q of catalog) {
    const m = q.source.match(/^data\/sources\/(\d+)_questions\.pdf#page=(\d+)$/);
    assert.ok(m && Number(m[1]) === q.round && Number(m[2]) === q.page, q.source);
  }
});

test('validation.json: 문제 PDF의 SHA-256·회차별 문항 수가 맞음', () => {
  const validation = JSON.parse(fs.readFileSync(at('data', 'validation.json'), 'utf-8'));
  assert.equal(validation.length, 7);
  for (const v of validation) {
    const bytes = fs.readFileSync(at('data', 'sources', `${v.round}_questions.pdf`));
    assert.equal(require('node:crypto').createHash('sha256').update(bytes).digest('hex'), v.sha256, `${v.round}회`);
    assert.equal(v.questions, 120); assert.equal(v.answers, 120);
    assert.equal(catalog.filter(q => q.round === v.round).length, v.questions);
    const sharedGroups = new Set(catalog.filter(q => q.round === v.round && q.context).map(q => q.context)).size;
    assert.ok(sharedGroups >= 1, `${v.round}회 공통 지문`);
  }
  assert.equal(validation.reduce((n, v) => n + v.shared_groups, 0), 42);
});

test('index.html: 상대 경로·필요한 스크립트·보안 설정, 정답 파일은 처음에 불러오지 않음', () => {
  const html = fs.readFileSync(at('index.html'), 'utf-8');
  const refs = [...html.matchAll(/(?:src|href)="([^"#][^"]*)"/g)].map(m => m[1]);
  for (const ref of refs) {
    assert.ok(!ref.startsWith('/') && !/^https?:/.test(ref), `상대 경로여야 함: ${ref}`);
    assert.ok(fs.existsSync(at(ref)), ref);
  }
  const scripts = [...html.matchAll(/<script src="([^"]+)"/g)].map(m => m[1]);
  assert.deepEqual(scripts, ['data/catalog.js', 'static/store.js', 'static/ai.js', 'static/app.js']);
  assert.ok(!html.includes('answers.js'), '정답 파일은 채점할 때만 불러옴');
  assert.match(html, /Content-Security-Policy[^>]*script-src 'self'[^>]*connect-src 'self' https:\/\/api\.openai\.com/);
  for (const file of ['static/app.js', 'static/ai.js', 'static/store.js', 'static/style.css'])
    assert.ok(!/(?:src|href)=["'`]\/[a-z]|url\(['"]?\/(?!\/)/.test(fs.readFileSync(at(file), 'utf-8')), `${file} 에 절대 경로가 있음`);
});

test('글꼴: 쓰는 글자만 남긴 woff2 이고 크기가 작음', () => {
  const file = at('static', 'fonts', 'NotoSansKR-subset.woff2');
  assert.equal(head(file, 4).toString(), 'wOF2');
  assert.ok(fs.statSync(file).size < 3_000_000, `${fs.statSync(file).size} bytes`);
  assert.ok(fs.existsSync(at('static', 'fonts', 'OFL.txt')));
  assert.ok(fs.readFileSync(at('static', 'style.css'), 'utf-8').includes('fonts/NotoSansKR-subset.woff2'));
});

test('휴대폰 화면 스타일: 줄 전체 폭 링크 규칙이 정답 줄까지 번지지 않음(정답 글자가 세로로 쪼개지던 문제)', () => {
  const css = fs.readFileSync(at('static', 'style.css'), 'utf-8');
  assert.ok(css.includes('.question-nav .source-link{order:3;flex:1 0 100%'), '문제 이동 줄에만 적용');
  assert.ok(!/(^|[}{,])\.source-link\{order:3/.test(css), '.source-link 에 직접 적용하면 .review-answer 안의 링크까지 줄 전체를 차지함');
  assert.match(css, /\.review-answer\{[^}]*flex-wrap:wrap/);
  assert.match(css, /\.review-answer strong\{[^}]*white-space:nowrap/);
});

test('문제 글의 띄어쓰기: 시험지 PDF 에서 빠졌던 공백을 되살림(제목의 82%가 붙어 나오던 문제)', () => {
  const byId = new Map(catalog.map(q => [q.id, q]));
  assert.equal(byId.get(59000).title, '1. 대외무역법령상 무역거래의 대상(객체)에 해당하지 않는 것은?');
  assert.equal(byId.get(59090).title, '1. 다음 offer의 내용상 밑줄 친 (A)～(D) 중에서 내용이 부적절한 것은?');
  for (const q of catalog) {
    const hangul = (q.title.match(/[가-힣]/g) || []).length;
    if (hangul >= 10) assert.ok((q.title.split(' ').length - 1) / hangul >= 0.08, `${q.id} ${q.title}`);
    assert.ok(!/[가-힣]{12,}/.test(q.title), `${q.id} 한글이 12자 넘게 붙어 있음: ${q.title}`);
  }
  assert.ok(byId.get(59021).body.includes('A. 보세창고') && byId.get(59021).body.includes('E. 종합보세구역'));
});
