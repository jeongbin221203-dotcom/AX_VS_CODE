/* 말하기 시험 진행 화면 (토익스피킹·오픽 공용) — TS 앱 templates/speaking/run.html + static/js/speaking.js.
   '단계(step)' 목록을 차례로 돌린다: 화면 보여 주기 → (표 읽기) → 질문 듣기 → 준비 → 삐 → 답변(녹음 + 음성 인식).
   연습: 문제(unit) 하나가 끝날 때마다 스스로 채점하고 저장. 모의고사: 전부 끝난 뒤 한꺼번에 채점하고 제출.
   화면 종류는 <body data-spk="…"> : tsp-practice | tsp-mock-run | opic-practice | opic-mock-run
   녹음 파일은 저장하지 않는다(이 화면에서 다시 듣기만). 음성 인식으로 받아 적은 글만 기록에 남는다. */
(function () {
  "use strict";
  const page = document.body.dataset.spk;
  const EXAM = page.startsWith("tsp") ? "tsp" : "opic";
  const MOCK = page.endsWith("mock-run");
  const esc = UI.esc;
  const NAME = EXAM === "tsp" ? "토익스피킹" : "오픽";
  const HOME = EXAM === "tsp" ? "toeic-speaking.html" : "opic.html";
  const $ = s => document.querySelector(s);
  const SPEED = Math.max(1, Number(window.SPK_SPEED) || 1);      // 점검용 시간 배속(화면 점검 스크립트가 window.SPK_SPEED 를 미리 정해 둔다). 평소 1
  const nowMs = () => Date.now() * SPEED;

  function message(title, body) {
    UI.$("app").innerHTML = `<div class="card empty"><h2>${esc(title)}</h2><p>${body}</p><a class="btn primary" href="${HOME}">${NAME} 홈으로</a></div>`;
  }
  /** 주소 설정으로 이번에 풀 문제 묶음(P)을 만든다. 만들 수 없으면 null (화면에 안내를 그림) */
  async function makePayload() {
    const q = UI.params();
    const rubrics = { ...Spk.TSP_RUBRIC, opic: Spk.OPIC_RUBRIC };
    const base = { exam: EXAM, tts: UI.tts(), rubrics, home: HOME };
    if (MOCK) {
      const mid = UI.intParam("mid");
      if (EXAM === "tsp") await Spk.loadTsp(); else await Spk.loadOpic();
      const m = mid !== null ? Spk.getMock(mid) : null;
      if (!m || m.exam !== EXAM) { message("모의고사를 찾을 수 없습니다", "지워졌거나 다른 브라우저의 기록일 수 있습니다."); return null; }
      if (m.finished_at) { UI.go(`${EXAM}-mock-result.html?mid=${mid}`); return null; }
      return { ...base, mode: "mock", mock_id: mid, mock: m, units: m.plan, minutes: EXAM === "opic" ? Spk.OPIC_MINUTES : null,
               title: EXAM === "tsp" ? "토익스피킹 실전 모의고사" : `오픽 실전 모의고사 · 난이도 ${m.settings.level}`,
               sub: EXAM === "tsp" ? "11문항 · 약 20분 · 문항마다 준비·답변 시간이 정해져 있습니다" : "15문항 · 40분 · 질문은 소리로만 나오고 다시 듣기는 한 번" };
    }
    const intN = (name, def, lo, hi) => { const v = parseInt(q.get(name) || "", 10); return Number.isFinite(v) ? Math.max(lo, Math.min(v, hi)) : def; };
    const weak = q.get("weak") === "1";
    if (EXAM === "tsp") {
      await Spk.loadTsp();
      const task = q.get("task");
      if (!(task in Spk.TSP_TASKS)) { message("없는 유형입니다", "토익스피킹 홈에서 유형을 고르세요."); return null; }
      const info = Spk.TSP_TASKS[task];
      const n = intN("n", info.n === 2 ? 2 : 1, 1, 10);
      return { ...base, mode: "practice", units: Spk.tspPractice(task, n, null, weak), task, n, weak,
               title: `${info.q} ${info.name}` + (weak ? " · 약한 문항" : ""), sub: `${info.en} · ${info.desc}` };
    }
    await Spk.loadOpic();
    let topic = q.get("topic") || null, kind = q.get("kind") || null;
    if (topic && !(topic in Spk.OPIC_TOPICS)) { message("없는 주제입니다", "오픽 홈에서 주제를 고르세요."); return null; }
    if (kind && !["intro", "describe", "routine", "past", "compare", "issue", "roleplay"].includes(kind)) { message("없는 문항 종류입니다", "오픽 홈에서 고르세요."); return null; }
    if (topic === "intro") { topic = null; kind = "intro"; }
    const showText = q.get("text") !== "0";
    const n = intN("n", kind === "roleplay" ? 1 : (!topic ? 3 : 5), 1, 10);
    const name = topic ? Spk.OPIC_TOPICS[topic][0] : "전체 주제";
    let kname = kind === "roleplay" ? "롤플레이" : (Spk.OPIC_KINDS[kind] || (topic ? "콤보 (묘사 → 습관 → 경험)" : "섞어서"));
    if (weak) kname = "약한 문항 다시";
    return { ...base, mode: "practice", units: Spk.opicPractice(topic, kind, n, showText, null, weak), topic, kind, n, weak, show_text: showText,
             title: `${name} · ${kname}`, sub: "질문을 듣고 바로 답합니다. 준비 시간은 없고, 다시 듣기는 한 번만 됩니다 (실제 시험처럼)." };
  }

  function pageHtml(P) {
    const head = (mock) => `<div class="page-head"><div>
        <div class="muted small">${NAME}${mock ? " · 실전 모의고사" : ""}</div><h1>${esc(P.title)}</h1><div class="muted small">${esc(P.sub)}</div></div>
        ${mock ? "" : `<form class="row" id="opt-form" onsubmit="return false">
          ${EXAM === "opic" ? `<label class="small"><input type="checkbox" id="opt-text" ${P.show_text ? "checked" : ""}> 질문 글자 보기</label>` : ""}
          <label class="small">개수 <select id="opt-n">${[1, 2, 3, 5, 7, 10].map(k => `<option ${P.n === k ? "selected" : ""}>${k}</option>`).join("")}</select></label>
          <a class="btn" href="${HOME}">돌아가기</a></form>`}</div>`;
    if (!P.units.length) {
      return head(MOCK) + `<div class="card empty">${P.weak ? "다시 풀 약한 문항이 없습니다. 자기 채점이 낮았던 문항(토익스피킹 60% 미만, 오픽 3점 이하)이 여기에 모입니다." : "이 조건의 문제가 아직 없습니다."} <a href="${HOME}">돌아가기</a></div>`;
    }
    const intro = MOCK
      ? `<p>${EXAM === "tsp" ? "실제 시험과 같은 순서·시간으로 11문항을 봅니다 (약 20분). 시험 중에는 준비·답변 시간을 줄일 수 없습니다."
        : `질문은 소리로만 나옵니다. 질문이 끝나면 5초 안에 <b>다시 듣기(1회)</b>를 누를 수 있고, 그다음 답변 녹음이 시작됩니다. 다 말했으면 <b>다음</b>을 누르세요. 전체 ${P.minutes}분.`}</p>
        <p class="small">모든 문항이 끝나면 녹음을 다시 듣고 모범 답안과 비교해 문항마다 스스로 채점합니다. 그 결과로 ${EXAM === "tsp" ? "점수(0~200)와 레벨" : "등급(NL~AL)"}을 추정합니다.</p>`
      : `<p>${P.units.length}개 문제를 연습합니다. 문제마다 답변이 끝나면 녹음·인식 글자·모범 답안을 보고 스스로 채점합니다.</p>`;
    return head(MOCK) + `<div id="intro" class="card" style="max-width:680px;margin:10px auto">${intro}
        <ul class="clean small">
          <li>마이크를 씁니다. 브라우저가 권한을 물으면 <b>허용</b>하세요. (https 주소나 이 PC 의 localhost 에서만 켜집니다. 파일로 직접 열면 브라우저에 따라 막힐 수 있습니다)</li>
          <li>크롬·엣지는 말한 내용을 글자로 바꿔 단어 수와 말하기 속도를 보여 줍니다.</li>
          <li>질문 음성은 브라우저 음성 합성입니다. 소리가 안 나면 설정에서 음성 속도를 확인하세요.</li>
          <li>녹음은 이 화면에서 다시 듣는 데만 쓰고 저장하지 않습니다. 기록에는 점수와 받아 적은 글만 남습니다.</li>
        </ul>
        <button class="btn primary" id="start-btn">시작하기</button></div>
      <div id="quiz-main" class="hidden">
        <div class="quiz-top"><b id="step-label"></b><span class="progress muted small" id="progress"></span>
          <span class="timer" id="total-timer" hidden></span><span class="timer" id="timer"></span></div>
        <div id="stage"></div></div>`;
  }

  UI.boot({ exam: EXAM === "tsp" ? "toeic-speaking" : "opic", page }, async () => {
    const P = await makePayload();
    if (!P) return;
    document.title = `${P.title} · TS`;
    UI.$("app").innerHTML = pageHtml(P);
    const opt = $("#opt-form");
    if (opt) {
      const go = () => {
        const q = Object.fromEntries(UI.params());
        q.n = $("#opt-n").value;
        if ($("#opt-text")) q.text = $("#opt-text").checked ? "1" : "0";
        location.href = UI.url(location.pathname.split("/").pop(), q);
      };
      opt.addEventListener("change", go);
    }
    if (P.units.length) engine(P);
  });

  // =============================================================================== 진행 엔진
  function engine(P) {
    const stage = $("#stage"), timerEl = $("#timer"), totalEl = $("#total-timer"), prog = $("#progress"), labelEl = $("#step-label");
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    const OPIC = EXAM === "opic";
    const units = P.units;
    const totalSteps = units.reduce((a, u) => a + u.steps.length, 0);
    let stepNo = 0, tick = null, cleanup = [], aborted = false, deadline = null, startedAt = nowMs();
    const answers = [];           // 모의고사: 모든 단계의 답
    const ratios = [];            // 연습: 채점한 점수 비율

    const sleep = ms => new Promise(r => setTimeout(r, ms / SPEED));
    function stopAll() {
      TTS.stop();
      clearInterval(tick);
      tick = null;
      cleanup.forEach(f => { try { f(); } catch (e) { /* 무시 */ } });
      cleanup = [];
    }

    // ------------------------------------------------------------ 시간
    /* sec 초 동안 기다린다. ctl.skip() 으로 일찍 끝낼 수 있다. */
    function countdown(sec, label, ctl) {
      return new Promise(resolve => {
        clearInterval(tick);
        const end = nowMs() + sec * 1000;
        let done = false;
        const finish = () => { if (done) return; done = true; clearInterval(tick); tick = null; resolve(); };
        const draw = () => {
          const left = Math.max(0, (end - nowMs()) / 1000);
          timerEl.textContent = `${label} ${UI.fmtTime(left)}`;
          timerEl.classList.toggle("over", left <= Math.min(5, sec / 3));
          if (left <= 0 || aborted) finish();
        };
        draw();
        tick = setInterval(draw, 200);
        if (ctl) ctl.skip = finish;
      });
    }
    function stopwatch(max, label, ctl) {            // 제한 없는 답변 (오픽): 최대 max 초
      return new Promise(resolve => {
        clearInterval(tick);
        const t0 = nowMs();
        let done = false;
        const finish = () => { if (done) return; done = true; clearInterval(tick); tick = null; resolve(); };
        tick = setInterval(() => {
          const s = (nowMs() - t0) / 1000;
          timerEl.textContent = `${label} ${UI.fmtTime(s)}`;
          timerEl.classList.toggle("over", s > max - 15);
          if (s >= max || aborted) finish();
        }, 250);
        if (ctl) ctl.skip = finish;
      });
    }
    function startTotal(minutes) {
      if (!minutes) return;
      deadline = nowMs() + minutes * 60000;
      totalEl.hidden = false;
      const t = setInterval(() => {
        const left = (deadline - nowMs()) / 1000;
        totalEl.textContent = `전체 ${UI.fmtTime(left)}`;
        totalEl.classList.toggle("over", left < 300);
        if (left <= 0) { clearInterval(t); aborted = true; stopAll(); }
      }, 500);
    }

    // ------------------------------------------------------------ 소리
    let actx = null;
    function beep(freq = 880, ms = 220) {
      try {
        actx = actx || new (window.AudioContext || window.webkitAudioContext)();
        const o = actx.createOscillator(), g = actx.createGain();
        o.frequency.value = freq;
        g.gain.setValueAtTime(0.15, actx.currentTime);
        g.gain.exponentialRampToValueAtTime(0.001, actx.currentTime + ms / 1000);
        o.connect(g).connect(actx.destination);
        o.start();
        o.stop(actx.currentTime + ms / 1000);
      } catch (e) { /* 소리 장치 없음 */ }
      return sleep(ms + 120);
    }
    const say = segs => TTS.play(segs, { rate: P.tts.rate, accent: P.tts.accent });

    // ------------------------------------------------------------ 녹음·음성 인식
    async function openMic() {
      if (!navigator.mediaDevices?.getUserMedia) return null;
      try { return await navigator.mediaDevices.getUserMedia({ audio: true }); } catch (e) { return null; }
    }
    /* 답변 하나를 녹음한다. 반환: {stop() → Promise<{url, text, seconds}>} */
    function capture(stream, liveEl) {
      const t0 = nowMs();
      let mr = null, chunks = [];
      if (stream && window.MediaRecorder) {
        try {
          mr = new MediaRecorder(stream);
          mr.ondataavailable = e => e.data.size && chunks.push(e.data);
          mr.start();
        } catch (e) { mr = null; }
      }
      let finalText = "", interim = "", active = true, rec = null, srOk = !!SR;
      const startRec = () => {
        if (!SR || !active) return;
        rec = new SR();
        rec.lang = "en-US";
        rec.continuous = true;
        rec.interimResults = true;
        rec.onresult = e => {
          interim = "";
          for (let i = e.resultIndex; i < e.results.length; i++) {
            if (e.results[i].isFinal) finalText += e.results[i][0].transcript + " ";
            else interim += e.results[i][0].transcript;
          }
          if (liveEl) liveEl.textContent = (finalText + interim).trim();
        };
        rec.onerror = e => { if (["not-allowed", "service-not-allowed", "audio-capture", "network"].includes(e.error)) { srOk = false; active = false; } };
        rec.onend = () => { if (active) { finalText += interim ? interim + " " : ""; interim = ""; setTimeout(startRec, 50); } };   // 말이 끊겨도 다시 듣기
        try { rec.start(); } catch (e) { srOk = false; }
      };
      startRec();
      const ctl = {
        stop() {
          active = false;
          const seconds = (nowMs() - t0) / 1000;
          try { rec && rec.stop(); } catch (e) { /* 무시 */ }
          const urlP = new Promise(r => {
            if (!mr || mr.state === "inactive") return r(null);
            mr.onstop = () => r(chunks.length ? URL.createObjectURL(new Blob(chunks, { type: mr.mimeType })) : null);
            try { mr.stop(); } catch (e) { r(null); }
          });
          return Promise.all([urlP, sleep(SR ? 500 : 0)]).then(([url]) => ({
            url, seconds, text: srOk ? (finalText + interim).trim() : null,
          }));
        },
      };
      cleanup.push(() => { if (active) ctl.stop(); });
      return ctl;
    }

    // ------------------------------------------------------------ 채점 도우미
    const norm = w => w.toLowerCase().replace(/[’']/g, "'").replace(/[^a-z0-9']/g, "");
    const wordsOf = s => String(s || "").split(/\s+/).map(w => ({ raw: w, n: norm(w) })).filter(w => w.n);
    const countWords = s => (String(s || "").match(/[A-Za-z0-9']+/g) || []).length;
    function lcs(a, b) {
      const dp = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
      for (let x = a.length - 1; x >= 0; x--) for (let y = b.length - 1; y >= 0; y--)
        dp[x][y] = a[x].n === b[y].n ? dp[x + 1][y + 1] + 1 : Math.max(dp[x + 1][y], dp[x][y + 1]);
      const hit = new Set();
      let x = 0, y = 0;
      while (x < a.length && y < b.length) {
        if (a[x].n === b[y].n) { hit.add(x); x++; y++; } else if (dp[x + 1][y] >= dp[x][y + 1]) x++; else y++;
      }
      return { match: dp[0][0], hit };
    }

    // ------------------------------------------------------------ 화면
    function showHtml(s) {
      const sh = s.show;
      if (sh.kind === "text") return `<div class="passage spk-read">${esc(sh.text)}</div>`;
      if (sh.kind === "scene") {
        return `<div class="scene"><span class="cap">사진 대신 장면 설명 · ${esc(sh.setting)}</span><p>${esc(sh.scene_ko)}</p>
          <ul class="clean spk-el">${sh.elements.map(e => `<li><span class="tag">${esc(e.where)}</span> ${esc(e.ko)}</li>`).join("")}</ul></div>`;
      }
      if (sh.kind === "table") {
        const i = sh.info;
        return `<div class="card spk-info"><h3>${esc(i.title)}</h3><div class="small muted">${esc(i.subtitle)}</div>
          <table><tbody>${i.rows.map(r => `<tr>${r.map(c => `<td>${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>
          ${(i.notes || []).map(n => `<div class="small">※ ${esc(n)}</div>`).join("")}</div>`;
      }
      if (sh.kind === "question") {
        return `${sh.intro ? `<div class="card small">${esc(sh.intro)}</div>` : ""}<div class="sentence spk-q">${esc(sh.question)}</div>`;
      }
      if (sh.kind === "opic") {
        return `<div class="card spk-opic"><div class="spk-ava" aria-hidden="true">Eva</div><div>
          ${sh.situation_ko ? `<div class="small muted">상황: ${esc(sh.situation_ko)}</div>` : ""}
          ${sh.question ? `<div class="sentence spk-q">${esc(sh.question)}</div>` : `<div class="muted">질문은 소리로만 나옵니다. 잘 들으세요.</div>`}</div></div>`;
      }
      return "";
    }
    function stepScreen(s, firstOfTask) {
      stepNo++;
      labelEl.textContent = s.label;
      prog.textContent = `${stepNo} / ${totalSteps} · ${s.title}`;
      stage.innerHTML = `${firstOfTask && s.directions ? `<details class="reveal-d" ${MOCK ? "open" : ""}><summary>Directions</summary><div class="small">${esc(s.directions)}</div></details>` : ""}
        <div class="spk-main">${showHtml(s)}</div>
        <div class="audio-box spk-status"><span class="status" data-st>준비</span><span class="spk-actions" data-act></span></div>
        <div class="small muted spk-live" data-live></div>`;
      window.scrollTo({ top: 0 });
      return { st: stage.querySelector("[data-st]"), act: stage.querySelector("[data-act]"), live: stage.querySelector("[data-live]") };
    }
    function button(box, text, cls) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "btn small " + (cls || "");
      b.textContent = text;
      box.appendChild(b);
      return b;
    }

    /* 단계 하나 실행 → 답 {step, url, text, seconds} */
    async function runStep(s, firstOfTask, stream) {
      const ui = stepScreen(s, firstOfTask);
      const ctl = {};
      if (s.read_first) {                                   // 표 먼저 읽기 (Q8~10)
        ui.st.textContent = "📋 표를 읽으세요";
        if (!MOCK) button(ui.act, "다 읽었어요").onclick = () => ctl.skip && ctl.skip();
        await countdown(s.read_first, "읽기", ctl);
        ui.act.innerHTML = "";
      }
      if (aborted) return null;
      if (s.intro && s.intro.length) {
        ui.st.textContent = "🔊 안내 듣기…";
        if (!(await say(s.intro))) return null;
        await sleep(400);
      }
      let replays = s.replay || 0;
      const playQ = async () => {
        for (let r = 0; r < (s.repeat || 1); r++) {
          if (s.say.length) {
            ui.st.textContent = r ? "🔊 한 번 더 듣기…" : "🔊 질문 듣기…";
            if (!(await say(s.say))) return false;
            if (r < (s.repeat || 1) - 1) await sleep(700);
          }
        }
        return true;
      };
      if (!(await playQ())) return null;
      if (aborted) return null;
      if (OPIC) {                                           // 다시 듣기 5초 창
        ui.st.textContent = "답변을 시작하세요 — 5초 뒤 자동 시작";
        let again = false;
        const w = {};
        if (replays > 0) button(ui.act, `🔁 다시 듣기 (${replays}회)`).onclick = () => { again = true; w.skip && w.skip(); };
        button(ui.act, "🎤 지금 답하기", "primary").onclick = () => w.skip && w.skip();
        await countdown(5, "시작까지", w);
        ui.act.innerHTML = "";
        if (again) { replays--; if (!(await playQ())) return null; }
      } else if (s.prep) {
        await beep(660);
        ui.st.textContent = "⏳ 준비하세요";
        if (!MOCK) button(ui.act, "준비 끝").onclick = () => ctl.skip && ctl.skip();
        await countdown(s.prep, "준비", ctl);
        ui.act.innerHTML = "";
      }
      if (aborted) return null;
      await beep(880);
      ui.st.textContent = "🎤 답하세요";
      const cap = capture(stream, ui.live);
      if (OPIC) {
        button(ui.act, "다음 →", "primary").onclick = () => ctl.skip && ctl.skip();
        await stopwatch(180, "답변", ctl);
      } else {
        if (!MOCK) button(ui.act, "답변 끝내기").onclick = () => ctl.skip && ctl.skip();
        await countdown(s.speak, "답변", ctl);
      }
      ui.act.innerHTML = "";
      ui.st.textContent = "저장 중…";
      const got = await cap.stop();
      timerEl.textContent = "";
      await beep(520, 160);
      return { step: s, ...got };
    }

    // ------------------------------------------------------------ 모범 답안 듣기·따라 말하기 (채점 화면)
    const sayTexts = [];
    let shadowing = null;
    /* 음성 인식만으로 sec 초 동안 듣는다 (녹음 없이) */
    function hear(sec, liveEl) {
      return new Promise(resolve => {
        const rec = new SR();
        rec.lang = "en-US"; rec.continuous = true; rec.interimResults = true;
        let fin = "", mid = "", done = false;
        rec.onresult = e => {
          mid = "";
          for (let i = e.resultIndex; i < e.results.length; i++) {
            if (e.results[i].isFinal) fin += e.results[i][0].transcript + " "; else mid += e.results[i][0].transcript;
          }
          liveEl.textContent = (fin + mid).trim();
        };
        const end = () => { if (done) return; done = true; try { rec.stop(); } catch (e) { /* 무시 */ } setTimeout(() => resolve((fin + mid).trim()), 400); };
        rec.onerror = () => end();
        rec.onend = () => end();
        try { rec.start(); } catch (e) { return resolve(""); }
        setTimeout(end, sec * 1000);
        shadowing = end;
      });
    }
    stage.addEventListener("click", async e => {
      const say1 = e.target.closest("[data-say]");
      if (say1) { TTS.stop(); say([{ text: sayTexts[+say1.dataset.say], gender: "female" }]); return; }
      const sh = e.target.closest("[data-shadow]");
      if (!sh) return;
      if (shadowing) { shadowing(); return; }                 // 다시 누르면 일찍 끝내기
      const k = +sh.dataset.shadow, text = sayTexts[k], box = stage.querySelector(`[data-shres="${k}"]`);
      TTS.stop();
      const secs = Math.max(8, Math.ceil(countWords(text) * 0.45) + 4);
      sh.textContent = "■ 끝내기";
      box.innerHTML = `<div class="small muted">🎤 말하세요 (최대 ${secs}초)</div><div class="small spk-live" data-l></div>`;
      const heard = await hear(secs, box.querySelector("[data-l]"));
      shadowing = null;
      sh.textContent = "🎤 다시 따라 말하기";
      const t = wordsOf(text), m = lcs(t, wordsOf(heard));
      const acc = t.length ? m.match / t.length : 0;
      box.innerHTML = heard ? `<div class="explain diff"><span class="verdict ${acc >= 0.8 ? "ok" : "bad"}">${Math.round(acc * 100)}%</span>` +
        t.map((w, i) => `<span class="${m.hit.has(i) ? "ok" : "miss"}">${esc(w.raw)}</span>`).join(" ") + `</div>` : `<div class="small muted">인식된 말이 없습니다.</div>`;
    });

    // ------------------------------------------------------------ 채점 화면
    function suggest(a) {
      const s = a.step;
      if (s.rubric === "read_aloud" && a.accuracy != null) return a.accuracy >= 0.9 ? 3 : a.accuracy >= 0.75 ? 2 : a.accuracy >= 0.4 ? 1 : 0;
      if (a.text === "" && SR) return s.rubric === "opic" ? 1 : 0;      // 인식된 말 없음
      return null;
    }
    function reviewCard(a, k) {
      const s = a.step;
      const words = a.text ? countWords(a.text) : null;
      const wpm = words && a.seconds > 3 ? Math.round(words / (a.seconds / 60)) : null;
      let mine = "";
      if (s.target && a.text) {
        const t = wordsOf(s.target), m = lcs(t, wordsOf(a.text));
        a.accuracy = t.length ? m.match / t.length : 0;
        mine = `<div class="explain diff"><span class="verdict ${a.accuracy >= 0.85 ? "ok" : "bad"}">${Math.round(a.accuracy * 100)}%</span>` +
          t.map((w, i) => `<span class="${m.hit.has(i) ? "ok" : "miss"}">${esc(w.raw)}</span>`).join(" ") +
          `<div class="small muted">빨간 단어는 음성 인식이 알아듣지 못한 단어입니다 (발음 확인용, 인식 오류일 수도 있음)</div></div>`;
      } else if (a.text) {
        mine = `<div class="explain"><b>내가 한 말 (음성 인식)</b><div>${esc(a.text)}</div></div>`;
      } else if (a.text === "") {
        mine = `<div class="explain">인식된 말이 없습니다.</div>`;
      }
      a.words = words;
      const q = s.question_text || (s.show && s.show.question) || "";
      const rub = P.rubrics[s.rubric] || [];
      const sug = suggest(a);
      const samples = (s.samples || []).filter(x => x.text).map((x, i) => {
        const n = sayTexts.push(x.text.replace(/[*\/]/g, " ").replace(/\s+/g, " ").trim()) - 1;
        return `<details class="reveal-d" ${!MOCK && i === 0 ? "open" : ""}><summary>${esc(x.label)}</summary><div class="spk-sample">${esc(x.text)}</div>${x.ko ? `<div class="translation">${esc(x.ko)}</div>` : ""}
          <div class="row spk-shadow"><button type="button" class="btn small" data-say="${n}">🔊 듣기</button>${SR ? `<button type="button" class="btn small" data-shadow="${n}">🎤 따라 말하기</button>` : ""}<span class="small muted">모범 답안을 듣고 그대로 말해 보세요 (섀도잉)</span></div>
          <div data-shres="${n}"></div></details>`;
      }).join("");
      const qn = q ? sayTexts.push(q) - 1 : -1;
      return `<div class="card spk-review" data-k="${k}">
        <div class="spread"><b>${esc(s.label)} · ${esc(s.title)}</b><span class="small muted">${a.seconds ? `${Math.round(a.seconds)}초` : ""}${words != null ? ` · ${words}단어` : ""}${wpm ? ` · 분당 ${wpm}단어` : ""}</span></div>
        ${q ? `<div class="sentence spk-q" style="margin:8px 0 2px">${esc(q)} <button type="button" class="btn small ghost" data-say="${qn}" title="질문 다시 듣기">🔊</button></div>` : ""}${s.question_ko ? `<div class="small muted">${esc(s.question_ko)}</div>` : ""}
        ${a.url ? `<audio controls src="${a.url}" class="spk-audio"></audio>` : `<div class="small muted">녹음 없음 (마이크 권한을 확인하세요)</div>`}
        ${mine}
        ${s.notes && s.notes.length ? `<details class="reveal-d"><summary>${s.rubric === "describe_picture" ? "장면 영어 표현" : s.rubric === "read_aloud" ? "발음 주의 단어" : "답변 뼈대"}</summary><ul class="clean">${s.notes.map(n => `<li>${esc(n)}</li>`).join("")}</ul></details>` : ""}
        ${samples}
        ${s.tips && s.tips.length ? `<details class="reveal-d"><summary>팁</summary><ul class="clean">${s.tips.map(t => `<li>${esc(t)}</li>`).join("")}</ul></details>` : ""}
        <div class="spk-rubric"><b>자기 채점</b> <span class="small muted">${sug != null ? `추천 ${sug}점 (음성 인식 기준) · ` : ""}가장 가까운 것을 고르세요</span>
          ${rub.map(([p, d]) => `<label class="rb"><input type="radio" name="rb${k}" value="${p}" ${sug === p ? "checked" : ""}> <b>${p}</b> ${esc(d)}</label>`).join("")}
        </div></div>`;
    }
    /* 답 목록을 채점 화면으로 → 고른 점수를 붙여 resolve */
    function review(list, submitLabel) {
      return new Promise(resolve => {
        timerEl.textContent = "";
        labelEl.textContent = "스스로 채점";
        prog.textContent = `${list.length}개 답변`;
        stage.innerHTML = list.map(reviewCard).join("") +
          `<div class="quiz-foot"><span class="small muted" data-left></span><button class="btn primary" data-submit disabled>${submitLabel}</button></div>`;
        const btn = stage.querySelector("[data-submit]"), left = stage.querySelector("[data-left]");
        const check = () => {
          const n = list.filter((a, k) => stage.querySelector(`input[name=rb${k}]:checked`)).length;
          btn.disabled = n < list.length;
          left.textContent = n < list.length ? `채점 안 한 답변 ${list.length - n}개` : "";
        };
        stage.addEventListener("change", check);
        check();
        window.scrollTo({ top: 0 });
        btn.onclick = () => {
          btn.disabled = true;
          list.forEach((a, k) => { a.points = +stage.querySelector(`input[name=rb${k}]:checked`).value; });
          resolve(list);
        };
      });
    }
    const rowOf = a => ({ ...a.step.ref, points: a.points, words: a.words, seconds: a.seconds ? Math.round(a.seconds * 10) / 10 : null,
                          accuracy: a.accuracy != null ? Math.round(a.accuracy * 1000) / 1000 : null, response: a.text || "" });

    // ------------------------------------------------------------ 진행
    async function runPractice(stream) {
      for (let u = 0; u < units.length; u++) {
        const got = [];
        for (let i = 0; i < units[u].steps.length; i++) {
          const a = await runStep(units[u].steps[i], i === 0, stream);
          if (!a) return;
          got.push(a);
        }
        const done = await review(got, u < units.length - 1 ? "저장하고 다음 문제 →" : "저장하고 끝내기");
        let info = "";
        try {
          Spk.record(EXAM, done.map(rowOf));
          if (EXAM === "tsp") { const est = Spk.tspEstimate(Spk.tspTaskStats()); if (est != null) info = `지금까지 기록으로 추정한 점수: ${est}점`; }
          else { const g = Spk.opicStats().grade; if (g) info = `지금까지 기록으로 추정한 등급: ${g}`; }
        } catch (e) { alert("기록 실패: " + e.message); }
        done.forEach(a => ratios.push(a.points / a.step.max));
        if (u === units.length - 1) return finishPractice(info);
      }
    }
    function finishPractice(info) {
      stopAll();
      const avg = ratios.length ? Math.round(ratios.reduce((a, b) => a + b, 0) / ratios.length * 100) : 0;
      labelEl.textContent = "끝";
      prog.textContent = "";
      stage.innerHTML = `<div class="card" id="practice-done" style="max-width:640px;margin:0 auto;text-align:center">
        <h2>연습 끝! 자기 채점 평균 ${avg}%</h2><p class="muted">${esc(info || "기록은 홈 화면의 추정치와 기록에 반영됩니다.")}</p>
        <div class="row" style="justify-content:center"><a class="btn primary" href="${location.pathname.split("/").pop() + location.search}" data-again>새 문제로 다시</a>
        <a class="btn" href="${P.home}">홈으로</a></div></div>`;
      stage.querySelector("[data-again]").addEventListener("click", ev => { ev.preventDefault(); location.reload(); });
    }
    async function runMock(stream) {
      startTotal(P.minutes);
      let lastTask = null;
      for (const u of units) {
        for (let i = 0; i < u.steps.length; i++) {
          if (aborted) break;
          const s = u.steps[i];
          const a = await runStep(s, s.ref.task !== lastTask, stream);
          lastTask = s.ref.task;
          if (!a) break;
          answers.push(a);
          if (!aborted) await sleep(600);
        }
        if (aborted) break;
      }
      stopAll();
      if (aborted) alert("시험 시간이 끝났습니다. 여기까지 답한 것으로 채점합니다.");
      if (!answers.length) {
        stage.innerHTML = `<div class="card empty">답변이 없습니다. <a href="${P.home}">돌아가기</a></div>`;
        return;
      }
      totalEl.hidden = true;
      const done = await review(answers, "채점 제출하고 결과 보기");
      const duration = Math.round((nowMs() - startedAt) / 1000);
      const send = () => {
        const btn = stage.querySelector("[data-submit]");
        try {
          Spk.finishMock(P.mock_id, done.map(rowOf), duration);
          submitted = true;
          UI.go(`${EXAM}-mock-result.html?mid=${P.mock_id}`);
        } catch (e) {
          alert("제출 실패: " + e.message + " (채점한 내용은 그대로입니다. 버튼을 다시 누르세요.)");
          btn.disabled = false;
          btn.onclick = () => { btn.disabled = true; send(); };       // review() 의 버튼은 한 번만 쓰는 Promise 라서 다시 연결한다
        }
      };
      send();
    }
    let submitted = false;

    $("#start-btn").addEventListener("click", async () => {
      $("#intro").classList.add("hidden");
      $("#quiz-main").classList.remove("hidden");
      await beep(440, 1);                                    // 사용자 동작 안에서 소리 장치 열기
      const stream = await openMic();
      if (!stream) prog.textContent = "마이크를 쓸 수 없어 녹음 없이 진행합니다 (스스로 채점은 가능)";
      if (stream) cleanup.push(() => stream.getTracks().forEach(t => t.stop()));
      await TTS.load();
      startedAt = nowMs();
      if (MOCK) await runMock(stream); else await runPractice(stream);
      if (stream) stream.getTracks().forEach(t => t.stop());
    });
    window.addEventListener("pagehide", stopAll);          // beforeunload 에서 정리하면 "나가시겠습니까?" 취소 뒤에도 음성·마이크가 꺼진 채 남는다
    window.addEventListener("beforeunload", e => {
      if (MOCK && answers.length && !submitted) { e.preventDefault(); e.returnValue = ""; }
    });
  }
})();
