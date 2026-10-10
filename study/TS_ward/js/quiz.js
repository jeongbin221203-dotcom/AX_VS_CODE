/* 뜻 고르기 시험: 문제마다 결과 저장(틀리면 복습 카드로), 끝나면 틀린 단어만 다시 풀기 */
UI.boot("quiz", () => {
  const { esc, $ } = UI;
  const level = UI.intParam("level");
  const P = UI.params();
  const tier = ["core", "stretch"].includes(P.get("tier")) ? P.get("tier") : null;
  const SETS = { all: "전체", weak: "자주 잊는 단어", missed: "최근 틀린 단어", starred: "★ 별표", learning: "학습 중" };
  const which = SETS[P.get("set")] ? P.get("set") : "all";
  const SIZES = [15, 30, 50];
  const n = SIZES.includes(parseInt(P.get("n"), 10)) ? parseInt(P.get("n"), 10) : 15;
  const grades = Ward.grades();
  const L = ["A", "B", "C", "D"];

  const failMap = Ward.failCounts();
  const missedSet = Ward.recentlyMissed();
  function pool(set) {
    const ws = Ward.words().filter(w => (!level || w.level === level) && (!tier || w.tier === tier));
    if (set === "all") return ws;
    if (set === "weak") return ws.filter(w => (failMap[w.id] || 0) >= Ward.WEAK_MIN_FAILS);
    if (set === "missed") return ws.filter(w => missedSet.has(w.id));
    if (set === "starred") return ws.filter(w => (Ward.cardOf(w.id) || {}).starred);
    if (set === "learning") return ws.filter(w => Ward.stateOf(Ward.cardOf(w.id)) === "learning");
    return ws;
  }
  const counts = Object.fromEntries(Object.keys(SETS).map(k => [k, pool(k).length]));

  // 문제 만들기: 오답 보기는 같은 등급·같은 품사의 뜻에서, 정답·서로와 뜻이 같은 것은 제외
  function build() {
    const p = pool(which);
    let picks;
    if (which === "weak" || which === "missed") {
      picks = UI.shuffle(p).sort((a, b) => (failMap[b.id] || 0) - (failMap[a.id] || 0)).slice(0, n);
      picks = UI.shuffle(picks);
    } else picks = UI.shuffle(p).slice(0, n);
    const levels = new Set(picks.map(w => w.level));
    const dpool = Ward.words().filter(w => levels.has(w.level));
    const base = dpool.length ? dpool : Ward.words();
    return picks.map(w => {
      const same = base.filter(x => x.id !== w.id && x.pos === w.pos && x.meaning !== w.meaning);
      const others = same.length >= 3 ? same : base.filter(x => x.id !== w.id);
      const distract = [], used = new Set([w.meaning]);
      for (const x of UI.shuffle(others)) {
        if (!used.has(x.meaning)) { distract.push(x); used.add(x.meaning); if (distract.length === 3) break; }
      }
      const opts = UI.shuffle([w, ...distract]);
      return { id: w.id, word: w.word, pos: w.pos, tier: w.tier, example: w.example, example_ko: w.example_ko, tip: w.tip,
               meaning: w.meaning, options: opts.map(o => o.meaning), answer: opts.findIndex(o => o.id === w.id) };
    });
  }

  const sel = (name, items, cur) => `<select data-q="${name}" aria-label="${name}">${items.map(([v, l]) => `<option value="${v}" ${String(cur ?? "") === String(v) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
  $("app").innerHTML = `
    <div class="page-head">
      <div><h1>단어 시험 ${level ? UI.gradeBadge(level) : ""}${tier ? ` <span class="tag ok">${Ward.TIERS[tier]}</span>` : ""}</h1>
        <div class="muted small">뜻 고르기 · 틀린 단어는 바로 복습 카드에 들어가 내일 다시 나옵니다. 2번 이상 틀리면 '자주 잊는 단어'로 모입니다.</div></div>
      <div class="row" id="filters">
        ${sel("level", [["", "모든 등급"], ...grades.map(g => [g.level, g.name])], level)}
        ${sel("tier", [["", "필수 + 도전"], ["core", "필수 단어"], ["stretch", "도전 단어"]], tier)}
        ${sel("set", Object.entries(SETS).map(([k, v]) => [k, `${v} (${counts[k]})`]), which)}
        ${sel("n", SIZES.map(s => [s, s + "문제"]), n)}
      </div>
    </div>
    <div id="body"></div>`;
  $("filters").addEventListener("change", () => {
    const q = {};
    $("filters").querySelectorAll("select").forEach(s => { q[s.dataset.q] = s.value; });
    location.href = UI.url("quiz.html", q);
  });

  const ALL = build();
  if (!ALL.length) {
    const msg = { weak: "아직 2번 이상 틀린 단어가 없습니다. 시험이나 카드에서 틀린 단어가 쌓이면 여기에 모입니다.", missed: "최근에 틀린 단어가 없습니다.",
                  starred: "별표한 단어가 없습니다. 카드·단어장·듣기에서 ★를 누르면 모입니다.", learning: "학습 중인 단어가 없습니다. 단어 카드로 먼저 공부해 보세요." }[which] || "조건에 맞는 단어가 없습니다.";
    $("body").innerHTML = `<div class="card empty">${msg}<div style="margin-top:12px"><a class="btn primary" href="${UI.url("quiz.html", { level, tier })}">전체 단어로 시험</a></div></div>`;
    return;
  }

  $("body").innerHTML = `
    <div class="card" style="max-width:680px;margin:0 auto" id="vq">
      <div class="spread small muted"><span id="vq-prog"></span><span id="vq-score"></span></div>
      <div class="flash-card" style="min-height:0;padding:16px 0">
        <div class="word" id="vq-word"></div>
        <div class="pos"><span id="vq-pos"></span> <span class="tag ok hidden" id="vq-tier">도전</span> <span class="tag hidden" id="vq-round">다시 풀기</span></div>
      </div>
      <div class="choices" id="vq-choices"></div>
      <div class="explain hidden" id="vq-explain"></div>
      <div class="quiz-foot"><span></span><button class="btn primary hidden" id="vq-next">다음 <span class="kbd">Enter</span></button></div>
    </div>
    <div class="card hidden" id="vq-done" style="max-width:680px;margin:0 auto">
      <h2 id="vq-final"></h2><p class="small" id="vq-note"></p><div id="vq-missed" class="small"></div>
      <div class="row" style="margin-top:14px">
        <button class="btn primary hidden" id="vq-retry">틀린 단어만 다시 풀기</button>
        <a class="btn" href="study.html">복습 카드로</a>
        <a class="btn" href="${location.search ? location.search : "quiz.html"}">새 문제로 다시</a>
        <a class="btn" href="${UI.url("quiz.html", { level, tier, set: "weak", n })}">자주 잊는 단어 시험</a>
      </div>
    </div>`;

  let Q = ALL, i = 0, score = 0, answered = false, round = 1, scheduled = 0, missed = [];

  function show() {
    const q = Q[i];
    if (!q) return end();
    answered = false;
    $("vq-prog").textContent = `${i + 1} / ${Q.length}`;
    $("vq-score").textContent = `맞힘 ${score}`;
    $("vq-word").textContent = q.word;
    $("vq-pos").textContent = q.pos;
    $("vq-tier").classList.toggle("hidden", q.tier !== "stretch");
    $("vq-round").classList.toggle("hidden", round === 1);
    $("vq-choices").classList.remove("locked");
    $("vq-choices").innerHTML = q.options.map((o, k) => `<button class="choice" data-k="${k}"><span class="letter">${L[k]}</span><span>${esc(o)}</span></button>`).join("");
    $("vq-explain").classList.add("hidden");
    $("vq-next").classList.add("hidden");
    UI.sayWord(q.word);
  }

  function pick(k) {
    if (answered) return;
    answered = true;
    const q = Q[i];
    const ok = k === q.answer;
    if (ok) score++; else missed.push(q);
    if (round === 1) { const r = Ward.quizAnswer(q.id, ok); if (r.scheduled) scheduled++; }   // 첫 회차만 기록 (다시 풀기는 연습이라 두 번 세지 않음)
    $("vq-choices").classList.add("locked");
    [...$("vq-choices").children].forEach((b, m) => { if (m === q.answer) b.classList.add("right"); else if (m === k) b.classList.add("wrong"); });
    $("vq-explain").innerHTML = `<b>${ok ? "정답" : "오답"}</b> · ${esc(q.word)} = ${esc(q.meaning)}<div>${esc(q.example)}</div><div class="muted">${esc(q.example_ko)}</div>${q.tip ? `<div class="muted">${esc(q.tip)}</div>` : ""}`;
    $("vq-explain").classList.remove("hidden");
    $("vq-next").classList.remove("hidden");
  }

  function end() {
    $("vq").classList.add("hidden");
    $("vq-done").classList.remove("hidden");
    $("vq-final").textContent = `${Q.length}문제 중 ${score}개 맞힘 (${Math.round(score / Q.length * 100)}%)`;
    $("vq-note").textContent = round === 1
      ? (scheduled ? `틀린 ${scheduled}개는 복습 카드에 들어가 내일 다시 나옵니다.` : "모두 맞혔어요!")
      : "다시 풀기는 기록에 세지 않습니다.";
    $("vq-missed").innerHTML = missed.length
      ? "<b>틀린 단어</b><ul class='clean'>" + missed.map(q => `<li>${esc(q.word)} — ${esc(q.meaning)}</li>`).join("") + "</ul>" : "";
    $("vq-retry").classList.toggle("hidden", !missed.length);
  }

  $("vq-choices").addEventListener("click", e => { const b = e.target.closest("[data-k]"); if (b) pick(Number(b.dataset.k)); });
  $("vq-next").addEventListener("click", () => { i++; show(); });
  $("vq-retry").addEventListener("click", () => {
    Q = UI.shuffle(missed); missed = []; i = 0; score = 0; round++;
    $("vq-done").classList.add("hidden"); $("vq").classList.remove("hidden");
    show();
  });
  document.addEventListener("keydown", e => {
    if (e.ctrlKey || e.metaKey || e.altKey || $("vq").classList.contains("hidden")) return;
    if (e.key === "Enter" && answered) { i++; show(); }
    else if (!answered && ["1", "2", "3", "4"].includes(e.key)) pick(Number(e.key) - 1);
  });
  TTS.load();
  show();
});
