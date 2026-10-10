/* 전체 기록 — 끝낸 풀이 200개까지 (TS 앱 history.html) */
UI.boot({ exam: "toeic", page: "history" }, () => {
  const { esc, $ } = UI;
  const rows = Stats.recentSessions(200);
  $("app").innerHTML = `<div class="page-head"><h1>전체 기록</h1><a class="btn" href="stats.html">← 통계</a></div>
    <div class="card">${rows.length ? `<div class="table-wrap"><table>
      <thead><tr><th>날짜</th><th>종류</th><th class="r">정답</th><th class="r">정답률</th><th class="r">추정 점수</th><th class="r">시간</th></tr></thead><tbody>
      ${rows.map(s => `<tr><td class="num">${esc(s.finished_at.slice(0, 16).replace("T", " "))}</td>
        <td><a href="result.html?sid=${s.id}">${esc(Sessions.sessionTitle(s))}</a> <span class="tag">${esc(Sessions.MODE_LABEL[s.mode])}</span></td>
        <td class="r num">${s.correct}/${s.total}</td><td class="r num">${TSU.pct(s.total ? s.correct / s.total : null)}</td>
        <td class="r num">${s.total_est !== null ? `<b>${s.total_est}</b>` : "–"}</td><td class="r num">${TSU.mmss(s.duration_sec)}</td></tr>`).join("")}</tbody></table></div>`
      : `<div class="empty">아직 기록이 없습니다.</div>`}</div>`;
});
