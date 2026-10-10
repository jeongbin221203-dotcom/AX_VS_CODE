/* 진단 테스트 — 등급을 고르게 섞은 짧은 세트(약 20분)로 현재 등급을 확인 (TS 앱 diagnostic.html) */
UI.boot({ exam: "toeic", page: "diagnostic" }, () => {
  const { esc, $ } = UI;
  const S = Score;
  const past = Stats.recentSessions(50).filter(s => s.mode === "diagnostic");
  const rows = Object.entries(S.DIAGNOSTIC_FORM).map(([part, per]) => `<tr><td>Part ${part} ${esc(S.PART_INFO[part].name)}</td>
    <td class="r num">${Object.values(per).reduce((a, b) => a + b, 0)}</td><td>${Object.keys(per).map(lv => UI.toeicBadge(Number(lv))).join(" ")}</td></tr>`).join("");
  $("app").innerHTML = `<div class="grid two">
    <div class="card"><h1>진단 테스트</h1>
      <p>지금 실력이 어느 등급인지 약 20분 동안 확인합니다. 결과로 나온 추정 점수가 '현재 점수'가 되고,
        등급 가이드·오늘 할 일·추천 난이도가 그 등급에 맞춰집니다.</p>
      <table class="small"><thead><tr><th>구성</th><th class="r">문항(약)</th><th>난이도</th></tr></thead><tbody>${rows}</tbody></table>
      <p class="small muted" style="margin-top:10px">여러 등급 문제를 섞어 냅니다. 어려운 문제를 맞히면 점수에 더 크게 반영됩니다.
        문항 수가 적어 오차가 크니, 2주 뒤에는 하프 모의고사로 다시 확인하세요.</p>
      <div id="err"></div><button class="btn primary" id="start">진단 시작</button></div>
    <div class="card"><h2>지난 진단</h2>${past.length ? `<table><tbody>${past.map(s => `<tr>
      <td><a href="result.html?sid=${s.id}">${esc(s.finished_at.slice(0, 10))}</a></td>
      <td class="r num"><b>${s.total_est}</b> <span class="muted small">LC ${s.lc_est} · RC ${s.rc_est}</span></td></tr>`).join("")}</tbody></table>`
      : `<div class="empty">아직 없습니다.</div>`}</div></div>`;
  $("start").addEventListener("click", async e => {
    e.target.disabled = true;                                  // 두 번 눌려 세션이 둘 만들어지지 않게
    try { UI.go(`solve.html?sid=${await Sessions.startDiagnostic()}`); }
    catch (err) { $("err").innerHTML = `<div class="flash error">${esc(err.message)}</div>`; e.target.disabled = false; }
  });
});
