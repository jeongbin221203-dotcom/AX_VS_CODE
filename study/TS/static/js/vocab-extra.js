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
      const r = await TS.post(TS.vbase + "/api/vocab/star", { word_id: st.dataset.star });
      st.classList.toggle("on", r.starred);
    }
  });

  // ---- 퀴즈: 문제마다 결과 저장(틀리면 복습 카드로), 끝나면 틀린 단어만 다시 풀기
  const ALL = TS.data("vq-data");
  if (!ALL) return;
  const $ = id => document.getElementById(id);
  const L = ["A", "B", "C", "D"];
  let Q = ALL, i = 0, score = 0, answered = false, round = 1, scheduled = 0;
  let missed = [];

  function shuffle(a) { for (let k = a.length - 1; k > 0; k--) { const j = Math.floor(Math.random() * (k + 1)); [a[k], a[j]] = [a[j], a[k]]; } return a; }

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
    // 첫 회차만 기록 (다시 풀기는 연습이라 두 번 세지 않음)
    if (round === 1) {
      TS.post(TS.vbase + "/api/vocab/quiz/answer", { word_id: q.id, correct: ok })
        .then(r => { if (r.scheduled) scheduled++; })
        .catch(e => { $("vq-explain").insertAdjacentHTML("beforeend", `<div class="muted">기록 실패: ${TS.esc(e.message)}</div>`); });
    }
    $("vq-choices").classList.add("locked");
    [...$("vq-choices").children].forEach((b, n) => {
      if (n === q.answer) b.classList.add("right");
      else if (n === k) b.classList.add("wrong");
    });
    $("vq-explain").innerHTML = `<span class="verdict ${ok ? "ok" : "bad"}">${ok ? "정답" : "오답"}</span>` +
      `<b>${TS.esc(q.word)}</b> ${TS.esc(q.meaning)}<div>${TS.esc(q.example)}</div><div class="muted">${TS.esc(q.example_ko || "")}</div>` +
      (q.tip ? `<div class="muted">${TS.esc(q.tip)}</div>` : "") + (ok || round > 1 ? "" : `<div class="muted">→ 복습 카드에 넣었습니다 (내일 다시)</div>`);
    $("vq-explain").classList.remove("hidden");
    $("vq-next").classList.remove("hidden");
    $("vq-score").textContent = `맞힘 ${score}`;
  }

  function end() {
    $("vq").classList.add("hidden");
    $("vq-done").classList.remove("hidden");
    $("vq-final").textContent = round === 1 ? `${Q.length}문제 중 ${score}개 맞힘 (${Math.round(score / Q.length * 100)}%)`
                                            : `다시 풀기 ${round - 1}회차: ${Q.length}문제 중 ${score}개 맞힘`;
    $("vq-note").textContent = round === 1 && missed.length ? `틀린 ${missed.length}개는 복습 카드에 넣었습니다. 내일 단어 카드에 다시 나옵니다.` : "";
    $("vq-missed").innerHTML = missed.length ? "<p><b>틀린 단어</b></p>" + missed.map(q =>
      `<div>• <b>${TS.esc(q.word)}</b> ${TS.esc(q.pos)} — ${TS.esc(q.meaning)}</div>`).join("") : "<p>모두 맞혔습니다!</p>";
    $("vq-retry").classList.toggle("hidden", !missed.length);
  }

  function retry() {
    Q = shuffle(missed.map(q => {                      // 선택지 순서도 새로 섞는다
      const order = shuffle(q.options.map((o, k) => k));
      return { ...q, options: order.map(k => q.options[k]), answer: order.indexOf(q.answer) };
    }));
    missed = []; i = 0; score = 0; round++;
    $("vq-done").classList.add("hidden");
    $("vq").classList.remove("hidden");
    show();
  }

  $("vq-choices").addEventListener("click", e => {
    const b = e.target.closest("[data-k]");
    if (b) pick(Number(b.dataset.k));
  });
  $("vq-next").addEventListener("click", () => { i++; show(); });
  $("vq-retry").addEventListener("click", retry);
  document.addEventListener("keydown", e => {
    if (e.target.closest?.("select, input")) return;
    const map = { "1": 0, "2": 1, "3": 2, "4": 3, a: 0, b: 1, c: 2, d: 3 };
    const k = e.key.toLowerCase();
    if ($("vq").classList.contains("hidden")) return;
    if (!answered && k in map) pick(map[k]);
    else if (answered && k === "enter") { i++; show(); }
  });
  TTS.load();
  show();
})();
