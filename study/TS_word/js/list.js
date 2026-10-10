/* 단어장: 등급·필수/도전·상태·검색, 발음·별표 */
UI.boot("list", () => {
  const { esc, $ } = UI;
  const P = UI.params();
  const level = UI.intParam("level");
  const tier = ["core", "stretch"].includes(P.get("tier")) ? P.get("tier") : null;
  const FILTERS = { all: "전체", new: "안 본 단어", learning: "학습 중", mastered: "암기 완료", starred: "★ 별표", weak: "자주 잊는 단어" };
  const flt = FILTERS[P.get("filter")] ? P.get("filter") : "all";
  const term = (P.get("q") || "").trim().toLowerCase();
  const grades = Ward.grades();
  const today = Ward.todayStr();
  const fails = Ward.failCounts();

  const rows = [];
  for (const w of Ward.words()) {
    if (level && w.level !== level) continue;
    if (tier && w.tier !== tier) continue;
    if (term && !w.word.toLowerCase().includes(term) && !w.meaning.includes(term)) continue;
    const c = Ward.cardOf(w.id);
    const state = Ward.stateOf(c);
    if (flt === "starred" && !(c && c.starred)) continue;
    if (["new", "learning", "mastered"].includes(flt) && state !== flt) continue;
    if (flt === "weak" && (fails[w.id] || 0) < Ward.WEAK_MIN_FAILS) continue;
    rows.push({ w, c, state });
  }

  const sel = (name, items, cur) => `<select data-q="${name}" aria-label="${name}">${items.map(([v, l]) => `<option value="${v}" ${String(cur ?? "") === String(v) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
  const MAX = 300;                       // 한 번에 그리는 줄 수 (2,000줄을 한꺼번에 그리면 느리다)
  let shown = MAX;

  $("app").innerHTML = `
    <div class="page-head">
      <div><h1>단어장 ${level ? UI.gradeBadge(level) : ""}</h1><div class="muted small" id="count"></div></div>
      <form class="row" id="filters" onsubmit="return false">
        ${sel("level", [["", "모든 등급"], ...grades.map(g => [g.level, g.name])], level)}
        ${sel("tier", [["", "필수 + 도전"], ["core", "필수"], ["stretch", "도전"]], tier)}
        ${sel("filter", Object.entries(FILTERS), flt)}
        <input type="search" id="q" placeholder="단어·뜻 검색" value="${esc(P.get("q") || "")}" style="min-width:150px">
      </form>
    </div>
    <div class="card" style="padding:0"><div style="overflow-x:auto"><table class="vtable"><thead><tr>
      <th></th><th>단어</th><th>뜻</th><th>예문</th><th>팁</th><th>상태</th><th></th></tr></thead><tbody id="rows"></tbody></table></div>
      <div id="more" class="empty hidden"><button class="btn" id="more-btn">더 보기</button></div>
      <div id="none" class="empty hidden">조건에 맞는 단어가 없습니다.</div></div>`;

  function draw() {
    $("count").textContent = `${rows.length}개 · 단어를 누르면 발음을 들려줍니다.`;
    $("none").classList.toggle("hidden", rows.length > 0);
    $("rows").innerHTML = rows.slice(0, shown).map(({ w, c, state }) => {
      const tag = state === "new" ? `<span class="tag">안 봄</span>` : state === "mastered" ? `<span class="tag ok">암기 완료</span>` : `<span class="tag">학습 중 · ${c.interval}일</span>`;
      const due = Ward.seen(c) && c.due <= today ? ` <span class="tag bad">복습</span>` : "";
      return `<tr><td><button class="btn small ghost" data-say="${esc(w.word)}" title="발음">🔊</button></td>
        <td><button class="btn ghost small" data-say="${esc(w.word)}"><b>${esc(w.word)}</b></button> <span class="muted small">${esc(w.pos)}</span>${w.tier === "stretch" ? ` <span class="tag ok">도전</span>` : ""}<div>${UI.gradeBadge(w.level)}</div></td>
        <td>${esc(w.meaning)}</td>
        <td class="small">${esc(w.example)}<div class="muted">${esc(w.example_ko)}</div></td>
        <td class="small muted">${esc(w.tip)}</td>
        <td class="small">${tag}${due}${c && c.lapses ? `<div class="muted">잊음 ${c.lapses}</div>` : ""}</td>
        <td><button class="star ${c && c.starred ? "on" : ""}" data-star="${w.id}" title="별표">★</button></td></tr>`;
    }).join("");
    $("more").classList.toggle("hidden", rows.length <= shown);
  }
  draw();

  $("more-btn").addEventListener("click", () => { shown += MAX; draw(); });
  $("filters").addEventListener("change", e => {
    if (e.target.id === "q") return;
    const q = { q: $("q").value };
    $("filters").querySelectorAll("select").forEach(s => { q[s.dataset.q] = s.value; });
    location.href = UI.url("list.html", q);
  });
  $("q").addEventListener("keydown", e => {
    if (e.key !== "Enter") return;
    const q = { q: $("q").value };
    $("filters").querySelectorAll("select").forEach(s => { q[s.dataset.q] = s.value; });
    location.href = UI.url("list.html", q);
  });
  document.addEventListener("click", e => {
    const s = e.target.closest("[data-say]");
    if (s) return UI.sayWord(s.dataset.say);
    const st = e.target.closest("[data-star]");
    if (st) st.classList.toggle("on", Ward.toggleStar(st.dataset.star));
  });
  TTS.load();
});
