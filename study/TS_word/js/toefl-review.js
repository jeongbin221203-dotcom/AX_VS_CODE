/* 토플 오답노트 (toefl-review.html) — 과제별로 마지막에 틀린 문제와 '틀린 문제만 다시 풀기' (TS 앱 templates/toefl/review.html) */
UI.boot({ exam: "toefl", page: "toefl-review" }, async () => {
  const { esc, $ } = UI;
  const T = Toefl;
  const wrongBy = {};
  for (const t of T.TASK_KEYS) wrongBy[t] = T.wrongItems(t);
  await T.load(T.TASK_KEYS.filter(t => Object.keys(wrongBy[t]).length));         // 틀린 문제가 있는 과제의 데이터만 읽는다
  const sections = Object.entries(T.SECTIONS).map(([key, sec]) => {
    const tasks = T.TASK_KEYS.filter(t => T.TASKS[t].section === key).map(t => ({
      key: t, ...T.TASKS[t],
      rows: Object.entries(wrongBy[t]).filter(([i]) => T.get(t, i))
        .map(([i, w]) => ({ id: i, ...w, label: T.itemLabel(t, T.get(t, i)), level: T.get(t, i).level }))
        .sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0)),
    }));
    return { key, ...sec, tasks, n: tasks.reduce((a, x) => a + x.rows.length, 0) };
  });
  const total = sections.reduce((a, s) => a + s.n, 0);
  const cutAuto = Math.trunc(T.WRONG_CUT_AUTO * 100), cutSelf = Math.trunc(T.WRONG_CUT_SELF * 5);
  $("app").innerHTML = `
    <div class="page-head">
      <div>
        <div class="muted small">마지막으로 풀었을 때 틀린 문제만 모읍니다 (자동 채점 ${cutAuto}% 미만 · 쓰기·인터뷰 자기 평가 ${cutSelf}점 미만). 다시 풀어 맞히면 빠집니다.</div>
        <h1>토플 오답노트</h1>
      </div>
      <a class="btn" href="toefl-history.html">기록 보기</a>
    </div>
    ${total ? "" : `<div class="card empty">틀린 문제가 없습니다. 연습을 하면 틀린 문제가 여기에 모입니다. <a href="toefl.html">토플 홈으로</a></div>`}
    ${sections.filter(s => s.n).map(s => `
    <div class="card" style="margin-top:14px" id="rv-${s.key}">
      <h2>${esc(s.ko)} <span class="muted small">${esc(s.name)} · ${s.n}문제</span></h2>
      ${s.tasks.filter(t => t.rows.length).map(t => `
      <div class="spread" style="margin-top:10px">
        <b>${esc(t.name)} <span class="muted small">${esc(t.en)} · ${t.rows.length}문제</span></b>
        <a class="btn small primary" href="toefl-practice.html?task=${t.key}&review=1&n=${Math.min(t.rows.length, 10)}">틀린 문제 다시 풀기 (${Math.min(t.rows.length, 10)})</a>
      </div>
      <table class="small" style="margin-top:6px"><tbody>
        ${t.rows.slice(0, 30).map(r => `<tr><td style="width:7em">${esc(r.at.slice(0, 10))}</td><td>${esc(r.label)}</td>
          <td class="r num muted" style="white-space:nowrap">밴드 ${T.LEVEL_BAND[r.level]} · ${r.wrong}/${r.n} 틀림</td></tr>`).join("")}
      </tbody></table>
      ${t.rows.length > 30 ? `<p class="small muted">외 ${t.rows.length - 30}문제</p>` : ""}`).join("")}
    </div>`).join("")}`;
});
