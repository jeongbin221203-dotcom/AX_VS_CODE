/* 학습 통계 화면 — 점수 추이, 최근 30일 학습량, 파트별 정답률, 약한 유형, 단어 진도 (TS 앱 stats.html) */
UI.boot({ exam: "toeic", page: "stats" }, () => {
  const { esc, $ } = UI;
  const S = Score, pct = TSU.pct;
  const accAll = Stats.partAccuracy(), acc7 = Stats.partAccuracy({ days: 7 });
  const lvl = Stats.levelAccuracy(), types = Stats.typeAccuracy();
  const history = Stats.scoreHistory(), daily = Stats.dailyCounts(30), times = Stats.timeByPart();
  const targetSec = window.TS_GUIDE.TARGET_SEC;
  const vocab = Ward.levelProgress();                       // 토익 화면이므로 토익 단어 세트

  const partRows = Object.entries(S.PART_INFO).map(([ps, info]) => {
    const p = Number(ps), a = accAll[p], w = acc7[p], t = times[p];
    return `<tr><td>Part ${p} <span class="muted small">${esc(info.name)}</span></td>
      <td class="r num">${pct(a.rate)} <span class="muted small">(${a.n})</span></td>
      <td class="r num">${pct(w.rate)} <span class="muted small">(${w.n})</span></td>
      <td class="r num">${t ? `<span class="${t > targetSec[p] ? "tag bad" : "tag ok"}">${TSU.pyRound(t)}초 / ${targetSec[p]}초</span>` : "–"}</td>
      ${S.GRADES.map(gr => { const x = Stats.levelAcc(lvl, p, gr.level); return `<td class="r num small">${x ? `${pct(x.rate)}<div class="muted">${x.n}</div>` : "–"}</td>`; }).join("")}</tr>`;
  }).join("");
  const typeRows = types.slice(0, 15).map(t => `<tr><td>P${t.part} · ${esc(t.qtype)}</td>
    <td style="width:30%"><div class="bar ${t.rate >= 0.75 ? "ok" : t.rate < 0.5 ? "bad" : ""}"><i style="width:${TSU.pyRound(t.rate * 100)}%"></i></div></td>
    <td class="r num">${pct(t.rate)} <span class="muted small">(${t.n})</span></td>
    <td class="r"><a class="btn small" href="${UI.url("practice.html", { start: 1, part: t.part, type: t.qtype, n: 10 })}">연습</a></td></tr>`).join("");
  const vocabRows = vocab.map(v => `<tr><td>${UI.toeicBadge(v.level)}</td><td class="r num">${v.seen}</td><td class="r num">${v.mastered}</td><td class="r num">${v.total}</td>
    <td style="width:30%"><div class="bar ok"><i style="width:${v.total ? TSU.pyRound(v.mastered / v.total * 100) : 0}%"></i></div></td></tr>`).join("");

  $("app").innerHTML = `<div class="page-head"><div><h1>학습 통계</h1><div class="muted small">연속 학습 ${Stats.streak()}일</div></div>
      <a class="btn" href="history.html">전체 기록</a></div>
    <div class="grid two">
      <div class="card"><h2>추정 점수 추이</h2>${history.length ? `<div class="chart-box"><canvas id="score-chart"></canvas></div>` : `<div class="empty">진단 테스트·모의고사 기록이 없습니다.</div>`}</div>
      <div class="card"><h2>최근 30일 학습량</h2><div class="chart-box"><canvas id="daily-chart"></canvas></div></div></div>
    <div class="card" style="margin-top:14px"><h2>파트별 정답률</h2><div class="table-wrap"><table>
      <thead><tr><th>파트</th><th class="r">전체</th><th class="r">최근 7일</th><th class="r">문항당 시간</th>${S.GRADES.map(gr => `<th class="r">${gr.name}</th>`).join("")}</tr></thead>
      <tbody>${partRows}</tbody></table></div>
      <p class="small muted">문항당 시간은 RC 파트의 최근 100문항 평균입니다 (목표: Part 5 20초, Part 6 30초, Part 7 60초).</p></div>
    <div class="grid two" style="margin-top:14px">
      <div class="card"><h2>약한 유형 <span class="small muted">(3문항 이상 푼 유형, 정답률 낮은 순)</span></h2>
        ${types.length ? `<table><tbody>${typeRows}</tbody></table>` : `<div class="empty">아직 기록이 부족합니다.</div>`}</div>
      <div class="card"><h2>단어 진도</h2><table><thead><tr><th>등급</th><th class="r">학습 시작</th><th class="r">암기 완료</th><th class="r">전체</th><th></th></tr></thead>
        <tbody>${vocabRows}</tbody></table></div></div>`;
  if (history.length) Charts.score($("score-chart"), history);
  Charts.daily($("daily-chart"), daily);
});
