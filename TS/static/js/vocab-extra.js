/* 단어 목록(발음·별표)과 뜻 고르기 퀴즈 */
(function () {
  "use strict";
  const tts = TS.data("tts-data") || { rate: 1, accent: "us" };
  const accent = tts.accent === "mix" ? "us" : tts.accent;
  const say = w => TTS.play([{ text: w.replace(/~/g, ""), gender: "female" }], { rate: tts.rate, accent });

  // ---- 목록
  document.addEventListener("click", async e => {
    const s = e.target.closest("[data-say]");
    if (s) return say(s.dataset.say);
    const st = e.target.closest("[data-star]");
    if (st) {
      const r = await TS.post("/api/vocab/star", { word_id: st.dataset.star });
      st.classList.toggle("on", r.starred);
    }
  });

  // ---- 퀴즈
  const Q = TS.data("vq-data");
  if (!Q) return;
  const $ = id => document.getElementById(id);
  const L = ["A", "B", "C", "D"];
  let i = 0, score = 0, answered = false;
  const missed = [];

  function show() {
    const q = Q[i];
    if (!q) return end();
    answered = false;
    $("vq-prog").textContent = `${i + 1} / ${Q.length}`;
    $("vq-score").textContent = `맞힘 ${score}`;
    $("vq-word").textContent = q.word;
    $("vq-pos").textContent = q.pos;
    $("vq-choices").classList.remove("locked");
    $("vq-choices").innerHTML = q.options.map((o, k) =>
      `<button class="choice" data-k="${k}"><span class="letter">${L[k]}</span><span>${TS.esc(o)}</span></button>`).join("");
    $("vq-explain").classList.add("hidden");
    $("vq-next").classList.add("hidden");
    say(q.word);
  }

  function pick(k) {
    if (answered) return;
    answered = true;
    const q = Q[i];
    const ok = k === q.answer;
    if (ok) score++; else missed.push(q);
    $("vq-choices").classList.add("locked");
    [...$("vq-choices").children].forEach((b, n) => {
      if (n === q.answer) b.classList.add("right");
      else if (n === k) b.classList.add("wrong");
    });
    $("vq-explain").innerHTML = `<span class="verdict ${ok ? "ok" : "bad"}">${ok ? "정답" : "오답"}</span>${TS.esc(q.example)}` +
      (q.tip ? `<div class="muted">${TS.esc(q.tip)}</div>` : "");
    $("vq-explain").classList.remove("hidden");
    $("vq-next").classList.remove("hidden");
    $("vq-score").textContent = `맞힘 ${score}`;
  }

  function end() {
    $("vq").classList.add("hidden");
    $("vq-done").classList.remove("hidden");
    $("vq-final").textContent = `${Q.length}문제 중 ${score}개 맞힘`;
    $("vq-missed").innerHTML = missed.length ? "<p><b>틀린 단어</b></p>" + missed.map(q =>
      `<div>• <b>${TS.esc(q.word)}</b> ${TS.esc(q.pos)} — ${TS.esc(q.meaning)}</div>`).join("") : "";
  }

  $("vq-choices").addEventListener("click", e => {
    const b = e.target.closest("[data-k]");
    if (b) pick(Number(b.dataset.k));
  });
  $("vq-next").addEventListener("click", () => { i++; show(); });
  document.addEventListener("keydown", e => {
    const map = { "1": 0, "2": 1, "3": 2, "4": 3, a: 0, b: 1, c: 2, d: 3 };
    const k = e.key.toLowerCase();
    if (!answered && k in map) pick(map[k]);
    else if (answered && k === "enter") { i++; show(); }
  });
  TTS.load();
  show();
})();
