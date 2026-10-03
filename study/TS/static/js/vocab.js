/* 단어 카드 학습: 앞면(단어) → 뒷면(뜻·예문) → 4단계 평가. '다시'는 이번 회차 끝에 한 번 더. */
(function () {
  "use strict";
  const D = TS.data("vs-data");
  if (!D) return;
  const $ = id => document.getElementById(id);
  const queue = D.cards.slice();
  const total = queue.length;
  let done = 0, flipped = false, cur = null, busy = false;
  const again = new Set();

  function say() {
    if (cur) TTS.play([{ text: cur.word.replace(/~/g, ""), gender: "female" }], { rate: D.tts.rate, accent: D.tts.accent === "mix" ? "us" : D.tts.accent });
  }

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
    $("vs-star").classList.toggle("on", !!cur.starred);
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

  async function rate(g) {
    if (!cur || !flipped || busy) return;
    busy = true;
    try {
      // 이번 회차에서 '다시'로 되돌아온 카드는 기록을 한 번만 남긴다
      if (!again.has(cur.id) || g === 0) await TS.post(TS.vbase + "/api/vocab/review", { word_id: cur.id, grade: g });
      queue.shift();
      if (g === 0) { again.add(cur.id); queue.push(cur); }
      else done++;
      show();
    } catch (e) { alert(e.message); }
    busy = false;
  }

  $("vs-flip").addEventListener("click", flip);
  $("vs-say").addEventListener("click", say);
  $("vs-star").addEventListener("click", async () => {
    if (!cur) return;
    const r = await TS.post(TS.vbase + "/api/vocab/star", { word_id: cur.id });
    cur.starred = r.starred;
    $("vs-star").classList.toggle("on", r.starred);
  });
  $("vs-grades").addEventListener("click", e => {
    const b = e.target.closest("[data-g]");
    if (b) rate(Number(b.dataset.g));
  });
  document.addEventListener("keydown", e => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === " " || e.key === "Enter") { e.preventDefault(); flip(); }
    else if (e.key.toLowerCase() === "s") say();
    else if (flipped && ["1", "2", "3", "4"].includes(e.key)) rate([0, 3, 4, 5][Number(e.key) - 1]);
  });
  TTS.load();
  show();
})();
