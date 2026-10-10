/* 등급별 학습 가이드 (guide.html?level=1~5) — 기본은 내 현재 등급 (TS 앱 guide.html + core/guide.py) */
UI.boot({ exam: "toeic", page: "guide" }, () => {
  const { esc, $ } = UI;
  const S = Score;
  const G = window.TS_GUIDE;
  let level = UI.intParam("level");
  if (level === null) { const [score] = Planner.currentScore(TSStore.settings()); const gr = S.gradeFor(score); level = gr ? gr.level : 1; }
  if (!G.GUIDE[level]) { $("app").innerHTML = `<div class="card empty"><h2>없는 등급입니다</h2><a class="btn" href="guide.html">내 등급 가이드</a></div>`; return; }
  const g = G.GUIDE[level], grade = S.GRADE_BY_LEVEL[level];
  const vocabN = t => Ward.words().filter(w => w.level === level && w.tier === t).length;
  const li = arr => arr.map(x => `<li>${esc(x)}</li>`).join("");
  const next = S.GRADE_BY_LEVEL[level + 1];

  const partRows = Object.entries(S.PART_INFO).map(([ps, info]) => {
    const p = Number(ps), n = Bank.count(p, level);
    const target = g.targets[p];
    return `<tr><td><b>Part ${p}</b><div class="small muted">${esc(info.name)}</div>${g.focus_parts.includes(p) ? `<span class="tag ok">집중</span>` : ""}</td>
      <td class="r num">${n}</td><td class="r num">${TSU.pct(target === undefined ? null : target)}</td>
      <td class="small"><ul class="clean">${li(G.PART_TIPS[p])}</ul></td>
      <td class="r">${n ? `<a class="btn small primary" href="${UI.url("practice.html", { start: 1, part: p, level, n: p === 1 || p === 2 || p === 5 ? 10 : 6 })}">풀기</a>` : ""}</td></tr>`;
  }).join("");
  const vocabCards = [["core", "필수 단어", `${grade.name} 점수대에서 꼭 알아야 할 단어`],
    ["stretch", "도전 단어", level < 5 ? `이 단어까지 알면 ${next.name}(${next.low}점+)에 도전할 만합니다` : "900점 이상 만점권을 가르는 단어"]].map(([t, label, hint]) => `
    <div class="card"><h2 style="margin:0">${label} ${vocabN(t)}개</h2><p class="small muted" style="margin:4px 0 10px">${esc(hint)}</p>
      <div class="row"><a class="btn ${t === "core" ? "primary" : ""}" href="${UI.url("study.html", { level, tier: t })}" data-set="toeic">카드 학습</a>
        <a class="btn" href="${UI.url("listen.html", { level, tier: t })}">🔊 듣기</a>
        <a class="btn" href="${UI.url("quiz.html", { level, tier: t })}">퀴즈</a>
        <a class="btn" href="${UI.url("list.html", { level, tier: t })}">목록</a></div></div>`).join("");

  $("app").innerHTML = `<div class="page-head"><div><div class="muted small">등급별 학습 가이드</div>
      <h1>${UI.toeicBadge(grade)} ${grade.name} · ${S.rangeText(grade)} <span class="muted">(${esc(grade.ko)})</span></h1></div>
      <div class="seg">${S.GRADES.map(gr => `<a href="guide.html?level=${gr.level}" class="${gr.level === level ? "on" : ""}">${gr.name}</a>`).join("")}</div></div>
    <div class="card"><h2>${esc(g.headline)}</h2><p><b>다음 목표</b> · ${esc(g.next_goal)}</p></div>
    <div class="grid two" style="margin-top:14px">
      <div class="card"><h2>이 등급의 흔한 모습</h2><ul class="clean">${li(g.state)}</ul></div>
      <div class="card"><h2>핵심 과제</h2><ul class="clean">${li(g.tasks)}</ul></div>
      <div class="card"><h2>문법 체크리스트</h2><ul class="clean">${li(g.grammar)}</ul>
        <a class="btn small" style="margin-top:10px" href="${UI.url("practice.html", { start: 1, part: 5, level, n: 20 })}">${grade.name} Part 5 20문항</a></div>
      <div class="card"><h2>하루 루틴 <span class="small muted">(합계 ${g.routine.reduce((s, r) => s + r[1], 0)}분)</span></h2>
        <table><tbody>${g.routine.map(([name, m]) => `<tr><td>${esc(name)}</td><td class="r num">${m}분</td></tr>`).join("")}</tbody></table>
        <p class="small muted" style="margin-top:8px">하루 새 단어 권장: ${g.daily_new_words}개 (설정에서 바꿀 수 있음)</p></div></div>
    <div class="card" style="margin-top:14px"><h2>이 등급 문제로 연습</h2>
      <p class="small muted">목표 정답률은 다음 등급으로 올라가기 위한 대략의 기준입니다.</p>
      <div class="table-wrap"><table><thead><tr><th>파트</th><th class="r">문제 수</th><th class="r">목표 정답률</th><th>풀이 전략</th><th></th></tr></thead>
      <tbody>${partRows}</tbody></table></div></div>
    <div class="grid two" style="margin-top:14px">${vocabCards}</div>`;
});
