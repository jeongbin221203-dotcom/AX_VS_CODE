/* 학습 기록 저장소 — 시험 기록·메모·AI 해설은 이 브라우저(IndexedDB)에만 저장합니다.
 * 서버는 문제·이미지·공식 정답·AI 호출만 맡고, 기록은 갖지 않습니다.
 * app.js 의 api() 가 /api/dashboard·attempts·wrong·questions/<id> 요청을 여기로 보냅니다(응답 모양은 예전 서버와 같음).
 * 브라우저와 Node 테스트(tests/store.test.js)에서 같은 파일을 씁니다. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.TradeStore = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const SUBJECTS = ['무역규범', '무역결제', '무역계약', '무역영어'];
  const LOCAL = /^\/api\/(dashboard|wrong|attempts|questions\/\d+)(\/|\?|$)/;
  const isLocal = url => LOCAL.test(url);
  const fail = (status, message) => { const e = new Error(message); e.status = status; return e; };
  const emptyState = () => ({ app: 'trade-study', version: 1, attempts: {}, notes: {}, explanations: {} });
  const validQid = q => Number.isInteger(q) && q >= 59000 && q <= 65119 && q % 1000 < 120;
  const isObj = x => x !== null && typeof x === 'object' && !Array.isArray(x);
  const round1 = x => Math.round(x * 10) / 10;
  const MSG_RANGE = '입력값의 범위를 확인해 주세요.';
  const MSG_ENDED = '이미 제출되었거나 시간이 종료되었습니다. 결과 화면을 확인해 주세요.';

  function intIn(value, lo, hi) {
    if (!Number.isInteger(value) || value < lo || value > hi) throw fail(400, MSG_RANGE);
    return value;
  }

  /* ---------- 저장 장치 ---------- */
  function memoryAdapter() {
    let saved;
    return {
      persistent: false,
      async read() { return saved === undefined ? undefined : structuredClone(saved); },
      async write(state) { saved = structuredClone(state); },
    };
  }

  function idbAdapter(name = 'trade-study') {
    let opening = null;
    const open = () => opening || (opening = new Promise((resolve, reject) => {
      const req = indexedDB.open(name, 1);
      req.onupgradeneeded = () => req.result.createObjectStore('kv');
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => { opening = null; reject(req.error); };
    }));
    const run = async (mode, work) => {
      const db = await open();
      return new Promise((resolve, reject) => {
        const tx = db.transaction('kv', mode);
        const request = work(tx.objectStore('kv'));
        tx.oncomplete = () => resolve(request.result);
        tx.onerror = tx.onabort = () => reject(tx.error);
      });
    };
    return {
      persistent: true,
      read: () => run('readonly', s => s.get('state')),
      write: state => run('readwrite', s => s.put(state, 'state')),
      // 같은 사이트를 여러 탭에서 열어도 읽고-고치고-쓰는 동안 서로 끼어들지 않게 함
      lock: fn => (typeof navigator !== 'undefined' && navigator.locks) ? navigator.locks.request(name + '-lock', fn) : fn(),
    };
  }

  function bestAdapter() {
    try { if (typeof indexedDB !== 'undefined' && indexedDB) return idbAdapter(); } catch (e) { /* 아래 대체 */ }
    return memoryAdapter();
  }

  /* ---------- 백업 파일 검사 ---------- */
  function cleanState(o) {
    const bad = () => fail(400, '이 앱의 학습 기록 백업 파일이 아니거나 손상되었어요.');
    if (!isObj(o) || o.app !== 'trade-study' || o.version !== 1) throw bad();
    if (!isObj(o.attempts) || !isObj(o.notes) || !isObj(o.explanations)) throw bad();
    const num = x => typeof x === 'number' && Number.isFinite(x);
    const str = (x, max) => typeof x === 'string' && x.length <= max;
    const out = emptyState();
    if (Object.keys(o.attempts).length > 5000 || Object.keys(o.notes).length > 840 || Object.keys(o.explanations).length > 840) throw bad();
    for (const [id, a] of Object.entries(o.attempts)) {
      if (!/^[A-Za-z0-9_-]{1,64}$/.test(id) || !isObj(a) || a.id !== id) throw bad();
      if (!str(a.title, 200) || !a.title || !['exam', 'study'].includes(a.mode)) throw bad();
      if (a.round !== null && !(Number.isInteger(a.round) && a.round >= 59 && a.round <= 65)) throw bad();
      if (!Number.isInteger(a.subject) || a.subject < -1 || a.subject > 3) throw bad();
      if (!num(a.started_at) || !(a.deadline === null || num(a.deadline)) || !(a.submitted_at === null || num(a.submitted_at))) throw bad();
      if (!Array.isArray(a.items) || !a.items.length || a.items.length > 840) throw bad();
      const seen = new Set();
      const items = a.items.map(it => {
        if (!isObj(it) || !validQid(it.qid) || seen.has(it.qid) || typeof it.flagged !== 'boolean') throw bad();
        if (!(it.choice === null || [1, 2, 3, 4].includes(it.choice))) throw bad();
        seen.add(it.qid);
        return { qid: it.qid, choice: it.choice, flagged: it.flagged };
      });
      if (!Number.isInteger(a.last_index) || a.last_index < 0 || a.last_index >= items.length) throw bad();
      const clean = { id, title: a.title, round: a.round, subject: a.subject, mode: a.mode, started_at: a.started_at,
        deadline: a.deadline, submitted_at: a.submitted_at, score: null, last_index: a.last_index, items };
      if (a.submitted_at !== null) {
        if (!isObj(a.correct) || !num(a.score) || a.score < 0 || a.score > 100) throw bad();
        clean.score = a.score;
        clean.correct = {};
        for (const it of items) {
          const c = a.correct[it.qid];
          if (![1, 2, 3, 4].includes(c)) throw bad();
          clean.correct[it.qid] = c;
        }
      }
      out.attempts[id] = clean;
    }
    for (const [key, n] of Object.entries(o.notes)) {
      const qid = Number(key);
      if (!/^\d+$/.test(key) || !validQid(qid) || !isObj(n) || !str(n.body, 20000) || !num(n.updated_at)) throw bad();
      out.notes[qid] = { body: n.body, mastered: n.mastered ? 1 : 0, updated_at: n.updated_at };
    }
    for (const [key, e] of Object.entries(o.explanations)) {
      const qid = Number(key);
      if (!/^\d+$/.test(key) || !validQid(qid) || !isObj(e) || !str(e.body, 50000) || !str(e.model, 100) || !num(e.created_at) || !str(e.status, 30)) throw bad();
      out.explanations[qid] = { body: e.body, model: e.model, created_at: e.created_at, status: e.status };
    }
    return out;
  }

  /* ---------- 저장소 ---------- */
  function createStore(opts) {
    const adapter = opts.adapter || bestAdapter();
    const now = opts.now || (() => Date.now() / 1000);
    const randomId = opts.randomId || (() => {
      const bytes = new Uint8Array(12);
      globalThis.crypto.getRandomValues(bytes);
      return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
    });
    let cache = null;
    const catalog = async () => {
      if (!cache) {
        const list = await opts.catalog();
        cache = { list, byId: new Map(list.map(q => [q.id, q])) };
      }
      return cache;
    };
    const load = async () => {
      const s = await adapter.read();
      return s && isObj(s.attempts) ? s : emptyState();
    };
    // 모든 변경은 한 줄로: 읽기 → 고치기 → 쓰기. work 가 예외를 던지면 쓰지 않는다.
    let chain = Promise.resolve();
    function mutate(work) {
      const inner = async () => { const s = await load(); const out = await work(s); await adapter.write(s); return out; };
      const run = () => adapter.lock ? adapter.lock(inner) : inner();
      const p = chain.then(run, run);
      chain = p.catch(() => {});
      return p;
    }

    const needAttempt = (s, id) => {
      const a = s.attempts[id];
      if (!a) throw fail(404, '시험 기록을 찾을 수 없습니다.');
      return a;
    };
    const pastDeadline = a => a.submitted_at === null && a.deadline !== null && now() >= a.deadline;

    // 채점: 공식 정답은 서버에서 받아 오고, 점수 계산과 저장은 여기서 한다.
    async function finish(id) {
      const a0 = needAttempt(await load(), id);
      if (a0.submitted_at !== null) return a0;
      let answers;
      try { answers = await opts.answers(a0.items.map(i => i.qid)); }
      catch (e) { throw fail(e.status || 502, e.status ? e.message : '채점하려면 서버에 연결해야 해요. 연결을 확인하고 다시 시도해 주세요.'); }
      return mutate(s => {
        const a = needAttempt(s, id);
        if (a.submitted_at !== null) return a;
        a.correct = {};
        let right = 0;
        const wrong = [];
        for (const it of a.items) {
          const c = Number(answers[it.qid]);
          if (![1, 2, 3, 4].includes(c)) throw fail(502, '공식 정답을 읽지 못했습니다. 잠시 후 다시 시도해 주세요.');
          a.correct[it.qid] = c;
          if (it.choice === c) right++; else wrong.push(it.qid);
        }
        a.score = round1(right * 100 / a.items.length);
        a.submitted_at = now();
        // 새로 틀린 문제는 복습 완료 표시를 풀어 다시 복습 목록에 올린다(메모 글은 그대로).
        for (const qid of wrong) if (s.notes[qid]) s.notes[qid].mastered = 0;
        return a;
      });
    }

    // 제한 시간이 지난 시험은 저장된 답으로 제출. 서버에 못 닿으면 그대로 두고 다음에 다시 시도.
    async function expire(id) {
      const a = needAttempt(await load(), id);
      if (pastDeadline(a)) { try { await finish(id); } catch (e) { /* 연결이 돌아오면 다시 */ } }
      return needAttempt(await load(), id);
    }

    async function attemptView(a) {
      const { byId } = await catalog();
      const done = a.submitted_at !== null;
      const questions = a.items.map(it => {
        const c = byId.get(it.qid);
        if (!c) throw fail(500, '문제 목록을 읽지 못했습니다.');
        const q = { ...c, choice: it.choice, flagged: it.flagged };
        if (done) { q.correct = a.correct[it.qid]; q.is_correct = it.choice === q.correct; }
        return q;
      });
      return { id: a.id, title: a.title, round: a.round, subject: a.subject, mode: a.mode, started_at: a.started_at,
        deadline: a.deadline, submitted_at: a.submitted_at, score: a.score, last_index: a.last_index, questions, server_time: now() };
    }

    // 한 번이라도 틀린(미응답 포함) 문제 → 가장 최근에 틀린 때의 내 답
    function wrongMap(s) {
      const map = new Map();
      const done = Object.values(s.attempts).filter(a => a.submitted_at !== null).sort((x, y) => x.submitted_at - y.submitted_at);
      for (const a of done) for (const it of a.items) if (it.choice !== a.correct[it.qid]) map.set(it.qid, { last_choice: it.choice });
      return map;
    }
    async function wrongItems(s) {
      const { byId } = await catalog();
      const items = [];
      for (const [qid, w] of wrongMap(s)) {
        const c = byId.get(qid);
        if (!c) continue;
        const n = s.notes[qid];
        items.push({ id: qid, round: c.round, subject: c.subject, number: c.number, title: c.title,
          note: n ? n.body : '', mastered: n ? n.mastered : 0, last_choice: w.last_choice });
      }
      return items.sort((x, y) => y.round - x.round || x.subject - y.subject || x.number - y.number);
    }
    const wrongCounts = items => SUBJECTS.map((name, subject) => ({ subject, name,
      pending: items.filter(i => i.subject === subject && !i.mastered).length, all: items.filter(i => i.subject === subject).length }));

    async function expireAll() {
      const s = await load();
      await Promise.all(Object.values(s.attempts).filter(pastDeadline).map(a => finish(a.id).catch(() => {})));
    }

    async function dashboard() {
      await expireAll();
      const s = await load();
      const all = Object.values(s.attempts);
      const history = all.sort((x, y) => y.started_at - x.started_at).slice(0, 100).map(a => ({
        id: a.id, title: a.title, round: a.round, subject: a.subject, mode: a.mode, started_at: a.started_at, deadline: a.deadline,
        submitted_at: a.submitted_at, score: a.score, last_index: a.last_index, total: a.items.length,
        answered: a.items.filter(i => i.choice !== null).length }));
      const done = all.filter(a => a.submitted_at !== null);
      const counts = wrongCounts(await wrongItems(s));
      return { history, completed: done.length, average: done.length ? round1(done.reduce((t, a) => t + a.score, 0) / done.length) : null,
        wrong: counts.reduce((t, c) => t + c.pending, 0), subjects: counts };
    }

    async function startAttempt(data) {
      const mode = data.mode === undefined ? 'exam' : data.mode;
      if (!['exam', 'study'].includes(mode)) throw fail(400, '풀이 방식을 선택해 주세요.');
      const minutes = intIn(data.minutes === undefined ? 0 : data.minutes, 0, 240);
      const subject = intIn(data.subject === undefined ? -1 : data.subject, -1, 3);
      let rows, title, round = null;
      if (data.wrong === true) {
        const s = await load();
        rows = (await wrongItems(s)).filter(i => !i.mastered && (subject < 0 || i.subject === subject))
          .sort((x, y) => x.round - y.round || x.subject - y.subject || x.number - y.number).slice(0, 120);
        title = (subject < 0 ? '전체' : SUBJECTS[subject]) + ' 오답 복습';
      } else {
        round = intIn(data.round, 59, 65);
        const { list } = await catalog();
        rows = list.filter(q => q.round === round && (subject < 0 || q.subject === subject))
          .sort((x, y) => x.subject - y.subject || x.number - y.number);
        title = `제${round}회 ` + (subject >= 0 ? SUBJECTS[subject] : '전 과목');
      }
      if (!rows.length) throw fail(400, '지금 풀 수 있는 문제가 없습니다.');
      const id = randomId();
      const started = now();
      return mutate(s => {
        s.attempts[id] = { id, title, round, subject, mode, started_at: started, deadline: minutes ? started + minutes * 60 : null,
          submitted_at: null, score: null, last_index: 0, items: rows.map(r => ({ qid: r.id, choice: null, flagged: false })) };
        return { id };
      });
    }

    async function saveAnswer(id, data) {
      const qid = data.qid;
      if (!validQid(qid)) throw fail(400, MSG_RANGE);
      if (data.choice !== undefined && data.choice !== null) intIn(data.choice, 1, 4);
      if ('flagged' in data && typeof data.flagged !== 'boolean') throw fail(400, '표시 상태를 확인해 주세요.');
      const a = await expire(id);
      if (a.submitted_at !== null || pastDeadline(a)) throw fail(409, MSG_ENDED);
      return mutate(s => {
        const cur = needAttempt(s, id);
        if (cur.submitted_at !== null || pastDeadline(cur)) throw fail(409, MSG_ENDED);
        const pos = cur.items.findIndex(i => i.qid === qid);
        if (pos < 0) throw fail(400, '이 시험에 포함되지 않은 문제입니다.');
        if ('choice' in data) cur.items[pos].choice = data.choice === undefined ? null : data.choice;
        if ('flagged' in data) cur.items[pos].flagged = data.flagged;
        cur.last_index = pos;
        return { ok: true };
      });
    }

    const knownCorrect = (s, qid) => {
      for (const a of Object.values(s.attempts)) if (a.submitted_at !== null && a.correct[qid] !== undefined) return a.correct[qid];
      return null;
    };

    async function reviewQuestion(qid) {
      const { byId } = await catalog();
      const c = byId.get(qid);
      if (!c) throw fail(404, '문제를 찾을 수 없습니다.');
      const s = await load();
      let correct = knownCorrect(s, qid);
      if (correct === null) correct = Number((await opts.answers([qid]))[qid]);
      const n = s.notes[qid];
      return { ...c, correct, note: n ? { ...n } : { body: '', mastered: 0 }, explanation: s.explanations[qid] || null };
    }

    function saveNote(qid, data) {
      if (!validQid(qid)) throw fail(404, '문제를 찾을 수 없습니다.');
      const body = data.body === undefined ? '' : data.body;
      const mastered = data.mastered === undefined ? false : data.mastered;
      if (typeof body !== 'string' || body.length > 20000 || typeof mastered !== 'boolean') throw fail(400, '메모는 20,000자 이내로 입력해 주세요.');
      return mutate(s => { s.notes[qid] = { body, mastered: mastered ? 1 : 0, updated_at: now() }; return { ok: true }; });
    }

    async function explanation(qid) {
      const have = (await load()).explanations[qid];
      if (have) return have;
      const made = await opts.explain(qid);
      return mutate(s => {
        if (!s.explanations[qid]) s.explanations[qid] = { body: made.body, model: made.model, created_at: made.created_at, status: made.status || 'AI 초안' };
        return s.explanations[qid];
      });
    }

    // 제출 전 실전 연습에서는 AI 를 쓰지 않고, 학습 연습에서는 정답을 숨긴 힌트만 받는다.
    async function chatPolicy(attemptId, qid) {
      if (!attemptId) return { reveal: true };
      const a = await expire(attemptId);
      if (qid !== undefined && qid !== null && !a.items.some(i => i.qid === qid)) throw fail(400, '시험 문항을 확인해 주세요.');
      if (a.submitted_at !== null) return { reveal: true };
      if (a.mode === 'exam') throw fail(409, '실전 연습 중에는 AI 도움 없이 풀어요. 제출 후 질문할 수 있습니다.');
      return { reveal: false };
    }

    async function exportJson() {
      const s = await load();
      return JSON.stringify({ app: 'trade-study', version: 1, exported_at: now(), attempts: s.attempts, notes: s.notes, explanations: s.explanations });
    }
    async function importState(raw) {
      const clean = cleanState(raw);
      await mutate(s => { s.attempts = clean.attempts; s.notes = clean.notes; s.explanations = clean.explanations; });
      return { attempts: Object.keys(clean.attempts).length, notes: Object.keys(clean.notes).length, explanations: Object.keys(clean.explanations).length };
    }
    async function importJson(text) {
      let raw;
      try { raw = JSON.parse(text); } catch (e) { throw fail(400, '이 앱의 학습 기록 백업 파일이 아니거나 손상되었어요.'); }
      return importState(raw);
    }

    async function handle(method, url, data = {}) {
      const u = new URL(url, 'http://local');
      const p = u.pathname;
      let m;
      if (method === 'GET' && p === '/api/dashboard') return dashboard();
      if (method === 'GET' && p === '/api/wrong') {
        const items = await wrongItems(await load());
        return { items, counts: wrongCounts(items) };
      }
      if (method === 'POST' && p === '/api/attempts') return startAttempt(data);
      if ((m = p.match(/^\/api\/attempts\/([^/]+)(\/answer|\/submit)?$/))) {
        const id = decodeURIComponent(m[1]);
        if (!m[2] && method === 'GET') return attemptView(await expire(id));
        if (m[2] === '/answer' && method === 'PATCH') return saveAnswer(id, data);
        if (m[2] === '/submit' && method === 'POST') return attemptView(await finish(id));
      }
      if ((m = p.match(/^\/api\/questions\/(\d+)(\/note|\/explanation)?$/))) {
        const qid = Number(m[1]);
        if (!validQid(qid)) throw fail(404, '문제를 찾을 수 없습니다.');
        if (!m[2] && method === 'GET') return reviewQuestion(qid);
        if (m[2] === '/note' && method === 'PUT') return saveNote(qid, data);
        if (m[2] === '/explanation' && method === 'POST') return explanation(qid);
      }
      throw fail(404, '요청을 찾을 수 없습니다.');
    }

    return { handle, chatPolicy, exportJson, importJson, importState, persistent: !!adapter.persistent, load };
  }

  return { createStore, idbAdapter, memoryAdapter, isLocal, cleanState, validQid };
});
