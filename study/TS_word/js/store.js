/* 단어 저장소 + 간격 반복(SM-2 변형). TS 앱의 core/srs.py 와 같은 규칙을 브라우저에서 돌린다.
   평가: 0 다시 · 3 어려움 · 4 보통 · 5 쉬움.  모름 → 내일 다시, 알면 1일 → 4일 → 이전 간격 × EF, 21일 이상 = 암기 완료.
   학습 기록은 이 브라우저의 localStorage 에만 저장된다(설정 → 백업으로 파일에 옮길 수 있다). */
(function () {
  "use strict";
  const KEY = "ward:v1";
  const MASTERED_DAYS = 21;
  const WEAK_MIN_FAILS = 2;
  const LOG_CAP = 60000;
  const TIERS = { core: "필수", stretch: "도전" };
  const GRADES = {
    toeic: [
      { level: 1, name: "Orange", range: "10~215점" }, { level: 2, name: "Brown", range: "220~465점" },
      { level: 3, name: "Green", range: "470~725점" }, { level: 4, name: "Blue", range: "730~855점" },
      { level: 5, name: "Gold", range: "860~990점" },
    ],
    toefl: [2, 3, 4, 5, 6].map((b, i) => ({ level: i + 1, name: "밴드 " + b, range: "CEFR " + ["A2", "B1", "B2", "C1", "C2"][i] })),
  };
  const SET_NAMES = { toeic: "토익 단어", toefl: "토플 학술 어휘" };
  // 토익 점수 → 등급(1~5). TS 앱의 등급표(Orange 10~, Brown 220~, Green 470~, Blue 730~, Gold 860~)와 같다
  const levelFromScore = score => (score >= 860 ? 5 : score >= 730 ? 4 : score >= 470 ? 3 : score >= 220 ? 2 : 1);
  const DEFAULTS = { set: "toeic", daily_new: 20, tts_rate: 1, tts_accent: "mix", my_level: 0 };   // my_level 0 = 자동(1등급부터)

  const pad = n => String(n).padStart(2, "0");
  const todayStr = (d = new Date()) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const nowStr = () => { const d = new Date(); return `${todayStr(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; };
  function addDays(dateStr, n) {
    const [y, m, d] = dateStr.split("-").map(Number);
    return todayStr(new Date(y, m - 1, d + n));
  }

  // ---- 저장 ------------------------------------------------------------------------
  let db = load();
  let saveError = false;

  function fresh() { return { v: 1, settings: { ...DEFAULTS }, cards: {}, log: [], quiz: [] }; }
  function load() {
    try {
      const raw = JSON.parse(localStorage.getItem(KEY) || "null");
      if (raw && typeof raw === "object") return normalize(raw);
    } catch (e) { /* 저장소를 못 읽으면 새로 시작 */ }
    return fresh();
  }
  function normalize(raw) {
    const d = fresh();
    d.settings = { ...DEFAULTS, ...(raw.settings || {}) };
    d.cards = raw.cards && typeof raw.cards === "object" ? raw.cards : {};
    d.log = Array.isArray(raw.log) ? raw.log : [];
    d.quiz = Array.isArray(raw.quiz) ? raw.quiz : [];
    return d;
  }
  function save() {
    if (db.log.length > LOG_CAP) db.log = db.log.slice(-LOG_CAP);
    if (db.quiz.length > LOG_CAP) db.quiz = db.quiz.slice(-LOG_CAP);
    try { localStorage.setItem(KEY, JSON.stringify(db)); saveError = false; } catch (e) { saveError = true; }
  }

  // ---- 단어 데이터 -----------------------------------------------------------------
  const settings = () => db.settings;
  function setSetting(patch) { Object.assign(db.settings, patch); save(); }
  const currentSet = () => (db.settings.set === "toefl" ? "toefl" : "toeic");

  function loadWords() {
    const set = currentSet();
    return new Promise((resolve, reject) => {
      if (window.WARD_DATA && window.WARD_DATA[set]) return resolve(window.WARD_DATA[set]);
      const s = document.createElement("script");
      s.src = `data/${set}.js`;
      s.onload = () => resolve(window.WARD_DATA[set]);
      s.onerror = () => reject(new Error(`data/${set}.js 를 읽을 수 없습니다 (tools/build_data.py 로 만들어야 합니다)`));
      document.head.appendChild(s);
    });
  }
  let words = [], byId = new Map();
  async function init() {
    words = await loadWords();
    byId = new Map(words.map(w => [w.id, w]));
    return words;
  }

  // ---- 카드 ------------------------------------------------------------------------
  const cardOf = id => db.cards[id] || null;
  const seen = c => !!c && c.reps + c.lapses > 0;
  function stateOf(c) {
    if (!seen(c)) return "new";
    return c.interval >= MASTERED_DAYS ? "mastered" : "learning";
  }

  // 파이썬 round() 와 같은 반올림(.5 는 짝수 쪽) — Math.round 는 6.5 → 7 이라 TS 앱과 복습 일정이 하루 어긋난다
  function pyRound(x) {
    const f = Math.floor(x), d = x - f;
    if (d < 0.5) return f;
    if (d > 0.5) return f + 1;
    return f % 2 === 0 ? f : f + 1;
  }

  function schedule(ef, interval, reps, grade) {
    if (![0, 3, 4, 5].includes(grade)) throw new Error("grade must be one of 0, 3, 4, 5");
    if (grade < 3) { reps = 0; interval = 1; }
    else {
      reps += 1;
      if (reps === 1) interval = 1;
      else if (reps === 2) interval = 4;
      else interval = Math.max(interval + 1, pyRound(interval * ef));
      if (grade === 5) interval = Math.max(interval + 1, pyRound(interval * 1.3));
    }
    ef = Math.max(1.3, ef + 0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02));
    return [Number(ef.toFixed(3)), interval, reps];
  }

  function review(id, grade) {
    const ts = nowStr();
    const old = db.cards[id];
    const wasNew = !old;
    let [ef, interval, reps, lapses] = wasNew ? [2.5, 0, 0, 0] : [old.ef, old.interval, old.reps, old.lapses];
    [ef, interval, reps] = schedule(ef, interval, reps, grade);
    if (grade < 3) lapses += 1;                 // 처음 보는 단어를 틀려도 '본 카드'로 세어 내일 복습에 들어가게 한다
    db.cards[id] = { ef, interval, reps, lapses, due: addDays(todayStr(), interval), first_seen: old ? old.first_seen : ts,
                     last_review: ts, starred: old ? old.starred : 0 };
    db.log.push({ i: id, g: grade, n: wasNew ? 1 : 0, t: ts });
    save();
    return db.cards[id];
  }

  function quizAnswer(id, ok) {
    db.quiz.push({ i: id, o: ok ? 1 : 0, t: nowStr() });
    save();
    if (ok) return { scheduled: false };
    const c = review(id, 0);                    // 틀리면 복습 카드에 '다시'로 넣는다
    return { scheduled: true, due: c.due };
  }

  function toggleStar(id) {
    const c = db.cards[id];
    if (!c) {
      const ts = nowStr();
      db.cards[id] = { ef: 2.5, interval: 0, reps: 0, lapses: 0, due: todayStr(), first_seen: ts, last_review: ts, starred: 1 };
      save();
      return true;
    }
    c.starred = c.starred ? 0 : 1;
    save();
    return !!c.starred;
  }

  function failCounts() {
    const out = {};
    for (const l of db.log) if (l.g === 0) out[l.i] = (out[l.i] || 0) + 1;
    return out;
  }

  function recentlyMissed() {
    const ev = db.log.map(l => [l.i, l.t, l.g > 0 ? 1 : 0]).concat(db.quiz.map(q => [q.i, q.t, q.o]));
    ev.sort((a, b) => (a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0));
    const last = {};
    for (const [id, , ok] of ev) last[id] = ok;
    return new Set(Object.keys(last).filter(id => !last[id]));
  }

  function newLearnedToday(ids) {
    const d = todayStr();
    return db.log.filter(l => l.n === 1 && l.t.slice(0, 10) === d && (!ids || ids.has(l.i))).length;
  }

  // 오늘 볼 카드: 복습 예정(due) 먼저, 그다음 새 단어(하루 한도까지)
  function queue({ level = null, tier = null, starredOnly = false, startLevel = null, dailyNew } = {}) {
    const today = todayStr();
    if (startLevel === null) startLevel = Number(db.settings.my_level) || null;   // 내 등급부터 새 단어 (700점대가 1등급 단어부터 나오지 않게)
    dailyNew = dailyNew ?? Number(db.settings.daily_new) ?? 20;
    let ws = words.filter(w => (!level || w.level === level) && (!tier || w.tier === tier));
    ws = ws.slice().sort((a, b) =>
      (Number(!!startLevel && a.level < startLevel) - Number(!!startLevel && b.level < startLevel)) ||
      (a.level - b.level) || (Number(a.tier !== "core") - Number(b.tier !== "core")));
    let due = ws.filter(w => seen(db.cards[w.id]) && db.cards[w.id].due <= today && (!starredOnly || db.cards[w.id].starred));
    due.sort((a, b) => (db.cards[a.id].due < db.cards[b.id].due ? -1 : db.cards[a.id].due > db.cards[b.id].due ? 1 : 0));
    const newLeft = Math.max(0, dailyNew - newLearnedToday(new Set(words.map(w => w.id))));
    let fresh = starredOnly ? [] : ws.filter(w => !seen(db.cards[w.id])).slice(0, newLeft);
    if (starredOnly) due = ws.filter(w => db.cards[w.id] && db.cards[w.id].starred);
    return { due, new: fresh, newLeft };
  }

  function levelProgress() {
    const today = todayStr();
    return GRADES[currentSet()].map(g => {
      const ws = words.filter(w => w.level === g.level);
      const sw = ws.filter(w => seen(db.cards[w.id]));
      const tiers = {};
      for (const t of ["core", "stretch"]) {
        const tw = ws.filter(w => w.tier === t);
        const tc = tw.filter(w => seen(db.cards[w.id]));
        tiers[t] = { total: tw.length, seen: tc.length, mastered: tc.filter(w => db.cards[w.id].interval >= MASTERED_DAYS).length };
      }
      return { ...g, total: ws.length, seen: sw.length, tiers,
               mastered: sw.filter(w => db.cards[w.id].interval >= MASTERED_DAYS).length,
               due: sw.filter(w => db.cards[w.id].due <= today).length };
    });
  }

  function streak() {
    const days = new Set(db.log.map(l => l.t.slice(0, 10)).concat(db.quiz.map(q => q.t.slice(0, 10))));
    let d = todayStr(), n = 0;
    if (!days.has(d)) d = addDays(d, -1);
    while (days.has(d)) { n++; d = addDays(d, -1); }
    return n;
  }

  function todayCounts() {
    const d = todayStr();
    return { reviewed: db.log.filter(l => l.t.slice(0, 10) === d).length, quiz: db.quiz.filter(q => q.t.slice(0, 10) === d).length };
  }

  // ---- 백업 ------------------------------------------------------------------------
  const exportJSON = () => JSON.stringify({ app: "ts-word", ...db }, null, 0);
  /* mode "replace": 지금 기록을 파일 내용으로 바꾼다 / "merge": 합친다(카드는 나중에 복습한 쪽, 별표는 어느 한쪽이라도, 기록은 중복 없이) */
  function importJSON(text, mode = "replace") {
    const raw = JSON.parse(text);
    if (!raw || typeof raw !== "object" || !raw.cards || typeof raw.cards !== "object") throw new Error("백업 파일 모양이 아닙니다");
    const inc = normalize(raw);
    if (mode !== "merge") { db = inc; save(); return { cards: Object.keys(db.cards).length }; }
    let added = 0;
    for (const [id, c] of Object.entries(inc.cards)) {
      const cur = db.cards[id];
      if (!cur) { db.cards[id] = c; added++; }
      else if ((c.last_review || "") > (cur.last_review || "")) db.cards[id] = { ...c, starred: c.starred || cur.starred ? 1 : 0 };
      else if (c.starred) cur.starred = 1;
    }
    const union = (mine, theirs, keyOf) => {
      const seen = new Set(mine.map(keyOf));
      for (const x of theirs) if (!seen.has(keyOf(x))) { mine.push(x); seen.add(keyOf(x)); }
      return mine.sort((a, b) => (a.t < b.t ? -1 : a.t > b.t ? 1 : 0));
    };
    db.log = union(db.log, inc.log, l => `${l.i}|${l.t}|${l.g}`);
    db.quiz = union(db.quiz, inc.quiz, q => `${q.i}|${q.t}|${q.o}`);
    save();
    return { cards: Object.keys(db.cards).length, added };
  }
  function reset() { db = fresh(); save(); }

  window.Ward = {
    GRADES, TIERS, SET_NAMES, MASTERED_DAYS, WEAK_MIN_FAILS, levelFromScore,
    todayStr, init, settings, setSetting, currentSet,
    words: () => words, wordById: id => byId.get(id), grades: () => GRADES[currentSet()],
    cardOf, stateOf, seen, schedule, review, quizAnswer, toggleStar, failCounts, recentlyMissed,
    newLearnedToday, queue, levelProgress, streak, todayCounts,
    exportJSON, importJSON, reset, saveFailed: () => saveError,
  };
})();
