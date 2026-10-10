/* 토플 과제 연습 (toefl-practice.html?task=r_words&level=3&n=3&review=1) — TS 앱 templates/toefl/practice.html + static/js/toefl.js.
   과제 종류(kind)별로 그린다.
   words 빈칸 단어 · set 읽기 세트 · response 응답 고르기 · listen_set 대화/강의 · sentence 문장 만들기
   email/discussion 쓰기(타이머+자기 평가) · repeat 따라 말하기(음성 인식 채점) · interview 인터뷰(녹음 45초)
   한 문제를 풀 때마다 Toefl.record 로 저장한다 (영역 추정 밴드에 바로 반영). */
(function () {
  const task = UI.params().get("task") || "";
  UI.boot({ exam: "toefl", page: "toefl-practice:" + task }, async () => {
    const { esc, $ } = UI;
    const T = Toefl.TASKS[task];
    if (!T) {
      $("app").innerHTML = `<div class="card empty"><h2>알 수 없는 과제입니다</h2><p>토플 홈에서 과제를 골라 주세요.</p><a class="btn primary" href="toefl.html">토플 홈</a></div>`;
      return;
    }
    const sec = Toefl.SECTIONS[T.section];
    const L = ["A", "B", "C", "D"];
    const LB = Toefl.LEVEL_BAND;
    document.title = `${T.name} · 토플 · TS`;

    // ---- 문제 고르기 -----------------------------------------------------------------
    await Toefl.load([task]);
    let level = UI.intParam("level");
    level = LB[level] ? level : null;
    let n = UI.intParam("n");
    n = n === null ? Toefl.DEFAULT_N[task] : Math.max(1, Math.min(n, 30));
    const review = UI.params().get("review") === "1";
    let items;
    if (review) {                                      // 오답노트: 마지막에 틀린 문제만, 오래전에 틀린 것부터
      const wrong = Toefl.wrongItems(task);
      items = Object.keys(wrong).sort((a, b) => (wrong[a].at < wrong[b].at ? -1 : wrong[a].at > wrong[b].at ? 1 : 0))
        .map(i => Toefl.get(task, i)).filter(Boolean).slice(0, n);
    } else items = Toefl.pick(task, level, n);
    const tts = UI.tts();
    const SET_LIKE = ["set", "listen_set", "repeat", "interview"].includes(T.kind);

    $("app").innerHTML = `
      <div class="page-head">
        <div>
          <div class="muted small">${esc(sec.name)} · ${esc(T.en)}</div>
          <h1>${esc(T.name)}${review ? ` <span class="tag bad">오답 다시 풀기</span>` : ""} ${level ? `<span class="tag">밴드 ${LB[level]} · ${Toefl.CEFR[LB[level]]}</span>` : ""}</h1>
          <div class="muted small">${esc(T.desc)}</div>
        </div>
        <div class="row">
          <select id="lv" aria-label="난이도"><option value="">모든 난이도</option>
            ${[1, 2, 3, 4, 5].map(lv => `<option value="${lv}" ${level === lv ? "selected" : ""}>밴드 ${LB[lv]} (${Toefl.count(task, lv)})</option>`).join("")}</select>
          <a class="btn" href="toefl.html#sec-${T.section}">토플 홈</a>
        </div>
      </div>
      ${items.length ? `
      <div id="intro" class="card" style="max-width:640px;margin:10px auto">
        <p>${items.length}개 ${SET_LIKE ? "세트" : "문제"}를 풉니다.</p>
        ${T.kind === "repeat" || T.kind === "interview"
          ? `<p class="small">마이크를 씁니다. 브라우저가 권한을 물으면 허용하세요. 크롬·엣지에서는 말한 내용을 글자로 바꿔 ${T.kind === "repeat" ? "정확도를 채점" : "보여 줍니다"}.
              (마이크는 https 주소나 이 PC(127.0.0.1·localhost)에서만 켜집니다. 녹음은 저장하지 않습니다)</p>`
          : T.kind === "email" || T.kind === "discussion"
            ? `<p class="small">실제 시험처럼 ${T.kind === "email" ? "7" : "10"}분 타이머가 돌아갑니다. 다 쓰면 모범 답안과 비교해 0~5점으로 스스로 채점합니다.</p>`
            : T.kind === "response" || T.kind === "listen_set"
              ? `<p class="small">듣기 음성은 브라우저 음성 합성으로 재생됩니다. 실제 시험은 한 번만 들을 수 있으니, 먼저 한 번만 듣고 푸는 연습을 하세요.</p>` : ""}
        <button class="btn primary" id="start-btn">시작하기</button>
      </div>
      <div id="quiz-main" class="hidden">
        <div class="quiz-top"><b>${esc(T.name)}</b><span class="progress muted small" id="progress"></span><span class="timer" id="timer"></span></div>
        <div id="stage"></div>
      </div>` : `<div class="card empty">${review
        ? `이 과제에는 다시 풀 틀린 문제가 없습니다. <a href="toefl-review.html">오답노트로</a>`
        : `이 조건의 문제가 아직 없습니다. 다른 난이도를 골라 보세요.`}</div>`}`;
    $("lv").onchange = e => UI.go(UI.url("toefl-practice.html", { task, level: e.target.value }));
    if (!items.length) return;

    // ---- 진행 --------------------------------------------------------------------------
    const stage = $("stage"), prog = $("progress"), timerEl = $("timer");
    let idx = 0, scores = [], tick = null, cleanup = [];
    const reg = f => cleanup.push(f);
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    function stopAll() {
      TTS.stop();
      clearInterval(tick);
      tick = null;
      cleanup.forEach(f => { try { f(); } catch (e) { /* 무시 */ } });
      cleanup = [];
    }
    function countdown(sec, onEnd, label) {
      clearInterval(tick);
      const end = Date.now() + sec * 1000;
      const draw = () => {
        const left = Math.max(0, (end - Date.now()) / 1000);
        timerEl.textContent = `${label || "남은 시간"} ${UI.fmtTime(left)}`;
        timerEl.classList.toggle("over", left < Math.min(60, sec / 4));
        if (left <= 0) { clearInterval(tick); tick = null; onEnd && onEnd(); }
      };
      draw();
      tick = setInterval(draw, 250);
    }
    function stopwatch() {
      clearInterval(tick);
      const t0 = Date.now();
      tick = setInterval(() => { timerEl.textContent = `경과 ${UI.fmtTime((Date.now() - t0) / 1000)}`; timerEl.classList.remove("over"); }, 500);
    }
    function setProgress(extra) {
      const avg = scores.length ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length * 100) : null;
      prog.textContent = `${idx + 1} / ${items.length}` + (avg !== null ? ` · 평균 ${avg}%` : "") + (extra ? ` · ${extra}` : "");
    }
    function save(item, results) {
      try {
        Toefl.record(task, item.id, item.level, results);
        const band = Toefl.sectionBands()[T.section];
        if (band != null) setProgress(`이 영역 추정 밴드 ${band}`);
      } catch (e) { alert("기록 실패: " + e.message); }
    }
    function head(it, extra) {
      return `<div class="item-head"><span class="tag">밴드 ${LB[it.level]}</span>${it.topic ? `<span class="tag">${esc(it.topic)}</span>` : ""}` +
        `${it.doc_type ? `<span class="tag">${esc(it.doc_type)}</span>` : ""}${it.course ? `<span class="tag">${esc(it.course)}</span>` : ""}${extra || ""}` +
        `<span style="margin-left:auto">${idx + 1} / ${items.length}</span></div>`;
    }
    function nextBtn() {
      return idx < items.length - 1 ? `<button class="btn primary" data-act="next">다음 → <span class="kbd">Enter</span></button>`
        : `<button class="btn primary" data-act="finish">결과 보기</button>`;
    }
    function next() { stopAll(); idx++; render(); }
    function finish() {
      stopAll();
      const avg = scores.length ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length * 100) : 0;
      timerEl.textContent = "";
      stage.innerHTML = `<div class="card" style="max-width:640px;margin:0 auto;text-align:center">
        <h2>끝! 평균 ${avg}%</h2><p class="muted">기록은 토플 홈의 영역별 추정 밴드에 반영됩니다.</p>
        <div class="row" style="justify-content:center"><a class="btn primary" href="${esc(location.pathname.split("/").pop() + location.search)}" data-again>새 문제로 다시</a>
        <a class="btn" href="toefl.html">토플 홈</a></div></div>`;
      stage.querySelector("[data-again]").onclick = e => { e.preventDefault(); location.reload(); };
    }
    function render() {
      if (idx >= items.length) return finish();
      setProgress();
      window.scrollTo({ top: 0 });
      RENDER[T.kind](items[idx]);
    }
    stage.addEventListener("click", e => {
      const a = e.target.closest("[data-act]");
      if (!a) return;
      if (a.dataset.act === "next") next();
      else if (a.dataset.act === "finish") finish();
    });
    document.addEventListener("keydown", e => {
      if (e.key === "Enter" && !e.target.closest?.("textarea, input") && stage.querySelector("[data-act=next], [data-act=finish]")) {
        e.preventDefault();
        stage.querySelector("[data-act=next], [data-act=finish]").click();
      }
    });

    // ---- 음성 ----------------------------------------------------------------------------
    function audioBox(id) {
      return `<div class="audio-box" id="${id || "abox"}"><button class="btn small primary" data-play>▶ 듣기</button>` +
        `<button class="btn small" data-stop>■ 정지</button><span class="status" data-status></span></div>`;
    }
    function bindAudio(box, segsFn) {
      const status = box.querySelector("[data-status]");
      box.querySelector("[data-play]").onclick = async () => {
        status.textContent = "재생 중…";
        const done = await TTS.play(segsFn(), { rate: tts.rate, accent: tts.accent });
        if (done) status.textContent = "재생 끝";
      };
      box.querySelector("[data-stop]").onclick = () => { TTS.stop(); status.textContent = "정지"; };
    }

    // ---- 객관식 공통 ------------------------------------------------------------------------
    function mcqHtml(q, qi, num) {
      return `<div class="q" data-qbox="${qi}"><div class="qtext">${num ? num + ". " : ""}${esc(q.q || "")}</div><div class="choices">` +
        q.choices.map((c, ci) => `<button type="button" class="choice" data-q="${qi}" data-c="${ci}"><span class="letter">${L[ci]}</span><span>${esc(c)}</span></button>`).join("") +
        `</div><div class="explain hidden" data-exp="${qi}"></div></div>`;
    }
    function bindMcq(it, qs, onGraded) {
      const chosen = {};
      let graded = false;
      const btn = stage.querySelector("[data-grade]");
      stage.querySelectorAll(".choice").forEach(b => b.onclick = () => {
        if (graded) return;
        const qi = +b.dataset.q;
        chosen[qi] = +b.dataset.c;
        stage.querySelectorAll(`.choice[data-q="${qi}"]`).forEach(x => x.classList.toggle("sel", +x.dataset.c === chosen[qi]));
        btn.disabled = Object.keys(chosen).length < qs.length;
        if (qs.length === 1) grade();
      });
      btn.onclick = grade;
      function grade() {
        if (graded) return;
        graded = true;
        const results = qs.map((q, qi) => {
          const ok = chosen[qi] === q.answer;
          stage.querySelectorAll(`.choice[data-q="${qi}"]`).forEach(x => {
            const c = +x.dataset.c;
            x.classList.remove("sel");
            if (c === q.answer) x.classList.add("right"); else if (c === chosen[qi]) x.classList.add("wrong");
          });
          stage.querySelectorAll(".choices").forEach(x => x.classList.add("locked"));
          const ex = stage.querySelector(`[data-exp="${qi}"]`);
          ex.innerHTML = `<span class="verdict ${ok ? "ok" : "bad"}">${ok ? "정답" : "오답"}</span>정답 (${L[q.answer]}) ${q.type ? `<span class="tag">${esc(q.type)}</span>` : ""}<div>${esc(q.explanation)}</div>`;
          ex.classList.remove("hidden");
          return { qidx: qi, score: ok ? 1 : 0, response: String(chosen[qi] ?? "") };
        });
        results.forEach(r => scores.push(r.score));
        btn.classList.add("hidden");
        stage.querySelector("[data-after]").innerHTML = (onGraded ? onGraded() : "") + `<div class="quiz-foot"><span></span>${nextBtn()}</div>`;
        save(it, results);
      }
    }
    const reveal = (title, body, open) => `<details class="reveal-d" ${open ? "open" : ""}><summary>${title}</summary>${body}</details>`;
    const transl = t => reveal("해석", `<div class="translation">${esc(t)}</div>`);

    // ---- 과제별 ------------------------------------------------------------------------------
    const RENDER = {
      // 빈칸 단어 완성
      words(it) {
        stopwatch();
        let k = 0;
        const blanks = [];
        const html = esc(it.text).replace(/\[\[([A-Za-z]+)\|([A-Za-z]+)\]\]/g, (_, a, b) => {
          blanks.push(a + b);
          const i = k++;
          return `<span class="tw">${a}<input class="tw-in" data-i="${i}" maxlength="${b.length}" size="${Math.max(2, b.length)}" autocomplete="off" autocapitalize="off" spellcheck="false" aria-label="빈칸 ${i + 1}"></span>`;
        });
        stage.innerHTML = head(it) + `<div class="passage">${html}</div>
          <div class="quiz-foot"><span class="muted small">빈칸에 단어의 나머지 철자를 쓰세요 · Tab 으로 다음 칸</span><button class="btn primary" data-check>채점하기</button></div><div data-after></div>`;
        stage.querySelector(".tw-in")?.focus();
        stage.querySelector("[data-check]").onclick = function () {
          this.classList.add("hidden");
          let ok = 0;
          stage.querySelectorAll(".tw-in").forEach(inp => {
            const full = blanks[+inp.dataset.i];
            const want = full.slice(full.length - inp.maxLength);
            const good = inp.value.trim().toLowerCase() === want.toLowerCase();
            if (good) ok++;
            inp.disabled = true;
            inp.classList.add(good ? "ok" : "bad");
            if (!good) inp.insertAdjacentHTML("afterend", `<sup class="tw-fix">${esc(full)}</sup>`);
          });
          scores.push(ok / blanks.length);
          stage.querySelector("[data-after]").innerHTML = `<div class="explain"><span class="verdict ${ok >= 7 ? "ok" : "bad"}">${ok} / ${blanks.length}</span>틀린 칸 위에 정답 단어를 표시했습니다.</div>` +
            transl(it.translation) + `<div class="quiz-foot"><span></span>${nextBtn()}</div>`;
          save(it, [{ qidx: 0, score: ok / blanks.length, response: [...stage.querySelectorAll(".tw-in")].map(i => i.value).join("|") }]);
        };
      },

      // 일상 글·학술 지문
      set(it) {
        stopwatch();
        stage.innerHTML = head(it) + `<div class="item-body split"><div class="passage-scroll"><div class="passage">` +
          `${it.title ? `<span class="doc-title">${esc(it.title)}</span>` : ""}${esc(it.text)}</div></div>` +
          `<div>${it.questions.map((q, i) => mcqHtml(q, i, i + 1)).join("")}` +
          `<div class="quiz-foot"><span></span><button class="btn primary" data-grade disabled>채점하기</button></div><div data-after></div></div></div>`;
        bindMcq(it, it.questions, () => transl(it.translation));
      },

      // 응답 고르기 (문장은 소리로만)
      response(it) {
        stopwatch();
        stage.innerHTML = head(it) + audioBox() + `<div class="card muted small" style="margin:10px 0">한 문장을 듣고 가장 알맞은 응답을 고르세요.</div>` +
          mcqHtml({ q: "", choices: it.choices }, 0) + `<button class="btn primary hidden" data-grade>채점</button><div data-after></div>`;
        bindAudio(stage.querySelector("#abox"), () => [{ text: it.prompt, gender: it.voice }]);
        stage.querySelector("[data-play]").click();
        bindMcq(it, [{ ...it, q: "" }], () => reveal("들은 문장", `<div class="script-line"><b>▶</b>${esc(it.prompt)}</div>`, true) + transl(it.translation));
      },

      // 대화·안내·강의
      listen_set(it) {
        stopwatch();
        const conv = Array.isArray(it.script);
        const segs = () => conv
          ? it.script.map(ln => ({ text: ln.t, gender: it.speakers[ln.s], nth: ln.s.endsWith("2") ? 1 : 0, pause: 350 }))
          : [{ text: it.script, gender: it.voice }];
        const kind = it.kind === "announcement" ? "안내 방송" : it.kind === "academic" ? "강의" : "대화";
        stage.innerHTML = head(it, `<span class="tag">${kind}</span>`) + audioBox() +
          `<div style="margin-top:12px">${it.questions.map((q, i) => mcqHtml(q, i, i + 1)).join("")}</div>` +
          `<div class="quiz-foot"><span></span><button class="btn primary" data-grade disabled>채점하기</button></div><div data-after></div>`;
        bindAudio(stage.querySelector("#abox"), segs);
        stage.querySelector("[data-play]").click();
        const script = conv ? it.script.map(ln => `<div class="script-line"><b>${esc(ln.s)}</b>${esc(ln.t)}</div>`).join("")
          : `<div class="script-line">${esc(it.script)}</div>`;
        bindMcq(it, it.questions, () => reveal("스크립트", script, true) + transl(it.translation));
      },

      // 문장 만들기
      sentence(it) {
        stopwatch();
        const order = it.chunks.map((c, i) => i);
        do { order.sort(() => Math.random() - 0.5); } while (order.length > 1 && order.every((v, i) => v === i));
        const placed = [];
        stage.innerHTML = head(it) + `<div class="card"><div class="small muted">상대방</div><div class="sentence">“${esc(it.context)}” <button class="btn small ghost" data-say>🔊</button></div>
          <div class="small muted">나의 대답 (덩어리를 눌러 순서대로 놓기, 놓은 것을 누르면 되돌리기)</div>
          <div class="ws-line" data-line></div><div class="ws-bank" data-bank></div>
          <div class="quiz-foot"><span></span><button class="btn primary" data-check disabled>채점하기</button></div><div data-after></div></div>`;
        const line = stage.querySelector("[data-line]"), bank = stage.querySelector("[data-bank]"), chk = stage.querySelector("[data-check]");
        stage.querySelector("[data-say]").onclick = () => TTS.play([{ text: it.context, gender: "female" }], { rate: tts.rate, accent: tts.accent });
        function draw() {
          line.innerHTML = placed.map((ci, k) => `<button class="chunk on" data-k="${k}">${esc(it.chunks[ci])}</button>`).join("") || `<span class="muted small">여기에 문장이 만들어집니다</span>`;
          bank.innerHTML = order.filter(ci => !placed.includes(ci)).map(ci => `<button class="chunk" data-ci="${ci}">${esc(it.chunks[ci])}</button>`).join("");
          chk.disabled = placed.length !== it.chunks.length;
        }
        line.onclick = e => { const b = e.target.closest("[data-k]"); if (b && !chk.classList.contains("hidden")) { placed.splice(+b.dataset.k, 1); draw(); } };
        bank.onclick = e => { const b = e.target.closest("[data-ci]"); if (b) { placed.push(+b.dataset.ci); draw(); } };
        draw();
        chk.onclick = () => {
          const mine = placed.map(ci => it.chunks[ci]).join(" ");
          const ok = mine === it.chunks.join(" ") || (it.alts || []).includes(mine);       // 부사 위치처럼 다른 순서도 맞는 문장
          chk.classList.add("hidden");
          bank.innerHTML = "";
          line.classList.add(ok ? "ok" : "bad");
          scores.push(ok ? 1 : 0);
          stage.querySelector("[data-after]").innerHTML = `<div class="explain"><span class="verdict ${ok ? "ok" : "bad"}">${ok ? "정답" : "오답"}</span>` +
            `정답: <b>${esc(it.answer)}</b><div>${esc(it.explanation)}</div><div class="muted">${esc(it.translation)}</div></div><div class="quiz-foot"><span></span>${nextBtn()}</div>`;
          save(it, [{ qidx: 0, score: ok ? 1 : 0, response: mine }]);
        };
      },

      email(it) { writing(it, 7 * 60, `<div class="card"><div class="small muted">상황</div><p>${esc(it.situation)}</p>
        <div class="small muted">받는 사람: <b>${esc(it.to)}</b> · 이메일에 꼭 넣을 내용</div><ul class="clean">${it.tasks.map(t => `<li>${esc(t)}</li>`).join("")}</ul></div>`); },

      discussion(it) { writing(it, 10 * 60, `<div class="card"><div class="small muted">교수 (${esc(it.course)})</div><p>${esc(it.professor)}</p>` +
        it.students.map(s => `<div class="passage" style="margin-top:8px;font-size:.95rem"><b>${esc(s.name)}</b>\n${esc(s.post)}</div>`).join("") + `</div>`); },

      repeat(it) { repeatTask(it); },
      interview(it) { interviewTask(it); },
    };

    // ---- 쓰기 (이메일·토론) --------------------------------------------------------------------
    function rubricHtml() {
      return `<div class="card"><b>자기 채점 (0~5)</b> <span class="small muted">모범 답안과 비교해 가장 가까운 것을 고르세요</span>` +
        Toefl.RUBRIC.map(([s, d]) => `<label class="rb"><input type="radio" name="rb" value="${s}"> <b>${s}</b> ${esc(d)}</label>`).join("") +
        `<div class="quiz-foot"><span></span><button class="btn primary" data-rb disabled>점수 저장</button></div></div>`;
    }
    function bindRubric(it, response) {
      const btn = stage.querySelector("[data-rb]");
      stage.querySelectorAll("input[name=rb]").forEach(r => r.onchange = () => { btn.disabled = false; });
      btn.onclick = () => {
        const v = +stage.querySelector("input[name=rb]:checked").value;
        scores.push(v / 5);
        save(it, [{ qidx: 0, score: v / 5, response }]);
        btn.outerHTML = nextBtn();
      };
    }
    function writing(it, seconds, promptHtml) {
      stage.innerHTML = head(it) + promptHtml + `<textarea class="wr" data-text placeholder="여기에 영어로 쓰세요" spellcheck="false"></textarea>
        <div class="spread small"><span class="muted" data-wc>0 단어</span><button class="btn primary" data-submit>제출하고 모범 답안 보기</button></div><div data-after></div>`;
      const ta = stage.querySelector("[data-text]"), wc = stage.querySelector("[data-wc]");
      ta.focus();
      ta.oninput = () => { const k = (ta.value.match(/[A-Za-z']+/g) || []).length; wc.textContent = `${k} 단어${T.kind === "email" ? " (권장 100~150)" : " (권장 100 이상)"}`; };
      const submit = () => {
        clearInterval(tick);
        timerEl.textContent = "";
        ta.readOnly = true;
        stage.querySelector("[data-submit]").classList.add("hidden");
        stage.querySelector("[data-after]").innerHTML =
          reveal("모범 답안", `<div class="passage" style="font-size:.95rem">${esc(it.sample)}</div>`, true) +
          reveal("모범 답안 해석", `<div class="translation">${esc(it.sample_ko)}</div>`) +
          reveal("팁", `<ul class="clean">${it.tips.map(t => `<li>${esc(t)}</li>`).join("")}</ul>`, true) + rubricHtml();
        bindRubric(it, ta.value);
      };
      stage.querySelector("[data-submit]").onclick = submit;
      countdown(seconds, () => { alert("시간이 끝났습니다. 지금까지 쓴 글로 제출합니다."); submit(); });
    }

    // ---- 듣고 따라 말하기 ------------------------------------------------------------------------
    async function repeatTask(it) {
      stopwatch();
      const SP = ToeflSpeech;
      const SR = SP.hasRecognition();
      const results = [];
      stage.innerHTML = head(it) + `<div class="card"><div class="spread"><b data-no></b><span class="small muted">${SR ? "음성 인식으로 자동 채점" : "이 브라우저는 음성 인식이 없어 스스로 채점합니다"}</span></div>
        <div class="audio-box" style="margin:10px 0"><span class="status" data-st>준비</span></div>
        <div class="sentence" data-sent></div><div class="small muted" data-heard></div><div data-res></div></div><div data-after></div>`;
      const st = stage.querySelector("[data-st]");
      for (let i = 0; i < it.sentences.length; i++) {
        const s = it.sentences[i];
        stage.querySelector("[data-no]").textContent = `문장 ${i + 1} / ${it.sentences.length}`;
        stage.querySelector("[data-sent]").textContent = "";
        stage.querySelector("[data-heard]").textContent = "";
        stage.querySelector("[data-res]").innerHTML = "";
        st.textContent = "듣기…";
        if (!(await TTS.play([{ text: s, gender: it.voice }], { rate: tts.rate, accent: tts.accent }))) return;
        const secs = Math.max(5, Math.ceil(s.split(/\s+/).length * 0.7) + 3);
        let score;
        if (SR) {
          st.textContent = `🎤 따라 말하세요 (${secs}초)`;
          countdown(secs, null, "말하기");
          const heard = await SP.listen(secs, stage.querySelector("[data-heard]"), null, reg);
          clearInterval(tick);
          if (heard === null || !heard.trim()) {
            st.textContent = heard === null ? "음성 인식을 쓸 수 없어 스스로 채점합니다" : "인식된 말이 없습니다 — 스스로 채점하세요";
            stage.querySelector("[data-sent]").textContent = s;
            score = await selfCheck();
          } else {
            const a = SP.wordsOf(s), m = SP.lcs(a, SP.wordsOf(heard));
            score = a.length ? m.match / a.length : 0;
            stage.querySelector("[data-res]").innerHTML = `<div class="explain diff"><span class="verdict ${score >= 0.8 ? "ok" : "bad"}">${Math.round(score * 100)}%</span>` +
              a.map((w, k) => `<span class="${m.hit.has(k) ? "ok" : "miss"}">${esc(w.raw)}</span>`).join(" ") + `</div>`;
          }
        } else {
          st.textContent = `🎤 따라 말한 뒤 스스로 채점하세요`;
          score = await selfCheck();
        }
        stage.querySelector("[data-sent]").textContent = s;
        stage.querySelector("[data-heard]").insertAdjacentHTML("beforeend", `<div>${esc(it.translations[i])}</div>`);
        results.push({ qidx: i, score, response: stage.querySelector("[data-heard]").textContent });
        await sleep(SR ? 2200 : 600);
      }
      const avg = results.reduce((a, r) => a + r.score, 0) / results.length;
      results.forEach(r => scores.push(r.score));
      st.textContent = "끝";
      stage.querySelector("[data-after]").innerHTML = `<div class="explain"><span class="verdict ${avg >= 0.8 ? "ok" : "bad"}">평균 ${Math.round(avg * 100)}%</span>` +
        reveal("7문장 다시 보기", it.sentences.map((s, k) => `<div class="script-line"><b>${k + 1}</b>${esc(s)} <span class="muted">(${Math.round(results[k].score * 100)}%)</span></div>`).join(""), true) +
        `</div><div class="quiz-foot"><span></span>${nextBtn()}</div>`;
      save(it, results);
    }
    function selfCheck() {
      return new Promise(resolve => {
        const box = stage.querySelector("[data-res]");
        box.innerHTML = `<div class="row" style="margin-top:8px"><button class="btn" data-s="1">✅ 정확히</button><button class="btn" data-s="0.5">△ 대부분</button><button class="btn" data-s="0">✗ 못함</button></div>`;
        box.onclick = e => { const b = e.target.closest("[data-s]"); if (b) { box.onclick = null; box.innerHTML = ""; resolve(+b.dataset.s); } };
      });
    }

    // ---- 인터뷰 -----------------------------------------------------------------------------------
    async function interviewTask(it) {
      stopwatch();
      const SP = ToeflSpeech;
      const SR = SP.hasRecognition();
      stage.innerHTML = head(it) + `<div class="card"><div class="small muted">인터뷰어</div><p>${esc(it.intro)}</p>
        <div class="audio-box"><span class="status" data-st>인터뷰어 소개 듣기…</span></div>
        <div data-qs></div></div><div data-after></div>`;
      const st = stage.querySelector("[data-st]"), qs = stage.querySelector("[data-qs]");
      const answers = [];
      if (!(await TTS.play([{ text: it.intro, gender: "female" }], { rate: tts.rate, accent: tts.accent }))) return;
      for (let i = 0; i < it.questions.length; i++) {
        qs.insertAdjacentHTML("beforeend", `<div class="q" data-qi="${i}"><div class="qtext">Q${i + 1}. ${esc(it.questions[i])}</div>
          <div class="small muted" data-heard></div><div data-rec></div></div>`);
        const box = qs.querySelector(`[data-qi="${i}"]`);
        box.scrollIntoView({ block: "nearest", behavior: "smooth" });
        st.textContent = `질문 ${i + 1} 듣기…`;
        if (!(await TTS.play([{ text: it.questions[i], gender: "female" }], { rate: tts.rate, accent: tts.accent }))) return;
        st.textContent = "🎤 답하세요 (45초)";
        const rec = await SP.recorder(reg);
        rec && rec.start();
        countdown(45, null, "답변");
        const heardEl = box.querySelector("[data-heard]");
        const ctl = {};
        let timeUp;
        const heardP = SR ? SP.listen(45, heardEl, ctl, reg) : new Promise(r => { timeUp = r; setTimeout(() => r(null), 45000); });
        const skip = document.createElement("button");
        skip.className = "btn small"; skip.textContent = "답변 끝내기"; skip.setAttribute("data-skip", "");
        box.appendChild(skip);
        skip.onclick = () => { if (ctl.stop) ctl.stop(); else if (timeUp) timeUp(null); };
        const heard = await heardP;
        skip.remove();
        clearInterval(tick);
        const text = heard || heardEl.textContent || "";
        const url = rec ? await rec.stop() : null;
        const words = (text.match(/[A-Za-z']+/g) || []).length;
        box.querySelector("[data-rec]").innerHTML = (url ? `<audio controls src="${url}" style="width:100%;margin-top:6px"></audio>` : "") +
          `<div class="small muted">${text ? `인식된 단어 ${words}개 (45초 답변은 보통 70~110단어)` : (SR ? "인식된 말이 없습니다" : "")}</div>` +
          reveal("모범 답변", `<div>${esc(it.samples[i])}</div><div class="muted">${esc(it.samples_ko[i])}</div>`);
        answers.push(text);
      }
      st.textContent = "인터뷰 끝";
      stage.querySelector("[data-after]").innerHTML = reveal("팁", `<ul class="clean">${it.tips.map(t => `<li>${esc(t)}</li>`).join("")}</ul>`, true) + rubricHtml();
      bindRubric(it, answers.join("\n---\n"));
    }

    // ---- 시작 ---------------------------------------------------------------------------------------
    $("start-btn").addEventListener("click", () => {
      $("intro").classList.add("hidden");
      $("quiz-main").classList.remove("hidden");
      TTS.load().then(render);
    });
    window.addEventListener("pagehide", stopAll);
  });
})();
