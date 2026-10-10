/* 토플 기록 (toefl-history.html) — 최근 90일 날짜별·과제별 푼 문항과 평균, 모의고사 (TS 앱 templates/toefl/history.html) */
UI.boot({ exam: "toefl", page: "toefl-history" }, () => {
  const { esc, $ } = UI;
  const T = Toefl;
  const rows = T.history();
  const days = new Map();
  for (const r of rows) { if (!days.has(r.d)) days.set(r.d, []); days.get(r.d).push(r); }
  const bands = T.sectionBands();
  const mocks = T.listMocks(50);
  const val = x => (x !== null && x !== undefined ? x : "–");
  const dayList = [...days.entries()];
  $("app").innerHTML = `
    <div class="page-head">
      <div>
        <div class="muted small">최근 90일 · 날짜마다 과제별로 푼 문항 수와 평균 (쓰기·인터뷰는 자기 평가 5점 만점)</div>
        <h1>토플 기록</h1>
      </div>
      <a class="btn" href="toefl-review.html">오답노트</a>
    </div>
    <div class="grid four">
      ${Object.entries(T.SECTIONS).map(([k, s]) => `<div class="card kpi"><div class="label">${esc(s.ko)} <span class="muted">${esc(s.name)}</span></div>
        <div class="value">${val(bands[k])}</div><div class="sub">추정 밴드</div></div>`).join("")}
    </div>
    ${mocks.length ? `<div class="card" style="margin-top:14px">
      <h2>모의고사</h2>
      <table class="small"><thead><tr><th>날짜</th><th class="r">읽기</th><th class="r">듣기</th><th class="r">말하기</th><th class="r">쓰기</th><th class="r">종합</th><th></th></tr></thead><tbody>
      ${mocks.map(m => `<tr><td>${esc(m.created_at.slice(0, 16).replace("T", " "))}</td>
        ${[m.r, m.l, m.s, m.w, m.total].map(x => `<td class="r num">${val(x)}</td>`).join("")}
        <td class="r"><a href="toefl-mock-result.html?id=${m.id}">결과</a></td></tr>`).join("")}
      </tbody></table></div>` : ""}
    <div class="card" style="margin-top:14px">
      <h2>날짜별</h2>
      ${dayList.length ? "" : `<p class="muted">아직 기록이 없습니다.</p>`}
      ${dayList.map(([d, list], i) => `
      <details class="reveal-d" ${i < 3 ? "open" : ""}>
        <summary>${esc(d)} <span class="muted small">· ${list.reduce((a, r) => a + r.n, 0)}문항</span></summary>
        <table class="small"><tbody>
          ${list.map(r => { const t = T.TASKS[r.task]; return `<tr><td>${esc(T.SECTIONS[t.section].ko)}</td><td>${esc(t.name)}</td><td class="r num">${r.items}문제 · ${r.n}문항</td>
            <td class="r num"><b>${t.auto ? Math.round(r.a * 100) + "%" : (Math.round(r.a * 5 * 10) / 10) + "/5"}</b></td></tr>`; }).join("")}
        </tbody></table>
      </details>`).join("")}
    </div>`;
});
