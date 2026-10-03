/* 받아쓰기: 문장을 듣고 입력 → 단어 단위로 비교(LCS)해 빠진 단어·틀린 단어를 표시 */
(function () {
  "use strict";
  const D = TS.data("dt-data");
  if (!D) return;
  const $ = id => document.getElementById(id);
  const lines = D.lines;
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
    TTS.play([{ text: l.text, gender: l.voice || (i % 2 ? "male" : "female") }],
      { rate: rate || D.tts.rate, accent: TTS.accentFor(D.tts.accent, l.src + i) })
      .then(() => { $("dt-status").textContent = ""; });
    $("dt-input").focus();
  }

  function show() {
    if (i >= lines.length) {
      $("dt").classList.add("hidden");
      $("dt-done").classList.remove("hidden");
      $("dt-final").textContent = `${lines.length}문장 · 단어 ${gotWords}/${totalWords} (${Math.round(gotWords / Math.max(totalWords, 1) * 100)}%)`;
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
    $("dt-score").textContent = `누적 단어 정확도 ${Math.round(gotWords / totalWords * 100)}%`;
    $("dt-result").innerHTML = `<div><b>${r.match}/${a.length}</b> 단어</div><div style="margin:6px 0">` +
      r.parts.map(p => `<span class="${p.t}">${TS.esc(p.w)}</span>`).join(" ") +
      `</div><div class="muted small">정답: ${TS.esc(lines[i].text)}</div>`;
    $("dt-result").classList.remove("hidden");
    $("dt-input").disabled = true;
    $("dt-check").classList.add("hidden");
    $("dt-next").classList.remove("hidden");
    $("dt-next").focus();
  }

  $("dt-play").addEventListener("click", () => play());
  $("dt-slow").addEventListener("click", () => play(Math.max(0.6, D.tts.rate * 0.75)));
  $("dt-check").addEventListener("click", check);
  $("dt-next").addEventListener("click", () => { i++; show(); });
  document.addEventListener("keydown", e => {
    if (e.key === " " && e.ctrlKey) { e.preventDefault(); play(); }
    else if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!checked) check(); else { i++; show(); }
    }
  });
  TTS.load();
  show();
})();
