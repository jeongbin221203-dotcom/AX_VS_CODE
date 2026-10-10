/* 토플 홈 (toefl.html) — 영역별 추정 밴드, 밴드 표, 과제별 연습 시작, 최근 기록 (TS 앱 templates/toefl/home.html) */
UI.boot({ exam: "toefl", page: "toefl" }, () => {
  const { esc, $ } = UI;
  const T = Toefl;

  function draw() {
    const bands = T.sectionBands();
    const stats = T.taskStats();
    const target = T.targetBand();
    const overall = T.overallBand(bands);
    const recLevel = T.recommendedLevel(overall, target);
    const recent = T.recent();
    const statText = t => {
      const s = stats[t.key];
      if (!s) return "–";
      return `${s.n}회 · ${t.auto ? "평균 " + Math.round(s.avg * 100) + "%" : "자기 평가 " + (Math.round(s.avg * 5 * 10) / 10) + "/5"}`;
    };
    const sections = Object.entries(T.SECTIONS).map(([key, sec]) => ({
      key, ...sec, band: bands[key],
      tasks: T.TASK_KEYS.filter(t => T.TASKS[t].section === key).map(t => ({ key: t, ...T.TASKS[t], total: T.count(t) })),
    }));

    $("app").innerHTML = `
      <div class="page-head">
        <div>
          <div class="muted small">TOEFL iBT · 2026년 1월 개편 형식 · 약 90분 · 영역별 1~6 밴드</div>
          <h1>토플</h1>
        </div>
        <div class="row">
          <a class="btn primary" href="toefl-mock.html">실전 모의고사</a>
          <a class="btn" href="words.html" data-set="toefl">학술 어휘</a>
          <label class="small">목표 밴드
            <select id="target">${[2, 2.5, 3, 3.5, 4, 4.5, 5, 5.5, 6].map(b => `<option value="${b}" ${target === b ? "selected" : ""}>${b} (${T.CEFR[Math.trunc(b)]})</option>`).join("")}</select></label>
        </div>
      </div>

      <div class="grid four">
        <div class="card kpi">
          <div class="label">종합 밴드 (추정)</div>
          <div class="value">${overall !== null ? overall : "–"}</div>
          <div class="sub">${overall ? `${T.cefr(overall)} · 목표 ${target}${overall < target ? ` · ${target - overall} 남음` : " · 목표 달성"}` : "네 영역을 모두 풀면 계산됩니다"}</div>
        </div>
        ${sections.map(s => `<div class="card kpi">
          <div class="label">${esc(s.name)} <span class="muted">${esc(s.ko)}</span></div>
          <div class="value">${s.band !== null ? s.band : "–"}</div>
          <div class="sub">${s.band ? T.cefr(s.band) : "기록 부족"}</div></div>`).join("")}
      </div>

      <div class="card" style="margin-top:14px">
        <div class="spread"><h2 style="margin:0">밴드 표</h2><span class="small muted">추정 밴드는 난이도별 정답률(쓰기·말하기는 자기 평가)로 계산합니다</span></div>
        <div class="ladder" style="grid-template-columns:repeat(6,1fr);margin-top:10px">
          ${[1, 2, 3, 4, 5, 6].map(b => `<div class="step${overall && Math.trunc(overall) === b ? " cur" : ""}${Math.trunc(target) === b ? " target" : ""}" style="${overall && Math.trunc(overall) === b ? "outline:2px solid var(--accent)" : ""}">
            <b>${b}</b>${T.CEFR[b]}<div class="small">예전 ${T.BAND_OLD[b]}</div></div>`).join("")}
        </div>
      </div>

      ${sections.map(s => `
      <div class="card" style="margin-top:14px" id="sec-${s.key}">
        <div class="spread">
          <h2 style="margin:0">${esc(s.name)} <span class="muted small">${esc(s.ko)} · ${esc(s.time)} · ${esc(s.note)}</span></h2>
          <span class="num"><b>${s.band !== null ? s.band : "–"}</b> <span class="muted small">밴드</span></span>
        </div>
        <div class="table-wrap" style="margin-top:8px">
        <table>
          <thead><tr><th>과제</th><th class="r">문제</th><th class="r">내 기록</th><th>연습</th></tr></thead>
          <tbody>
          ${s.tasks.map(t => `<tr>
            <td><b>${esc(t.name)}</b> <span class="muted small">${esc(t.en)}</span><div class="small muted">${esc(t.desc)}</div></td>
            <td class="r num">${t.total}</td>
            <td class="r num small">${statText(t)}</td>
            <td>
              <form class="row" method="get" action="toefl-practice.html">
                <input type="hidden" name="task" value="${t.key}">
                <select name="level" aria-label="난이도">
                  <option value="">모든 난이도</option>
                  ${[1, 2, 3, 4, 5].map(lv => `<option value="${lv}" ${lv === recLevel ? "selected" : ""}>밴드 ${T.LEVEL_BAND[lv]} (${T.count(t.key, lv)})</option>`).join("")}
                </select>
                <button class="btn small primary" ${t.total ? "" : "disabled"}>풀기</button>
              </form>
            </td></tr>`).join("")}
          </tbody>
        </table>
        </div>
      </div>`).join("")}

      ${recent.length ? `<div class="card" style="margin-top:14px">
        <h2>최근 기록</h2>
        <table class="small"><tbody>
        ${recent.map(r => `<tr><td>${esc(r.at.slice(0, 16).replace("T", " "))}</td><td>${esc(T.TASKS[r.task].name)}</td><td>밴드 ${T.LEVEL_BAND[r.level]}</td>
          <td class="r num">${T.TASKS[r.task].auto ? Math.round(r.a * 100) + "%" : (Math.round(r.a * 5 * 10) / 10) + "/5"}</td></tr>`).join("")}
        </tbody></table>
      </div>` : ""}
      <p class="small muted" style="margin-top:12px">시험 형식은 2026년 1월 개편(ETS) 기준입니다. 실제 시험은 읽기·듣기가 적응형이라 앞 단계 결과에 따라 난이도가 바뀝니다.</p>`;

    $("target").onchange = e => { TSStore.setSettings({ toefl_target: e.target.value }); location.reload(); };      // 구역 강조(subNavSpy)가 옛 요소를 붙잡지 않도록 새로 읽는다
  }
  draw();
  $("app").addEventListener("click", ev => { const a = ev.target.closest("a[data-set]"); if (a) Ward.setSetting({ set: a.dataset.set }); });
});
