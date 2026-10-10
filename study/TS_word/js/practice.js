/* 파트 연습 — 파트·등급·유형·문항 수를 골라 풀기 시작 (TS 앱 practice.html + /practice/start)
   ?start=1&part=5&level=2&type=품사&n=10 처럼 주소로 바로 시작할 수도 있다 (오늘 할 일·통계의 '연습' 버튼이 씀). */
UI.boot({ exam: "toeic", page: "practice" }, async () => {
  const { esc, $ } = UI;
  const S = Score;
  const q = UI.params();
  let error = "";

  if (q.get("start") === "1") {
    try {
      let level = UI.intParam("level");
      if (level !== null && !S.GRADE_BY_LEVEL[level]) level = null;
      const sid = await Sessions.startPractice(UI.intParam("part"), level, q.get("type") || null, UI.intParam("n") ?? 10);
      return UI.go(`solve.html?sid=${sid}`);
    } catch (e) {
      if (!(e instanceof Sessions.StudyError)) throw e;
      error = e.message;
    }
  }

  const [score] = Planner.currentScore(TSStore.settings());
  const grade = score !== null ? S.gradeFor(score) : null;
  const lvlAcc = Stats.levelAccuracy();
  const vocabDue = Ward.queue({ dailyNew: 0 }).due.length;
  const tips = window.TS_GUIDE.PART_TIPS;
  const nOptions = p => (p === 1 || p === 2 || p === 5 ? [5, 10, 20, 30] : p === 3 || p === 4 ? [3, 6, 9, 12] : p === 6 ? [4, 8, 12] : [4, 8, 12, 20]);

  const parts = Object.entries(S.PART_INFO).map(([ps, info]) => {
    const p = Number(ps);
    const rec = grade ? Planner.recommendedLevel(p, grade.level, lvlAcc) : null;
    const levelOpts = S.GRADES.map(gr => `<option value="${gr.level}" ${rec === gr.level ? "selected" : ""}>${gr.name} (${Bank.count(p, gr.level)})${rec === gr.level ? " · 추천" : ""}</option>`).join("");
    const typeOpts = Bank.typeList(p).map(([t, n]) => `<option value="${esc(t)}">${esc(t)} (${n})</option>`).join("");
    const nOpts = nOptions(p).map((n, i) => `<option value="${n}" ${i === 1 ? "selected" : ""}>${n}문항</option>`).join("");
    const accRow = S.GRADES.map(gr => { const a = Stats.levelAcc(lvlAcc, p, gr.level); return `<td class="r num">${a ? TSU.pct(a.rate) + ` <span class="muted">(${a.n})</span>` : "–"}</td>`; }).join("");
    return `<div class="card"><div class="spread"><div>
        <h2 style="margin:0">Part ${p} · ${esc(info.name)} <span class="tag">${info.section}</span></h2>
        <div class="small muted">실제 시험 ${info.real_count}문항${info.sec_per_q ? ` · 목표 문항당 ${info.sec_per_q}초` : ""}</div></div>
      <form class="row" method="get" action="practice.html">
        <input type="hidden" name="start" value="1"><input type="hidden" name="part" value="${p}">
        <select name="level" aria-label="등급"><option value="">모든 등급</option>${levelOpts}</select>
        <select name="type" aria-label="유형"><option value="">모든 유형</option>${typeOpts}</select>
        <select name="n" aria-label="문항 수">${nOpts}</select>
        <button class="btn primary">풀기</button></form></div>
      <div class="table-wrap" style="margin-top:10px"><table class="small">
        <thead><tr><th>등급별</th>${S.GRADES.map(gr => `<th class="r">${gr.name}</th>`).join("")}</tr></thead>
        <tbody><tr><td class="muted">문제</td>${S.GRADES.map(gr => `<td class="r num">${Bank.count(p, gr.level)}</td>`).join("")}</tr>
          <tr><td class="muted">내 정답률</td>${accRow}</tr></tbody></table></div>
      <details style="margin-top:8px"><summary class="small">풀이 전략</summary>
        <ul class="clean small">${tips[p].map(t => `<li>${esc(t)}</li>`).join("")}</ul></details></div>`;
  }).join("");

  $("app").innerHTML = `${error ? `<div class="flash error">${esc(error)}</div>` : ""}
    <div class="page-head"><div><h1>파트 연습</h1>
      <div class="muted small">등급을 고르지 않으면 모든 등급에서 섞어 냅니다. 안 푼 문제를 먼저 냅니다.${grade ? ` 지금 등급: ${UI.toeicBadge(grade)}` : ""}</div></div></div>
    ${vocabDue ? `<div class="vocab-nudge">📘 복습할 단어가 <b>${vocabDue}개</b> 남아 있어요. 단어를 먼저 보고 문제를 풀면 훨씬 잘 풀립니다.
      <a class="btn small primary" href="study.html">단어 먼저 하기</a></div>` : ""}
    <div class="stack">${parts}</div>`;
});
