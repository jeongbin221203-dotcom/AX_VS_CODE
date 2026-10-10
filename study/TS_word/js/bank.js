/* 토익 문제 은행 (window.Bank) — TS 앱 core/content.py 의 Bank 를 브라우저에서 돌린다.
   문제는 data/toeic-p1.js ~ p7.js (파트별)에 있고, 필요한 파트만 <script> 로 읽는다 (file:// 에서도 동작).

   용어
   - item: 묶음 하나 (Part 1·2·5 는 문항 1개, Part 3·4·6·7 은 문항 여러 개인 세트). ref = "파트:id" (예 "5:p5-001")
   - question: 채점 단위 문항. qkey = "파트:id:문항번호" (예 "3:p3-004:1")
   문제 데이터에는 정답·해설이 들어 있다(개인 학습용). 풀이 화면에는 publicItem() 으로 정답을 뺀 사본만 넘기고,
   채점 뒤에만 reveal() 로 정답·해설을 보여 준다. */
(function (root) {
  "use strict";
  const S = root.Score || require("./scoring.js");
  const U = root.TSU || require("./tsutil.js");

  const items = {};            // part -> [item]
  const byId = new Map();      // ref -> item
  const loading = {};          // part -> Promise
  let loadedBase = "";         // data/ 폴더 앞 경로 (기본 "")

  const ref = item => `${item.part}:${item.id}`;
  const splitRef = r => { const i = r.indexOf(":"); return [parseInt(r.slice(0, i), 10), r.slice(i + 1)]; };

  function setItems(part, list) {
    const arr = list.map(it => ({ ...it, part }));
    items[part] = arr;
    for (const it of arr) byId.set(ref(it), it);
  }

  /** 파트 데이터를 읽는다. parts 생략 = 전부. 이미 읽었으면 바로 끝난다. */
  function load(parts) {
    parts = (parts || S.PARTS).filter(p => S.PARTS.includes(p));
    return Promise.all(parts.map(loadPart)).then(() => undefined);
  }
  function loadPart(part) {
    if (items[part]) return Promise.resolve();
    if (root.TS_DATA && root.TS_DATA["p" + part]) { setItems(part, root.TS_DATA["p" + part]); return Promise.resolve(); }
    if (loading[part]) return loading[part];
    loading[part] = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = `${loadedBase}data/toeic-p${part}.js`;
      s.onload = () => {
        if (!root.TS_DATA || !root.TS_DATA["p" + part]) return reject(new Error(`data/toeic-p${part}.js 에 문제가 없습니다`));
        setItems(part, root.TS_DATA["p" + part]);
        resolve();
      };
      s.onerror = () => { delete loading[part]; reject(new Error(`data/toeic-p${part}.js 를 읽을 수 없습니다 (tools/build_data.py 로 만들어야 합니다)`)); };
      document.head.appendChild(s);
    });
    return loading[part];
  }

  const item = r => byId.get(r) || null;
  const itemsOf = part => items[part] || [];

  /** item 을 채점 단위 문항 목록으로 편다. */
  function questions(it) {
    const { part, id, level } = it;
    if (S.SET_PARTS.includes(part)) {
      return it.questions.map((q, i) => ({ qkey: `${part}:${id}:${i}`, part, item_id: id, qidx: i, level, qtype: q.type, answer: q.answer, explanation: q.explanation }));
    }
    return [{ qkey: `${part}:${id}:0`, part, item_id: id, qidx: 0, level, qtype: it.type, answer: it.answer, explanation: it.explanation }];
  }
  function question(qkey) {
    const [part, iid, qidx] = qkey.split(":");
    const it = item(`${part}:${iid}`);
    if (!it) return null;
    const qs = questions(it), i = parseInt(qidx, 10);
    return i >= 0 && i < qs.length ? qs[i] : null;
  }
  function countQuestions(part, level, qtype) {
    let n = 0;
    for (const it of itemsOf(part)) {
      if (level && it.level !== level) continue;
      n += questions(it).filter(q => !qtype || q.qtype === qtype).length;
    }
    return n;
  }
  function types(part) {
    const seen = [];
    for (const it of itemsOf(part)) for (const q of questions(it)) if (!seen.includes(q.qtype)) seen.push(q.qtype);
    return seen;
  }

  // ---- 뽑기 --------------------------------------------------------------
  /** 조건에 맞는 item 을 문항 수가 nQuestions 에 이를 때까지 뽑아 ref 목록으로 돌려준다.
      lastSeen: {ref: 마지막으로 푼 시각} — 안 푼 문제 먼저, 그다음 오래전에 푼 문제 순으로 낸다.
      opts: {levels, qtype, exclude:Set, rng, graphic, lastSeen} */
  function pick(part, nQuestions, opts = {}) {
    const { levels, qtype, exclude, graphic, lastSeen } = opts;
    const rng = opts.rng || U.makeRng();
    let pool = itemsOf(part).filter(it =>
      (!levels || !levels.length || levels.includes(it.level)) &&
      (!qtype || questions(it).some(q => q.qtype === qtype)) &&
      (!exclude || !exclude.has(ref(it))) &&
      (graphic === undefined || graphic === null || !!it.graphic === graphic));
    U.shuffle(pool, rng);
    if (lastSeen) {
      pool = pool.map((it, i) => [it, i]);
      pool.sort((a, b) => { const x = lastSeen[ref(a[0])] || "", y = lastSeen[ref(b[0])] || ""; return x < y ? -1 : x > y ? 1 : a[1] - b[1]; });
      pool = pool.map(p => p[0]);
    }
    const out = [];
    let count = 0;
    for (const it of pool) {
      if (count >= nQuestions) break;
      out.push(ref(it));
      count += questions(it).length;
    }
    return out;
  }

  /** Part 7 은 단일/이중/삼중 지문을 나눠 뽑는다. */
  function pickP7(singlesQ, doubles, triples, opts = {}) {
    const { levels, exclude, lastSeen } = opts;
    const rng = opts.rng || U.makeRng();
    const pool = kind => {
      let p = itemsOf(7).filter(it => it.kind === kind && (!levels || !levels.length || levels.includes(it.level)) && (!exclude || !exclude.has(ref(it))));
      U.shuffle(p, rng);
      if (lastSeen) {
        p = p.map((it, i) => [it, i]);
        p.sort((a, b) => { const x = lastSeen[ref(a[0])] || "", y = lastSeen[ref(b[0])] || ""; return x < y ? -1 : x > y ? 1 : a[1] - b[1]; });
        p = p.map(x => x[0]);
      }
      return p;
    };
    const out = [];
    let count = 0;
    const rest = pool("single");
    while (count < singlesQ && rest.length) {
      const need = singlesQ - count;
      // 목표 문항 수에 정확히 맞춘다 (실제 시험 단일 지문 29문항). 남은 수가 1이 되는 세트는 피한다.
      let pk = rest.find(it => it.questions.length <= need && need - it.questions.length !== 1);
      if (!pk) pk = need >= 2 ? rest[0] : null;
      if (!pk) break;
      rest.splice(rest.indexOf(pk), 1);
      out.push(ref(pk));
      count += pk.questions.length;
    }
    for (const it of pool("double").slice(0, doubles)) out.push(ref(it));
    for (const it of pool("triple").slice(0, triples)) out.push(ref(it));
    return out;
  }

  // ---- 화면에 보낼 형태 -----------------------------------------------------
  const HIDDEN = ["answer", "explanation", "translation"];
  /** 정답·해설·번역을 뺀 사본. 듣기 스크립트는 음성 재생에 필요하므로 포함하되 화면에는 채점 뒤에만 보인다. */
  function publicItem(r) {
    const it = byId.get(r);
    const out = {};
    for (const [k, v] of Object.entries(it)) if (!HIDDEN.includes(k)) out[k] = v;
    out.ref = r;
    if (it.questions) out.questions = it.questions.map(q => { const c = {}; for (const [k, v] of Object.entries(q)) if (!HIDDEN.includes(k)) c[k] = v; return c; });
    return out;
  }
  /** 채점 뒤 보여 줄 정답·해설·번역. */
  function reveal(r) {
    const it = byId.get(r);
    return { ref: r, translation: it.translation || "",
             questions: questions(it).map(q => ({ qidx: q.qidx, answer: q.answer, explanation: q.explanation, type: q.qtype })) };
  }

  // ---- 문제 데이터를 읽지 않고 보는 개수 요약 (data/meta.js = window.TS_META) -----------------
  const meta = () => (root.TS_META && root.TS_META.toeic) || null;
  /** 파트의 문항 수. level/qtype 을 주면 그 조건만. (데이터를 읽어 두었으면 실제 데이터에서 센다) */
  function count(part, level, qtype) {
    if (items[part]) return countQuestions(part, level, qtype);
    const m = meta() && meta().parts[part];
    if (!m) return 0;
    if (qtype) return (m.types.find(t => t[0] === qtype) || [0, 0])[1];       // 요약은 등급×유형 교차를 갖지 않는다 (유형 합계)
    return level ? m.levels[level] || 0 : m.questions;
  }
  const typeList = part => (items[part] ? types(part).map(t => [t, countQuestions(part, null, t)]) : ((meta() && meta().parts[part] && meta().parts[part].types) || []));

  root.Bank = { meta, count, typeList, ref, splitRef, setItems, load, loadPart, item, itemsOf, questions, question, countQuestions, types, pick, pickP7,
                publicItem, reveal, loaded: part => !!items[part], setBase: b => { loadedBase = b; } };
  if (typeof module !== "undefined") module.exports = root.Bank;
})(typeof window !== "undefined" ? window : globalThis);
