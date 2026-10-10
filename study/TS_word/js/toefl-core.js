/* 토플(2026년 1월 개편) 핵심 로직 (window.Toefl) — TS 앱 core/toefl.py 를 브라우저에서 돌린다.
   과제 정의, 문제 은행 읽기, 풀이 기록, 영역별 밴드 추정, 오답 모으기, 실전 모의고사 구성·채점.

   저장: TSStore.col("toefl_attempts") — 풀이 한 문항 {task,item_id,qidx,level,score(0~1),response,created_at}
         TSStore.col("toefl_mocks")    — 모의고사 {created_at,finished_at,plan(문제 번호 목록),result,r,l,s,w,total}
   문제: data/toefl-<과제>.js 11개 (과제별로 필요할 때만 읽음). 개수 요약은 data/meta.js 의 TS_META.toefl.
   점수: 영역별 1~6 밴드(0.5 단위), 종합 = 네 영역 평균을 0.5 단위로 반올림. 난이도 level 1~5 = 목표 밴드 2~6.
   node 에서도 돌도록 document 는 문제 파일을 읽을 때만 쓴다. */
(function (root) {
  "use strict";
  const U = root.TSU || require("./tsutil.js");
  const store = () => root.TSStore || require("./tsstore.js");

  const SECTIONS = {
    R: { name: "Reading", ko: "읽기", time: "약 30분", note: "적응형 · 약 50문항" },
    L: { name: "Listening", ko: "듣기", time: "약 29분", note: "적응형 · 약 47문항 · 이전 문제로 못 돌아감" },
    S: { name: "Speaking", ko: "말하기", time: "약 8분", note: "11문항: 따라 말하기 7 + 인터뷰 4" },
    W: { name: "Writing", ko: "쓰기", time: "약 23분", note: "12문항: 문장 만들기 10 + 이메일 1 + 학술 토론 1" },
  };
  // kind: 화면 종류 · auto: 자동 채점 여부
  const TASKS = {
    r_words: { section: "R", name: "빈칸 단어 완성", en: "Complete the Words", kind: "words", auto: true,
               desc: "학술 문단에서 단어의 빠진 뒷부분 철자를 채운다 (빈칸 10개)." },
    r_daily: { section: "R", name: "일상 글 읽기", en: "Read in Daily Life", kind: "set", auto: true,
               desc: "공지·이메일·문자·일정표 같은 짧은 글을 읽고 2~3문항." },
    r_academic: { section: "R", name: "학술 지문 읽기", en: "Read an Academic Passage", kind: "set", auto: true,
                  desc: "200단어 안팎의 학술 지문을 읽고 5문항." },
    l_response: { section: "L", name: "응답 고르기", en: "Listen and Choose a Response", kind: "response", auto: true,
                  desc: "한 문장을 듣고 가장 자연스러운 대답을 고른다." },
    l_conversation: { section: "L", name: "대화 듣기", en: "Listen to a Conversation", kind: "listen_set", auto: true,
                      desc: "캠퍼스·일상 대화를 듣고 2문항." },
    l_talk: { section: "L", name: "안내·강의 듣기", en: "Announcements & Academic Talks", kind: "listen_set", auto: true,
              desc: "캠퍼스 안내(2문항)와 교수 강의(4문항)." },
    s_repeat: { section: "S", name: "듣고 따라 말하기", en: "Listen and Repeat", kind: "repeat", auto: true,
                desc: "안내 문장 7개를 듣고 그대로 따라 말한다 (점점 길어짐)." },
    s_interview: { section: "S", name: "인터뷰", en: "Take an Interview", kind: "interview", auto: false,
                   desc: "인터뷰 질문 4개에 45초씩 답한다." },
    w_sentence: { section: "W", name: "문장 만들기", en: "Build a Sentence", kind: "sentence", auto: true,
                  desc: "상대의 말에 대한 대답을 단어 덩어리로 어순에 맞게 완성한다." },
    w_email: { section: "W", name: "이메일 쓰기", en: "Write an Email", kind: "email", auto: false,
               desc: "상황에 맞는 이메일을 7분 안에 쓴다 (요구 사항 3가지)." },
    w_discussion: { section: "W", name: "학술 토론 글쓰기", en: "Academic Discussion", kind: "discussion", auto: false,
                    desc: "교수 질문과 학생 두 명의 글을 읽고 10분 안에 내 의견을 쓴다." },
  };
  const TASK_KEYS = Object.keys(TASKS);
  const DEFAULT_N = { r_words: 3, r_daily: 4, r_academic: 2, l_response: 10, l_conversation: 3, l_talk: 2,
                      s_repeat: 1, s_interview: 1, w_sentence: 10, w_email: 1, w_discussion: 1 };

  const CEFR = { 1: "A1", 2: "A2", 3: "B1", 4: "B2", 5: "C1", 6: "C2" };
  // 예전 0~120점 대응 — ETS 공식 환산표(종합 점수): 6=114+, 5.5=107+, 5=95+, 4.5=86+, 4=72+, 3.5=58+, 3=44+, 2.5=34+, 2=24+, 1.5=12+, 1=0+
  const BAND_OLD = { 1: "0~11", 1.5: "12~23", 2: "24~33", 2.5: "34~43", 3: "44~57", 3.5: "58~71", 4: "72~85",
                     4.5: "86~94", 5: "95~106", 5.5: "107~113", 6: "114~120" };
  const LEVEL_BAND = { 1: 2, 2: 3, 3: 4, 4: 5, 5: 6 };
  // 쓰기·말하기 자기 평가 기준 (0~5) → 밴드 1~6
  const RUBRIC = [
    [5, "요구 사항을 모두 충족, 구성이 매끄럽고 어휘·문법이 다양하며 거의 오류 없음"],
    [4, "요구 사항 충족, 이해하기 쉬움. 작은 오류가 있지만 의미 전달에 문제없음"],
    [3, "대체로 충족하지만 설명이 부족하거나 오류가 눈에 띔"],
    [2, "일부만 충족, 문장이 단순하고 오류 때문에 의미가 흐려지는 곳이 있음"],
    [1, "과제와 관련은 있지만 내용이 매우 짧거나 이해하기 어려움"],
    [0, "답하지 못함 / 과제와 무관"],
  ];
  const WRONG_CUT_AUTO = 0.8;     // 자동 채점: 80% 미만이면 틀린 것 (객관식은 0/1)
  const WRONG_CUT_SELF = 0.6;     // 자기 평가(쓰기·인터뷰): 5점 중 3점 미만
  const ATTEMPT_CAP = 6000;       // 풀이 기록이 이만큼을 넘으면 오래된 것부터 정리 (브라우저 저장 용량 보호)

  /** 0.5 단위로 가장 가까운 값(.25 는 올림). 파이썬 round() 는 짝수 쪽이라 5.25 → 5.0 이 되므로 쓰지 않는다. */
  const halfUp = x => Math.floor(x * 2 + 0.5) / 2;
  const cefr = band => (band ? (CEFR[Math.trunc(band)] || "") : "");

  // ---- 표시 (TS 앱 템플릿의 Jinja 출력과 같게) ----------------------------------------------
  /** 평균 점수(0~1) → 정답률 정수 %. Jinja `(a*100)|round|int` = 파이썬 round() 라 .5 는 짝수 쪽 (62.5 → 62). Math.round 는 63 이 된다. */
  const pct = a => U.pyRound(a * 100);
  /** 파이썬 round(x, 1) — 정확히 반으로 갈리는 값(x.25, x.75)만 짝수 쪽 (2.25 → 2.2), 나머지는 toFixed 와 같다. */
  function round1(x) {
    const t = x * 4;
    if (Number.isInteger(t) && t % 2 !== 0) return U.pyRound(x * 10) / 10;
    return Number(x.toFixed(1));
  }
  /** 자기 평가 평균(0~1) → "2.2" 같은 5점 만점 표시 (Jinja `(a*5)|round(1)`: 정수도 "3.0") */
  const self5 = a => round1(a * 5).toFixed(1);
  /** 밴드 표시: 파이썬 float 출력처럼 정수도 "4.0", 없으면 "–" */
  const fmtBand = b => (b === null || b === undefined ? "–" : (Number.isInteger(Number(b)) ? Number(b).toFixed(1) : String(Number(b))));
  /** 정답률/자기 평가 점수 한 칸: 자동 채점 과제는 "62%", 자기 평가 과제는 "2.2/5" */
  const scoreText = (task, a, sep = "/") => (TASKS[task].auto ? pct(a) + "%" : self5(a) + sep + "5");

  let now = () => U.nowStr();
  let today = () => U.todayStr();
  const setClock = (n, t) => { now = n || (() => U.nowStr()); today = t || (() => U.todayStr()); };

  // ---------------------------------------------------------------- 문제 은행
  const items = {};            // task -> [item]
  const byId = new Map();      // "task|id" -> item
  const loading = {};
  let base = "";

  function setItems(task, list) {
    items[task] = list;
    for (const it of list) byId.set(`${task}|${it.id}`, it);
  }
  function loadTask(task) {
    if (!TASKS[task]) return Promise.reject(new Error("알 수 없는 과제: " + task));
    if (items[task]) return Promise.resolve();
    if (root.TS_DATA && root.TS_DATA["tf_" + task]) { setItems(task, root.TS_DATA["tf_" + task]); return Promise.resolve(); }
    if (loading[task]) return loading[task];
    loading[task] = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = `${base}data/toefl-${task}.js`;
      s.onload = () => {
        if (!root.TS_DATA || !root.TS_DATA["tf_" + task]) return reject(new Error(`data/toefl-${task}.js 에 문제가 없습니다`));
        setItems(task, root.TS_DATA["tf_" + task]);
        resolve();
      };
      s.onerror = () => { delete loading[task]; reject(new Error(`data/toefl-${task}.js 를 읽을 수 없습니다 (tools/build_data.py 로 만들어야 합니다)`)); };
      document.head.appendChild(s);
    });
    return loading[task];
  }
  /** 과제 데이터를 읽는다. tasks 생략 = 전부 */
  const load = tasks => Promise.all((tasks || TASK_KEYS).map(loadTask)).then(() => undefined);
  const itemsOf = task => items[task] || [];
  const get = (task, id) => byId.get(`${task}|${id}`) || null;

  const metaOf = () => (root.TS_META && root.TS_META.toefl) || null;
  /** 문제 묶음 수 (데이터를 읽지 않고 요약으로, 없으면 읽은 데이터로) */
  function count(task, level) {
    const m = metaOf();
    if (m && m.tasks && m.tasks[task]) return level ? (m.tasks[task].levels[level] || 0) : m.tasks[task].items;
    return itemsOf(task).filter(it => !level || it.level === level).length;
  }
  const totalItems = () => TASK_KEYS.reduce((a, t) => a + count(t), 0);

  // ---------------------------------------------------------------- 기록
  const attemptsCol = () => store().col("toefl_attempts");
  const mocksCol = () => store().col("toefl_mocks");

  /** 한 문제(묶음)의 결과들을 저장한다. results = [{qidx, score(0~1), response}] → 저장한 개수 */
  function record(task, itemId, level, results) {
    const ts = now();
    const col = attemptsCol();
    let n = 0;
    store().batch(() => {
      for (const r of results) {
        const score = Math.max(0, Math.min(1, Number(r.score) || 0));
        col.add({ created_at: ts, task, item_id: String(itemId), qidx: parseInt(r.qidx, 10) || 0, level, score, response: String(r.response ?? "").slice(0, 3000) });
        n++;
      }
      const all = col.all();
      if (all.length > ATTEMPT_CAP + 200) all.slice(0, all.length - ATTEMPT_CAP).forEach(a => col.remove(a.id));
    });
    return n;
  }
  const allAttempts = () => attemptsCol().all();

  /** 마지막으로 푼 날짜 (YYYY-MM-DD, 없으면 "") */
  const lastDate = () => allAttempts().reduce((m, a) => (a.created_at > m ? a.created_at : m), "").slice(0, 10);
  function lastSeen(task) {
    const out = {};
    for (const a of allAttempts()) if (a.task === task && (!out[a.item_id] || a.created_at > out[a.item_id])) out[a.item_id] = a.created_at;
    return out;
  }
  function taskStats() {
    const acc = {};
    for (const a of allAttempts()) { const x = acc[a.task] = acc[a.task] || { n: 0, sum: 0 }; x.n++; x.sum += a.score; }
    const out = {};
    for (const [t, x] of Object.entries(acc)) out[t] = { n: x.n, avg: x.sum / x.n };
    return out;
  }
  /** 최근 푼 문제 (문제·시각별 한 줄) */
  function recent(limit = 12) {
    const g = new Map();
    for (const a of allAttempts()) {
      const k = `${a.task}\u0000${a.item_id}\u0000${a.created_at}`;
      const x = g.get(k) || { task: a.task, item_id: a.item_id, level: a.level, at: a.created_at, n: 0, sum: 0 };
      x.n++; x.sum += a.score;
      g.set(k, x);
    }
    return [...g.values()]
      .sort((p, q) => (p.task < q.task ? -1 : p.task > q.task ? 1 : p.item_id < q.item_id ? -1 : p.item_id > q.item_id ? 1 : 0))
      .sort((p, q) => (p.at < q.at ? 1 : p.at > q.at ? -1 : 0))
      .slice(0, limit).map(x => ({ task: x.task, item_id: x.item_id, level: x.level, at: x.at, n: x.n, a: x.sum / x.n }));
  }
  /** 마지막으로 푼 회차 기준 틀린 문항이 있는 문제 → {item_id: {at, wrong, n}}. 다시 풀어 맞히면 빠진다. */
  function wrongItems(task) {
    const cut = TASKS[task].auto ? WRONG_CUT_AUTO : WRONG_CUT_SELF;
    const rows = allAttempts().filter(a => a.task === task);
    const maxAt = {};
    for (const a of rows) if (!maxAt[a.item_id] || a.created_at > maxAt[a.item_id]) maxAt[a.item_id] = a.created_at;
    const out = {};
    for (const a of rows) {
      if (a.created_at !== maxAt[a.item_id]) continue;
      const d = out[a.item_id] = out[a.item_id] || { at: a.created_at, wrong: 0, n: 0 };
      d.n++;
      if (a.score < cut) d.wrong++;
    }
    for (const k of Object.keys(out)) if (!out[k].wrong) delete out[k];
    return out;
  }
  /** 오답노트·기록에 보여 줄 문제 한 줄 요약 */
  function itemLabel(task, it) {
    let text = "";
    const first = ["title", "prompt", "context", "situation", "professor", "intro"].find(k => it[k]);
    if (first) text = String(it[first]);
    else text = task === "s_repeat" ? ((it.sentences || [it.text || ""])[0] || "") : String(it.text || "");
    text = text.replace(/\[\[/g, "").replace(/\]\]/g, "").replace(/\|/g, "");
    const topic = it.topic || it.course || it.doc_type || "";
    return (topic ? `[${topic}] ` : "") + (text.length > 90 ? text.slice(0, 90) + "…" : text);
  }
  /** 날짜 × 과제별 푼 문항 수와 평균 (최근 days 일) */
  function history(days = 90) {
    const from = U.addDays(today(), -days);
    const g = new Map();
    for (const a of allAttempts()) {
      const d = a.created_at.slice(0, 10);
      if (a.created_at < from) continue;
      const k = `${d}\u0000${a.task}`;
      const x = g.get(k) || { d, task: a.task, n: 0, ids: new Set(), sum: 0 };
      x.n++; x.ids.add(a.item_id); x.sum += a.score;
      g.set(k, x);
    }
    return [...g.values()].map(x => ({ d: x.d, task: x.task, n: x.n, items: x.ids.size, a: x.sum / x.n }))
      .sort((p, q) => (p.d < q.d ? 1 : p.d > q.d ? -1 : p.task < q.task ? -1 : p.task > q.task ? 1 : 0));
  }

  // ---------------------------------------------------------------- 밴드 추정
  /** 가장 최근 lastN 개 중 난이도별 [개수, 평균 점수] */
  function levelAcc(tasks, lastN = 150) {
    const rows = allAttempts().filter(a => tasks.includes(a.task)).slice(-lastN).reverse();      // 최근 것부터 (파이썬과 합계 순서를 같게)
    const acc = {};
    for (const a of rows) (acc[a.level] = acc[a.level] || []).push(a.score);
    const out = {};
    for (const [lv, v] of Object.entries(acc)) out[lv] = [v.length, v.reduce((p, q) => p + q, 0) / v.length];
    return out;
  }
  /** 난이도 L(=목표 밴드 L+1)에서 65% 이상이면 그 밴드, 45% 이상이면 반 밴드 아래로 본다. acc = {level: [n, avg]} */
  function bandFromLevels(acc, minN = 4) {
    const vals = Object.values(acc || {});
    if (!vals.length || vals.reduce((a, [n]) => a + n, 0) < minN) return null;
    let est = 1.0;
    for (let lv = 1; lv <= 5; lv++) {
      const [n, a] = acc[lv] || [0, 0.0];
      if (n < 2) continue;
      if (a >= 0.65) est = Math.max(est, LEVEL_BAND[lv]);
      else if (a >= 0.45) est = Math.max(est, LEVEL_BAND[lv] - 0.5);
    }
    return est;
  }
  /** 자기 평가 평균(0~5) → 밴드 1~6 */
  function selfBand(tasks, lastN = 20) {
    const rows = allAttempts().filter(a => tasks.includes(a.task)).slice(-lastN).reverse();
    if (!rows.length) return null;
    return halfUp(1 + 5 * rows.reduce((a, r) => a + r.score, 0) / rows.length);
  }
  function sectionBands() {
    const out = {};
    for (const s of Object.keys(SECTIONS)) {
      const auto = TASK_KEYS.filter(t => TASKS[t].section === s && TASKS[t].auto);
      const manual = TASK_KEYS.filter(t => TASKS[t].section === s && !TASKS[t].auto);
      const parts = [];
      if (auto.length) { const b = bandFromLevels(levelAcc(auto)); if (b !== null) parts.push(b); }
      if (manual.length) { const b = selfBand(manual); if (b !== null) parts.push(b); }
      out[s] = parts.length ? halfUp(parts.reduce((a, b) => a + b, 0) / parts.length) : null;
    }
    return out;
  }
  function overallBand(bands) {
    const vals = Object.values(bands).filter(b => b !== null && b !== undefined);
    if (vals.length < 4) return null;
    return halfUp(vals.reduce((a, b) => a + b, 0) / 4);      // 실제 시험: 네 영역 평균을 0.5 단위로 반올림
  }
  const targetBand = () => { const t = parseFloat(store().settings().toefl_target); return Number.isFinite(t) ? t : 4.5; };
  /** 홈에서 권하는 난이도: 종합(없으면 목표) 밴드 이상이 되는 첫 난이도 */
  function recommendedLevel(overall, target) {
    const goal = overall || target;
    for (const lv of [1, 2, 3, 4, 5]) if (LEVEL_BAND[lv] >= goal) return lv;
    return 3;
  }

  // ---------------------------------------------------------------- 연습 문제 뽑기
  /** 안 푼 문제 → 오래전에 푼 문제 순 (같으면 무작위) */
  function pick(task, level, n, rng) {
    const seen = lastSeen(task);
    const pool = itemsOf(task).filter(it => !level || it.level === level);
    U.shuffle(pool, rng);
    pool.sort((a, b) => ((seen[a.id] || "") < (seen[b.id] || "") ? -1 : (seen[a.id] || "") > (seen[b.id] || "") ? 1 : 0));
    return pool.slice(0, n);
  }

  // ---------------------------------------------------------------- 실전 모의고사
  // 읽기·듣기는 2단계 적응형: 1모듈(중간 난이도) 정답률이 ADAPT_CUT 이상이면 2모듈은 어려운 문제, 아니면 쉬운 문제.
  const ADAPT_CUT = 0.6;
  const M1_LEVELS = [3], HARD_LEVELS = [4, 5], EASY_LEVELS = [1, 2];
  // [과제, 개수, 담화 종류] — 실제 시험 문항 수를 줄여 한 모듈 15분 안팎
  const READ_MODULE = [["r_words", 1, null], ["r_daily", 2, null], ["r_academic", 1, null]];
  const LISTEN_MODULE = [["l_response", 6, null], ["l_conversation", 1, null], ["l_talk", 1, "announcement"], ["l_talk", 1, "academic"]];
  const MOCK_ORDER = ["R", "L", "W", "S"];       // 개편 시험 순서 (ETS 공식: Reading → Listening → Writing → Speaking)

  function pickKind(task, levels, n, kind, used, rng) {
    const seen = lastSeen(task);
    const ok = it => !used.has(it.id) && (kind === null || it.kind === kind);
    const pool = itemsOf(task).filter(it => levels.includes(it.level) && ok(it));
    U.shuffle(pool, rng);
    pool.sort((a, b) => ((seen[a.id] || "") < (seen[b.id] || "") ? -1 : (seen[a.id] || "") > (seen[b.id] || "") ? 1 : 0));
    if (pool.length < n) {                    // 모자라면 가까운 난이도로 채움
      const inPool = new Set(pool);
      const rest = itemsOf(task).filter(it => ok(it) && !inPool.has(it));
      const dist = it => Math.min(...levels.map(lv => Math.abs(it.level - lv)));
      rest.sort((a, b) => dist(a) - dist(b));
      pool.push(...rest);
    }
    const out = pool.slice(0, n);
    out.forEach(it => used.add(it.id));
    return out;
  }
  const modulePick = (spec, levels, used, rng) => {
    const out = [];
    for (const [task, n, kind] of spec) for (const it of pickKind(task, levels, n, kind, used, rng)) out.push({ task, id: it.id });
    return out;
  };
  /** 모의고사 계획 (문제는 {task, id} 번호로만 적는다 — 저장 용량을 아끼려고). 문제 데이터가 모두 읽혀 있어야 한다. */
  function buildMock(target, rng) {
    const used = new Set();
    const lv = Math.max(1, Math.min(5, Math.floor(target + 0.5) - 1));        // 말하기·쓰기 난이도 = 목표 밴드 근처
    const sections = {
      R: { name: "Reading", minutes_per_module: 15, modules: [
        modulePick(READ_MODULE, M1_LEVELS, used, rng),
        { hard: modulePick(READ_MODULE, HARD_LEVELS, used, rng), easy: modulePick(READ_MODULE, EASY_LEVELS, used, rng) }] },
      L: { name: "Listening", minutes_per_module: 14, modules: [
        modulePick(LISTEN_MODULE, M1_LEVELS, used, rng),
        { hard: modulePick(LISTEN_MODULE, HARD_LEVELS, used, rng), easy: modulePick(LISTEN_MODULE, EASY_LEVELS, used, rng) }] },
      S: { name: "Speaking", items: modulePick([["s_repeat", 1, null], ["s_interview", 1, null]], [lv], used, rng) },
      W: { name: "Writing", items: modulePick([["w_sentence", 10, null]], [Math.max(1, lv - 1), lv, Math.min(5, lv + 1)], used, rng)
        .concat(modulePick([["w_email", 1, null], ["w_discussion", 1, null]], [lv], used, rng)) },
    };
    return { order: MOCK_ORDER.slice(), sections, adapt_cut: ADAPT_CUT, target, level: lv };
  }
  function createMock(target, rng) {
    const plan = buildMock(target, rng);
    return mocksCol().add({ finished_at: null, plan, result: null, r: null, l: null, s: null, w: null, total: null }).id;
  }
  const getMock = id => mocksCol().all().find(m => m.id === id) || null;
  const listMocks = (limit = 20) => mocksCol().all().filter(m => m.finished_at).slice().sort((a, b) => b.id - a.id).slice(0, limit);
  const unfinishedMocks = () => mocksCol().all().filter(m => !m.finished_at).slice().sort((a, b) => b.id - a.id);

  /** payload = {items:[{task, item_id, results:[{qidx, score, response}]}], routes:{R:"hard"}, duration_sec}.
      풀이 기록을 저장하고(연습 밴드에도 반영) 이 시험만으로 영역별 밴드를 계산한다. 이미 끝난 시험이면 그대로 돌려준다. */
  function finishMock(mid, payload) {
    const m = getMock(mid);
    if (!m) throw new Error("모의고사가 없습니다.");
    if (m.finished_at) return m;
    const auto = {}, selfs = {};
    for (const s of Object.keys(SECTIONS)) { auto[s] = {}; selfs[s] = []; }
    const detail = [], rows = [];
    for (const row of (payload && payload.items) || []) {                 // 먼저 모두 검사 — 중간에 실패해도 일부만 저장되지 않게
      if (!row || typeof row !== "object") throw new Error("답안 형식이 올바르지 않습니다.");
      const task = String(row.task), iid = String(row.item_id);
      const it = get(task, iid);
      const results = row.results || [];
      if (!it || !TASKS[task] || !Array.isArray(results) || !results.length) continue;
      if (!results.every(r => r && typeof r === "object")) throw new Error("답안 형식이 올바르지 않습니다.");
      const sc = results.map(r => Math.max(0, Math.min(1, Number(r.score) || 0)));
      rows.push([task, iid, it, results, sc]);
    }
    return store().batch(() => {
      for (const [task, iid, it, results, sc] of rows) {
        record(task, iid, it.level, results);
        const sec = TASKS[task].section;
        if (TASKS[task].auto) (auto[sec][it.level] = auto[sec][it.level] || []).push(...sc);
        else selfs[sec].push(...sc);
        detail.push({ task, item_id: iid, level: it.level, avg: sc.reduce((a, b) => a + b, 0) / sc.length, n: sc.length });
      }
      const bands = {};
      for (const s of Object.keys(SECTIONS)) {
        const parts = [];
        const lvAcc = {};
        for (const [lvl, v] of Object.entries(auto[s])) lvAcc[lvl] = [v.length, v.reduce((a, b) => a + b, 0) / v.length];
        if (Object.keys(lvAcc).length) { const b = bandFromLevels(lvAcc, 1); if (b !== null) parts.push(b); }
        if (selfs[s].length) parts.push(halfUp(1 + 5 * selfs[s].reduce((a, b) => a + b, 0) / selfs[s].length));
        bands[s] = parts.length ? halfUp(parts.reduce((a, b) => a + b, 0) / parts.length) : null;
      }
      const total = overallBand(bands);
      const result = { bands, total, routes: (payload && payload.routes) || {}, detail, duration_sec: (payload && payload.duration_sec) ?? null };
      return mocksCol().update(mid, { finished_at: now(), result, plan: null, r: bands.R, l: bands.L, s: bands.S, w: bands.W, total });
    });
  }
  const deleteMock = id => mocksCol().remove(id);

  root.Toefl = {
    SECTIONS, TASKS, TASK_KEYS, DEFAULT_N, CEFR, BAND_OLD, LEVEL_BAND, RUBRIC, WRONG_CUT_AUTO, WRONG_CUT_SELF, ATTEMPT_CAP,
    ADAPT_CUT, M1_LEVELS, HARD_LEVELS, EASY_LEVELS, READ_MODULE, LISTEN_MODULE, MOCK_ORDER,
    halfUp, cefr, pct, round1, self5, fmtBand, scoreText, setClock, setItems, load, loadTask, itemsOf, get, count, totalItems,
    record, lastDate, lastSeen, taskStats, recent, wrongItems, itemLabel, history, levelAcc, bandFromLevels, selfBand, sectionBands, overallBand,
    targetBand, recommendedLevel, pick, buildMock, createMock, getMock, listMocks, unfinishedMocks, finishMock, deleteMock,
  };
  if (typeof module !== "undefined") module.exports = root.Toefl;
})(typeof window !== "undefined" ? window : globalThis);
