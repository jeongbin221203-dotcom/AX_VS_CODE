/* 토익스피킹·오픽의 목록·안내 화면들 — TS 앱 templates/speaking/*.html (run.html 은 spk-run.js).
   화면 종류는 <body data-spk="…">:
     toeic-speaking  토익스피킹 홈          tsp-guide        답변 틀·채점 기준      tsp-mock       모의고사 시작
     opic            오픽 홈                opic-survey      설문·난이도            opic-guide     답변 틀·등급 기준     opic-mock  모의고사 시작
     tsp-mock-result / opic-mock-result     모의고사 결과 (?mid=)
     speaking-history                       답변 기록 (?exam=toeic|opic&page=) */
(function () {
  "use strict";
  const { esc, $ } = UI;
  const S = Spk;
  const kind = document.body.dataset.spk;
  const r0 = x => TSU.pyRound(x);                                             // jinja |round|int (파이썬 반올림)
  const r1 = x => (TSU.pyRound(x * 10) / 10).toFixed(1);                      // jinja |round(1)
  const when = s => String(s || "").slice(0, 16).replace("T", " ");
  const kpi = (label, value, sub) => `<div class="card kpi"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div><div class="sub">${esc(sub || "")}</div></div>`;
  const head = (small, title, buttons) => `<div class="page-head"><div><div class="muted small">${esc(small)}</div><h1>${esc(title)}</h1></div><div class="row">${buttons || ""}</div></div>`;

  function trendCard(rows, opic, titleExtra) {
    if (!rows.length) return "";
    return `<div class="card" style="margin-top:14px"><h2>날짜별 변화 <span class="small muted">${titleExtra || "최근 60일 · 자기 채점 평균과 답변 단어 수"}</span></h2>
      <table class="small"><thead><tr><th>날짜</th><th class="r">답변</th><th class="r">${titleExtra ? "자기 채점 평균" : "평균"}</th><th class="r">평균 단어</th></tr></thead><tbody>
      ${rows.slice().reverse().map(d => `<tr><td>${d.d}</td><td class="r num">${d.n}</td><td class="r num">${opic ? r1(d.r * 5) + "/5" : r0(d.r * 100) + "%"}</td><td class="r num">${d.w !== null ? r0(d.w) : "–"}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  // ========================================================================= 토익스피킹
  async function tspHome() {
    await S.loadTsp();
    const draw = () => {
      const stats = S.tspTaskStats();
      const est = S.tspEstimate(stats);
      const level = S.tspLevel(est);
      const target = S.tspTarget();
      const tl = S.tspLevel(target);
      const mocks = S.listMocks("tsp", 5);
      const tasks = S.TSP_ORDER.map(t => ({ key: t, ...S.TSP_TASKS[t], count: S.tspItems(t).length, stat: stats[t], weak: Object.keys(S.weakItems("tsp", "tsp:" + t)).length }));
      const answers = tasks.reduce((a, t) => a + (t.stat ? t.stat.n : 0), 0);
      const recent = S.recent("tsp", 12);
      $("app").innerHTML = `
        <div class="page-head"><div><div class="muted small">${esc(TSNav.EXAMS["toeic-speaking"].summary)}</div><h1>토익스피킹</h1></div>
          <div class="row"><a class="btn primary" href="tsp-mock.html">실전 모의고사</a><a class="btn" href="tsp-guide.html">답변 틀·채점 기준</a>
            <label class="small">목표 <select id="tsp-target">${S.TSP_TARGETS.map(t => `<option value="${t}" ${target === t ? "selected" : ""}>${t}점</option>`).join("")}</select></label></div></div>
        <div class="grid four">
          ${kpi("추정 점수", est !== null ? est : "–", est !== null ? level[1] : "다섯 유형을 모두 연습하면 계산됩니다")}
          ${kpi("목표", target, tl[1] + (est !== null ? " · " + (est >= target ? "달성" : (target - est) + "점 남음") : ""))}
          ${kpi("최근 모의고사", mocks.length ? mocks[0].score : "–", mocks.length ? mocks[0].created_at.slice(0, 10) : "아직 없음")}
          ${kpi("답변 기록", answers, "문항 답변 수 (연습 + 모의고사)")}
        </div>
        <div class="card" style="margin-top:14px"><div class="spread"><h2 style="margin:0">레벨 표</h2><span class="small muted">점수는 스스로 채점한 결과로 추정합니다 (ETS 공식 환산 아님)</span></div>
          <div class="ladder" style="grid-template-columns:repeat(auto-fill,minmax(110px,1fr));margin-top:10px">
            ${S.TSP_LEVELS.map(([lv, name, low]) => `<div class="step${tl && tl[0] === lv ? " target" : ""}" style="${level && level[0] === lv ? "outline:2px solid var(--accent)" : ""}"><b>${esc(name)}</b>${low}${lv === 8 ? "+" : "~"}</div>`).join("")}
          </div></div>
        <div class="card" style="margin-top:14px" id="tasks"><h2>유형별 연습</h2><div class="table-wrap"><table>
          <thead><tr><th>문항</th><th>유형</th><th class="r">문제</th><th class="r">내 기록</th><th>연습</th></tr></thead><tbody>
          ${tasks.map(t => `<tr><td class="num"><b>${t.q}</b></td>
            <td><b>${esc(t.name)}</b> <span class="muted small">${esc(t.en)}</span><div class="small muted">${esc(t.desc)}</div>
              <div class="small muted">준비 ${t.prep}초 · 답변 ${t.speak.join("/")}초 · 문항당 0~${t.max}점</div></td>
            <td class="r num">${t.count}</td>
            <td class="r num small">${t.stat ? `${t.stat.n}회 · ${r0(t.stat.ratio * 100)}%${t.stat.words ? `<div class="muted">평균 ${r0(t.stat.words)}단어</div>` : ""}` : "–"}</td>
            <td class="row" style="flex-wrap:nowrap">${t.count ? `<a class="btn small primary" href="tsp-practice.html?task=${t.key}">연습</a>` : `<span class="btn small primary" aria-disabled="true" style="opacity:.45">연습</span>`}
              ${t.weak ? `<a class="btn small" href="tsp-practice.html?task=${t.key}&weak=1&n=${Math.min(t.weak, 10)}" title="마지막 자기 채점이 60% 미만">약한 문항 ${t.weak}</a>` : ""}</td></tr>`).join("")}
          </tbody></table></div></div>
        ${mocks.length ? `<div class="card" style="margin-top:14px"><h2>모의고사 기록</h2><table class="small"><tbody>
          ${mocks.map(m => `<tr><td>${when(m.created_at)}</td><td><b>${m.score}점</b></td><td>${m.result.level ? esc(m.result.level[1]) : ""}</td><td class="r"><a href="tsp-mock-result.html?mid=${m.id}">결과 보기</a></td></tr>`).join("")}
          </tbody></table></div>` : ""}
        ${trendCard(S.trend("tsp"), false)}
        ${recent.length ? `<div class="card" style="margin-top:14px"><h2>최근 답변</h2><table class="small"><tbody>
          ${recent.map(r => { const t = r.task.split(":").pop(); return `<tr><td>${esc(r.created_at.slice(5, 16).replace("T", " "))}</td><td>${esc(t in S.TSP_TASKS ? S.TSP_TASKS[t].name : t)}${r.mock_id ? " · 모의고사" : ""}</td>
            <td class="r num">${r1(r.points)}/${r.max_points}</td><td class="r num muted">${r.words !== null && r.words !== undefined ? r.words + "단어" : ""}${r.accuracy !== null && r.accuracy !== undefined ? " · " + r0(r.accuracy * 100) + "%" : ""}</td></tr>`; }).join("")}
          </tbody></table></div>` : ""}
        <p class="small muted" style="margin-top:12px">시험 형식은 2022년 6월 개편 기준입니다. 문제는 모두 이 앱용으로 새로 쓴 것이며, 사진 묘사는 사진 대신 장면 설명으로 연습합니다.
          <a href="speaking-history.html?exam=toeic">답변 기록 전체 보기</a></p>`;
      $("tsp-target").addEventListener("change", ev => {
        if (S.TSP_TARGETS.includes(Number(ev.target.value))) { TSStore.setSettings({ tsp_target: ev.target.value }); draw(); }
      });
    };
    draw();
  }

  function tspGuide() {
    const target = S.tspTarget();
    const hl = on => (on ? ` style="background:var(--accent-soft)"` : "");
    $("app").innerHTML = `${head("TOEIC Speaking · 유형별 답변 틀 · 채점 기준 · 목표별 학습 경로", "답변 틀·채점 기준", `<a class="btn" href="toeic-speaking.html">토익스피킹 홈</a>`)}
      <div class="card"><h2>목표 점수별 학습 경로 <span class="small muted">지금 목표: ${target}점</span></h2>
        <div class="table-wrap"><table><thead><tr><th>목표</th><th>핵심</th><th>연습 순서</th></tr></thead><tbody>
          <tr${hl(target <= 130)}><td><b>IM2~IM3</b><div class="small muted">120~130</div></td>
            <td>모든 문항에 <b>침묵 없이</b> 완전한 문장으로 답한다. 틀을 외워 그대로 쓰는 것으로 충분.</td>
            <td class="small">① Q5~7 틀 외우기 → ② Q8~10 표 읽는 법 → ③ Q11 이유 1개 + 예시 1개 → ④ 모의고사 주 1회</td></tr>
          <tr${hl(target >= 140 && target <= 150)}><td><b>IH</b><div class="small muted">140~150</div></td>
            <td>Q7·Q11에서 <b>이유와 구체적 예시</b>, Q3~4에서 사람·사물·배경을 고르게. 시간을 꽉 채운다.</td>
            <td class="small">① Q11 이유 2개 구조 → ② Q3~4 위치 표현 8개 → ③ Q1~2 강세·끊어 읽기 → ④ 녹음 듣고 단어 수 늘리기 (Q11 100단어 이상)</td></tr>
          <tr${hl(target >= 160)}><td><b>AL 이상</b><div class="small muted">160~</div></td>
            <td>틀이 드러나지 않게 <b>자연스러운 연결어·다양한 문장 구조</b>, 문법 실수 최소화, 억양까지.</td>
            <td class="small">① 고득점 모범 답안 따라 말하기(섀도잉) → ② Q11 반론 인정 후 재반박 → ③ 관계사·분사구문 넣기 → ④ 분당 120~150단어 유지</td></tr>
        </tbody></table></div></div>
      <div class="card" style="margin-top:14px"><h2>유형별 답변 틀</h2>
        <h3>Q1~2 문장 읽기 <span class="small muted">준비 45초 · 읽기 45초</span></h3>
        <ul class="clean small">
          <li>준비 시간에 <b>소리 내어</b> 한 번 읽는다. 고유명사·숫자 발음을 먼저 정한다.</li>
          <li>나열 <b>A ↗, B ↗, and C ↘</b> · Yes/No 의문문 ↗ · Wh- 의문문 ↘ · 쉼표·마침표에서 끊기.</li>
          <li>틀려도 다시 읽지 말고 이어간다. 너무 빨리 읽지 않는다 (45초 안에 여유 있게 끝남).</li></ul>
        <h3>Q3~4 사진 묘사 <span class="small muted">준비 45초 · 답변 30초</span></h3>
        <div class="frame">This picture was taken at/in [장소].
The first thing I see is [가장 눈에 띄는 사람], who is [동작 -ing].
Next to him/her, [다른 사람] is [동작].
On the left / On the right side, there is/are [사물].
In the background, I can see [배경].
It looks like [느낌·추측].</div>
        <h3>Q5~7 질문에 답하기 <span class="small muted">준비 3초 · 15/15/30초</span></h3>
        <div class="frame">Q5·Q6 (15초): 질문의 단어를 그대로 살려 첫 문장 + 덧붙임 한 문장
  "The last time I went to a movie theater was last Saturday, and I went with my sister."
Q7 (30초): 답 → 이유 1 → 이유 2/예시 → 마무리
  "I'd prefer A. First, ... Also, ... For example, ... So, I think A is better for me."</div>
        <h3>Q8~10 표 보고 답하기 <span class="small muted">표 45초 · 15/15/30초</span></h3>
        <div class="frame">45초 동안: 제목·날짜·장소 → 시간순 항목 → 취소·변경·별도 비용 같은 '함정' 확인
Q8: "The [행사] will be held on [날짜] at [장소], and it starts at [시간]."
Q9: "I'm sorry, but you have the wrong information. Actually, ..." (잘못 안 정보 바로잡기)
Q10: "There are two [항목]. First, at [시간], ... will [내용]. After that, at [시간], ..."</div>
        <h3>Q11 의견 말하기 <span class="small muted">준비 45초 · 60초</span></h3>
        <div class="frame">의견:  I agree that ... / I prefer ... for two reasons.
이유 1: First, ...
예시:   For example, when I ..., I was able to ...
이유 2: Also / On top of that, ...
마무리: For these reasons, I believe ...</div>
        <p class="small muted">준비 45초에는 문장을 쓰지 말고 키워드(의견·이유·예시)만 정한다. 예시는 '내 경험'이 가장 말하기 쉽다.</p></div>
      <div class="card" style="margin-top:14px"><h2>채점 기준 (자기 채점용)</h2>
        ${S.TSP_ORDER.map(t => { const v = S.TSP_TASKS[t]; return `<h3>${v.q} ${esc(v.name)} <span class="small muted">0~${v.max}점</span></h3>
          <table class="small"><tbody>${S.TSP_RUBRIC[t].map(([p, d]) => `<tr><td class="num"><b>${p}</b></td><td>${esc(d)}</td></tr>`).join("")}</tbody></table>`; }).join("")}
        <p class="small muted">ETS가 공개한 채점 기준을 학습용으로 풀어 쓴 것입니다. 점수 환산(문항 합 35점 → 200점)은 공식 환산표가 공개되지 않아 비례로 계산한 추정치입니다.</p></div>
      <div class="card" style="margin-top:14px"><h2>녹음 다시 들을 때 체크리스트</h2><ul class="clean">
        <li><b>발음</b>: r/l, f/p, v/b, th — 음성 인식이 틀리게 받아 적은 단어가 의심 단어</li>
        <li><b>유창성</b>: 3초 넘는 침묵이 있었나? "어…" 대신 "Well," "Let me see," 로 채웠나?</li>
        <li><b>문법</b>: 3인칭 단수 -s, 시제(과거 경험은 과거형), 관사 a/the</li>
        <li><b>내용</b>: 질문에 정확히 답했나? 이유·예시가 구체적인가?</li>
        <li><b>시간</b>: 답변 시간을 거의 다 썼나? (분당 단어 수가 100보다 낮으면 말이 느리거나 멈춤이 많은 것)</li></ul></div>
      <div class="card" style="margin-top:14px"><h2>레벨 표</h2><table class="small"><tbody>
        ${S.TSP_LEVELS.map(([lv, name, low]) => `<tr><td><b>${esc(name)}</b></td><td class="r muted">${low}${lv === 8 ? "" : "~"}</td></tr>`).join("")}</tbody></table></div>`;
  }

  /** 모의고사 시작 화면 공통: 지난 모의고사 목록 */
  function pastList(exam) {
    const past = S.listMocks(exam);
    if (!past.length) return "";
    return `<div class="card" style="margin-top:14px"><h2>지난 모의고사</h2><table class="small"><tbody>
      ${past.map(m => `<tr><td>${when(m.created_at)}</td><td><b>${esc(m.score || "–")}${exam === "tsp" ? "점" : ""}</b></td>
        <td class="muted">${m.result.answered}/${m.result.total}문항${m.result.words ? ` · 평균 ${r0(m.result.words)}단어` : ""}</td>
        <td class="r"><a href="${exam}-mock-result.html?mid=${m.id}">결과</a></td></tr>`).join("")}</tbody></table></div>`;
  }
  async function tspMock() {
    await S.loadTsp();
    const e = TSNav.EXAMS["toeic-speaking"];
    $("app").innerHTML = `<div class="page-head"><div><div class="muted small">${esc(e.en)}</div><h1>${esc(e.name)} 실전 모의고사</h1><div class="muted small">${esc(e.summary)}</div></div>
        <a class="btn" href="toeic-speaking.html">${esc(e.name)} 홈</a></div>
      <div class="grid two"><div class="card"><h2>시험 구성</h2><table><tbody>
        ${S.TSP_ORDER.map(t => { const v = S.TSP_TASKS[t]; return `<tr><td class="num"><b>${v.q}</b></td><td>${esc(v.name)} <span class="small muted">${esc(v.en)}</span>
          <div class="small muted">준비 ${v.prep}초 · 답변 ${v.speak.join("/")}초</div></td></tr>`; }).join("")}</tbody></table>
        <p class="small muted">안 풀어 본 문제부터 고릅니다. 시험 중에는 준비·답변 시간을 건너뛸 수 없습니다.</p></div>
        <div class="card"><h2>시작하기</h2>
          <ul class="clean small" style="margin:10px 0"><li>조용한 곳에서 이어폰을 끼고 보세요. 마이크 권한을 허용해야 녹음됩니다.</li><li>끝나면 녹음을 다시 들으며 문항마다 스스로 채점합니다.</li></ul>
          <button class="btn primary" id="make">모의고사 만들기</button></div></div>${pastList("tsp")}`;
    $("make").addEventListener("click", () => {
      const plan = S.tspMockPlan();
      if (plan.reduce((a, u) => a + u.steps.length, 0) < 11) { UI.flash("문제가 아직 모자라 모의고사를 만들 수 없습니다.", "error"); return; }
      UI.go(`tsp-mock-run.html?mid=${S.createMock("tsp", plan, { target: S.tspTarget() })}`);
    });
  }

  // ========================================================================= 오픽
  async function opicHome() {
    await S.loadOpic();
    const saved = UI.params().get("saved");
    if (saved === "survey") UI.flash("설문을 저장했습니다. 모의고사와 추천 연습에 이 주제가 나옵니다.", "ok");
    const st = S.opicSettings();
    const stats = S.opicStats();
    const counts = S.topicCounts();
    const done = {};
    for (const r of S.topicProgress()) { const it = S.item(r.task, r.item_id); if (it) (done[it.topic] = done[it.topic] || []).push(r.points); }
    const rows = Object.entries(S.OPIC_TOPICS).filter(([k]) => k !== "intro").map(([key, [name, group, isSurvey]]) => {
      const c = counts[key] || {}, pts = done[key] || [];
      return { key, name, group, survey: isSurvey, picked: st.survey.includes(key), counts: c, total: Object.values(c).reduce((a, b) => a + b, 0),
               n: pts.length, avg: pts.length ? pts.reduce((a, b) => a + b, 0) / pts.length : null };
    });
    const mocks = S.listMocks("opic", 5);
    const recent = S.recent("opic", 12);
    const weakN = Object.keys(S.weakItems("opic", "opic_q")).length;
    const introN = S.opicQ().filter(q => q.kind === "intro").length, rpN = S.opicRp().length;
    const btn = (href, label, cls, on = true) => (on ? `<a class="btn small ${cls || ""}" href="${href}">${esc(label)}</a>` : `<span class="btn small ${cls || ""}" style="opacity:.45">${esc(label)}</span>`);
    $("app").innerHTML = `
      <div class="page-head"><div><div class="muted small">${esc(TSNav.EXAMS.opic.summary)}</div><h1>오픽</h1></div>
        <div class="row"><a class="btn primary" href="opic-mock.html">실전 모의고사</a><a class="btn" href="opic-survey.html">설문·난이도</a><a class="btn" href="opic-guide.html">답변 틀·등급 기준</a></div></div>
      <div class="grid four">
        ${kpi("추정 등급", stats.grade || "–", stats.grade ? S.OPIC_GRADE_NAME[stats.grade] : "답변을 5개 이상 채점하면 계산됩니다")}
        ${kpi("목표 등급", st.target, S.OPIC_GRADE_NAME[st.target])}
        ${kpi("설문 난이도", st.level, S.OPIC_LEVELS[st.level])}
        ${kpi("답변 기록", stats.n, stats.words ? `최근 평균 ${r0(stats.words)}단어 / 답변` : "음성 인식 단어 수 기록 없음")}
      </div>
      <div class="card" style="margin-top:14px"><div class="spread"><h2 style="margin:0">등급 표</h2><span class="small muted">등급은 스스로 채점한 결과와 답변 길이로 추정합니다 (ACTFL 공식 판정 아님)</span></div>
        <div class="ladder" style="grid-template-columns:repeat(9,1fr);margin-top:10px">
          ${S.OPIC_GRADES.map(g => `<div class="step${st.target === g ? " target" : ""}" style="${stats.grade === g ? "outline:2px solid var(--accent)" : ""}"><b>${g}</b></div>`).join("")}</div></div>
      <div class="grid two" style="margin-top:14px">
        <div class="card"><div class="spread"><h2 style="margin:0">내 설문 주제</h2><a class="small" href="opic-survey.html">바꾸기</a></div>
          ${st.survey.length ? `<p>${st.survey.map(t => `<a class="tag" href="opic-practice.html?topic=${t}" style="margin:2px">${esc(S.OPIC_TOPICS[t][0])}</a>`).join("")}</p>
            <p class="small muted">모의고사의 설문 문항(2~7번, 14~15번)은 이 주제에서 나옵니다. 거주(사는 곳)는 누구나 받습니다.</p>`
            : `<div class="flash error">아직 설문을 하지 않았습니다. <a href="opic-survey.html">설문에서 주제를 고르세요</a> — 고르지 않으면 모든 설문 주제에서 나옵니다.</div>`}</div>
        <div class="card"><h2>빠른 연습</h2><div class="row" style="flex-wrap:wrap">
          ${btn("opic-practice.html?kind=intro&n=1", "자기소개", "", introN > 0)}
          ${["describe", "routine", "past", "compare", "issue"].map(k => btn(`opic-practice.html?kind=${k}&n=3`, `${S.OPIC_KINDS[k]} 3문항`)).join("")}
          ${btn("opic-practice.html?kind=roleplay&n=1", "롤플레이 1세트", "primary", rpN > 0)}
          ${weakN ? `<a class="btn small" href="opic-practice.html?weak=1&n=${Math.min(weakN, 10)}" title="마지막 자기 채점이 3점 이하">약한 문항 ${weakN}개 다시</a>` : ""}</div>
          <p class="small muted" style="margin-top:8px">실제 시험은 질문이 소리로만 나옵니다. 연습 화면에서 <b>질문 글자 보기</b>를 끄면 실전처럼 연습할 수 있습니다.</p></div></div>
      <div class="card" style="margin-top:14px" id="topics"><h2>주제별 연습</h2><div class="table-wrap"><table>
        <thead><tr><th>주제</th><th class="r">묘사</th><th class="r">습관</th><th class="r">경험</th><th class="r">비교</th><th class="r">이슈</th><th class="r">롤플레이</th><th class="r">내 기록</th><th>연습</th></tr></thead><tbody>
        ${["거주", "여가", "취미", "운동", "휴가", "돌발"].map(group => `<tr><td colspan="9" class="small muted"><b>${group}</b>${group === "돌발" ? " (설문 없이 나오는 주제)" : ""}</td></tr>` +
          rows.filter(t => t.group === group).map(t => `<tr><td><b>${esc(t.name)}</b>${t.picked ? ` <span class="tag ok">설문</span>` : ""}</td>
            ${["describe", "routine", "past", "compare", "issue", "roleplay"].map(k => `<td class="r num small">${t.counts[k] || "–"}</td>`).join("")}
            <td class="r num small">${t.n ? `${t.n}회 · ${r1(t.avg)}/5` : "–"}</td>
            <td class="row" style="flex-wrap:nowrap">${t.total ? `<a class="btn small primary" href="opic-practice.html?topic=${t.key}">콤보</a>` : `<span class="btn small primary" style="opacity:.45">콤보</span>`}
              ${t.counts.roleplay ? `<a class="btn small" href="opic-practice.html?topic=${t.key}&kind=roleplay">롤플레이</a>` : ""}</td></tr>`).join("")).join("")}
        </tbody></table></div></div>
      ${mocks.length ? `<div class="card" style="margin-top:14px"><h2>모의고사 기록</h2><table class="small"><tbody>
        ${mocks.map(m => `<tr><td>${when(m.created_at)}</td><td><b>${esc(m.score || "–")}</b></td><td>난이도 ${m.result.level ?? "–"}</td><td class="r"><a href="opic-mock-result.html?mid=${m.id}">결과 보기</a></td></tr>`).join("")}</tbody></table></div>` : ""}
      ${trendCard(S.trend("opic"), true)}
      ${recent.length ? `<div class="card" style="margin-top:14px"><h2>최근 답변</h2><table class="small"><tbody>
        ${recent.map(r => { const it = S.item(r.task, r.item_id);
          const label = it ? `${esc(S.OPIC_TOPICS[it.topic][0])} · ${esc(it.kind ? S.OPIC_KINDS[it.kind] : (r.qidx < it.steps.length ? S.OPIC_KINDS[it.steps[r.qidx].kind] : "롤플레이"))}` : esc(r.item_id);
          return `<tr><td>${esc(r.created_at.slice(5, 16).replace("T", " "))}</td><td>${label}${r.mock_id ? " · 모의고사" : ""}</td>
            <td class="r num">${r1(r.points)}/5</td><td class="r num muted">${r.words !== null && r.words !== undefined ? r.words + "단어" : ""}</td></tr>`; }).join("")}</tbody></table></div>` : ""}
      <p class="small muted" style="margin-top:12px">문제는 모두 이 앱용으로 새로 쓴 것입니다. 실제 시험의 진행자 화면·문항 수·난이도 구성은 공개 자료를 바탕으로 재현했습니다.
        <a href="speaking-history.html?exam=opic">답변 기록 전체 보기</a></p>`;
  }

  function opicSurvey() {
    let st = S.opicSettings();
    const draw = (errors) => {
      $("app").innerHTML = `${head("OPIc · Background Survey + Self Assessment", "설문·난이도", `<a class="btn" href="opic.html">오픽 홈</a>`)}
        <div class="muted small" style="margin:-8px 0 12px">실제 시험 전에 하는 설문을 그대로 연습합니다. 고른 주제에서 모의고사 문항이 나옵니다.</div>
        <form id="survey">
        <div class="card"><h2>1. 주제 고르기 (Background Survey)</h2>
          <p class="small muted">실제 설문은 직업·학생 여부·거주 형태를 묻고, 여가·취미·운동·휴가에서 합계 12개 이상을 고릅니다.
            흔히 쓰는 전략은 <b>"일 경험 없음 · 학생 아님 · 가족과 함께 거주"</b>로 답해 직장·학교 질문을 피하고, 서로 이어지는 주제(예: 공원 가기 · 걷기 · 조깅)를 묶어 고르는 것입니다.
            이 앱은 연습할 주제만 고르면 됩니다.</p>
          ${Object.entries(S.SURVEY_GROUPS).map(([group, need]) => `<h3 style="margin:14px 0 6px">${group} <span class="small muted">${need}개 이상</span></h3><div class="topic-grid">
            ${Object.entries(S.OPIC_TOPICS).filter(([, v]) => v[1] === group && v[2]).map(([key, v]) => `<label><input type="checkbox" name="topic" value="${key}" ${st.survey.includes(key) ? "checked" : ""}> ${esc(v[0])}</label>`).join("")}</div>`).join("")}
          <p class="small muted" style="margin-top:10px">거주(사는 곳·집) 주제는 누구나 받으므로 자동으로 포함됩니다.</p></div>
        <div class="card" style="margin-top:14px"><h2>2. 난이도 (Self Assessment)</h2>
          <p class="small muted">실제 시험은 처음에 1~6단계를 고르고, 7번 문항 뒤에 "쉽게 / 비슷하게 / 어렵게"로 한 번 더 조정합니다.
            난이도가 높을수록 비교·사회 이슈·롤플레이 문제 해결이 많이 나옵니다. IH·AL을 노리면 5~6단계를 고르는 경우가 많습니다(수험 커뮤니티의 일반적인 조언).</p>
          <div class="topic-grid">${Object.entries(S.OPIC_LEVELS).map(([lv, name]) => `<label><input type="radio" name="level" value="${lv}" ${st.level === Number(lv) ? "checked" : ""}> ${esc(name)}</label>`).join("")}</div></div>
        <div class="card" style="margin-top:14px"><h2>3. 목표 등급</h2>
          <div class="topic-grid">${S.OPIC_TARGETS.map(g => `<label><input type="radio" name="target" value="${g}" ${st.target === g ? "checked" : ""}> <b>${g}</b>&nbsp;<span class="small muted">${esc(S.OPIC_GRADE_NAME[g])}</span></label>`).join("")}</div></div>
        <div class="quiz-foot"><span></span><button class="btn primary" type="submit">저장</button></div></form>`;
      document.querySelectorAll(".flash.survey-error").forEach(n => n.remove());
      for (const msg of errors || []) UI.flash(msg, "error").classList.add("survey-error");
      $("survey").addEventListener("submit", ev => {
        ev.preventDefault();
        const f = $("survey");
        const picked = [...f.querySelectorAll("input[name=topic]:checked")].map(i => i.value).filter(t => t in S.OPIC_TOPICS && S.OPIC_TOPICS[t][2]);
        const errs = [];
        for (const [group, need] of Object.entries(S.SURVEY_GROUPS)) {
          if (picked.filter(t => S.OPIC_TOPICS[t][1] === group).length < need) errs.push(`${group} 주제를 ${need}개 이상 고르세요.`);
        }
        const lvEl = f.querySelector("input[name=level]:checked"), tgEl = f.querySelector("input[name=target]:checked");
        let level = lvEl ? Number(lvEl.value) : 4;
        if (!lvEl || !(level in S.OPIC_LEVELS)) { errs.push("난이도를 고르세요."); level = 4; }
        const target = tgEl ? tgEl.value : "IH";
        if (!tgEl || !S.OPIC_TARGETS.includes(target)) errs.push("목표 등급을 고르세요.");
        if (errs.length) { st = { survey: picked, level, target: S.OPIC_TARGETS.includes(target) ? target : "IH" }; draw(errs); window.scrollTo({ top: 0 }); return; }
        if (!picked.includes("home")) picked.unshift("home");                // 거주 주제는 누구나 받는다
        TSStore.setSettings({ opic_survey: picked.join(","), opic_level: String(level), opic_target: target });
        UI.go("opic.html?saved=survey");
      });
    };
    draw([]);
  }

  async function opicMock() {
    await S.loadOpic();
    const st = S.opicSettings();
    const e = TSNav.EXAMS.opic;
    $("app").innerHTML = `<div class="page-head"><div><div class="muted small">${esc(e.en)}</div><h1>${esc(e.name)} 실전 모의고사</h1><div class="muted small">${esc(e.summary)}</div></div>
        <a class="btn" href="opic.html">${esc(e.name)} 홈</a></div>
      <div class="grid two"><div class="card"><h2>시험 구성</h2><table><tbody>
        <tr><td class="num"><b>1</b></td><td>자기소개</td></tr>
        <tr><td class="num"><b>2~7</b></td><td>설문 주제 콤보 2개 (묘사 → 습관·비교 → 경험)</td></tr>
        <tr><td class="num"><b>8~10</b></td><td>돌발 주제 콤보</td></tr>
        <tr><td class="num"><b>11~13</b></td><td>롤플레이 (질문하기 → 문제 해결 → 관련 경험)</td></tr>
        <tr><td class="num"><b>14~15</b></td><td>비교·사회 이슈 (고난도) <span class="small muted">난이도 1~2는 없음 (13문항)</span></td></tr></tbody></table>
        <p class="small muted">전체 ${S.OPIC_MINUTES}분 · 질문은 소리로만 나오고 다시 듣기는 한 번. 실제 시험의 7번 뒤 난이도 재조정은 생략합니다.</p></div>
        <div class="card"><h2>시작하기</h2>
          <p class="small">설문 주제: ${st.survey.length ? st.survey.map(t => `<span class="tag">${esc(S.OPIC_TOPICS[t][0])}</span> `).join("") : `<span class="muted">고르지 않음 (모든 주제)</span>`} · <a href="opic-survey.html">바꾸기</a></p>
          <label class="small">난이도 <select id="level">${Object.entries(S.OPIC_LEVELS).map(([lv, name]) => `<option value="${lv}" ${st.level === Number(lv) ? "selected" : ""}>${esc(name)}</option>`).join("")}</select></label>
          <ul class="clean small" style="margin:10px 0"><li>조용한 곳에서 이어폰을 끼고 보세요. 마이크 권한을 허용해야 녹음됩니다.</li><li>끝나면 녹음을 다시 들으며 문항마다 스스로 채점합니다.</li></ul>
          <button class="btn primary" id="make">모의고사 만들기</button></div></div>${pastList("opic")}`;
    $("make").addEventListener("click", () => {
      let level = Number($("level").value);
      if (!(level in S.OPIC_LEVELS)) level = st.level;
      const plan = S.opicMockPlan(st.survey, level);
      if (plan.reduce((a, u) => a + u.steps.length, 0) < 10) { UI.flash("문제가 아직 모자라 모의고사를 만들 수 없습니다.", "error"); return; }
      UI.go(`opic-mock-run.html?mid=${S.createMock("opic", plan, { level, survey: st.survey, target: st.target })}`);
    });
  }

  function opicGuide() {
    const st = S.opicSettings();
    const hl = on => (on ? ` style="background:var(--accent-soft)"` : "");
    $("app").innerHTML = `${head("OPIc · 등급 기준 · 문항 유형별 답변 틀 · 목표별 학습 경로", "답변 틀·등급 기준", `<a class="btn" href="opic.html">오픽 홈</a>`)}
      <div class="card"><h2>목표 등급별 학습 경로 <span class="small muted">지금 목표: ${esc(st.target)}</span></h2><div class="table-wrap"><table>
        <thead><tr><th>목표</th><th>평가자가 보는 것</th><th>연습</th></tr></thead><tbody>
        <tr${hl(["IM1", "IM2"].includes(st.target))}><td><b>IM1~IM2</b><div class="small muted">난이도 3~4</div></td>
          <td>문장을 여러 개 이어 질문에 답한다. 현재 시제 위주라도 끊기지 않으면 된다.</td>
          <td class="small">주제별 묘사·습관 틀 외우기 → 답변 1분 이상 → 기본 답변(IM) 따라 말하기</td></tr>
        <tr${hl(st.target === "IM3")}><td><b>IM3</b><div class="small muted">난이도 4~5</div></td>
          <td>답변이 길고(1분 30초 안팎) 경험 문항에서 과거 시제를 대체로 맞게 쓴다.</td>
          <td class="small">경험(past) 문항 집중 → 시간 순서 연결어(first, after that, finally) → 롤플레이 질문하기</td></tr>
        <tr${hl(st.target === "IH")}><td><b>IH</b><div class="small muted">난이도 5~6</div></td>
          <td>문단 단위로 말하고, 현재·과거·미래를 넘나들며 비교·설명한다. 롤플레이 문제 해결을 해낸다.</td>
          <td class="small">비교(compare) 문항 → 롤플레이 대안 2~3개 → 고득점 답변(IH~AL) 섀도잉 → 모의고사 15문항</td></tr>
        <tr${hl(st.target === "AL")}><td><b>AL</b><div class="small muted">난이도 5~6</div></td>
          <td>사건을 생생한 이야기로 풀고(배경 → 사건 → 결과 → 느낌), 사회 이슈에도 자기 생각을 논리적으로 말한다. 끝까지 흐트러지지 않는다.</td>
          <td class="small">사회 이슈(issue) 문항 → 감정·묘사 형용사 늘리기 → 실수 없이 2분 → 돌발 주제도 같은 수준으로</td></tr>
        </tbody></table></div></div>
      <div class="card" style="margin-top:14px"><h2>문항 유형별 답변 틀</h2>
        <h3>자기소개 <span class="small muted">채점 비중이 낮다는 것이 일반적 견해 — 짧고 자연스럽게</span></h3>
        <div class="frame">Hi, my name is ... I'm ... years old, and I live in ... with my family.
These days, I'm ... (요즘 하는 일). In my free time, I enjoy ...
I'm the kind of person who ... That's a little bit about me.</div>
        <h3>묘사 (describe) — 장소·사람·물건</h3>
        <div class="frame">서론: Oh, you want to know about my favorite ... Sure.
전체: It's located in ... / It's a ... with ...
세부: When you walk in, the first thing you see is ... / There are ...
느낌: What I like most about it is ... because ...
마무리: So, that's my favorite ... I really love it.</div>
        <h3>습관·루틴 (routine)</h3>
        <div class="frame">Usually / Whenever I ..., I first ... Then, I ... After that, ...
On weekends, it's a little different. ...
I've been doing this for about ... years, and ...</div>
        <h3>경험 (past) — 가장 중요</h3>
        <div class="frame">배경: I remember one time, about two years ago, when I ...
사건: Everything was fine at first, but suddenly ...
해결: So, I ... and finally ...
느낌: I was so ... At that moment I realized ...
마무리: I'll never forget that day.</div>
        <h3>비교·변화 (compare)</h3>
        <div class="frame">In the past, ... But these days, ...
One big difference is ... Another change is ...
I think this happened because ...
Personally, I like the way it is now / I miss the old days because ...</div>
        <h3>사회 이슈 (issue)</h3>
        <div class="frame">These days, one of the biggest issues about ... is ...
Many people are worried that ...
For example, I recently read/heard that ...
In my opinion, we should ... / I think it will get better if ...</div>
        <h3>롤플레이</h3>
        <div class="frame">질문하기 (11번): Hi, I'm calling because ... I have a few questions.
  First, ...? Also, ...? And ...? Lastly, ...? Thank you so much.
문제 해결 (12번): Hi, it's me. I'm afraid I have some bad news. ...
  How about these options? First, we could ... Or, ... If that doesn't work, ...
  Let me know which one you prefer. Sorry again.
관련 경험 (13번): '경험' 틀과 같다 — 과거 시제 이야기</div></div>
      <div class="card" style="margin-top:14px"><h2>시간 벌기·연결 표현</h2><div class="grid two">
        <ul class="clean small"><li>Let me think... / That's a good question.</li><li>How can I put this... / What else...</li><li>Oh, I almost forgot to mention that ...</li><li>Anyway, going back to the question, ...</li></ul>
        <ul class="clean small"><li>To be honest, ... / Actually, ...</li><li>On top of that, ... / Not only that, but ...</li><li>The thing is, ... / What I mean is ...</li><li>Long story short, ...</li></ul></div></div>
      <div class="card" style="margin-top:14px"><h2>자기 채점 기준</h2><table class="small"><tbody>
        ${S.OPIC_RUBRIC.map(([p, d]) => `<tr><td class="num"><b>${p}</b></td><td>${esc(d)}</td></tr>`).join("")}</tbody></table>
        <p class="small muted">평균 점수와 답변 길이(음성 인식 단어 수)로 NL~AL을 추정합니다. 예: 평균 3점대면 IM, 그중 답변이 평균 110단어 이상이면 IM3. ACTFL 공식 판정 방식이 아닌 학습용 추정입니다.</p></div>
      <div class="card" style="margin-top:14px"><h2>녹음 다시 들을 때 체크리스트</h2><ul class="clean">
        <li>질문에 맞는 답을 했나? (묘사를 물었는데 경험을 말하지 않았나)</li>
        <li>경험 문항을 <b>과거 시제</b>로 끝까지 말했나?</li>
        <li>같은 표현(very, good, like)만 반복하지 않았나?</li>
        <li>서론 → 본론 → 마무리가 있었나? 마지막 문장이 흐지부지 끝나지 않았나?</li>
        <li>롤플레이에서 질문을 3~4개 했나? 대안을 2개 이상 냈나?</li></ul></div>`;
  }

  // ========================================================================= 모의고사 결과
  async function mockResult(exam) {
    if (exam === "tsp") await S.loadTsp(); else await S.loadOpic();
    const mid = UI.intParam("mid");
    const m = mid !== null ? S.getMock(mid) : null;
    if (!m || !m.finished_at || m.exam !== exam) {
      $("app").innerHTML = `<div class="card empty"><h2>결과를 찾을 수 없습니다</h2><p>아직 끝나지 않았거나 지워진 모의고사입니다.</p><a class="btn primary" href="${exam === "tsp" ? "toeic-speaking" : "opic"}.html">홈으로</a></div>`;
      return;
    }
    const tsp = exam === "tsp", r = m.result;
    const rows = new Map(S.mockAnswers(mid).map(a => [`${a.task.replace(/^tsp:/, "")}|${a.item_id}|${a.qidx}`, a]));
    const steps = m.plan.flatMap(u => u.steps).map(s => ({ ...s, row: rows.get(`${s.ref.task}|${s.ref.item_id}|${s.ref.qidx}`) || null }));
    const target = m.settings.target;
    const kpis = tsp
      ? kpi("추정 점수", r.score, r.level ? r.level[1] : "") + kpi("문항 점수 합", r1(r.raw), `만점 ${r.raw_max} (Q1~10 3점 · Q11 5점)`) +
        kpi("목표", target || "–", target ? (r.score >= target ? "달성" : (target - r.score) + "점 남음") : "")
      : kpi("추정 등급", r.grade || "–", r.grade_name) + kpi("자기 채점 평균", r1(r.avg), "5점 만점 (자기소개 제외)") +
        kpi("목표", target || "–", r.grade && target ? (S.OPIC_GRADES.indexOf(r.grade) >= S.OPIC_GRADES.indexOf(target) ? "달성" : "아직") + " · 난이도 " + r.level : "");
    $("app").innerHTML = `${head(`${tsp ? "토익스피킹" : "오픽"} 실전 모의고사 · ${when(m.created_at)}`, "결과",
        `<a class="btn primary" href="${tsp ? "tsp-mock" : "opic-mock"}.html">새 모의고사</a><a class="btn" href="${tsp ? "toeic-speaking" : "opic"}.html">홈</a>`)}
      <div class="grid four">${kpis}${kpi("답변", `${r.answered}/${r.total}`, (r.words ? `평균 ${r0(r.words)}단어` : "") + (r.duration ? ` · ${UI.mmss(r.duration)}` : ""))}</div>
      ${tsp && r.by_task ? `<div class="card" style="margin-top:14px"><h2>유형별</h2><table><tbody>
        ${S.TSP_ORDER.map(t => { const v = S.TSP_TASKS[t], p = r0((r.by_task[t] || 0) * 100); return `<tr><td class="num"><b>${v.q}</b></td><td>${esc(v.name)}</td><td class="r num">${p}%</td>
          <td style="width:40%"><div class="bar"><i style="width:${p}%"></i></div></td></tr>`; }).join("")}</tbody></table>
        <p class="small muted">가장 낮은 유형부터 <a href="toeic-speaking.html#tasks">유형별 연습</a>을 해 보세요.</p></div>` : ""}
      <div class="card" style="margin-top:14px"><h2>문항별</h2>
        ${steps.map(s => `<details class="reveal-d"><summary>${esc(s.label)} · ${esc(s.title)} —
          ${s.row ? `<b>${r1(s.row.points)}/${s.row.max_points}</b>${s.row.words !== null && s.row.words !== undefined ? ` <span class="muted small">${s.row.words}단어</span>` : ""}` : `<span class="muted">답 없음</span>`}</summary>
          ${s.question_text || s.show.question ? `<div class="sentence spk-q">${esc(s.question_text || s.show.question)}</div>` : ""}
          ${s.question_ko ? `<div class="small muted">${esc(s.question_ko)}</div>` : ""}
          ${s.show.kind === "text" ? `<div class="passage">${esc(s.show.text)}</div>` : ""}
          ${s.show.kind === "scene" ? `<div class="small">${esc(s.show.scene_ko)}</div>` : ""}
          ${s.row && s.row.response ? `<div class="explain"><b>내가 한 말</b><div>${esc(s.row.response)}</div></div>` : ""}
          ${(s.samples || []).filter(x => x.text).map(x => `<div class="explain"><b>${esc(x.label)}</b><div class="spk-sample">${esc(x.text)}</div>${x.ko ? `<div class="translation">${esc(x.ko)}</div>` : ""}</div>`).join("")}
        </details>`).join("")}</div>
      <p class="small muted" style="margin-top:12px">점수·등급은 스스로 채점한 결과로 계산한 추정치입니다. 실제 채점은 ${tsp ? "ETS" : "ACTFL"} 공인 평가자가 합니다.</p>`;
  }

  // ========================================================================= 답변 기록
  async function history() {
    const q = UI.params();
    const examName = q.get("exam");
    if (examName !== "toeic" && examName !== "opic") { $("app").innerHTML = `<div class="card empty"><h2>없는 기록입니다</h2><a class="btn primary" href="toeic-speaking.html">토익스피킹 홈</a></div>`; return; }
    const key = examName === "toeic" ? "tsp" : "opic";
    if (key === "tsp") await S.loadTsp(); else await S.loadOpic();
    const name = key === "tsp" ? "토익스피킹" : "오픽";
    const home = key === "tsp" ? "toeic-speaking.html" : "opic.html";
    const per = 40;
    const [rowsAll, total] = S.history(key, 100000, 0);
    const pages = Math.max(1, Math.ceil(total / per));
    const page = Math.min(pages, Math.max(1, parseInt(q.get("page") || "1", 10) || 1));
    const rows = rowsAll.slice((page - 1) * per, page * per).map(r => {
      const t = r.task.replace(/^tsp:/, "");
      const it = S.item(t, r.item_id) || {};
      let label;
      if (key === "tsp") label = t in S.TSP_TASKS ? `${S.TSP_TASKS[t].q} ${S.TSP_TASKS[t].name}` : t;
      else {
        const k = it.kind || (it.steps ? (it.steps[r.qidx] || {}).kind : "");
        label = `${it.topic ? S.OPIC_TOPICS[it.topic][0] : ""} · ${S.OPIC_KINDS[k] || k || ""}`;
      }
      return { ...r, label, question: S.questionOf(r.task, r.item_id, r.qidx) };
    });
    const days = {};
    for (const r of rows) (days[r.created_at.slice(0, 10)] = days[r.created_at.slice(0, 10)] || []).push(r);
    const trend = S.trend(key);
    $("app").innerHTML = `${head(`채점한 답변 ${total}개 · 연습과 모의고사 모두 · 녹음 파일은 저장하지 않고 음성 인식으로 받아 적은 글만 남습니다`, `${name} 기록`, `<a class="btn" href="${home}">${name} 홈</a>`)}
      ${trend.length ? `<div class="card"><h2>날짜별 변화 <span class="small muted">최근 60일</span></h2><table class="small"><thead><tr><th>날짜</th><th class="r">답변</th><th class="r">자기 채점 평균</th><th class="r">평균 단어</th></tr></thead><tbody>
        ${trend.slice().reverse().map(d => `<tr><td>${d.d}</td><td class="r num">${d.n}</td><td class="r num">${key === "tsp" ? r0(d.r * 100) + "%" : r1(d.r * 5) + "/5"}</td><td class="r num">${d.w !== null ? r0(d.w) : "–"}</td></tr>`).join("")}</tbody></table></div>` : ""}
      ${!total ? `<div class="card empty">아직 채점한 답변이 없습니다. <a href="${home}">연습하러 가기</a></div>` : ""}
      ${Object.entries(days).map(([d, rs]) => `<div class="card" style="margin-top:14px"><h2>${d} <span class="small muted">${rs.length}개</span></h2>
        ${rs.map(r => `<details class="reveal-d"><summary>${esc(r.created_at.slice(11, 16))} · ${esc(r.label)} — <b>${r1(r.points)}/${r.max_points}</b>
          <span class="small muted">${r.words !== null && r.words !== undefined ? r.words + "단어" : ""}${r.seconds ? " · " + r0(r.seconds) + "초" : ""}${r.accuracy !== null && r.accuracy !== undefined ? " · 일치 " + r0(r.accuracy * 100) + "%" : ""}${r.mock_id ? " · 모의고사" : ""}</span></summary>
          ${r.question ? `<div class="sentence spk-q" style="margin:8px 0">${esc(r.question)}</div>` : ""}
          ${r.response ? `<div class="explain"><b>내가 한 말</b><div>${esc(r.response)}</div></div>` : `<div class="small muted">받아 적은 글 없음 (음성 인식을 쓰지 못한 답변)</div>`}
          ${r.mock_id ? `<p class="small"><a href="${key}-mock-result.html?mid=${r.mock_id}">이 모의고사 결과 보기</a></p>` : ""}</details>`).join("")}</div>`).join("")}
      ${pages > 1 ? `<div class="row" style="justify-content:center;margin-top:14px">
        ${page > 1 ? `<a class="btn small" href="?exam=${examName}&page=${page - 1}">← 최근</a>` : ""}<span class="small muted">${page} / ${pages}</span>
        ${page < pages ? `<a class="btn small" href="?exam=${examName}&page=${page + 1}">이전 기록 →</a>` : ""}</div>` : ""}`;
  }

  // ========================================================================= 시작
  const exam = kind.startsWith("tsp") || kind === "toeic-speaking" ? "toeic-speaking" : kind.startsWith("opic") ? "opic" : (UI.params().get("exam") === "opic" ? "opic" : "toeic-speaking");
  const pageName = kind === "speaking-history" ? "speaking-history:" + (UI.params().get("exam") === "opic" ? "opic" : "toeic") : kind;
  const PAGES = {
    "toeic-speaking": tspHome, "tsp-guide": tspGuide, "tsp-mock": tspMock, "tsp-mock-result": () => mockResult("tsp"),
    opic: opicHome, "opic-survey": opicSurvey, "opic-guide": opicGuide, "opic-mock": opicMock, "opic-mock-result": () => mockResult("opic"),
    "speaking-history": history,
  };
  UI.boot({ exam, page: pageName }, async () => {
    const f = PAGES[kind];
    if (!f) throw new Error("알 수 없는 화면: " + kind);
    await f();
  });
})();
