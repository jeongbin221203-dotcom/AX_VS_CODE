/* 단어 홈: 오늘 할 일(복습·새 단어), 등급별 진행, 시험·듣기 바로가기 */
UI.boot("home", () => {
  const { esc } = UI;
  const set = Ward.currentSet();
  const grades = Ward.grades();
  const prog = Ward.levelProgress();
  const q = Ward.queue({});
  const daily = Number(Ward.settings().daily_new) || 0;
  const today = Ward.todayCounts();
  const missed = Ward.recentlyMissed().size;
  const weak = Object.values(Ward.failCounts()).filter(n => n >= Ward.WEAK_MIN_FAILS).length;
  const starred = Ward.words().filter(w => (Ward.cardOf(w.id) || {}).starred).length;
  const totalSeen = prog.reduce((s, p) => s + p.seen, 0);
  const totalMastered = prog.reduce((s, p) => s + p.mastered, 0);
  const all = Ward.words().length;
  const done = !q.due.length && !q.new.length;
  const myLv = Number(Ward.settings().my_level) || 0;
  const mine = myLv ? prog.find(p => p.level === myLv) : null;
  const myName = mine ? grades.find(g => g.level === myLv).name : "";

  const levels = prog.map(p => {
    const g = grades.find(x => x.level === p.level);
    const rows = [["core", "필수", "이 점수대에서 꼭 알아야 할 단어"],
                  ["stretch", "도전", p.level < 5 ? "알면 다음 등급에 도전할 만한 단어" : (set === "toeic" ? "900점 이상 만점권 단어" : "밴드 6 최상위 학술 어휘")]]
      .map(([t, label, hint]) => {
        const x = p.tiers[t];
        const pct = x.total ? Math.round(x.mastered / x.total * 100) : 0;
        return `<div style="margin-top:10px">
          <div class="spread small"><span><b>${label} 단어</b> <span class="muted">${esc(hint)}</span></span><span class="num">암기 ${x.mastered} / ${x.total}</span></div>
          <div class="bar ${t === "core" ? "ok" : ""}" style="margin:5px 0"><i style="width:${pct}%"></i></div>
          <div class="row">
            <a class="btn small ${t === "core" ? "primary" : ""}" href="${UI.url("study.html", { level: p.level, tier: t })}">카드</a>
            <a class="btn small" href="${UI.url("quiz.html", { level: p.level, tier: t })}">퀴즈</a>
            <a class="btn small" href="${UI.url("list.html", { level: p.level, tier: t })}">목록</a>
            <a class="btn small" href="${UI.url("listen.html", { level: p.level, tier: t })}">듣기</a>
          </div></div>`;
      }).join("");
    return `<div class="card"><div class="spread"><h2 style="margin:0">${esc(g.name)}</h2><span class="muted small">${esc(g.range)}</span></div>
      <p class="small muted" style="margin:6px 0 4px">오늘 복습 ${p.due} · 학습 시작 ${p.seen} / ${p.total}</p>${rows}</div>`;
  }).join("");

  UI.$("app").innerHTML = `
    <div class="card vocab-hero">
      <div>
        <h2>📘 오늘의 단어 — 먼저 하세요</h2>
        <div class="small muted">문제를 못 푸는 가장 큰 이유는 단어입니다. 단어를 먼저 끝내면 같은 문제가 훨씬 쉬워집니다.${mine ? ` 새 단어는 내 등급(${esc(myName)})부터 나옵니다.` : ` <a href="settings.html">내 등급을 정하면</a> 그 등급 단어부터 나옵니다.`}</div>
        <div class="nums">
          <div><b>${q.due.length}</b><span>복습할 단어</span></div>
          <div><b>${q.new.length}</b><span>새 단어 (하루 ${daily}개)</span></div>
          <div><b>${mine ? mine.seen : totalSeen}<small style="font-size:1rem;font-weight:600"> / ${mine ? mine.total : all}</small></b><span>${mine ? esc(myName) + " 단어 본 수 · 외운 " + mine.mastered : "본 단어 · 외운 " + totalMastered}</span></div>
          <div><b>${Ward.streak()}</b><span>연속 학습일</span></div>
        </div>
        <div class="bar"><i style="width:${all ? Math.round(totalSeen / all * 100) : 0}%"></i></div>
      </div>
      <div class="cta">
        ${done ? `<div class="done-note">✅ 오늘 단어를 다 했어요</div><a class="btn" href="quiz.html">단어 시험 보기</a>`
               : `<a class="btn primary big" href="study.html">단어 공부 시작</a>`}
        <a class="btn small" href="listen.html">🎧 듣기 모드</a>
      </div>
    </div>

    <div class="card">
      <div class="spread">
        <div><h2 style="margin:0">단어 시험</h2><span class="small muted">틀린 단어는 복습 카드에 들어가고, 2번 이상 틀리면 '자주 잊는 단어'로 모입니다.</span></div>
        <div class="row">
          <a class="btn ${weak ? "primary" : ""}" href="quiz.html?set=weak&n=30">자주 잊는 단어 (${weak})</a>
          <a class="btn" href="quiz.html?set=missed&n=30">최근 틀린 단어 (${missed})</a>
          <a class="btn" href="quiz.html">전체 시험</a>
          ${starred ? `<a class="btn" href="study.html?starred=1">★ 별표 ${starred}</a>` : ""}
        </div>
      </div>
    </div>
    <div class="card small muted">오늘 새로 본 단어 ${Ward.newLearnedToday(new Set(Ward.words().map(w => w.id)))} / ${daily}개 · 오늘 복습 ${today.reviewed}번 · 시험 ${today.quiz}문제 (하루 한도는 <a href="settings.html">설정</a>에서)</div>
    <div class="grid three" style="margin-top:14px">${levels}</div>`;
});
