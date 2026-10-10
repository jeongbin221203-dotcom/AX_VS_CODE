/* 풀이 화면 (solve.html?sid=번호) — 세션을 읽어 Engine 에 넘긴다 (TS 앱 quiz.html + views/quiz.quiz) */
(function () {
  const sid = UI.intParam("sid");
  const sess = sid !== null ? TSStore.getSession(sid) : null;
  UI.boot({ exam: "toeic", page: "solve:" + (sess ? sess.mode : "practice") }, async () => {
    const { esc, $ } = UI;
    const s = sid !== null ? Sessions.getSession(sid) : null;
    if (!s) { $("app").innerHTML = `<div class="card empty"><h2>풀이를 찾을 수 없습니다</h2><p>지워졌거나 다른 브라우저의 기록일 수 있습니다.</p><a class="btn primary" href="toeic.html">오늘 화면으로</a></div>`; return; }
    if (s.finished_at) return UI.go(`result.html?sid=${sid}`);
    await Bank.load([...new Set(s.items.map(r => Bank.splitRef(r)[0]))]);
    const items = s.items.filter(r => Bank.item(r)).map(r => Bank.publicItem(r));
    const graded = {};
    if (s.mode === "practice" || s.mode === "review") {             // 이어 풀기: 이미 채점한 문제는 결과를 함께 보낸다
      const by = {};
      for (const a of Sessions.sessionAttempts(sid)) (by[`${TSStore.partOf(a)}:${TSStore.itemIdOf(a)}`] = by[`${TSStore.partOf(a)}:${TSStore.itemIdOf(a)}`] || [])
        .push({ qidx: TSStore.qidxOf(a), chosen: a.c, correct: !!a.o });
      for (const [ref, res] of Object.entries(by)) graded[ref] = { ...Bank.reveal(ref), results: res };
    }
    const real = s.mode === "mock" && !!(Score.MOCK_FORMS[s.variant || ""] || {}).real;
    const P = { sid, mode: s.mode, items, graded, real, time_limit: s.time_limit, created_at: s.created_at,
                target_sec: window.TS_GUIDE.TARGET_SEC, lc_parts: Score.LC_PARTS, tts: UI.ttsRaw() };
    const exam = s.mode === "mock" || s.mode === "diagnostic";
    const title = Sessions.sessionTitle(s);
    document.title = `${title} · TS 단어`;
    const rules = real ? `
      <li><b>실제 시험과 같은 진행입니다.</b> LC는 음성에 맞춰 자동으로 넘어가며, 다시 듣기·되돌아가기·일시정지가 없습니다.</li>
      <li>답할 시간: Part 1·2 5초, Part 3·4는 문제를 읽어 준 뒤 8초(표 문제 12초). 음성이 나오는 동안 미리 문제를 읽어 두세요.</li>
      <li>LC가 끝나면 RC 75분이 자동으로 시작됩니다. RC 안에서는 문제를 자유롭게 오갈 수 있고, 시간이 끝나면 자동 제출합니다.</li>
      <li>새로고침하거나 창을 닫아도 답안은 이 브라우저에 남아 이어서 볼 수 있습니다.</li>`
      : exam ? `
      <li>끝까지 풀고 <b>제출</b>하면 채점과 추정 점수가 나옵니다. 중간에 정답은 보여 주지 않습니다.</li>
      <li>듣기(LC)는 문제마다 <b>한 번만</b> 재생됩니다. 화면에 들어가면 자동으로 시작합니다.</li>
      <li>RC 제한 시간은 RC 문제를 보고 있는 동안만 줄어듭니다. 시간이 끝나면 자동 제출합니다.</li>`
      : `
      <li>답을 고르면 바로 채점하고 해설을 보여 줍니다 (세트 문제는 모두 고른 뒤 <b>채점하기</b>).</li>
      <li>틀린 문항은 오답노트에 자동으로 들어갑니다. 복습에서 서로 다른 날 2번 맞히면 빠집니다.</li>
      <li>듣기 문제는 다시 듣기(R)와 채점 뒤 스크립트·해석 보기가 가능합니다.</li>`;
    $("app").innerHTML = `
      <div id="intro" class="card" style="max-width:640px;margin:30px auto">
        <h1>${esc(title)}</h1>
        <p class="muted">문제 ${items.length}개 묶음${s.time_limit ? ` · RC 제한 시간 ${Math.floor(s.time_limit / 60)}분` : ""}</p>
        <ul class="clean small">${rules}
          <li>단축키: <span class="kbd">1~4</span>/<span class="kbd">A~D</span> 선택 · <span class="kbd">Enter</span> 채점·다음 ·
            <span class="kbd">←</span><span class="kbd">→</span> 이동 · <span class="kbd">↑</span><span class="kbd">↓</span> 세트 안 문항 이동 · <span class="kbd">R</span> 다시 듣기</li></ul>
        <p class="small muted">듣기 음성은 브라우저 음성 합성으로 만듭니다. 크롬·엣지에서 가장 자연스럽습니다. 음성 속도·억양은 설정에서 바꿀 수 있습니다.</p>
        <button class="btn primary" id="start-btn">시작하기</button></div>
      <div id="quiz-main" class="hidden">
        <div class="quiz-top"><b>${esc(title)}</b><span class="progress muted small" id="progress"></span><span class="timer" id="timer"></span>
          ${exam ? `<button class="btn small primary" id="submit-btn">제출하기</button>` : `<button class="btn small" id="finish-btn">그만하고 결과 보기</button>`}</div>
        <div class="qnav" id="qnav"></div><div id="stage"></div></div>`;
    Engine.run(P, {
      grade: (ref, list) => Sessions.gradeItem(sid, ref, list),
      submit: body => { Sessions.submitSession(sid, body); return `result.html?sid=${sid}`; },
      draft: { get: () => TSStore.getDraft(sid), set: o => TSStore.setDraft(sid, o), clear: () => TSStore.clearDraft(sid) },
    });
  });
})();
