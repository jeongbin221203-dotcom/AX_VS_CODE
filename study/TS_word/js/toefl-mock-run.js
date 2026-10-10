/* 토플 실전 모의고사 진행 (toefl-mock-run.html?id=N): Reading → Listening → Writing → Speaking
   - 읽기·듣기: 2단계 적응형 (1모듈 정답률로 2모듈 어려움/쉬움 결정), 모듈 시간 제한
   - 듣기: 음성 한 번만, 되돌아가기 없음 · 읽기: 모듈 안에서 앞뒤 이동
   - 시험 중 정답 비공개. 끝나면 쓰기·인터뷰(·인식 안 된 따라 말하기) 자기 채점 후 제출
   - 영역 하나를 끝낼 때마다 진행 상황을 localStorage `ts-tmock-<id>` 에 저장 → 새로고침해도 끝낸 영역은 다시 안 본다
   TS 앱 static/js/toefl-mock.js 를 옮긴 것 (서버 호출만 Toefl.finishMock 으로). */
(function () {
  const id = UI.intParam("id");
  UI.boot({ exam: "toefl", page: "toefl-mock-run" }, async () => {
    const { esc, $ } = UI;
    const T = Toefl;
    const mock = id !== null ? T.getMock(id) : null;
    if (!mock) { $("app").innerHTML = `<div class="card empty"><h2>모의고사를 찾을 수 없습니다</h2><p>지워졌거나 다른 브라우저의 기록일 수 있습니다.</p><a class="btn primary" href="toefl-mock.html">모의고사 화면으로</a></div>`; return; }
    if (mock.finished_at) return UI.go(`toefl-mock-result.html?id=${id}`);
    document.title = "토플 모의고사 · TS";
    await T.load();

    // 계획(문제 번호)을 문제 데이터로 풀어 쓴다
    const resolve = list => list.map(e => ({ task: e.task, item: T.get(e.task, e.id) })).filter(e => e.item);
    const plan = {
      order: mock.plan.order, adapt_cut: mock.plan.adapt_cut, target: mock.plan.target,
      sections: Object.fromEntries(Object.entries(mock.plan.sections).map(([k, s]) => [k, s.modules
        ? { name: s.name, minutes_per_module: s.minutes_per_module, modules: [resolve(s.modules[0]), { hard: resolve(s.modules[1].hard), easy: resolve(s.modules[1].easy) }] }
        : { name: s.name, items: resolve(s.items) }])),
    };
    const tts = UI.tts();
    const L = ["A", "B", "C", "D"];
    const LB = T.LEVEL_BAND;

    $("app").innerHTML = `
      <div id="intro" class="card" style="max-width:640px;margin:20px auto">
        <h1>토플 실전 모의고사</h1>
        <p>Reading → Listening → Writing → Speaking 순서로 이어집니다. 영역 사이에만 잠깐 쉴 수 있습니다.</p>
        <p class="small muted">듣기 음성은 브라우저 음성 합성, 말하기는 마이크를 씁니다. 크롬·엣지를 권장합니다.</p>
        <button class="btn primary" id="start-btn">시작하기</button>
      </div>
      <div id="quiz-main" class="hidden">
        <div class="quiz-top"><b id="sec-name"></b><span class="progress muted small" id="progress"></span><span class="timer" id="timer"></span></div>
        <div class="qnav" id="qnav"></div>
        <div id="stage"></div>
      </div>`;
    const stage = $("stage"), prog = $("progress"), timerEl = $("timer"), qnav = $("qnav"), secName = $("sec-name");
    const SP = ToeflSpeech;
    const results = {};          // "task|id" -> {task, item_id, results:[{qidx, score, response}]}
    const routes = {};
    const review = [];           // 끝나고 자기 채점할 것
    const t0 = Date.now();
    let tick = null, cleanup = [];
    const reg = f => cleanup.push(f);

    // 영역을 끝낼 때마다 진행 상황을 브라우저에 저장 — 새로고침·실수로 닫아도 끝낸 영역은 다시 안 본다
    const KEY = `ts-tmock-${id}`;
    let doneSecs = [];
    const packReview = r => (r.kind === "write" ? { kind: r.kind, task: r.task, item_id: r.item.id, text: r.text }
      : r.kind === "interview" ? { kind: r.kind, task: r.task, item_id: r.item.id, answers: r.answers.map(a => ({ q: a.q, text: a.text })) }
        : { kind: r.kind, task: r.task, item_id: r.item.id, list: r.list });
    function persist() {
      try { localStorage.setItem(KEY, JSON.stringify({ done: doneSecs, results, routes, review: review.map(packReview) })); } catch (e) { /* 저장 불가 */ }
    }
    function loadSaved() {
      try {
        const s = JSON.parse(localStorage.getItem(KEY) || "null");
        if (!s || !Array.isArray(s.done) || !s.done.length) return false;
        if (!confirm(`이전에 끝낸 영역(${s.done.map(k => T.SECTIONS[k] ? T.SECTIONS[k].name : k).join(", ")})이 저장되어 있습니다.\n확인 = 이어서 하기 / 취소 = 처음부터 다시`)) { localStorage.removeItem(KEY); return false; }
        doneSecs = s.done.filter(k => plan.order.includes(k));
        Object.assign(results, s.results || {});
        Object.assign(routes, s.routes || {});
        for (const r of s.review || []) { const item = T.get(r.task, r.item_id); if (item) review.push({ ...r, item }); }
        return true;
      } catch (e) { return false; }
    }

    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const say = segs => TTS.play(segs, { rate: tts.rate, accent: tts.accent });
    function stopAll() { TTS.stop(); clearInterval(tick); tick = null; cleanup.forEach(f => { try { f(); } catch (e) { /* 무시 */ } }); cleanup = []; }
    function setResult(task, item, list) { results[`${task}|${item.id}`] = { task, item_id: item.id, results: list }; }

    function countdown(sec, label) {
      clearInterval(tick);
      const end = Date.now() + sec * 1000;
      let done;
      const p = new Promise(r => { done = r; });
      const draw = () => {
        const left = Math.max(0, (end - Date.now()) / 1000);
        timerEl.textContent = `${label} ${UI.fmtTime(left)}`;
        timerEl.classList.toggle("over", left < Math.min(60, sec / 5));
        if (left <= 0) { clearInterval(tick); tick = null; done(); }
      };
      draw();
      tick = setInterval(draw, 250);
      return p;
    }

    // ---- 화면 조각 ---------------------------------------------------------------------------
    function mcq(q, key, num, chosen) {
      return `<div class="q"><div class="qtext">${num ? num + ". " : ""}${esc(q.q || "")}</div><div class="choices">` +
        q.choices.map((c, ci) => `<button type="button" class="choice${chosen === ci ? " sel" : ""}" data-key="${key}" data-c="${ci}">` +
          `<span class="letter">${L[ci]}</span><span>${esc(c)}</span></button>`).join("") + `</div></div>`;
    }
    function bindChoices(ans, onChange) {
      stage.querySelectorAll(".choice").forEach(b => b.onclick = () => {
        const k = b.dataset.key;
        ans[k] = +b.dataset.c;
        stage.querySelectorAll(`.choice[data-key="${k}"]`).forEach(x => x.classList.toggle("sel", +x.dataset.c === ans[k]));
        onChange && onChange();
      });
    }
    function segsFor(task, it) {
      if (task === "l_response") return [{ text: it.prompt, gender: it.voice }];
      if (Array.isArray(it.script)) return it.script.map(ln => ({ text: ln.t, gender: it.speakers[ln.s], nth: ln.s.endsWith("2") ? 1 : 0, pause: 350 }));
      return [{ text: it.script, gender: it.voice }];
    }
    function gate(title, lines, btn) {
      stopAll();
      qnav.innerHTML = "";
      timerEl.textContent = "";
      return new Promise(res => {
        stage.innerHTML = `<div class="card" style="max-width:640px;margin:20px auto"><h2>${esc(title)}</h2>` +
          `<ul class="clean">${lines.map(l => `<li>${l}</li>`).join("")}</ul><button class="btn primary" data-go>${esc(btn || "시작")}</button></div>`;
        stage.querySelector("[data-go]").onclick = res;
      });
    }

    // ---- 읽기·듣기 모듈 -------------------------------------------------------------------------
    function scoreEntry(e, ans) {
      const { task, item } = e;
      if (task === "r_words") {
        const blanks = [...item.text.matchAll(/\[\[([A-Za-z]+)\|([A-Za-z]+)\]\]/g)];
        const list = blanks.map((b, i) => {
          const v = (ans[`${item.id}|w${i}`] || "").trim().toLowerCase();
          return { qidx: i, score: v === b[2].toLowerCase() ? 1 : 0, response: v };
        });
        const ok = list.reduce((a, r) => a + r.score, 0) / list.length;
        setResult(task, item, [{ qidx: 0, score: ok, response: list.map(r => r.response).join("|") }]);
        return [ok, 1];
      }
      const qs = task === "l_response" ? [item] : item.questions;
      const list = qs.map((q, qi) => {
        const c = ans[`${item.id}|${qi}`];
        return { qidx: qi, score: c === q.answer ? 1 : 0, response: c === undefined ? "" : String(c) };
      });
      setResult(task, item, list);
      return [list.reduce((a, r) => a + r.score, 0), list.length];
    }

    async function runModule(secKey, entries, no) {
      const sec = plan.sections[secKey];
      const listening = secKey === "L";
      const ans = {};
      let page = 0, ended = false;
      let finish;
      const done = new Promise(r => { finish = r; });
      secName.textContent = `${sec.name} · 모듈 ${no}`;
      countdown(sec.minutes_per_module * 60, "남은 시간").then(() => { if (!ended) { ended = true; stopAll(); finish(); } });

      const drawNav = () => {
        if (listening) { qnav.innerHTML = ""; return; }
        qnav.innerHTML = entries.map((e, i) => `<button type="button" class="${i === page ? "cur" : ""}" data-p="${i}">${i + 1}</button>`).join("");
        qnav.onclick = ev => { const b = ev.target.closest("[data-p]"); if (b) { page = +b.dataset.p; draw(); } };
      };
      const foot = () => {
        const last = page === entries.length - 1;
        const prev = !listening && page > 0 ? `<button class="btn" data-prev>← 이전</button>` : "<span></span>";
        const next = last ? `<button class="btn primary" data-submit>모듈 ${no} 제출</button>` : `<button class="btn primary" data-next>다음 →</button>`;
        return `<div class="quiz-foot">${prev}${next}</div>`;
      };
      async function draw() {
        TTS.stop();
        const e = entries[page];
        const { task, item } = e;
        prog.textContent = `${page + 1} / ${entries.length} · ${T.TASKS[task].name}`;
        drawNav();
        let html = "";
        if (task === "r_words") {
          let n = 0;
          html = `<div class="passage">${esc(item.text).replace(/\[\[([A-Za-z]+)\|([A-Za-z]+)\]\]/g, (_, a, b) => {
            const k = `${item.id}|w${n++}`;
            return `<span class="tw">${a}<input class="tw-in" data-k="${k}" maxlength="${b.length}" size="${Math.max(2, b.length)}" value="${esc(ans[k] || "")}" autocomplete="off" autocapitalize="off" spellcheck="false"></span>`;
          })}</div>`;
        } else if (task === "r_daily" || task === "r_academic") {
          html = `<div class="item-body split"><div class="passage-scroll"><div class="passage">${item.title ? `<span class="doc-title">${esc(item.title)}</span>` : ""}${esc(item.text)}</div></div>` +
            `<div>${item.questions.map((q, i) => mcq(q, `${item.id}|${i}`, i + 1, ans[`${item.id}|${i}`])).join("")}</div></div>`;
        } else if (task === "l_response") {
          html = `<div class="audio-box"><span class="status" data-st>듣기…</span></div><div class="card muted small" style="margin:10px 0">한 문장을 듣고 가장 알맞은 응답을 고르세요. (한 번만 재생)</div>` +
            mcq({ q: "", choices: item.choices }, `${item.id}|0`, "", ans[`${item.id}|0`]);
        } else {
          html = `<div class="audio-box"><span class="status" data-st>듣기…</span></div>` +
            `<div style="margin-top:10px">${item.questions.map((q, i) => mcq(q, `${item.id}|${i}`, i + 1, ans[`${item.id}|${i}`])).join("")}</div>`;
        }
        stage.innerHTML = `<div class="item-head"><b>${esc(T.TASKS[task].name)}</b></div>` + html + foot();
        stage.querySelectorAll(".tw-in").forEach(inp => inp.oninput = () => { ans[inp.dataset.k] = inp.value; });
        bindChoices(ans);
        const nb = stage.querySelector("[data-next]"), pb = stage.querySelector("[data-prev]"), sb = stage.querySelector("[data-submit]");
        if (nb) nb.onclick = () => { page++; draw(); };
        if (pb) pb.onclick = () => { page--; draw(); };
        if (sb) sb.onclick = () => {
          const left = entries.reduce((a, en) => a + (en.task === "r_words" ? 0 : (en.task === "l_response" ? 1 : en.item.questions.length)
            - Object.keys(ans).filter(k => k.startsWith(en.item.id + "|") && !k.includes("|w")).length), 0);
          if (left > 0 && !confirm(`답하지 않은 문항이 ${left}개 있습니다. 제출할까요?`)) return;
          if (!ended) { ended = true; stopAll(); finish(); }
        };
        if (listening) {                              // 한 번만 재생
          const st = stage.querySelector("[data-st]");
          if (!e.played) {
            e.played = true;
            if (nb) nb.disabled = true;
            if (sb) sb.disabled = true;
            await say(segsFor(task, item));
            if (st) st.textContent = "재생 끝 — 답을 고르고 다음으로";
            if (nb) nb.disabled = false;
            if (sb) sb.disabled = false;
          } else if (st) st.textContent = "재생 끝";
        }
      }
      draw();
      await done;
      let got = 0, tot = 0;
      entries.forEach(e => { const [g, t] = scoreEntry(e, ans); got += g; tot += t; });
      return tot ? got / tot : 0;
    }

    async function runAdaptive(secKey) {
      const sec = plan.sections[secKey];
      const acc = await runModule(secKey, sec.modules[0], 1);
      const route = acc >= plan.adapt_cut ? "hard" : "easy";
      routes[secKey] = route;
      await gate(`${sec.name} 모듈 2`, [`모듈 1이 끝났습니다. 모듈 2 (${sec.minutes_per_module}분)를 시작합니다.`,
        secKey === "L" ? "음성은 한 번만 재생됩니다." : "모듈 안에서는 앞뒤로 이동할 수 있습니다."], "모듈 2 시작");
      await runModule(secKey, sec.modules[1][route], 2);
    }

    // ---- 말하기 -----------------------------------------------------------------------------------
    async function runRepeat(it) {
      secName.textContent = "Speaking · 듣고 따라 말하기";
      stage.innerHTML = `<div class="card"><b data-no></b><div class="audio-box" style="margin:10px 0"><span class="status" data-st></span></div>
        <div class="small muted" data-heard></div></div>`;
      const st = stage.querySelector("[data-st]"), heardEl = stage.querySelector("[data-heard]");
      const list = [];
      for (let i = 0; i < it.sentences.length; i++) {
        const s = it.sentences[i];
        prog.textContent = `문장 ${i + 1} / 7`;
        stage.querySelector("[data-no]").textContent = `문장 ${i + 1} / ${it.sentences.length}`;
        heardEl.textContent = "";
        st.textContent = "듣기…";
        await say([{ text: s, gender: it.voice }]);
        const secs = Math.max(5, Math.ceil(s.split(/\s+/).length * 0.7) + 3);
        st.textContent = "🎤 따라 말하세요";
        const cd = countdown(secs, "말하기");
        const heard = SP.hasRecognition() ? await SP.listen(secs, heardEl, null, reg) : (await sleep(secs * 1000), null);
        clearInterval(tick);
        list.push({ qidx: i, score: heard ? SP.repeatScore(s, heard) : null, response: heard || "" });
        await sleep(500);
      }
      if (list.some(r => r.score === null)) review.push({ kind: "repeat", task: "s_repeat", item: it, list });
      else setResult("s_repeat", it, list);
    }

    async function runInterview(it) {
      secName.textContent = "Speaking · 인터뷰";
      stage.innerHTML = `<div class="card"><div class="small muted">인터뷰어</div><p>${esc(it.intro)}</p>
        <div class="audio-box"><span class="status" data-st>듣기…</span></div><div data-qs></div></div>`;
      const st = stage.querySelector("[data-st]"), qs = stage.querySelector("[data-qs]");
      const answers = [];
      await say([{ text: it.intro, gender: "female" }]);
      for (let i = 0; i < it.questions.length; i++) {
        prog.textContent = `질문 ${i + 1} / 4`;
        qs.insertAdjacentHTML("beforeend", `<div class="q" data-qi="${i}"><div class="qtext">Q${i + 1}. ${esc(it.questions[i])}</div><div class="small muted" data-heard></div></div>`);
        const box = qs.querySelector(`[data-qi="${i}"]`), heardEl = box.querySelector("[data-heard]");
        st.textContent = `질문 ${i + 1} 듣기…`;
        await say([{ text: it.questions[i], gender: "female" }]);
        st.textContent = "🎤 답하세요 (45초)";
        const rec = await SP.recorder(reg);
        rec && rec.start();
        countdown(45, "답변");
        const ctl = {};
        let timeUp;
        const heardP = SP.hasRecognition() ? SP.listen(45, heardEl, ctl, reg) : new Promise(r => { timeUp = r; setTimeout(() => r(null), 45000); });
        const skip = document.createElement("button");
        skip.className = "btn small"; skip.textContent = "답변 끝내기"; skip.setAttribute("data-skip", "");
        box.appendChild(skip);
        skip.onclick = () => { if (ctl.stop) ctl.stop(); else if (timeUp) timeUp(null); };
        const heard = await heardP;
        skip.remove();
        clearInterval(tick);
        answers.push({ q: it.questions[i], text: heard || heardEl.textContent || "", url: rec ? await rec.stop() : null });
      }
      review.push({ kind: "interview", task: "s_interview", item: it, answers });
    }

    // ---- 쓰기 ---------------------------------------------------------------------------------------
    function runSentence(it, n, total) {
      return new Promise(resolve => {
        secName.textContent = "Writing · 문장 만들기";
        prog.textContent = `${n} / ${total}`;
        const order = it.chunks.map((c, i) => i).sort(() => Math.random() - 0.5);
        const placed = [];
        stage.innerHTML = `<div class="card"><div class="small muted">상대방</div><div class="sentence">“${esc(it.context)}”</div>
          <div class="small muted">나의 대답</div><div class="ws-line" data-line></div><div class="ws-bank" data-bank></div>
          <div class="quiz-foot"><span></span><button class="btn primary" data-ok>다음 →</button></div></div>`;
        const line = stage.querySelector("[data-line]"), bank = stage.querySelector("[data-bank]");
        const draw = () => {
          line.innerHTML = placed.map((ci, k) => `<button class="chunk on" data-k="${k}">${esc(it.chunks[ci])}</button>`).join("") || `<span class="muted small">덩어리를 눌러 문장을 만드세요</span>`;
          bank.innerHTML = order.filter(ci => !placed.includes(ci)).map(ci => `<button class="chunk" data-ci="${ci}">${esc(it.chunks[ci])}</button>`).join("");
        };
        line.onclick = e => { const b = e.target.closest("[data-k]"); if (b) { placed.splice(+b.dataset.k, 1); draw(); } };
        bank.onclick = e => { const b = e.target.closest("[data-ci]"); if (b) { placed.push(+b.dataset.ci); draw(); } };
        draw();
        stage.querySelector("[data-ok]").onclick = () => {
          const mine = placed.map(ci => it.chunks[ci]).join(" ");
          setResult("w_sentence", it, [{ qidx: 0, score: (mine === it.chunks.join(" ") || (it.alts || []).includes(mine)) ? 1 : 0, response: mine }]);
          resolve();
        };
      });
    }
    async function runWrite(task, it) {
      const email = task === "w_email";
      secName.textContent = `Writing · ${T.TASKS[task].name}`;
      prog.textContent = email ? "7분" : "10분";
      const prompt = email
        ? `<div class="card"><div class="small muted">상황</div><p>${esc(it.situation)}</p><div class="small muted">받는 사람: <b>${esc(it.to)}</b> · 꼭 넣을 내용</div><ul class="clean">${it.tasks.map(t => `<li>${esc(t)}</li>`).join("")}</ul></div>`
        : `<div class="card"><div class="small muted">교수 (${esc(it.course)})</div><p>${esc(it.professor)}</p>` +
          it.students.map(s => `<div class="passage" style="margin-top:8px;font-size:.95rem"><b>${esc(s.name)}</b>\n${esc(s.post)}</div>`).join("") + `</div>`;
      stage.innerHTML = prompt + `<textarea class="wr" data-text placeholder="여기에 영어로 쓰세요" spellcheck="false"></textarea>
        <div class="spread small"><span class="muted" data-wc>0 단어</span><button class="btn primary" data-done>제출</button></div>`;
      const ta = stage.querySelector("[data-text]"), wc = stage.querySelector("[data-wc]");
      ta.focus();
      ta.oninput = () => { wc.textContent = `${(ta.value.match(/[A-Za-z']+/g) || []).length} 단어`; };
      await Promise.race([countdown(email ? 420 : 600, "남은 시간"),
        new Promise(r => { stage.querySelector("[data-done]").onclick = () => { if (confirm("제출할까요?")) r(); }; })]);
      clearInterval(tick);
      review.push({ kind: "write", task, item: it, text: ta.value });
    }

    // ---- 끝: 자기 채점 ---------------------------------------------------------------------------------
    function rubric(name) {
      return `<div class="small" style="margin-top:8px"><b>자기 채점</b>` + T.RUBRIC.map(([s, d]) =>
        `<label class="rb"><input type="radio" name="${name}" value="${s}"> <b>${s}</b> ${esc(d)}</label>`).join("") + `</div>`;
    }
    function selfReview() {
      stopAll();
      qnav.innerHTML = "";
      secName.textContent = "자기 채점";
      prog.textContent = "";
      timerEl.textContent = "";
      return new Promise(resolve => {
        const blocks = review.map((r, i) => {
          if (r.kind === "write") {
            return `<div class="card"><h2>${esc(T.TASKS[r.task].name)}</h2><div class="small muted">내 답 (${(r.text.match(/[A-Za-z']+/g) || []).length}단어)</div>
              <div class="passage" style="font-size:.95rem">${esc(r.text || "(없음)")}</div>
              <details class="reveal-d" open><summary>모범 답안</summary><div class="passage" style="font-size:.95rem">${esc(r.item.sample)}</div><div class="translation">${esc(r.item.sample_ko)}</div></details>
              ${rubric("rv" + i)}</div>`;
          }
          if (r.kind === "interview") {
            return `<div class="card"><h2>인터뷰</h2>` + r.answers.map((a, k) => `<div class="q"><div class="qtext">Q${k + 1}. ${esc(a.q)}</div>
              ${a.url ? `<audio controls src="${a.url}" style="width:100%"></audio>` : ""}<div class="small muted">${esc(a.text || "")}</div>
              <details class="reveal-d"><summary>모범 답변</summary>${esc(r.item.samples[k])}<div class="muted">${esc(r.item.samples_ko[k])}</div></details></div>`).join("") +
              rubric("rv" + i) + `</div>`;
          }
          return `<div class="card"><h2>듣고 따라 말하기</h2><p class="small muted">음성 인식이 안 된 문장이 있어 스스로 채점합니다. 7문장을 얼마나 정확히 따라 했나요?</p>
            <ol class="small">${r.item.sentences.map(s => `<li>${esc(s)}</li>`).join("")}</ol>
            <div class="row">${[["1", "거의 정확"], ["0.5", "절반 정도"], ["0", "거의 못함"]].map(([v, l]) => `<label class="rb"><input type="radio" name="rv${i}" value="${v}"> ${l}</label>`).join("")}</div></div>`;
        });
        stage.innerHTML = `<div class="card"><h2>마지막 단계: 자기 채점</h2><p class="small muted">모범 답안과 비교해 0~5점 중 가장 가까운 것을 고르세요. 다 고르면 결과를 계산합니다.</p></div>` +
          blocks.join("") + `<div class="quiz-foot"><span></span><button class="btn primary" data-fin disabled>결과 보기</button></div>`;
        const fin = stage.querySelector("[data-fin]");
        const check = () => { fin.disabled = review.some((r, i) => !stage.querySelector(`input[name="rv${i}"]:checked`)); };
        stage.querySelectorAll("input[type=radio]").forEach(x => x.onchange = check);
        check();
        fin.onclick = () => {
          review.forEach((r, i) => {
            const v = +stage.querySelector(`input[name="rv${i}"]:checked`).value;
            if (r.kind === "repeat") setResult(r.task, r.item, r.list.map(x => ({ ...x, score: x.score === null ? v : x.score })));
            else setResult(r.task, r.item, [{ qidx: 0, score: v / 5, response: r.kind === "write" ? r.text : r.answers.map(a => a.text).join("\n---\n") }]);
          });
          resolve();
        };
      });
    }

    // ---- 진행 -------------------------------------------------------------------------------------------
    async function run() {
      loadSaved();
      for (const key of plan.order) {
        if (doneSecs.includes(key)) continue;
        const sec = plan.sections[key];
        if (key === "R") {
          await gate("Reading", ["모듈 2개, 모듈마다 15분. 모듈 안에서는 문제 번호로 앞뒤 이동이 됩니다.", "1모듈 결과에 따라 2모듈 난이도가 달라집니다."], "Reading 시작");
          await runAdaptive("R");
        } else if (key === "L") {
          await gate("Listening", ["모듈 2개, 모듈마다 14분.", "음성은 <b>한 번만</b> 재생되고, 지난 문제로 돌아갈 수 없습니다."], "Listening 시작");
          await runAdaptive("L");
        } else if (key === "S") {
          await gate("Speaking", ["듣고 따라 말하기 7문장, 인터뷰 4문항(45초씩).", "마이크 권한을 허용하세요. 인식이 안 되면 끝에서 스스로 채점합니다."], "Speaking 시작");
          for (const e of sec.items) {
            if (e.task === "s_repeat") await runRepeat(e.item); else await runInterview(e.item);
          }
        } else if (key === "W") {
          await gate("Writing", ["문장 만들기 10문항 → 이메일(7분) → 학술 토론(10분)."], "Writing 시작");
          const sents = sec.items.filter(e => e.task === "w_sentence");
          for (let i = 0; i < sents.length; i++) await runSentence(sents[i].item, i + 1, sents.length);
          for (const e of sec.items.filter(e => e.task !== "w_sentence")) await runWrite(e.task, e.item);
        }
        doneSecs.push(key);
        persist();
      }
      if (review.length) await selfReview();
      await submit();
    }

    async function submit() {
      stage.innerHTML = `<div class="empty">결과 계산 중…</div>`;
      try {
        T.finishMock(id, { items: Object.values(results), routes, duration_sec: Math.round((Date.now() - t0) / 1000) });
        window.onbeforeunload = null;
        try { localStorage.removeItem(KEY); } catch (e) { /* 무시 */ }
        UI.go(`toefl-mock-result.html?id=${id}`);
      } catch (e) {
        stage.innerHTML = `<div class="flash error">제출 실패: ${esc(e.message)} <button class="btn small" id="retry">다시 제출</button></div>`;
        $("retry").onclick = submit;             // 결과만 다시 보낸다
      }
    }

    window.onbeforeunload = () => "모의고사를 그만둘까요? 끝낸 영역은 저장되지만, 지금 보는 영역은 처음부터 다시 봐야 합니다.";
    $("start-btn").addEventListener("click", () => {
      $("intro").classList.add("hidden");
      $("quiz-main").classList.remove("hidden");
      TTS.load().then(run);
    });
  });
})();
