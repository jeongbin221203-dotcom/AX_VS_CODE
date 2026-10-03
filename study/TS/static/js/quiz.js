/* 풀이 엔진
   모드: practice·review = 문제마다 바로 채점 / diagnostic·mock = 끝에 한 번에 제출 / result = 채점 결과 다시 보기
   파트별 화면: 1 사진 묘사, 2 질의응답, 3 대화, 4 담화 (브라우저 음성), 5 단문 빈칸, 6 장문 빈칸, 7 독해 */
(function () {
  "use strict";
  const P = TS.data("payload");
  const esc = TS.esc;
  const L = ["A", "B", "C", "D"];
  const INSTANT = P.mode === "practice" || P.mode === "review";
  const EXAM = P.mode === "diagnostic" || P.mode === "mock";
  const RESULT = P.mode === "result";
  const REAL = EXAM && !!P.real;           // 실전 모의고사: LC 자동 진행, 되돌아가기 없음
  const PART_NAME = { 1: "사진 묘사", 2: "질의응답", 3: "짧은 대화", 4: "짧은 담화", 5: "단문 빈칸", 6: "장문 빈칸", 7: "독해" };
  const GRADE = { 1: "Orange", 2: "Brown", 3: "Green", 4: "Blue", 5: "Gold" };
  const isLC = it => P.lc_parts.includes(it.part);
  const nq = it => (it.questions ? it.questions.length : 1);

  const items = P.items;
  const graded = P.graded || {};
  const answers = {};          // ref -> {qidx: 선택}
  const elapsed = {};          // ref -> ms
  const played = {};           // 시험 모드: 한 번 재생한 듣기 문제
  let idx = 0, curQ = 0, started = false, rcUsed = 0, totalMs = 0, submitting = false, wrongOnly = false;
  let audioState = "idle";     // idle | playing | done
  let realToken = 0;           // 실전 LC 자동 진행 취소용
  const firstRC = items.findIndex(it => !P.lc_parts.includes(it.part));
  const inLC = i => i >= 0 && i < items.length && P.lc_parts.includes(items[i].part);
  const BACKUP = `ts-exam-${P.sid}`;

  const $ = sel => document.querySelector(sel);
  const stage = $("#stage"), qnav = $("#qnav"), timerEl = $("#timer"), progEl = $("#progress");

  // 문항 전체 번호
  const qlist = [];
  items.forEach((it, i) => { for (let q = 0; q < nq(it); q++) qlist.push({ i, q }); });

  // 복원: 이미 채점된 선택을 answers 에 반영
  for (const [ref, g] of Object.entries(graded)) {
    answers[ref] = {};
    for (const r of g.results || []) answers[ref][r.qidx] = r.chosen;
  }

  // ---------------------------------------------------------------- 도우미
  const qOf = (it, qidx) => (it.questions ? it.questions[qidx] : it);
  const choicesOf = (it, qidx) => (it.part === 1 ? it.statements : qOf(it, qidx).choices);
  const chosen = (it, qidx) => (answers[it.ref] || {})[qidx];
  const isGraded = it => !!graded[it.ref];
  const allAnswered = it => { for (let q = 0; q < nq(it); q++) if (chosen(it, q) === undefined) return false; return true; };
  const resultOf = (it, qidx) => (graded[it.ref]?.results || []).find(r => r.qidx === qidx);
  const revealOf = (it, qidx) => graded[it.ref]?.questions.find(r => r.qidx === qidx);
  const hashGender = (ref, flip) => { let h = 0; for (const c of ref) h = (h * 17 + c.charCodeAt(0)) >>> 0; return ((h + (flip ? 1 : 0)) % 2) ? "male" : "female"; };
  const allCorrect = it => (graded[it.ref]?.results || []).every(r => r.correct);

  function fmtPassage(text, it) {
    let h = esc(text);
    h = h.replace(/\[(\d)\]/g, '<b class="tag">[$1]</b>');                      // 문장 삽입 위치
    if (it.part === 6) {
      h = h.replace(/\{(\d)\}/g, (_, n) => {
        const qi = Number(n) - 1;
        const rv = revealOf(it, qi);
        const fill = rv ? esc(it.questions[qi].choices[rv.answer]) : "";
        const cls = "blank" + (qi === curQ ? " cur" : "");
        return `<span class="${cls}" data-blank="${qi}">(${n})${fill ? " " + fill : ""}</span>`;
      });
    }
    return h;
  }

  function graphicHtml(g) {
    if (!g) return "";
    const [head, ...rows] = g.rows;
    return `<div class="graphic table-wrap"><table><caption>${esc(g.title)}</caption>
      <thead><tr>${head.map(c => `<th>${esc(c)}</th>`).join("")}</tr></thead>
      <tbody>${rows.map(r => `<tr>${r.map(c => `<td>${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  }

  function choicesHtml(it, qidx) {
    const list = choicesOf(it, qidx);
    const done = isGraded(it);
    const hideText = (it.part === 1 || it.part === 2) && !done;       // 듣기 선택지는 채점 전에 글자를 숨김
    const rv = revealOf(it, qidx);
    const mine = chosen(it, qidx);
    const row = hideText ? (list.length === 3 ? " row3" : " row4") : "";
    return `<div class="choices${row}${done || RESULT ? " locked" : ""}">` + list.map((c, ci) => {
      let cls = "choice";
      if (rv) {
        if (ci === rv.answer) cls += " right";
        else if (ci === mine) cls += " wrong";
      } else if (ci === mine) cls += " sel";
      return `<button type="button" class="${cls}" data-q="${qidx}" data-c="${ci}"><span class="letter">${L[ci]}</span>` +
        (hideText ? "" : `<span>${esc(c)}</span>`) + `</button>`;
    }).join("") + `</div>`;
  }

  function explainHtml(it, qidx) {
    const rv = revealOf(it, qidx);
    if (!rv) return "";
    const r = resultOf(it, qidx);
    const v = !r || r.chosen < 0 ? '<span class="verdict bad">미응답</span>' :
      r.correct ? '<span class="verdict ok">정답</span>' : '<span class="verdict bad">오답</span>';
    return `<div class="explain">${v}정답 (${L[rv.answer]}) <span class="tag">${esc(rv.type)}</span><div>${esc(rv.explanation)}</div></div>`;
  }

  function questionBlock(it, qidx, label) {
    const q = qOf(it, qidx);
    const text = it.questions ? q.q : "";
    return `<div class="q${qidx === curQ && nq(it) > 1 ? " cur" : ""}" data-qbox="${qidx}">
      ${label || text ? `<div class="qtext">${esc(label || "")}${text ? " " + esc(text) : ""}</div>` : ""}
      ${choicesHtml(it, qidx)}${explainHtml(it, qidx)}</div>`;
  }

  function numberOf(it, qidx) {
    const i = items.indexOf(it);
    return qlist.findIndex(x => x.i === i && x.q === qidx) + 1;
  }

  // ---------------------------------------------------------------- 음성
  function segmentsFor(it) {
    const accent = TTS.accentFor(P.tts.accent, it.ref);
    const hl = label => () => { const s = $("#audio-status"); if (s) s.textContent = `재생 중 ${label}`; };
    if (it.part === 1) {
      const g = hashGender(it.ref);
      return { accent, segs: it.statements.map((s, i) => ({ text: `${L[i]}. ${s}`, gender: g, pause: 900, onStart: hl(`(${L[i]})`) })) };
    }
    if (it.part === 2) {
      const g1 = hashGender(it.ref), g2 = hashGender(it.ref, true);
      return {
        accent, segs: [{ text: it.question, gender: g1, pause: 1100, onStart: hl("질문") }]
          .concat(it.choices.map((c, i) => ({ text: `${L[i]}. ${c}`, gender: g2, pause: 800, onStart: hl(`(${L[i]})`) }))),
      };
    }
    if (it.part === 3) {
      return {
        accent, segs: it.script.map((ln, i) => ({
          text: ln.t, gender: it.speakers[ln.s], nth: ln.s.endsWith("2") ? 1 : 0, pause: 350,
          onStart: hl(`${ln.s} · ${i + 1}/${it.script.length}`),
        })),
      };
    }
    return { accent, segs: [{ text: it.script, gender: it.voice, pause: 0, onStart: hl("담화") }] };
  }

  async function playAudio(it) {
    if (!TTS.supported) return;
    const { accent, segs } = segmentsFor(it);
    audioState = "playing";
    updateAudioBox(it);
    const finished = await TTS.play(segs, { rate: P.tts.rate, accent });
    if (items[idx] === it && finished) {
      audioState = "done";
      updateAudioBox(it);
    }
  }

  const sleep = ms => new Promise(r => setTimeout(r, ms));

  async function countdown(sec, my, label) {
    for (let t = sec; t > 0; t--) {
      if (my !== realToken) return false;
      const st = $("#audio-status");
      if (st) st.textContent = `${label} · 답안 시간 ${t}초`;
      await sleep(1000);
    }
    return my === realToken;
  }

  /* 실전 LC: 음성 → (Part 3·4 는 문제를 하나씩 읽고 8초, 시각 자료 문제 12초) → 다음 문제로 자동 이동 */
  async function runRealLC(it) {
    const my = ++realToken;
    played[it.ref] = true;
    if (!TTS.supported) return;
    audioState = "playing";
    updateAudioBox(it);
    const { accent, segs } = segmentsFor(it);
    if (!(await TTS.play(segs, { rate: 1, accent })) || my !== realToken) return;
    if (it.part === 1 || it.part === 2) {
      if (!(await countdown(5, my, `${numberOf(it, 0)}번`))) return;
    } else {
      for (let q = 0; q < it.questions.length; q++) {
        curQ = q;
        render({ keepQ: true });
        updateAudioBox(it);
        const n = numberOf(it, q);
        if (!(await TTS.play([{ text: `Number ${n}. ${it.questions[q].q}`, gender: "male" }], { rate: 1, accent: "us" })) || my !== realToken) return;
        if (!(await countdown(it.questions[q].type === "시각 자료 연계" ? 12 : 8, my, `${n}번`))) return;
      }
    }
    audioState = "done";
    saveBackup();
    if (inLC(idx + 1)) go(idx + 1, undefined, true);
    else if (firstRC >= 0) {
      go(firstRC, undefined, true);
      showBanner("Listening이 끝났습니다. 지금부터 Reading 75분이 시작됩니다.");
    } else submitExam(true);
  }

  function showBanner(text) {
    const b = document.createElement("div");
    b.className = "flash ok";
    b.textContent = text;
    stage.prepend(b);
    setTimeout(() => b.remove(), 8000);
  }

  // 시험 답안 임시 저장 (새로고침·실수로 닫기 대비, 이 브라우저에만)
  function saveBackup() {
    if (!EXAM) return;
    try { localStorage.setItem(BACKUP, JSON.stringify({ answers, idx, rcUsed, totalMs, elapsed })); } catch (e) { /* 저장 불가 */ }
  }
  function loadBackup() {
    if (!EXAM) return;
    try {
      const b = JSON.parse(localStorage.getItem(BACKUP) || "null");
      if (!b) return;
      Object.assign(answers, b.answers || {});
      Object.assign(elapsed, b.elapsed || {});
      idx = Math.min(b.idx || 0, items.length - 1);
      rcUsed = b.rcUsed || 0;
      totalMs = b.totalMs || 0;
    } catch (e) { /* 무시 */ }
  }

  function canReplay(it) { return !EXAM || !played[it.ref]; }

  function audioBoxHtml(it) {
    if (!TTS.supported) return `<div class="audio-box"><span class="status">이 브라우저는 음성 합성을 지원하지 않습니다. 크롬이나 엣지를 쓰세요.</span></div>`;
    return `<div class="audio-box" id="audio-box"></div>`;
  }

  function updateAudioBox(it) {
    const box = $("#audio-box");
    if (!box) return;
    const accentName = { us: "미국", uk: "영국", au: "호주", ca: "캐나다" }[TTS.accentFor(P.tts.accent, it.ref)] || "";
    let btn = "";
    if (REAL && isLC(it)) btn = "";
    else if (audioState === "playing") btn = `<button class="btn small" data-act="stop">■ 정지</button>`;
    else if (canReplay(it) || RESULT || isGraded(it)) btn = `<button class="btn small primary" data-act="play">▶ ${audioState === "done" ? "다시 듣기" : "듣기"}</button> <span class="kbd">R</span>`;
    const status = audioState === "playing" ? "재생 중…" : audioState === "done" ? (EXAM ? "재생 끝 (시험 모드는 한 번만 들을 수 있습니다)" : "재생 끝") : "";
    box.innerHTML = `${btn}<span class="status" id="audio-status">${status}</span><span class="status" style="margin-left:auto">${accentName} 억양 · 속도 ${P.tts.rate}×</span>`;
  }

  // ---------------------------------------------------------------- 화면 그리기
  function render(opts) {
    opts = opts || {};
    const it = items[idx];
    if (!it) return;
    const done = isGraded(it);
    if (!opts.keepQ) {
      curQ = 0;
      if (!done) for (let q = 0; q < nq(it); q++) if (chosen(it, q) === undefined) { curQ = q; break; }
    }
    const g = graded[it.ref];
    const head = `<div class="item-head"><b>Part ${it.part}</b> ${PART_NAME[it.part]}
      ${EXAM ? "" : `<span class="badge g${it.level}">${GRADE[it.level]}</span>`}
      ${it.topic && !EXAM ? `<span class="tag">${esc(it.topic)}</span>` : ""}${it.doc_type && !EXAM ? `<span class="tag">${esc(it.doc_type)}</span>` : ""}
      ${it.kind && it.kind !== "single" ? `<span class="tag">${it.kind === "double" ? "이중 지문" : "삼중 지문"}</span>` : ""}
      <span style="margin-left:auto">${idx + 1} / ${items.length}</span>
      <span class="timer" id="item-timer"></span></div>`;
    let body = "";
    const num = q => `${numberOf(it, q)}.`;

    if (it.part === 1) {
      body = `<div class="stack">${audioBoxHtml(it)}
        <div class="scene"><span class="cap">사진 장면 (설명으로 대신함)</span>${esc(it.scene)}</div>
        ${questionBlock(it, 0, num(0))}</div>`;
    } else if (it.part === 2) {
      body = `<div class="stack">${audioBoxHtml(it)}
        <div class="card muted small">질문과 세 개의 응답을 듣고 가장 알맞은 응답을 고르세요.</div>
        ${questionBlock(it, 0, num(0))}</div>`;
    } else if (it.part === 3 || it.part === 4) {
      body = `<div class="stack">${audioBoxHtml(it)}${graphicHtml(it.graphic)}
        ${it.questions.map((q, i) => questionBlock(it, i, num(i))).join("")}</div>`;
    } else if (it.part === 5) {
      const s = esc(it.question).replace("-------", '<span class="gap">-------</span>');
      body = `<div class="card"><div class="sentence">${num(0)} ${s}</div>${choicesHtml(it, 0)}${explainHtml(it, 0)}</div>`;
    } else if (it.part === 6) {
      body = `<div class="item-body split"><div class="passage-scroll"><div class="passage"><span class="doc-type">${esc(it.doc_type)}</span><span class="doc-title">${esc(it.title)}</span>${fmtPassage(it.passage, it)}</div></div>
        <div>${it.questions.map((q, i) => questionBlock(it, i, `${num(i)} 빈칸 (${i + 1})`)).join("")}</div></div>`;
    } else if (it.part === 7) {
      body = `<div class="item-body split"><div class="passage-scroll">${it.passages.map(p =>
        `<div class="passage"><span class="doc-type">${esc(p.doc_type)}</span><span class="doc-title">${esc(p.title)}</span>${fmtPassage(p.text, it)}</div>`).join("")}</div>
        <div>${it.questions.map((q, i) => questionBlock(it, i, num(i))).join("")}</div></div>`;
    }

    stage.innerHTML = head + body + revealHtml(it, g) + footHtml(it);
    if (isLC(it)) updateAudioBox(it);
    renderNav();
    const qb = stage.querySelector(".q.cur");
    if (opts.scrollQ && qb) qb.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function revealHtml(it, g) {
    if (!g) return "";
    let script = "";
    if (it.part === 1) script = it.statements.map((s, i) => `<div class="script-line"><b>(${L[i]})</b>${esc(s)}</div>`).join("");
    if (it.part === 2) script = `<div class="script-line"><b>Q</b>${esc(it.question)}</div>` + it.choices.map((c, i) => `<div class="script-line"><b>(${L[i]})</b>${esc(c)}</div>`).join("");
    if (it.part === 3) script = it.script.map(ln => `<div class="script-line"><b>${esc(ln.s)}</b>${esc(ln.t)}</div>`).join("");
    if (it.part === 4) script = `<div class="script-line">${esc(it.script)}</div>`;
    return `<div class="reveal">
      ${script ? `<details open><summary>스크립트</summary>${script}</details>` : ""}
      ${g.translation ? `<details${isLC(it) ? "" : " open"}><summary>해석</summary><div class="translation">${esc(g.translation)}</div></details>` : ""}
    </div>`;
  }

  function footHtml(it) {
    const last = idx === items.length - 1;
    let right = "";
    if (INSTANT) {
      if (!isGraded(it)) right = `<button class="btn primary" data-act="grade" ${allAnswered(it) ? "" : "disabled"}>채점하기 <span class="kbd">Enter</span></button>`;
      else right = last ? `<button class="btn primary" data-act="finish">결과 보기</button>` : `<button class="btn primary" data-act="next">다음 문제 → <span class="kbd">Enter</span></button>`;
    } else if (REAL && isLC(it)) {
      return `<div class="quiz-foot"><span class="muted small">LC는 음성에 맞춰 자동으로 넘어갑니다 (실제 시험과 같이 되돌아가기·다시 듣기 없음)</span></div>`;
    } else if (EXAM) {
      right = last ? `<button class="btn primary" data-act="submit">제출하기</button>` : `<button class="btn primary" data-act="next">다음 → <span class="kbd">Enter</span></button>`;
    } else {
      right = last ? "" : `<button class="btn primary" data-act="next">다음 →</button>`;
    }
    const canPrev = idx > 0 && !(REAL && (inLC(idx - 1) || isLC(it)));
    const left = canPrev ? `<button class="btn" data-act="prev">← 이전</button>` : "<span></span>";
    return `<div class="quiz-foot">${left}<div class="row">${right}</div></div>`;
  }

  function renderNav() {
    qnav.innerHTML = qlist.map((x, n) => {
      const it = items[x.i];
      let cls = x.i === idx ? "cur" : "";
      const r = resultOf(it, x.q);
      if (r) cls += r.correct ? " ok" : " bad";
      else if (chosen(it, x.q) !== undefined) cls += " answered";
      const locked = REAL && (inLC(x.i) || inLC(idx));
      return `<button type="button" class="${cls}" data-go="${x.i}"${locked ? " disabled" : ""}>${n + 1}</button>`;
    }).join("");
    const answeredN = qlist.filter(x => chosen(items[x.i], x.q) !== undefined).length;
    const gradedList = qlist.map(x => resultOf(items[x.i], x.q)).filter(Boolean);
    if (EXAM) progEl.textContent = `${answeredN} / ${qlist.length}문항 답함`;
    else progEl.textContent = gradedList.length
      ? `${gradedList.length} / ${qlist.length}문항 채점 · 정답 ${gradedList.filter(r => r.correct).length}`
      : `${qlist.length}문항`;
  }

  // ---------------------------------------------------------------- 이동·채점
  function go(i, opts, auto) {
    if (i < 0 || i >= items.length) return;
    if (REAL && !auto && started && (inLC(i) || inLC(idx))) return;      // 실전: LC 는 자동으로만 이동
    realToken++;
    TTS.stop();
    audioState = "idle";
    idx = i;
    saveBackup();
    render(opts);
    if (RESULT) $("#quiz-main").scrollIntoView({ block: "start" });
    else window.scrollTo({ top: 0 });
    maybeAutoplay();
  }

  function step(dir) {
    let i = idx + dir;
    if (RESULT && wrongOnly) while (i >= 0 && i < items.length && allCorrect(items[i])) i += dir;
    go(i);
  }

  function maybeAutoplay() {
    const it = items[idx];
    if (!started || !isLC(it) || RESULT || isGraded(it)) return;
    if (REAL) { runRealLC(it); return; }
    if (EXAM && played[it.ref]) return;
    if (EXAM) played[it.ref] = true;
    playAudio(it);
  }

  function choose(qidx, ci) {
    const it = items[idx];
    if (RESULT || isGraded(it) || !started) return;
    answers[it.ref] = answers[it.ref] || {};
    answers[it.ref][qidx] = ci;
    // 다음으로 답하지 않은 문항으로 이동
    curQ = qidx;
    for (let q = 0; q < nq(it); q++) if (chosen(it, q) === undefined) { curQ = q; break; }
    saveBackup();
    if (INSTANT && nq(it) === 1) { grade(); return; }
    render({ keepQ: true, scrollQ: true });
  }

  async function grade() {
    const it = items[idx];
    if (!INSTANT || isGraded(it) || submitting) return;
    submitting = true;
    const per = Math.round((elapsed[it.ref] || 0) / nq(it));
    const list = [];
    for (let q = 0; q < nq(it); q++) list.push({ qidx: q, chosen: chosen(it, q) ?? -1, elapsed_ms: per });
    try {
      graded[it.ref] = await TS.post(`/api/quiz/${P.sid}/grade`, { ref: it.ref, answers: list });
      for (const r of graded[it.ref].results) answers[it.ref][r.qidx] = r.chosen;
      if (!isLC(it)) TTS.stop();
      render({ keepQ: true });
    } catch (e) {
      alert(e.message);
    } finally {
      submitting = false;
    }
  }

  async function finish() {
    if (submitting) return;
    submitting = true;
    try {
      const body = { duration_sec: Math.round(totalMs / 1000) };
      if (EXAM) {
        body.items = {};
        for (const it of items) {
          const per = Math.round((elapsed[it.ref] || 0) / nq(it));
          body.items[it.ref] = [];
          for (let q = 0; q < nq(it); q++) body.items[it.ref].push({ qidx: q, chosen: chosen(it, q) ?? -1, elapsed_ms: per });
        }
      }
      const r = await TS.post(`/api/quiz/${P.sid}/submit`, body);
      TTS.stop();
      try { localStorage.removeItem(BACKUP); } catch (e) { /* 무시 */ }
      window.onbeforeunload = null;
      location.href = r.redirect;
    } catch (e) {
      alert(e.message);
      submitting = false;
    }
  }

  function submitExam(force) {
    const left = qlist.filter(x => chosen(items[x.i], x.q) === undefined).length;
    if (!force && !confirm(left ? `답하지 않은 문항이 ${left}개 있습니다. 제출할까요?` : "제출할까요?")) return;
    finish();
  }

  // ---------------------------------------------------------------- 시간
  let last = performance.now();
  function tick() {
    const now = performance.now();
    const d = now - last;
    last = now;
    if (!started || document.hidden || submitting || RESULT) return;
    const it = items[idx];
    totalMs += d;
    if (!isGraded(it)) elapsed[it.ref] = (elapsed[it.ref] || 0) + d;
    if (EXAM && !isLC(it)) rcUsed += d;
    if (EXAM && Math.floor(totalMs / 5000) !== Math.floor((totalMs - d) / 5000)) saveBackup();
    // 문제별 시간 (RC 목표 시간 대비)
    const el = $("#item-timer");
    if (el) {
      const sec = (elapsed[it.ref] || 0) / 1000;
      const tgt = P.target_sec[it.part] ? P.target_sec[it.part] * nq(it) : null;
      el.textContent = TS.fmtTime(sec) + (tgt ? ` / ${TS.fmtTime(tgt)}` : "");
      el.classList.toggle("over", !!tgt && sec > tgt);
    }
    if (EXAM && P.time_limit) {
      const remain = P.time_limit - rcUsed / 1000;
      timerEl.textContent = `RC 남은 시간 ${TS.fmtTime(remain)}`;
      timerEl.classList.toggle("over", remain < 300);
      if (remain <= 0) { alert("RC 제한 시간이 끝났습니다. 지금까지의 답안을 제출합니다."); submitExam(true); }
    } else if (!RESULT) {
      timerEl.textContent = `경과 ${TS.fmtTime(totalMs / 1000)}`;
    }
  }

  // ---------------------------------------------------------------- 이벤트
  stage.addEventListener("click", e => {
    const c = e.target.closest(".choice");
    if (c) return choose(Number(c.dataset.q), Number(c.dataset.c));
    const b = e.target.closest("[data-blank]");
    if (b) { curQ = Number(b.dataset.blank); render({ keepQ: true, scrollQ: true }); return; }
    const qb = e.target.closest("[data-qbox]");
    if (qb && !e.target.closest("button")) { curQ = Number(qb.dataset.qbox); render({ keepQ: true }); return; }
    const a = e.target.closest("[data-act]");
    if (!a) return;
    const act = a.dataset.act;
    const it = items[idx];
    if (act === "play") { if (EXAM) played[it.ref] = true; playAudio(it); }
    else if (act === "stop") { TTS.stop(); audioState = "done"; updateAudioBox(it); }
    else if (act === "grade") grade();
    else if (act === "next") step(1);
    else if (act === "prev") step(-1);
    else if (act === "finish") finish();
    else if (act === "submit") submitExam(false);
  });
  $("#finish-btn")?.addEventListener("click", () => {
    const n = qlist.filter(x => resultOf(items[x.i], x.q)).length;
    if (n === 0 && !confirm("채점한 문제가 없습니다. 그래도 끝낼까요?")) return;
    finish();
  });
  $("#submit-btn")?.addEventListener("click", () => submitExam(false));
  qnav.addEventListener("click", e => {
    const b = e.target.closest("[data-go]");
    if (b) go(Number(b.dataset.go));
  });

  document.addEventListener("keydown", e => {
    if (!started || e.ctrlKey || e.metaKey || e.altKey || e.target.closest?.("input, textarea, select")) return;
    const it = items[idx];
    const k = e.key.toLowerCase();
    const map = { "1": 0, "2": 1, "3": 2, "4": 3, a: 0, b: 1, c: 2, d: 3 };
    if (k in map && !RESULT && !isGraded(it)) {
      if (map[k] < choicesOf(it, curQ).length) { e.preventDefault(); choose(curQ, map[k]); }
    } else if (k === "enter") {
      e.preventDefault();
      if (INSTANT && !isGraded(it)) { if (allAnswered(it)) grade(); }
      else if (idx < items.length - 1) step(1);
      else if (INSTANT) finish();
    } else if (k === "arrowright") step(1);
    else if (k === "arrowleft") step(-1);
    else if (k === "r" && !REAL && isLC(it) && (canReplay(it) || isGraded(it) || RESULT)) { if (EXAM) played[it.ref] = true; playAudio(it); }
    else if (k === "arrowdown" && nq(it) > 1) { curQ = Math.min(nq(it) - 1, curQ + 1); render({ keepQ: true, scrollQ: true }); }
    else if (k === "arrowup" && nq(it) > 1) { curQ = Math.max(0, curQ - 1); render({ keepQ: true, scrollQ: true }); }
  });

  // 시험 중 페이지를 떠나려 하면 확인
  if (EXAM) window.onbeforeunload = () => (started && !submitting ? "시험을 그만둘까요?" : undefined);

  // ---------------------------------------------------------------- 시작
  function start() {
    started = true;
    $("#intro")?.classList.add("hidden");
    $("#quiz-main").classList.remove("hidden");
    last = performance.now();
    go(idx, undefined, true);
  }

  if (!items.length) {
    stage.innerHTML = '<div class="empty">문제가 없습니다.</div>';
    return;
  }
  if (INSTANT) {
    const firstOpen = items.findIndex(it => !isGraded(it));
    idx = firstOpen >= 0 ? firstOpen : items.length - 1;
  }
  if (RESULT) {
    const t = $("#wrong-only");
    if (t) t.addEventListener("change", () => {
      wrongOnly = t.checked;
      if (wrongOnly && allCorrect(items[idx])) { const j = items.findIndex(x => !allCorrect(x)); if (j >= 0) go(j); }
    });
    started = true;
    render();
  } else {
    TTS.load();
    loadBackup();
    const needIntro = EXAM || items.some(isLC);
    if (needIntro) {
      $("#start-btn").addEventListener("click", start);
    } else start();
    setInterval(tick, 250);
  }
})();
