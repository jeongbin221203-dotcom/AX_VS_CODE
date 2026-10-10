/* 시험 학습 기록 저장소 (window.TSStore) — TS 앱의 SQLite(sessions·attempts·wrong_notes·settings)를
   브라우저 localStorage 한 키(`ts:v1`)로 옮긴 것. 단어 기록(`ward:v1`, Ward)과는 따로 저장한다.

   데이터 모양 (db)
     settings  {target_score, exam_date, ... 문자열 값}      → DEFAULT_SETTINGS
     sessions  [{id, created_at, finished_at, mode, variant, part, level, items:[ref], time_limit, seen_before,
                 requested, total, correct, lc_total, lc_correct, rc_total, rc_correct, lc_est, rc_est, total_est, duration_sec}]
     attempts  [{s:세션id, k:qkey, c:고른 번호(-1=무응답), o:정답 0/1, m:걸린 ms, t:시각, l:등급, y:유형}]  (용량을 줄이려고 짧은 이름)
     notes     {qkey: {part, item_id, qidx, level, qtype, wrong_count, right_streak, status:'open'|'cleared',
                        first_wrong_at, last_wrong_at, last_seen_at, memo}}
     ext       {컬렉션이름: [레코드...]}   — 토플·토익스피킹·오픽처럼 다른 시험이 기록을 넣는 곳 (col() 참고)
   용량: localStorage 는 보통 5~10MB. 풀이 기록(attempts)은 25,000건을 넘으면 오래된 것부터 버린다(세션 요약·오답노트는 남음). */
(function (root) {
  "use strict";
  const U = root.TSU || require("./tsutil.js");
  const KEY = "ts:v1";
  const ATTEMPT_CAP = 25000;
  const WRONG_CLEAR_STREAK = 2;            // 오답노트 문항을 서로 다른 날 2번 맞히면 졸업

  const DEFAULT_SETTINGS = {
    target_score: "800", current_score: "", current_score_at: "", exam_date: "", daily_questions: "40",
    toefl_target: "4.5", toefl_exam_date: "", tsp_exam_date: "", opic_exam_date: "",
    tsp_target: "140", opic_target: "IH", opic_level: "4", opic_survey: "",
  };
  // daily_new_words·tts_rate·tts_accent 는 단어 설정(Ward.settings: daily_new, tts_rate, tts_accent)을 그대로 쓴다.

  const store = (() => { try { return typeof localStorage !== "undefined" ? localStorage : null; } catch (e) { return null; } })();
  const mem = {};                                  // localStorage 가 없을 때(테스트) 쓰는 임시 저장소
  const getItem = k => (store ? store.getItem(k) : (k in mem ? mem[k] : null));
  const setItem = (k, v) => { if (store) store.setItem(k, v); else mem[k] = String(v); };
  const delItem = k => { if (store) store.removeItem(k); else delete mem[k]; };

  let db, lastRaw = null, saveError = false, batchDepth = 0, dirty = false;

  function fresh() { return { v: 1, settings: { ...DEFAULT_SETTINGS }, seq: { session: 0 }, sessions: [], attempts: [], notes: {}, ext: {} }; }
  function normalize(raw) {
    const d = fresh();
    if (!raw || typeof raw !== "object") return d;
    d.settings = { ...DEFAULT_SETTINGS, ...(raw.settings && typeof raw.settings === "object" ? raw.settings : {}) };
    for (const k of Object.keys(d.settings)) d.settings[k] = String(d.settings[k] ?? "");
    d.sessions = Array.isArray(raw.sessions) ? raw.sessions.filter(s => s && typeof s === "object" && Number.isFinite(s.id)) : [];
    d.attempts = Array.isArray(raw.attempts) ? raw.attempts.filter(a => a && typeof a.k === "string") : [];
    d.notes = raw.notes && typeof raw.notes === "object" && !Array.isArray(raw.notes) ? raw.notes : {};
    d.ext = raw.ext && typeof raw.ext === "object" && !Array.isArray(raw.ext) ? raw.ext : {};
    const maxId = d.sessions.reduce((m, s) => Math.max(m, s.id), 0);
    d.seq = { session: Math.max(maxId, Number(raw.seq && raw.seq.session) || 0) };
    return d;
  }
  function load() {
    lastRaw = getItem(KEY);
    try { if (lastRaw) return normalize(JSON.parse(lastRaw)); } catch (e) { /* 깨진 저장소는 새로 시작 */ }
    return fresh();
  }
  db = load();

  /** 다른 탭이 저장했으면 그 내용을 다시 읽는다 (두 탭이 서로의 기록을 덮어쓰지 않게 쓰기 전에 부른다). */
  function sync() {
    const raw = getItem(KEY);
    if (raw !== lastRaw) db = load();
  }
  function save() {
    if (batchDepth) { dirty = true; return; }
    if (db.attempts.length > ATTEMPT_CAP) db.attempts = db.attempts.slice(-ATTEMPT_CAP);
    try { lastRaw = JSON.stringify(db); setItem(KEY, lastRaw); saveError = false; } catch (e) { saveError = true; }
    dirty = false;
  }
  /** 여러 변경을 한 번에 저장한다 (저장은 맨 바깥 batch 가 끝날 때 한 번). */
  function batch(fn) {
    sync();
    batchDepth++;
    try { return fn(); } finally { batchDepth--; if (!batchDepth && dirty) save(); }
  }

  // ---- 설정 ---------------------------------------------------------------
  const settings = () => db.settings;
  function setSettings(patch) {
    sync();
    for (const [k, v] of Object.entries(patch)) if (k in DEFAULT_SETTINGS) db.settings[k] = String(v ?? "");
    save();
  }

  // ---- 세션 ---------------------------------------------------------------
  function createSession(f) {
    sync();
    db.seq.session = Math.max(db.seq.session, db.sessions.reduce((m, s) => Math.max(m, s.id), 0)) + 1;
    const s = { id: db.seq.session, created_at: U.nowStr(), finished_at: null, mode: f.mode, variant: f.variant ?? null,
                part: f.part ?? null, level: f.level ?? null, items: f.items.slice(), time_limit: f.time_limit ?? null,
                seen_before: f.seen_before ?? null, requested: f.requested ?? null, total: 0, correct: 0, lc_total: 0,
                lc_correct: 0, rc_total: 0, rc_correct: 0, lc_est: null, rc_est: null, total_est: null, duration_sec: null };
    db.sessions.push(s);
    save();
    return s.id;
  }
  const getSession = id => db.sessions.find(s => s.id === id) || null;
  function updateSession(id, patch) {
    sync();
    const s = getSession(id);
    if (!s) return null;
    Object.assign(s, patch);
    save();
    return s;
  }
  const sessions = () => db.sessions;
  function deleteSession(id) {
    sync();
    db.sessions = db.sessions.filter(s => s.id !== id);
    db.attempts = db.attempts.filter(a => a.s !== id);
    delItem("ts:draft:" + id);
    save();
  }

  // ---- 풀이 기록·오답노트 -----------------------------------------------------
  const partOf = a => parseInt(a.k.split(":")[0], 10);
  const itemIdOf = a => a.k.split(":")[1];
  const qidxOf = a => parseInt(a.k.split(":")[2], 10);
  const attempts = () => db.attempts;
  const attemptsOf = sid => db.attempts.filter(a => a.s === sid);

  /** 문항 하나를 푼 기록 + 오답노트 갱신. q = Bank.questions() 의 한 항목. 정답 여부(true/false)를 돌려준다. */
  function recordAttempt(sid, q, chosen, elapsedMs, ts) {
    sync();
    const ok = chosen === q.answer;
    db.attempts.push({ s: sid, k: q.qkey, c: chosen, o: ok ? 1 : 0, m: elapsedMs ?? null, t: ts, l: q.level, y: q.qtype });
    if (chosen < 0) { save(); return false; }          // 답하지 않은 문항은 오답노트·통계에 넣지 않는다
    const note = db.notes[q.qkey];
    if (!ok) {
      if (note) { note.wrong_count += 1; note.right_streak = 0; note.status = "open"; note.last_wrong_at = ts; note.last_seen_at = ts; }
      else db.notes[q.qkey] = { part: q.part, item_id: q.item_id, qidx: q.qidx, level: q.level, qtype: q.qtype, wrong_count: 1,
                                right_streak: 0, status: "open", first_wrong_at: ts, last_wrong_at: ts, last_seen_at: ts, memo: "" };
    } else if (note) {
      // 정답 위치를 외워 같은 날 졸업하지 못하게 — 다른 날에 맞혀야 센다
      const sameDay = !!note.last_seen_at && note.last_seen_at.slice(0, 10) === ts.slice(0, 10) && note.right_streak > 0;
      const streak = sameDay ? note.right_streak : note.right_streak + 1;
      note.right_streak = streak;
      if (streak >= WRONG_CLEAR_STREAK) note.status = "cleared";
      note.last_seen_at = ts;
    }
    save();
    return ok;
  }

  const notes = () => db.notes;
  /** 오답노트 목록 (최근 틀린 순). 각 항목에 qkey 가 들어 있다. */
  function wrongNotes(status = "open", part = null, qtype = null) {
    const out = [];
    for (const [qkey, n] of Object.entries(db.notes)) {
      if (n.status !== status || (part && n.part !== part) || (qtype && n.qtype !== qtype)) continue;
      out.push({ qkey, ...n });
    }
    // 최근 오답 순 (같은 시각이면 먼저 만들어진 쪽을 앞에 — 정렬이 항상 같게)
    out.sort((a, b) => (a.last_wrong_at < b.last_wrong_at ? 1 : a.last_wrong_at > b.last_wrong_at ? -1 : 0));
    return out;
  }
  function setNoteMemo(qkey, memo) {
    sync();
    if (db.notes[qkey]) { db.notes[qkey].memo = String(memo).slice(0, 2000); save(); }
  }
  function setNoteStatus(qkey, status) {
    if (status !== "open" && status !== "cleared") throw new Error("상태 값 오류");
    sync();
    if (db.notes[qkey]) { db.notes[qkey].status = status; db.notes[qkey].right_streak = 0; save(); }
  }

  // ---- 시험 중 임시 저장 (새로고침·실수로 닫기 대비) ---------------------------------
  const draftKey = sid => "ts:draft:" + sid;
  function getDraft(sid) { try { return JSON.parse(getItem(draftKey(sid)) || "null"); } catch (e) { return null; } }
  function setDraft(sid, obj) { try { setItem(draftKey(sid), JSON.stringify(obj)); } catch (e) { /* 저장 불가 */ } }
  function clearDraft(sid) { try { delItem(draftKey(sid)); } catch (e) { /* 무시 */ } }

  /** 끝났거나 없어진 세션의 임시 저장을 지운다 (오늘 화면을 열 때마다 불러 저장소를 깨끗하게) */
  function pruneDrafts() {
    if (!store) return 0;
    let n = 0;
    for (const k of Object.keys(store).filter(k => k.startsWith("ts:draft:"))) {
      const s = getSession(parseInt(k.slice(9), 10));
      if (!s || s.finished_at) { delItem(k); n++; }
    }
    return n;
  }

  // ---- 다른 시험이 쓰는 컬렉션 -----------------------------------------------------
  /** col("toefl_attempts") → {all(), add(rec), update(id, patch), remove(id), where(fn)}
      레코드마다 id(숫자, 컬렉션 안에서 증가)와 created_at 이 자동으로 붙는다. 백업에 함께 들어가고 합치기 때 중복이 없게 합쳐진다. */
  function col(name) {
    const arr = () => (db.ext[name] = db.ext[name] || []);
    return {
      all: () => arr(),
      where: fn => arr().filter(fn),
      add(rec) {
        sync();
        const a = arr();
        const r = { ...rec, id: a.reduce((m, x) => Math.max(m, x.id || 0), 0) + 1, created_at: rec.created_at || U.nowStr() };
        a.push(r);
        save();
        return r;
      },
      update(id, patch) { sync(); const r = arr().find(x => x.id === id); if (r) { Object.assign(r, patch); save(); } return r || null; },
      remove(id) { sync(); db.ext[name] = arr().filter(x => x.id !== id); save(); },
    };
  }

  // ---- 백업·복원 ----------------------------------------------------------------
  const data = () => db;
  const exportData = () => JSON.parse(JSON.stringify(db));
  const sessionKey = s => `${s.created_at}|${s.mode}|${s.variant || ""}|${s.items.length}`;
  /** raw: exportData() 가 만든 객체. mode "replace" 는 통째로 바꾸고, "merge" 는 지금 기록을 지키며 더한다
      (세션은 시작 시각·종류로 같은 것을 찾고, 풀이 기록은 중복 없이, 오답노트는 나중에 본 쪽을 남김). */
  function importData(raw, mode = "replace") {
    const inc = normalize(raw);
    sync();
    if (mode !== "merge") {
      db = inc; save();
      return { sessions: db.sessions.length, attempts: db.attempts.length, notes: Object.keys(db.notes).length };
    }
    const before = { sessions: db.sessions.length, attempts: db.attempts.length, notes: Object.keys(db.notes).length };
    const have = new Map(db.sessions.map(s => [sessionKey(s), s.id]));
    const idMap = new Map();
    let nextId = Math.max(db.seq.session, db.sessions.reduce((m, s) => Math.max(m, s.id), 0));
    for (const s of inc.sessions) {
      const k = sessionKey(s);
      if (have.has(k)) { idMap.set(s.id, have.get(k)); continue; }
      nextId += 1;
      idMap.set(s.id, nextId);
      db.sessions.push({ ...s, id: nextId });
      have.set(k, nextId);
    }
    db.seq.session = nextId;
    const seen = new Set(db.attempts.map(a => `${a.s}|${a.k}|${a.t}`));
    for (const a of inc.attempts) {
      const sid = idMap.has(a.s) ? idMap.get(a.s) : a.s;
      const key = `${sid}|${a.k}|${a.t}`;
      if (seen.has(key)) continue;
      seen.add(key);
      db.attempts.push({ ...a, s: sid });
    }
    db.attempts.sort((x, y) => (x.t < y.t ? -1 : x.t > y.t ? 1 : 0));
    db.sessions.sort((x, y) => x.id - y.id);
    for (const [qkey, n] of Object.entries(inc.notes)) {
      const cur = db.notes[qkey];
      if (!cur) db.notes[qkey] = n;
      else if ((n.last_seen_at || "") > (cur.last_seen_at || "")) db.notes[qkey] = { ...n, memo: n.memo || cur.memo || "" };
      else if (!cur.memo && n.memo) cur.memo = n.memo;
    }
    for (const [k, v] of Object.entries(inc.settings)) if (db.settings[k] === DEFAULT_SETTINGS[k] && v !== DEFAULT_SETTINGS[k]) db.settings[k] = v;
    for (const [name, list] of Object.entries(inc.ext)) {
      const mine = (db.ext[name] = db.ext[name] || []);
      const keyOf = r => { const { id, ...rest } = r; return JSON.stringify(rest); };
      const has = new Set(mine.map(keyOf));
      let next = mine.reduce((m, x) => Math.max(m, x.id || 0), 0);
      for (const r of list) { if (has.has(keyOf(r))) continue; has.add(keyOf(r)); mine.push({ ...r, id: ++next }); }
    }
    save();
    return { sessions: db.sessions.length, attempts: db.attempts.length, notes: Object.keys(db.notes).length,
             added: { sessions: db.sessions.length - before.sessions, attempts: db.attempts.length - before.attempts, notes: Object.keys(db.notes).length - before.notes } };
  }
  function reset() { sync(); db = fresh(); save(); }
  const usageBytes = () => (lastRaw ? lastRaw.length : 0);

  root.TSStore = { KEY, ATTEMPT_CAP, WRONG_CLEAR_STREAK, DEFAULT_SETTINGS, settings, setSettings, createSession, getSession, updateSession,
                   sessions, deleteSession, attempts, attemptsOf, partOf, itemIdOf, qidxOf, recordAttempt, notes, wrongNotes,
                   setNoteMemo, setNoteStatus, getDraft, setDraft, clearDraft, pruneDrafts, col, batch, data, exportData, importData, reset,
                   reload: () => { db = load(); }, saveFailed: () => saveError, usageBytes };
  if (typeof module !== "undefined") module.exports = root.TSStore;
})(typeof window !== "undefined" ? window : globalThis);
