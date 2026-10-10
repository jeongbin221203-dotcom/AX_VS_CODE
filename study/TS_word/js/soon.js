/* 아직 만들지 않은 시험의 안내 화면 (TS 앱 exam_soon.html).
   body 의 data-exam 속성(toefl | toeic-speaking | opic)을 읽는다.
   ★ 2단계: 이 시험의 진짜 홈 화면을 만들면 이 파일을 쓰는 HTML(toefl.html 등)을 새 화면으로 바꾼다. */
(function () {
  const key = document.body.dataset.exam;
  const e = TSNav.EXAMS[key];
  UI.boot({ exam: key, page: key }, () => {
    const { esc, $ } = UI;
    document.title = `${e.name} · TS 단어`;
    const scale = e.scale ? `<div class="card"><h2>${esc(e.scale_title)}</h2><table><tbody>
      ${e.scale.map(([a, b]) => `<tr><td><b>${esc(a)}</b></td><td class="r muted">${esc(b)}</td></tr>`).join("")}</tbody></table></div>` : "";
    const sections = e.sections ? `<div class="card"><h2>${esc(e.sections_title)}</h2><table><tbody>
      ${e.sections.map(([a, b, c]) => `<tr><td class="num"><b>${esc(a)}</b></td><td>${esc(b)}${c ? `<div class="small muted">${esc(c)}</div>` : ""}</td></tr>`).join("")}</tbody></table></div>` : "";
    $("app").innerHTML = `<div class="page-head"><div><div class="muted small">${esc(e.en)}</div>
        <h1>${esc(e.name)} <span class="tag">준비 중</span></h1><div class="muted small">${esc(e.summary || "")}</div></div>
        <div class="row">${e.set ? `<a class="btn primary" href="study.html" data-set="${e.set}">📘 학술 어휘 공부하기</a>` : ""}
          <a class="btn" href="toeic.html" data-set="toeic">토익 공부하러 가기</a></div></div>
      ${scale || sections ? `<div class="grid two">${scale}${sections}</div>` : ""}
      <div class="card" style="margin-top:14px"><p class="muted">이 시험의 문제 풀이 화면은 아직 이 정적 사이트(TS_word)로 옮기지 않았습니다. 곧 이어서 추가됩니다.
        목표·시험일은 <a href="settings.html">설정</a>에서 미리 정해 둘 수 있습니다.</p></div>`;
    $("app").addEventListener("click", ev => { const a = ev.target.closest("a[data-set]"); if (a) Ward.setSetting({ set: a.dataset.set }); });
  });
})();
