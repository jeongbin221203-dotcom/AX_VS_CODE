/* 토플 모의고사 결과 (toefl-mock-result.html?id=N) — 종합·영역별 밴드와 과제별 결과 (TS 앱 templates/toefl/mock_result.html) */
(function () {
  const id = UI.intParam("id");
  UI.boot({ exam: "toefl", page: "toefl-mock-result" }, () => {
    const { esc, $ } = UI;
    const T = Toefl;
    const m = id !== null ? T.getMock(id) : null;
    if (!m || !m.finished_at || !m.result) {
      $("app").innerHTML = `<div class="card empty"><h2>결과를 찾을 수 없습니다</h2><p>끝나지 않았거나 지워진 모의고사입니다.</p><a class="btn primary" href="toefl-mock.html">모의고사 화면으로</a></div>`;
      return;
    }
    const r = m.result;
    const target = T.targetBand();
    const val = T.fmtBand;
    $("app").innerHTML = `
      <div class="page-head">
        <div><div class="muted small">${esc(m.finished_at.slice(0, 16).replace("T", " "))}${r.duration_sec ? " · " + UI.mmss(r.duration_sec) : ""}</div>
          <h1>토플 모의고사 결과</h1></div>
        <div class="row"><a class="btn" href="toefl-mock.html">다시 보기</a><a class="btn primary" href="toefl.html">토플 홈</a></div>
      </div>
      <div class="grid four">
        <div class="card kpi">
          <div class="label">종합 밴드</div>
          <div class="value">${val(r.total)}</div>
          <div class="sub">${r.total ? `${T.cefr(r.total)} · 예전 점수 약 ${T.BAND_OLD[r.total]} · 목표 ${T.fmtBand(target)}` : ""}</div>
        </div>
        ${["R", "L", "W", "S"].map(k => {
          const b = r.bands[k];
          return `<div class="card kpi">
            <div class="label">${esc(T.SECTIONS[k].name)}</div>
            <div class="value">${val(b)}</div>
            <div class="sub">${esc(T.cefr(b))}${(r.routes || {})[k] ? ` · 2모듈 ${r.routes[k] === "hard" ? "어려움" : "쉬움"}` : ""}</div>
          </div>`;
        }).join("")}
      </div>
      <div class="card" style="margin-top:14px">
        <h2>과제별</h2>
        <div class="table-wrap"><table>
          <thead><tr><th>영역</th><th>과제</th><th>난이도</th><th class="r">결과</th></tr></thead>
          <tbody>
          ${r.detail.map(d => {
            const t = T.TASKS[d.task];
            return `<tr><td>${esc(T.SECTIONS[t.section].name)}</td><td>${esc(t.name)}</td><td>밴드 ${T.LEVEL_BAND[d.level]}</td>
              <td class="r num">${T.scoreText(d.task, d.avg, " / ")}
                ${t.auto && d.n > 1 ? `<span class="muted small">(${d.n}문항)</span>` : ""}</td></tr>`;
          }).join("")}
          </tbody>
        </table></div>
        <p class="small muted" style="margin-top:8px">밴드는 난이도별 정답률(65% 이상이면 그 밴드)과 쓰기·말하기 자기 평가로 계산한 추정치입니다. 실제 점수와 다를 수 있습니다.
          이 시험의 풀이 기록은 토플 홈의 영역별 추정 밴드에도 반영됩니다.</p>
      </div>`;
  });
})();
