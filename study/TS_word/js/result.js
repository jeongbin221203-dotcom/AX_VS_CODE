/* 결과 화면 (result.html?sid=번호) — 점수, 파트별 정답률, 틀린 유형, 문제 다시 보기 (TS 앱 result.html) */
UI.boot({ exam: "toeic", page: "result" }, async () => {
  const { esc, $ } = UI;
  const sid = UI.intParam("sid");
  const s = sid !== null ? Sessions.getSession(sid) : null;
  if (!s) { $("app").innerHTML = `<div class="card empty"><h2>결과를 찾을 수 없습니다</h2><a class="btn primary" href="toeic.html">오늘 화면으로</a></div>`; return; }
  const S = Score;
  const attempts = Sessions.sessionAttempts(sid);
  const part = a => TSStore.partOf(a);
  const byPart = {}, byType = {};
  for (const a of attempts) {
    const d = byPart[part(a)] || (byPart[part(a)] = { n: 0, c: 0 });
    d.n++; d.c += a.o;
    const k = `${part(a)}|${a.y}`;
    const t = byType[k] || (byType[k] = { part: part(a), type: a.y, n: 0, c: 0 });
    t.n++; t.c += a.o;
  }
  const weak = Object.values(byType).filter(v => v.c < v.n).sort((a, b) => (a.c / a.n - b.c / b.n) || (b.n - a.n)).slice(0, 6);
  const grade = s.total_est !== null ? S.gradeFor(s.total_est) : null;
  const title = Sessions.sessionTitle(s);
  document.title = `결과 · ${title} · TS 단어`;
  const pct = TSU.pct;

  await Bank.load([...new Set(s.items.map(r => Bank.splitRef(r)[0]))]);
  const answered = {};
  for (const a of attempts) (answered[`${part(a)}:${TSStore.itemIdOf(a)}`] = answered[`${part(a)}:${TSStore.itemIdOf(a)}`] || [])
    .push({ qidx: TSStore.qidxOf(a), chosen: a.c, correct: !!a.o });
  const graded = {};
  for (const [ref, res] of Object.entries(answered)) if (Bank.item(ref)) graded[ref] = { ...Bank.reveal(ref), results: res };
  const items = s.items.filter(r => r in graded).map(r => ({ ...Bank.item(r), ref: r }));

  const sameCond = s.mode === "practice" && s.part
    ? `<a class="btn" href="${UI.url("practice.html", { start: 1, part: s.part, level: s.level || "", type: s.variant || "", n: s.requested || s.total || 10 })}">같은 조건으로 다시</a>` : "";
  const kpis = [];
  if (s.total_est !== null) kpis.push(`<div class="card kpi"><div class="label">추정 점수</div><div class="value">${s.total_est}</div>
    <div class="sub">LC ${s.lc_est} · RC ${s.rc_est} ${UI.toeicBadge(grade)}</div></div>`);
  kpis.push(`<div class="card kpi"><div class="label">정답</div><div class="value">${s.correct} / ${s.total}</div><div class="sub">${pct(s.total ? s.correct / s.total : null)}</div></div>`);
  if (s.lc_total && s.rc_total) kpis.push(`<div class="card kpi"><div class="label">LC / RC 정답률</div>
    <div class="value">${pct(s.lc_correct / s.lc_total)} · ${pct(s.rc_correct / s.rc_total)}</div>
    <div class="sub">LC ${s.lc_correct}/${s.lc_total} · RC ${s.rc_correct}/${s.rc_total}</div></div>`);
  kpis.push(`<div class="card kpi"><div class="label">걸린 시간</div><div class="value">${TSU.mmss(s.duration_sec)}</div>
    <div class="sub">${s.total ? `문항당 ${TSU.pyRound(s.duration_sec / s.total)}초` : ""}</div></div>`);

  let scoreNote = "";
  if (s.total_est !== null) {
    if (s.variant === "full") {
      const next = grade && grade.level < 5 ? S.GRADE_BY_LEVEL[grade.level + 1] : null;
      scoreNote = `<div class="card" style="margin-top:14px"><div class="spread"><h2 style="margin:0">내 위치</h2>
        <span class="small muted">LC ${s.lc_correct}/${s.lc_total}개 → ${s.lc_est}점 · RC ${s.rc_correct}/${s.rc_total}개 → ${s.rc_est}점</span></div>
        <div class="ladder" style="margin-top:10px">${S.GRADES.map(gr => `<div class="step g${gr.level}${grade && grade.level === gr.level ? " cur" : ""}"><b>${gr.name}</b>${gr.low}~${gr.high}
          ${grade && grade.level === gr.level ? `<div>${s.total_est}점</div>` : ""}</div>`).join("")}</div>
        ${next ? `<p class="small" style="margin:8px 0 0">다음 등급 ${next.name}까지 <b>${next.low - s.total_est}점</b>.
          <a href="guide.html?level=${grade.level}">${grade.name} 등급 가이드 보기</a></p>` : ""}
        ${s.seen_before ? `<p class="small" style="margin:6px 0 0"><span class="tag bad">주의</span> 이 시험에는 전에 풀어 본 문제 묶음이 ${s.seen_before}개 섞여 있어 점수가 실제보다 높을 수 있습니다.</p>` : ""}</div>
        <p class="small muted" style="margin-top:10px">점수는 맞힌 개수(원점수)를 흔히 쓰이는 환산 구간으로 바꾼 값입니다. 실제 환산표는 시험마다 달라 ±30점 정도 차이가 날 수 있습니다.</p>`;
    } else scoreNote = `<p class="small muted" style="margin-top:10px">추정 점수는 정답률을 난이도로 보정해 일반적인 환산 구간에 맞춘 값입니다. 실제 토익 점수와 차이가 날 수 있습니다.</p>`;
  }

  const partRows = Object.keys(byPart).map(Number).sort((a, b) => a - b).map(p => {
    const d = byPart[p], r = d.c / d.n;
    return `<tr><td>Part ${p} ${esc(S.PART_INFO[p].name)}</td><td class="r num">${d.c}/${d.n}</td>
      <td style="width:40%"><div class="bar ${r >= 0.75 ? "ok" : r < 0.5 ? "bad" : ""}"><i style="width:${TSU.pyRound(r * 100)}%"></i></div></td></tr>`;
  }).join("");
  const weakHtml = weak.length ? `<table><thead><tr><th>유형</th><th class="r">정답</th><th></th></tr></thead><tbody>${weak.map(w => `<tr>
      <td>Part ${w.part} · ${esc(w.type)}</td><td class="r num">${w.c}/${w.n}</td>
      <td class="r"><a class="btn small" href="${UI.url("practice.html", { start: 1, part: w.part, type: w.type, n: 10 })}">이 유형 연습</a></td></tr>`).join("")}</tbody></table>`
    : `<div class="empty">모두 맞혔습니다.</div>`;

  $("app").innerHTML = `<div class="quiz-page">
    <div class="page-head"><div><div class="muted small">${esc(Sessions.MODE_LABEL[s.mode])} · ${esc((s.finished_at || s.created_at).slice(0, 16).replace("T", " "))}</div>
      <h1>${esc(title)} 결과</h1></div>
      <div class="row">${sameCond}<a class="btn" href="review.html">오답노트</a><a class="btn primary" href="toeic.html">오늘 할 일로</a></div></div>
    ${!s.finished_at ? `<div class="flash error">아직 끝나지 않은 세션입니다. <a href="solve.html?sid=${sid}">이어서 풀기</a></div>` : ""}
    <div class="grid four">${kpis.join("")}</div>${scoreNote}
    <div class="grid two" style="margin-top:14px">
      <div class="card"><h2>파트별</h2><table><thead><tr><th>파트</th><th class="r">정답</th><th>정답률</th></tr></thead><tbody>${partRows}</tbody></table></div>
      <div class="card"><h2>이번에 틀린 유형</h2>${weakHtml}</div></div>
    ${items.length ? `<div class="card" style="margin-top:14px"><div class="spread"><h2 style="margin:0">문제 다시 보기</h2>
      <label class="small"><input type="checkbox" id="wrong-only"> 틀린 문제만 넘기기</label></div>
      <div id="quiz-main"><div class="quiz-top" style="position:static;border:0;padding:6px 0;margin:0">
        <span class="progress muted small" id="progress"></span><span class="timer hidden" id="timer"></span></div>
        <div class="qnav" id="qnav"></div><div id="stage"></div></div></div>` : ""}</div>`;
  if (items.length) {
    Engine.run({ sid, mode: "result", items, graded, lc_parts: S.LC_PARTS, target_sec: window.TS_GUIDE.TARGET_SEC, tts: UI.ttsRaw() },
               { draft: { get: () => null, set() {}, clear() {} } });
  }
});
