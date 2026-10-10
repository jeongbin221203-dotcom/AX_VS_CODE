/* 받아쓰기: 듣기 문제의 문장을 듣고 입력 → 단어 단위로 비교(LCS)해 빠진 단어·틀린 단어를 표시 (TS 앱 dictation.html + dictation.js) */
UI.boot({ exam: "toeic", page: "dictation" }, async () => {
  const { esc, $ } = UI;
  const part = UI.intParam("part") ?? 2;
  const level = UI.intParam("level");
  await Bank.load([part]);

  const splitSentences = text => text.split(/(?<=[.!?])\s+/);
  let lines = [];
  for (const it of Bank.itemsOf(part)) {
    if (level && it.level !== level) continue;
    if (part === 1) lines = lines.concat(it.statements.map(t => ({ text: t, voice: null, src: it.id })));
    else if (part === 2) { lines.push({ text: it.question, voice: null, src: it.id }); lines = lines.concat(it.choices.map(c => ({ text: c, voice: null, src: it.id }))); }
    else if (part === 3) lines = lines.concat(it.script.map(l => ({ text: l.t, voice: it.speakers[l.s], src: it.id })));
    else if (part === 4) lines = lines.concat(splitSentences(it.script).map(x => x.trim()).filter(x => x.split(/\s+/).length >= 4).map(t => ({ text: t, voice: it.voice, src: it.id })));
  }
  lines = lines.filter(l => { const n = l.text.split(/\s+/).filter(Boolean).length; return n >= 3 && n <= 30; });
  lines = TSU.shuffle(lines).slice(0, 20);
  const tts = UI.ttsRaw();

  const partOpts = [1, 2, 3, 4].map(p => `<option value="${p}" ${part === p ? "selected" : ""}>Part ${p} ${esc(Score.PART_INFO[p].name)}</option>`).join("");
  const levelOpts = Score.GRADES.map(g => `<option value="${g.level}" ${level === g.level ? "selected" : ""}>${g.name}</option>`).join("");
  $("app").innerHTML = `<div class="page-head"><div><h1>받아쓰기</h1>
      <div class="muted small">듣기 문제의 문장을 듣고 그대로 적습니다. 들리지 않은 단어가 빨간색으로 표시됩니다 (대소문자·문장부호는 무시).</div></div>
      <form class="row" id="dt-filter"><select name="part" aria-label="파트">${partOpts}</select>
        <select name="level" aria-label="등급"><option value="">모든 등급</option>${levelOpts}</select>
        <a class="btn" href="${UI.url("dictation.html", { part, level })}">새 문장</a></form></div>
    ${lines.length ? `<div class="card" style="max-width:760px;margin:0 auto" id="dt">
      <div class="spread small muted"><span id="dt-prog"></span><span id="dt-score"></span></div>
      <div class="audio-box" style="margin:12px 0"><button class="btn primary" id="dt-play">▶ 듣기 <span class="kbd">Ctrl+Space</span></button>
        <button class="btn" id="dt-slow">느리게</button><span class="status" id="dt-status"></span></div>
      <textarea id="dt-input" placeholder="들은 문장을 적으세요. Enter = 확인" autocomplete="off" spellcheck="false"></textarea>
      <div class="explain hidden diff" id="dt-result"></div>
      <div class="quiz-foot"><button class="btn" id="dt-check">확인 <span class="kbd">Enter</span></button>
        <button class="btn primary hidden" id="dt-next">다음 문장 <span class="kbd">Enter</span></button></div></div>
      <div class="card empty hidden" id="dt-done" style="max-width:760px;margin:0 auto"><h2 id="dt-final"></h2>
        <a class="btn primary" href="${UI.url("dictation.html", { part, level })}">새 문장으로 다시</a></div>`
      : `<div class="card empty">이 조건의 문장이 없습니다.</div>`}`;
  $("dt-filter").addEventListener("change", () => {
    const f = $("dt-filter");
    location.href = UI.url("dictation.html", { part: f.elements.part.value, level: f.elements.level.value });
  });
  if (!lines.length) return;

  let i = 0, checked = false, totalWords = 0, gotWords = 0;
  const norm = w => w.toLowerCase().replace(/[’']/g, "'").replace(/[^a-z0-9'$%]/g, "");
  const words = s => s.split(/\s+/).map(w => ({ raw: w, n: norm(w) })).filter(w => w.n);

  /* 정답 단어열(a)과 입력(b)의 최장 공통 부분열로 정렬 */
  function diff(a, b) {
    const m = a.length, n = b.length;
    const dp = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
    for (let x = m - 1; x >= 0; x--) for (let y = n - 1; y >= 0; y--)
      dp[x][y] = a[x].n === b[y].n ? dp[x + 1][y + 1] + 1 : Math.max(dp[x + 1][y], dp[x][y + 1]);
    const out = [];
    let x = 0, y = 0;
    while (x < m || y < n) {
      if (x < m && y < n && a[x].n === b[y].n) { out.push({ t: "ok", w: a[x].raw }); x++; y++; }
      else if (y < n && (x >= m || dp[x][y + 1] >= dp[x + 1][y])) { out.push({ t: "extra", w: b[y].raw }); y++; }
      else { out.push({ t: "miss", w: a[x].raw }); x++; }
    }
    return { parts: out, match: dp[0][0] };
  }

  function play(rate) {
    const l = lines[i];
    $("dt-status").textContent = "재생 중…";
    TTS.play([{ text: l.text, gender: l.voice || (i % 2 ? "male" : "female") }], { rate: rate || tts.rate, accent: TTS.accentFor(tts.accent, l.src + i) })
      .then(() => { $("dt-status").textContent = ""; });
    $("dt-input").focus();
  }
  function show() {
    if (i >= lines.length) {
      $("dt").classList.add("hidden");
      $("dt-done").classList.remove("hidden");
      $("dt-final").textContent = `${lines.length}문장 · 단어 ${gotWords}/${totalWords} (${TSU.pyRound(gotWords / Math.max(totalWords, 1) * 100)}%)`;
      return;
    }
    checked = false;
    $("dt-prog").textContent = `${i + 1} / ${lines.length}`;
    $("dt-input").value = "";
    $("dt-input").disabled = false;
    $("dt-result").classList.add("hidden");
    $("dt-check").classList.remove("hidden");
    $("dt-next").classList.add("hidden");
    $("dt-input").focus();
  }
  function check() {
    if (checked) return;
    checked = true;
    const a = words(lines[i].text), b = words($("dt-input").value);
    const r = diff(a, b);
    totalWords += a.length;
    gotWords += r.match;
    $("dt-score").textContent = `누적 단어 정확도 ${TSU.pyRound(gotWords / totalWords * 100)}%`;
    $("dt-result").innerHTML = `<div><b>${r.match}/${a.length}</b> 단어</div><div style="margin:6px 0">` +
      r.parts.map(p => `<span class="${p.t}">${esc(p.w)}</span>`).join(" ") +
      `</div><div class="muted small">정답: ${esc(lines[i].text)}</div>`;
    $("dt-result").classList.remove("hidden");
    $("dt-input").disabled = true;
    $("dt-check").classList.add("hidden");
    $("dt-next").classList.remove("hidden");
    $("dt-next").focus();
  }
  $("dt-play").addEventListener("click", () => play());
  $("dt-slow").addEventListener("click", () => play(Math.max(0.6, tts.rate * 0.75)));
  $("dt-check").addEventListener("click", check);
  $("dt-next").addEventListener("click", () => { i++; show(); });
  document.addEventListener("keydown", e => {
    if (e.key === " " && e.ctrlKey) { e.preventDefault(); play(); }
    else if (e.key === "Enter" && !e.shiftKey && e.target.closest && !e.target.closest("select, a, #dt-filter")) {
      e.preventDefault();
      if (!checked) check(); else { i++; show(); }
    }
  });
  TTS.load();
  show();
});
