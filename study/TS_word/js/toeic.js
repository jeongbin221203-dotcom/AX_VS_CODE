/* 토익 오늘(대시보드) — 오늘의 단어 카드, 현재/목표 점수, 오늘 할 일, 파트별 정답률, 점수 추이 (TS 앱 dashboard.html) */
UI.boot({ exam: "toeic", page: "toeic" }, () => {
  TSStore.pruneDrafts();
  const { esc, $ } = UI;
  const S = Score;
  const settings = { ...TSStore.settings(), daily_new_words: String(Ward.settings().daily_new) };
  const plan = Planner.build(settings);
  const vh = Planner.vocabHero(plan, settings);
  const acc = Stats.partAccuracy({ lastN: 60 });
  const recent = Stats.recentSessions(5);
  const history = Stats.scoreHistory(10);
  const ongoing = Stats.unfinishedSessions();
  const g = plan.grade;
  const streak = Stats.streak();

  const ongoingHtml = ongoing.length ? `<div class="card" style="margin-bottom:14px"><h2 style="margin:0 0 6px">이어서 풀기</h2>
    <table class="small"><tbody>${ongoing.map(o => `<tr><td><a href="solve.html?sid=${o.id}">${esc(Sessions.sessionTitle(o))}</a>
      <div class="muted">${esc(o.created_at.slice(5, 16).replace("T", " "))} 시작${o.answered ? ` · ${o.answered}문항 풂` : ""}</div></td>
      <td class="r"><a class="btn" href="solve.html?sid=${o.id}">이어서</a></td></tr>`).join("")}</tbody></table></div>` : "";

  const heroHtml = `<div class="card vocab-hero"><div>
      <h2>📘 오늘의 단어 — 먼저 하세요</h2>
      <div class="small muted">문제를 못 푸는 가장 큰 이유는 단어입니다. 단어를 먼저 끝내면 같은 문제가 훨씬 쉬워집니다.</div>
      <div class="nums">
        <div><b>${vh.due}</b><span>복습할 단어</span></div>
        <div><b>${vh.new}</b><span>새 단어 (하루 ${vh.daily_new}개)</span></div>
        <div><b>${vh.seen}<small style="font-size:1rem;font-weight:600"> / ${vh.total}</small></b><span>${esc(vh.grade_name)} 단어 본 수 · 외운 ${vh.mastered}</span></div>
      </div>
      <div class="bar"><i style="width:${vh.total ? TSU.pyRound(vh.seen / vh.total * 100) : 0}%"></i></div></div>
    <div class="cta">${vh.done
      ? `<div class="done-note">✅ 오늘 단어를 다 했어요</div><a class="btn" href="quiz.html">단어 시험 보기</a>`
      : `<a class="btn primary big" href="study.html">단어 공부 시작</a>`}
      <a class="btn small" href="listen.html">🎧 듣기 모드</a></div></div>`;

  const kpi = `<div class="grid four">
    <div class="card kpi"><div class="label">현재 점수</div><div class="value">${plan.score !== null ? plan.score : "–"}</div>
      <div class="sub">${UI.toeicBadge(g)} ${esc(plan.score_source || "진단 테스트를 먼저 보세요")}</div></div>
    <div class="card kpi"><div class="label">목표 점수</div><div class="value">${plan.target}</div>
      <div class="sub">${UI.toeicBadge(plan.target_grade)} ${plan.gap !== null ? (plan.gap > 0 ? plan.gap + "점 남음" : "목표 달성") : ""}</div></div>
    <div class="card kpi"><div class="label">시험일</div>
      <div class="value">${plan.days_left !== null ? "D" + (plan.days_left >= 0 ? "-" : "+") + Math.abs(plan.days_left) : "–"}</div>
      <div class="sub">${plan.exam ? esc(plan.exam) + (plan.weekly_gain ? ` · 주당 +${plan.weekly_gain}점 필요` : "") : `<a href="settings.html">시험일 설정</a>`}</div></div>
    <div class="card kpi"><div class="label">오늘 푼 문항 / 단어</div><div class="value">${plan.today.questions} · ${plan.today.words}</div>
      <div class="sub">목표 ${plan.daily_q}문항<div class="bar" style="margin-top:6px"><i style="width:${Math.min(100, TSU.pyRound(plan.today.questions / plan.daily_q * 100))}%"></i></div></div></div>
  </div>`;

  const ladder = `<div class="card" style="margin-top:14px"><div class="spread"><h2 style="margin:0">등급 사다리</h2>
    <span class="small muted">칸을 누르면 그 등급의 학습 가이드</span></div><div class="ladder" style="margin-top:10px">
    ${S.GRADES.map(gr => `<a href="guide.html?level=${gr.level}" class="step g${gr.level}${g && g.level === gr.level ? " cur" : ""}${plan.target_grade && plan.target_grade.level === gr.level ? " target" : ""}">
      <b>${gr.name}</b>${gr.low}~${gr.high}${g && g.level === gr.level ? "<div>현재</div>" : plan.target_grade && plan.target_grade.level === gr.level ? "<div>목표</div>" : ""}</a>`).join("")}
    </div></div>`;

  const tasks = plan.tasks.map(t => `<li class="${t.done ? "done" : ""}"><span class="check"></span>
      <div><div class="t">${esc(t.title)}</div><div class="d">${esc(t.detail)}</div></div>
      <a class="btn small go ${t.done ? "" : "primary"}" href="${t.href}">${t.done ? "더 하기" : "시작"}</a></li>`).join("");
  const accRows = Object.entries(S.PART_INFO).map(([p, info]) => {
    const a = acc[p];
    return `<tr><td>Part ${p} <span class="muted small">${esc(info.name)}</span></td>
      <td style="width:42%">${a.n ? `<div class="bar ${a.rate >= 0.75 ? "ok" : a.rate < 0.5 ? "bad" : ""}"><i style="width:${TSU.pyRound(a.rate * 100)}%"></i></div>` : ""}</td>
      <td class="r num">${TSU.pct(a.rate)} <span class="muted small">(${a.n})</span></td></tr>`;
  }).join("");
  const mid = `<div class="grid two" style="margin-top:14px">
    <div class="card"><h2>오늘 할 일</h2>
      <p class="small muted">${esc(plan.guide.headline)} · 집중 파트: ${plan.focus.map(p => "Part " + p).join(", ")}</p>
      <ul class="tasks">${tasks}</ul></div>
    <div class="card"><h2>파트별 최근 정답률 <span class="small muted">(파트마다 최근 60문항)</span></h2>
      <table><tbody>${accRows}</tbody></table>
      <div class="row" style="margin-top:10px">
        <a class="btn small" href="review.html">오답노트 ${plan.open_notes}</a>
        <a class="btn small" href="words.html">단어 복습 ${plan.vocab_due}</a>
        <a class="btn small" href="stats.html">자세한 통계</a></div></div></div>`;

  const recentHtml = recent.length ? `<table><tbody>${recent.map(s => `<tr>
      <td><a href="result.html?sid=${s.id}">${esc(Sessions.sessionTitle(s))}</a><div class="muted small">${esc(s.finished_at.slice(0, 16).replace("T", " "))}</div></td>
      <td class="r num">${s.correct}/${s.total}${s.total_est !== null ? `<div class="small"><b>${s.total_est}점</b></div>` : ""}</td></tr>`).join("")}</tbody></table>
      <a class="small" href="history.html">전체 기록 →</a>` : `<div class="empty">아직 기록이 없습니다.</div>`;
  const bottom = `<div class="grid two" style="margin-top:14px">
    <div class="card"><h2>점수 추이</h2>${history.length ? `<div class="chart-box"><canvas id="score-chart"></canvas></div>`
      : `<div class="empty">진단 테스트나 모의고사를 보면 추정 점수가 여기에 쌓입니다.<br><br><a class="btn primary" href="diagnostic.html">진단 테스트 보기</a></div>`}</div>
    <div class="card"><h2>최근 학습</h2>${recentHtml}</div></div>`;

  $("app").innerHTML = `${ongoingHtml}${heroHtml}
    <div class="page-head"><div><div class="muted small">연속 학습 ${streak}일</div><h1>오늘의 토익</h1></div>
      <div class="row"><a class="btn" href="guide.html">내 등급 가이드</a><a class="btn primary" href="mock.html">모의고사</a></div></div>
    ${kpi}${ladder}${mid}${bottom}`;
  if (history.length) Charts.score($("score-chart"), history);
});
