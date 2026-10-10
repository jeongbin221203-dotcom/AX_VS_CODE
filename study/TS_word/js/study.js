/* 단어 카드 학습: 앞면(단어) → 뒷면(뜻·예문) → 4단계 평가. '다시'는 이번 회차 끝에 한 번 더. */
UI.boot("study", () => {
  const { esc, $ } = UI;
  const level = UI.intParam("level");
  const tier = ["core", "stretch"].includes(UI.params().get("tier")) ? UI.params().get("tier") : null;
  const starredOnly = UI.params().get("starred") === "1";
  const grades = Ward.grades();

  // 오늘 볼 카드 (복습 먼저, 새 단어는 하루 한도까지). 점수가 없으니 1등급부터.
  const qu = Ward.queue({ level, tier, starredOnly });
  const queue = [...qu.due.map(w => ({ ...w, is_new: false })), ...qu.new.map(w => ({ ...w, is_new: true }))];
  const total = queue.length;
  let done = 0, flipped = false, cur = null;
  const again = new Set();

  const base = { tier };
  const levelSeg = UI.seg([["", "전체"], ...grades.map(g => [g.level, g.name])], level ?? "", v => UI.url("study.html", { ...base, level: v }));
  const tierSeg = UI.seg([["", "필수+도전"], ["core", "필수"], ["stretch", "도전"]], tier ?? "", v => UI.url("study.html", { level, tier: v }));

  $("app").innerHTML = `
    <div class="page-head">
      <div><h1>단어 카드 ${level ? UI.gradeBadge(level) : ""}${tier ? " " + Ward.TIERS[tier] : ""}${starredOnly ? " ★ 별표" : ""}</h1>
        <div class="muted small" id="vs-progress"></div></div>
      ${levelSeg}${tierSeg}
    </div>
    ${total ? `
    <div class="card flash-card" id="card">
      <div class="spread small muted"><span><span id="vs-kind"></span> <span class="tag ok hidden" id="vs-tier">도전</span></span><button class="star" id="vs-star" title="별표">★</button></div>
      <div class="word" id="vs-word"></div>
      <div class="pos" id="vs-pos"></div>
      <button class="btn small ghost" id="vs-say">🔊 발음 <span class="kbd">S</span></button>
      <div id="vs-back" class="hidden">
        <div class="meaning" id="vs-meaning"></div>
        <div class="ex" id="vs-ex"></div>
        <div class="muted small" id="vs-exko"></div>
        <div class="tip" id="vs-tip"></div>
      </div>
      <div style="margin-top:18px"><button class="btn primary" id="vs-flip">뜻 보기 <span class="kbd">Space</span></button></div>
    </div>
    <div class="grade-btns hidden" id="vs-grades">
      <button class="btn" data-g="0">다시<small>모름 · 1</small></button>
      <button class="btn" data-g="3">어려움<small>겨우 떠올림 · 2</small></button>
      <button class="btn" data-g="4">보통<small>알았음 · 3</small></button>
      <button class="btn" data-g="5">쉬움<small>바로 앎 · 4</small></button>
    </div>
    <div class="card empty hidden" id="vs-done">
      <h2>오늘 카드 끝!</h2><p>내일 복습할 카드가 준비됩니다.</p>
      <a class="btn" href="index.html">단어 홈</a> <a class="btn primary" href="${UI.url("quiz.html", { level })}">뜻 고르기 시험</a>
    </div>` : `
    <div class="card empty"><h2>지금 볼 카드가 없습니다</h2>
      <p>${starredOnly ? "별표한 단어가 없습니다. 카드·단어장에서 ★를 누르면 모입니다." : "오늘 복습은 끝났고, 새 단어 하루 한도도 채웠습니다."}</p>
      <a class="btn" href="settings.html">하루 새 단어 수 바꾸기</a> <a class="btn primary" href="${UI.url("quiz.html", { level })}">뜻 고르기 시험</a></div>`}`;
  if (!total) return;

  const say = () => { if (cur) UI.sayWord(cur.word); };

  function show() {
    cur = queue[0];
    if (!cur) {
      $("card").classList.add("hidden");
      $("vs-grades").classList.add("hidden");
      $("vs-done").classList.remove("hidden");
      $("vs-progress").textContent = `${total}장 완료`;
      return;
    }
    flipped = false;
    $("vs-kind").textContent = again.has(cur.id) ? "다시 보기" : cur.is_new ? "새 단어" : "복습";
    $("vs-word").textContent = cur.word;
    $("vs-pos").textContent = cur.pos;
    $("vs-tier").classList.toggle("hidden", cur.tier !== "stretch");
    $("vs-meaning").textContent = cur.meaning;
    $("vs-ex").textContent = cur.example;
    $("vs-exko").textContent = cur.example_ko;
    $("vs-tip").textContent = cur.tip;
    $("vs-tip").classList.toggle("hidden", !cur.tip);
    $("vs-star").classList.toggle("on", !!(Ward.cardOf(cur.id) || {}).starred);
    $("vs-back").classList.add("hidden");
    $("vs-flip").classList.remove("hidden");
    $("vs-grades").classList.add("hidden");
    $("vs-progress").textContent = `${done} / ${total} · 남은 카드 ${queue.length}`;
  }

  function flip() {
    if (!cur || flipped) return;
    flipped = true;
    $("vs-back").classList.remove("hidden");
    $("vs-flip").classList.add("hidden");
    $("vs-grades").classList.remove("hidden");
    say();
  }

  function rate(g) {
    if (!cur || !flipped) return;
    // 이번 회차에서 '다시'로 되돌아온 카드는 기록을 한 번만 남긴다
    if (!again.has(cur.id) || g === 0) Ward.review(cur.id, g);
    queue.shift();
    if (g === 0) { again.add(cur.id); queue.push(cur); }
    else done++;
    show();
  }

  $("vs-flip").addEventListener("click", flip);
  $("vs-say").addEventListener("click", say);
  $("vs-star").addEventListener("click", () => { if (cur) $("vs-star").classList.toggle("on", Ward.toggleStar(cur.id)); });
  $("vs-grades").addEventListener("click", e => { const b = e.target.closest("[data-g]"); if (b) rate(Number(b.dataset.g)); });
  document.addEventListener("keydown", e => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === " " || e.key === "Enter") { e.preventDefault(); flip(); }
    else if (e.key.toLowerCase() === "s") say();
    else if (flipped && ["1", "2", "3", "4"].includes(e.key)) rate([0, 3, 4, 5][Number(e.key) - 1]);
  });
  TTS.load();
  show();
});
