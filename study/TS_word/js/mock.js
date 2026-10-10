/* 모의고사 — 실전(200문항, LC 자동 진행 + RC 75분) · 하프 · 미니, 안 푼 문제로 볼 수 있는 횟수, 지난 모의고사 (TS 앱 mock.html) */
UI.boot({ exam: "toeic", page: "mock" }, async () => {
  const { esc, $ } = UI;
  const S = Score;
  await Bank.load();                                           // 안 푼 문제 수를 세려면 모든 파트가 필요하다
  const forms = S.MOCK_FORMS, full = forms.full;
  const fresh = Sessions.freshMockCapacity("full");
  const ongoing = Stats.unfinishedSessions();
  const past = Stats.recentSessions(100).filter(s => s.mode === "mock");
  const selected = UI.params().get("form") || "full";

  const ongoingHtml = ongoing.length ? `<div class="card" style="margin-bottom:14px"><h2 style="margin:0 0 6px">이어서 풀기</h2><table class="small"><tbody>
    ${ongoing.map(o => `<tr><td><a href="solve.html?sid=${o.id}">${esc(Sessions.sessionTitle(o))}</a>
      <div class="muted">${esc(o.created_at.slice(5, 16).replace("T", " "))} 시작${o.answered ? ` · ${o.answered}문항 풂` : ""}</div></td>
      <td class="r"><a class="btn" href="solve.html?sid=${o.id}">이어서</a></td></tr>`).join("")}</tbody></table></div>` : "";

  const short = Object.entries(forms).filter(([k]) => k !== "full").map(([key, f]) => `<div class="card" ${selected === key ? 'style="border-color:var(--accent)"' : ""}>
      <div class="spread"><h2 style="margin:0">${esc(f.name)}</h2><button class="btn" data-start="${key}">시작</button></div>
      <p class="muted small" style="margin-top:6px">${esc(f.desc)} · 문제마다 원하는 때 듣고 넘어갈 수 있음</p>
      <table class="small"><tbody>
        <tr><td>LC</td><td class="r num">P1 ${f.p1} · P2 ${f.p2} · P3 ${f.p3} · P4 ${f.p4}</td></tr>
        <tr><td>RC</td><td class="r num">P5 ${f.p5} · P6 ${f.p6} · P7 ${f.p7_single}+${f.p7_double}이중+${f.p7_triple}삼중 · ${f.rc_minutes}분</td></tr>
      </tbody></table></div>`).join("");

  const pastHtml = past.length ? `<div class="table-wrap"><table><thead><tr><th>날짜</th><th>종류</th><th class="r">정답</th><th class="r">LC</th><th class="r">RC</th><th class="r">점수</th><th class="r">시간</th></tr></thead><tbody>
    ${past.map(s => `<tr><td><a href="result.html?sid=${s.id}">${esc(s.finished_at.slice(0, 16).replace("T", " "))}</a></td>
      <td>${esc((forms[s.variant] || {}).name || s.variant || "")}</td><td class="r num">${s.correct}/${s.total}</td>
      <td class="r num">${s.lc_est ?? "–"}</td><td class="r num">${s.rc_est ?? "–"}</td><td class="r num"><b>${s.total_est ?? "–"}</b></td>
      <td class="r num">${TSU.mmss(s.duration_sec)}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">아직 없습니다.</div>`;

  $("app").innerHTML = `${ongoingHtml}
    <div class="page-head"><div><h1>모의고사</h1>
      <div class="muted small">내 실제 위치를 알려면 <b>실전 모의고사</b>를 보세요. 등급을 고르지 않고 실제 시험과 같은 구성·진행으로 치릅니다.</div></div>
      <a class="btn" href="diagnostic.html">진단 테스트 (20분)</a></div>
    <div id="err"></div>
    <div class="card" ${selected === "full" ? "" : ""}>
      <div class="spread"><h2 style="margin:0">실전 모의고사 <span class="tag ok">실제 시험과 동일</span></h2>
        <button class="btn primary" data-start="full" data-confirm="약 2시간 동안 멈출 수 없습니다. 소리를 켜고 조용한 곳에서 시작하세요. 시작할까요?">시작 (약 2시간)</button></div>
      <div class="grid two" style="margin-top:12px"><div><table class="small"><tbody>
        <tr><td><b>Listening</b> 100문항 · 약 45분</td><td class="r num">Part 1 6 · Part 2 25 · Part 3 39 · Part 4 30</td></tr>
        <tr><td><b>Reading</b> 100문항 · 75분</td><td class="r num">Part 5 30 · Part 6 16 · Part 7 54 (단일 29 + 이중 2세트 + 삼중 3세트)</td></tr></tbody></table></div>
        <ul class="clean small">
          <li>LC는 음성에 맞춰 <b>자동으로 넘어갑니다</b>. 다시 듣기·되돌아가기·일시정지 없음. 문제마다 답할 시간은 Part 1·2 5초, Part 3·4 8초(표 문제 12초).</li>
          <li>Part 3·4는 실제처럼 문제를 읽어 주고, 마지막 세트들은 표를 보고 푸는 문제입니다.</li>
          <li>LC가 끝나면 RC 75분이 시작되고, RC 안에서는 자유롭게 이동할 수 있습니다. 시간이 끝나면 자동 제출.</li>
          <li>문제 등급은 보이지 않고, 쉬운 문제부터 어려운 문제까지 실제 시험 분포로 섞입니다.</li>
          <li>점수는 맞힌 개수(원점수)를 환산한 값입니다 (실제 환산표와 ±30점 정도 차이).</li></ul></div>
      <p class="small ${fresh.times >= 1 ? "muted" : ""}" style="margin:8px 0 0">${fresh.times >= 1
        ? `안 푼 문제만으로 볼 수 있는 실전 모의고사: <b>${fresh.times}회</b> (가장 부족한 구성: ${esc(fresh.limit)}).`
        : `<span class="tag bad">주의</span> 안 푼 ${esc(fresh.limit)} 문제가 모자라 이번 시험에는 전에 푼 문제가 섞입니다. 점수가 실제보다 높게 나올 수 있습니다.`}</p></div>
    <h2 style="margin-top:22px">짧은 연습 모의고사</h2><div class="grid two">${short}</div>
    <div class="card" style="margin-top:14px"><h2>지난 모의고사</h2>${pastHtml}</div>`;

  $("app").addEventListener("click", async e => {
    const b = e.target.closest("[data-start]");
    if (!b) return;
    if (b.dataset.confirm && !confirm(b.dataset.confirm)) return;
    b.disabled = true;
    try { UI.go(`solve.html?sid=${await Sessions.startMock(b.dataset.start)}`); }
    catch (err) { $("err").innerHTML = `<div class="flash error">${esc(err.message)}</div>`; b.disabled = false; }
  });
});
