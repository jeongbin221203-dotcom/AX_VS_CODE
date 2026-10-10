/* 토플 실전 모의고사 안내·시작 (toefl-mock.html) — 구성 설명, 시작(계획 만들기), 지난 모의고사 (TS 앱 templates/toefl/mock.html) */
UI.boot({ exam: "toefl", page: "toefl-mock" }, () => {
  const { esc, $ } = UI;
  const T = Toefl;
  const target = T.targetBand();
  const cut = Math.trunc(T.ADAPT_CUT * 100);
  const past = T.listMocks();
  const ongoing = T.unfinishedMocks();
  // 끝났거나 없어진 모의고사의 영역별 임시 저장(ts-tmock-N)을 정리
  try {
    const live = new Set(ongoing.map(m => m.id));
    for (const k of Object.keys(localStorage)) {
      const m = /^ts-tmock-(\d+)$/.exec(k);
      if (m && !live.has(Number(m[1]))) localStorage.removeItem(k);
    }
  } catch (e) { /* 저장소 없음 */ }
  const saved = id => { try { return JSON.parse(localStorage.getItem("ts-tmock-" + id) || "null"); } catch (e) { return null; } };
  const ongoingRows = ongoing.map(m => ({ m, s: saved(m.id) })).filter(x => x.s && Array.isArray(x.s.done) && x.s.done.length);
  const val = x => (x !== null && x !== undefined ? x : "–");

  $("app").innerHTML = `
    <div class="page-head">
      <div><div class="muted small">TOEFL iBT · 2026년 1월 개편 형식</div><h1>실전 모의고사</h1></div>
      <a class="btn" href="toefl.html">토플 홈</a>
    </div>
    ${ongoingRows.length ? `<div class="card" style="margin-bottom:14px"><h2 style="margin:0 0 6px">이어서 보기</h2>
      <table class="small"><tbody>${ongoingRows.map(({ m, s }) => `<tr><td>${esc(m.created_at.slice(5, 16).replace("T", " "))} 시작
        <div class="muted">끝낸 영역: ${esc(s.done.map(k => T.SECTIONS[k].name).join(", "))}</div></td>
        <td class="r"><a class="btn" href="toefl-mock-run.html?id=${m.id}">이어서</a></td></tr>`).join("")}</tbody></table></div>` : ""}
    <div class="card">
      <div class="spread">
        <h2 style="margin:0">네 영역을 순서대로 · 약 75분</h2>
        <button class="btn primary" id="go">시작</button>
      </div>
      <div class="table-wrap" style="margin-top:12px">
      <table>
        <thead><tr><th>순서</th><th>영역</th><th>구성</th><th>시간·방식</th></tr></thead>
        <tbody>
          <tr><td>1</td><td><b>Reading</b></td><td>모듈마다 빈칸 단어 1 · 일상 글 2 · 학술 지문 1</td>
            <td class="small">모듈당 15분 · 2단계 적응형 · 모듈 안에서는 앞뒤 이동 가능</td></tr>
          <tr><td>2</td><td><b>Listening</b></td><td>모듈마다 응답 고르기 6 · 대화 1 · 안내 1 · 강의 1</td>
            <td class="small">모듈당 14분 · 2단계 적응형 · 음성은 한 번만, 되돌아가기 없음</td></tr>
          <tr><td>3</td><td><b>Writing</b></td><td>문장 만들기 10 · 이메일 1 · 학술 토론 1</td>
            <td class="small">약 23분 · 이메일 7분 · 토론 10분</td></tr>
          <tr><td>4</td><td><b>Speaking</b></td><td>듣고 따라 말하기 7문장 · 인터뷰 4문항</td>
            <td class="small">약 8분 · 인터뷰 45초씩 녹음</td></tr>
        </tbody>
      </table>
      </div>
      <ul class="clean small" style="margin-top:10px">
        <li><b>적응형:</b> 읽기·듣기 1모듈(밴드 4 난이도)에서 ${cut}% 이상 맞히면 2모듈은 어려운 문제(밴드 5~6), 아니면 쉬운 문제(밴드 2~3)가 나옵니다.</li>
        <li>시험 중에는 정답을 보여 주지 않습니다. 끝나면 쓰기·인터뷰 답을 모범 답안과 비교해 스스로 채점하고, 영역별 밴드와 종합 밴드가 나옵니다.</li>
        <li>말하기·쓰기 난이도는 목표 밴드(${target}, ${T.CEFR[Math.trunc(target)]}) 근처로 고릅니다. 목표는 토플 홈에서 바꿀 수 있습니다.</li>
        <li>끝낸 영역은 이 브라우저에 저장됩니다. 새로고침하거나 창을 닫아도 <b>보던 영역만</b> 처음부터 다시 보면 됩니다. 마이크는 https 주소나 이 PC(127.0.0.1·localhost)에서만 켜집니다.</li>
      </ul>
    </div>
    <div class="card" style="margin-top:14px">
      <h2>지난 모의고사</h2>
      ${past.length ? `<div class="table-wrap"><table>
        <thead><tr><th>날짜</th><th class="r">Reading</th><th class="r">Listening</th><th class="r">Speaking</th><th class="r">Writing</th><th class="r">종합</th></tr></thead>
        <tbody>${past.map(p => `<tr><td><a href="toefl-mock-result.html?id=${p.id}">${esc(p.finished_at.slice(0, 16).replace("T", " "))}</a></td>
          <td class="r num">${val(p.r)}</td><td class="r num">${val(p.l)}</td><td class="r num">${val(p.s)}</td>
          <td class="r num">${val(p.w)}</td><td class="r num"><b>${val(p.total)}</b></td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">아직 없습니다.</div>`}
    </div>`;

  $("go").onclick = async () => {
    if (!confirm("약 75분 동안 이어서 봅니다. 소리를 켜고, 마이크를 허용할 수 있는 곳에서 시작하세요. 시작할까요?")) return;
    $("go").disabled = true;
    try {
      await T.load();                                       // 모의고사에 쓸 문제를 고르려면 모든 과제의 데이터가 필요하다
      for (const m of ongoing) if (!saved(m.id)) T.deleteMock(m.id);       // 한 영역도 못 끝낸 옛 시도는 정리
      UI.go(`toefl-mock-run.html?id=${T.createMock(target)}`);
    } catch (e) { $("go").disabled = false; alert("시작할 수 없습니다: " + e.message); }
  };
});
