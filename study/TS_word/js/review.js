/* 오답노트 — 틀린 문항이 자동으로 모이고, 복습에서 서로 다른 날 2번 맞히면 '졸업' (TS 앱 review.html + /review/start)
   ?start=1&n=10&part=5 로 바로 복습을 시작할 수도 있다. */
UI.boot({ exam: "toeic", page: "review" }, async () => {
  const { esc, $ } = UI;
  const q = UI.params();
  let flashMsg = "";

  if (q.get("start") === "1") {
    try { return UI.go(`solve.html?sid=${await Sessions.startReview(UI.intParam("part"), UI.intParam("n") ?? 10)}`); }
    catch (e) { if (!(e instanceof Sessions.StudyError)) throw e; flashMsg = "복습할 오답이 없습니다."; }
  }

  async function render() {
    const status = q.get("status") === "cleared" ? "cleared" : "open";
    const part = UI.intParam("part");
    const qtype = q.get("type") || null;
    const notes = TSStore.wrongNotes(status, part, qtype);
    await Bank.load([...new Set(notes.map(n => n.part))]);
    const rows = notes.map(n => ({ ...n, item: Bank.item(`${n.part}:${n.item_id}`) })).filter(n => n.item);
    const allOpen = TSStore.wrongNotes("open");
    const byPart = {};
    for (const p of Object.keys(Score.PART_INFO)) byPart[p] = allOpen.filter(n => n.part === Number(p)).length;
    const types = [...new Set(allOpen.map(n => n.qtype))].sort();
    const href = o => UI.url("review.html", { status, part: part || "", type: qtype || "", ...o });
    const body = rows.length ? `<div class="table-wrap"><table>
      <thead><tr><th>파트</th><th>문제</th><th>유형</th><th class="r">틀림</th><th class="r">연속 정답</th><th>메모</th><th></th></tr></thead><tbody>
      ${rows.map(r => {
        const pv = Sessions.preview(r.item, r.qidx);
        return `<tr><td class="num">P${r.part} ${UI.toeicBadge(r.level)}</td>
          <td class="small">${esc(pv.length > 90 ? pv.slice(0, 87) + "..." : pv)}<div class="muted">마지막 오답 ${esc(r.last_wrong_at.slice(0, 10))}</div></td>
          <td class="small">${esc(r.qtype)}</td><td class="r num">${r.wrong_count}</td><td class="r num">${r.right_streak}</td>
          <td style="min-width:200px"><form class="row" data-memo="${esc(r.qkey)}">
            <input name="memo" value="${esc(r.memo)}" placeholder="왜 틀렸는지" style="flex:1;min-width:120px"><button class="btn small">저장</button></form></td>
          <td class="r">${status === "open"
            ? `<button class="btn small ghost" data-status="cleared" data-q="${esc(r.qkey)}" title="복습 목록에서 빼기">졸업 처리</button>`
            : `<button class="btn small ghost" data-status="open" data-q="${esc(r.qkey)}">다시 복습</button>`}</td></tr>`;
      }).join("")}</tbody></table></div>`
      : `<div class="empty">${status === "open" ? "틀린 문제가 없습니다. 파트 연습이나 모의고사를 풀면 여기에 쌓입니다." : "아직 졸업한 문제가 없습니다."}</div>`;

    $("app").innerHTML = `${flashMsg ? `<div class="flash ok">${esc(flashMsg)}</div>` : ""}
      <div class="page-head"><div><h1>오답노트</h1>
        <div class="muted small">틀린 문항이 자동으로 모입니다. 복습에서 <b>서로 다른 날 2번</b> 맞히면 '졸업'으로 옮겨집니다 (같은 날 반복은 한 번으로 셉니다).</div></div>
        <div class="row"><a class="btn primary" href="${UI.url("review.html", { start: 1, n: 10, part: part || "" })}">${part ? `Part ${part} ` : ""}10문항 복습</a>
          <a class="btn" href="${UI.url("review.html", { start: 1, n: 30, part: part || "" })}">30문항</a></div></div>
      <div class="card"><div class="spread"><div class="seg">
          <a href="${href({ status: "open" })}" class="${status === "open" ? "on" : ""}">복습 중 ${allOpen.length}</a>
          <a href="${href({ status: "cleared" })}" class="${status === "cleared" ? "on" : ""}">졸업</a></div>
        <form class="row" id="filters"><select name="part" aria-label="파트"><option value="">모든 파트</option>
          ${Object.entries(byPart).map(([p, n]) => `<option value="${p}" ${part === Number(p) ? "selected" : ""}>Part ${p} (${n})</option>`).join("")}</select>
          <select name="type" aria-label="유형"><option value="">모든 유형</option>
          ${types.map(t => `<option value="${esc(t)}" ${qtype === t ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></form></div></div>
      <div class="card">${body}</div>`;
    flashMsg = "";
  }

  document.addEventListener("submit", e => {
    const f = e.target.closest("[data-memo]");
    if (!f) return;
    e.preventDefault();
    TSStore.setNoteMemo(f.dataset.memo, f.elements.memo.value);
    const b = f.querySelector("button"); b.textContent = "저장됨"; setTimeout(() => { b.textContent = "저장"; }, 1200);
  });
  document.addEventListener("click", async e => {
    const b = e.target.closest("[data-status]");
    if (!b) return;
    TSStore.setNoteStatus(b.dataset.q, b.dataset.status);
    await render();
  });
  document.addEventListener("change", e => {
    if (e.target.closest("#filters")) {
      const f = $("filters");
      location.href = UI.url("review.html", { status: q.get("status") || "", part: f.elements.part.value, type: f.elements.type.value });
    }
  });
  await render();
});
