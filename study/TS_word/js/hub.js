/* 메인(허브) 화면: 시험 4개 카드 (window.Hub) — TS 앱 templates/home.html + views/main.home.
   카드마다 지금 위치·목표·문제 수·마지막 학습·시험일·바로가기가 있다.

   다른 시험이 자기 카드를 채우려면 js/hub-extra.js 에서 등록한다 (index.html 이 hub.js 다음에 읽음):
     Hub.register("toefl", () => ({ now: "밴드 4.5", now_sub: "B2", n: "300문제 · 어휘 800", last: "2026-10-10",
                                    links: [["영역 연습", "toefl.html#sec-R"], ["모의고사", "toefl-mock.html"]] }));
   함수가 돌려주는 값은 기본 카드 위에 덮어쓰인다. 쓸 수 있는 항목:
     now, now_sub, target, n, last(YYYY-MM-DD), date(시험일), links:[[라벨, href], ...](3개까지 보기 좋음),
     vocab:{title, href, sub, set}  (set 을 주면 누를 때 단어 세트도 바뀜) */
(function () {
  "use strict";
  const { esc } = UI;
  const providers = {};
  const register = (key, fn) => { providers[key] = fn; };

  const lastDate = recs => recs.reduce((m, r) => (r.created_at && r.created_at > m ? r.created_at : m), "").slice(0, 10);
  const dday = date => {
    if (!date || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return null;
    return TSU.daysBetween(TSU.todayStr(), date);
  };

  /** 단어 세트를 잠깐 바꿔서 계산한 뒤 원래대로 돌려놓는다 (허브는 토익·토플 단어를 둘 다 보여 주므로) */
  async function withSet(set, fn) {
    const orig = Ward.currentSet();
    if (orig !== set) { Ward.setSetting({ set }); await Ward.init(); }
    try { return await fn(); } finally { if (orig !== set) { Ward.setSetting({ set: orig }); await Ward.init(); } }
  }

  async function cards() {
    const st = TSStore.settings();
    const wardSet = Ward.settings();
    const merged = { ...st, daily_new_words: wardSet.daily_new };
    // 토익
    const [score, src] = Planner.currentScore(st);
    const grade = score !== null ? Score.gradeFor(score) : null;
    const vh = await withSet("toeic", () => Planner.vocabHero(Planner.build(merged), merged));
    const toeicWords = (window.WARD_DATA.toeic || []).length;
    const toeic = {
      mark: "LC·RC",
      vocab: { title: "📘 단어 공부 시작", href: "study.html", set: "toeic", sub: vh.done ? "오늘 단어를 다 했어요 ✅" : `오늘 복습 ${vh.due}개 · 새 단어 ${vh.new}개` },
      now: score !== null ? `${score}점` : "–", now_sub: grade ? `${grade.name} · ${src}` : "진단 테스트로 시작",
      target: `${st.target_score}점`,
      n: `${(Bank.meta() ? Bank.meta().questions : 0).toLocaleString()}문항 · 단어 ${toeicWords.toLocaleString()}`,
      last: lastDate(TSStore.sessions()), date: st.exam_date,
      links: [["파트 연습", "practice.html"], ["모의고사", "mock.html"], ["진단 테스트", "diagnostic.html"]],
    };
    const toefl = {
      mark: "R·L·S·W", vocab: { title: "📘 학술 어휘 공부 시작", href: "study.html", set: "toefl", sub: "지문 핵심 단어부터" },
      now: "–", now_sub: "준비 중", target: `밴드 ${st.toefl_target}`, n: "준비 중", last: "", date: st.toefl_exam_date, links: [],
    };
    const tsp = { mark: "11문항", now: "–", now_sub: "준비 중", target: `${st.tsp_target}점`, n: "준비 중", last: "", date: st.tsp_exam_date, links: [] };
    const opic = { mark: "NL~AL", now: "–", now_sub: "준비 중", target: st.opic_target || "IH", n: "준비 중", last: "", date: st.opic_exam_date, links: [] };
    const base = { toeic, toefl, "toeic-speaking": tsp, opic };
    return TSNav.EXAM_KEYS.map(key => {
      let extra = {};
      try { extra = providers[key] ? providers[key]() || {} : {}; } catch (e) { extra = {}; }
      const e = { key, ...TSNav.EXAMS[key], ...base[key], ...extra };
      e.dday = dday(e.date);
      return e;
    });
  }

  function cardHtml(e) {
    const mark = e.dday !== null
      ? `<span class="hub-mark ${e.dday >= 0 ? "hub-dday" : ""}">${e.dday > 0 ? "D-" + e.dday : e.dday === 0 ? "D-DAY" : "시험 끝"}</span>`
      : `<span class="hub-mark">${esc(e.mark)}</span>`;
    const vocab = e.vocab ? `<a class="hub-vocab" href="${e.vocab.href}"${e.vocab.set ? ` data-set="${e.vocab.set}"` : ""}><b>${esc(e.vocab.title)}</b><span>${esc(e.vocab.sub)}</span></a>` : "";
    const quick = (e.links || []).map(([label, href]) => `<a class="btn small" href="${href}">${esc(label)}</a>`).join("");
    return `<section class="card hub-card">
      <a class="hub-top" href="${e.href}"${e.set ? ` data-set="${e.set}"` : ""}>${mark}<span class="hub-en">${esc(e.en)}</span><h2>${esc(e.name)}</h2></a>
      <div class="hub-now"><b>${esc(e.now)}</b><span class="small muted">${esc(e.now_sub)}</span></div>
      <div class="hub-meta">
        <span>목표 <b>${esc(e.target)}</b></span><span>${esc(e.n)}</span>
        <span>${e.last ? "마지막 학습 " + esc(e.last) : "아직 기록 없음"}</span>
        <span>${e.date ? "시험일 " + esc(e.date) : `<a href="settings.html">시험일 정하기</a>`}</span>
      </div>${vocab}
      <div class="hub-links">${quick ? `<div class="hub-quick">${quick}</div>` : ""}
        <a class="btn primary hub-go" href="${e.href}"${e.set ? ` data-set="${e.set}"` : ""}>${esc(e.name)} 들어가기 →</a></div>
    </section>`;
  }

  async function run() {
    await UI.boot({ exam: null, page: "index" }, async () => {
      const list = await cards();
      const streak = Stats.streak();
      UI.$("app").innerHTML = `
        <div class="page-head hub-head"><div>
          <div class="muted small">${streak ? `연속 학습 ${streak}일 · ` : ""}시험을 골라 들어가세요</div>
          <h1>어떤 시험을 공부할까요?</h1></div></div>
        <div class="hub">${list.map(cardHtml).join("")}</div>
        <p class="small muted" style="margin-top:14px">위쪽 탭(토익·토플·토익스피킹·오픽)으로 언제든 다른 시험으로 바로 옮길 수 있고, <b>TS</b> 로고를 누르면 이 화면으로 돌아옵니다.</p>`;
      UI.$("app").addEventListener("click", ev => {
        const a = ev.target.closest("a[data-set]");
        if (a) Ward.setSetting({ set: a.dataset.set });
      });
    });
  }

  window.Hub = { register, run };
  document.addEventListener("DOMContentLoaded", run);
})();
