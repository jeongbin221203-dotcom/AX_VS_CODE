/* 메인(허브) 카드 확장 자리 — 2단계(토플·토익스피킹·오픽)가 자기 카드 내용을 등록하는 곳.
   예:
     Hub.register("toefl", () => ({
       now: "밴드 4.5", now_sub: "B2 (영역별 R 4.5 · L 4.0 ...)", n: "300문제 · 어휘 800", last: "2026-10-10",
       links: [["영역 연습", "toefl.html#sec-R"], ["모의고사", "toefl-mock.html"]],
     }));
   돌려주지 않은 항목은 기본값(준비 중)이 쓰인다. 자세한 항목은 js/hub.js 맨 위 설명 참고. */

/* ---- 토플 카드 (TS 앱 views/main.py 의 toefl 항목) ---- */
Hub.register("toefl", () => {
  const T = Toefl;
  const bands = T.sectionBands();
  const overall = T.overallBand(bands);
  const filled = Object.entries(bands).filter(([, v]) => v);
  return {
    now: overall ? `밴드 ${overall}` : "–",
    now_sub: overall ? T.cefr(overall) : (filled.length ? "영역별 " + filled.map(([k, v]) => `${k} ${v}`).join(" · ") : "네 영역을 풀면 계산"),
    n: `${T.totalItems().toLocaleString()}문제 · 어휘 ${((window.WARD_DATA && window.WARD_DATA.toefl) || []).length.toLocaleString()}`,
    last: T.lastDate(),
    links: [["영역 연습", "toefl.html#sec-R"], ["모의고사", "toefl-mock.html"]],
  };
});

/* ---- 토익스피킹·오픽 카드 (TS 앱 views/main.py 의 toeic-speaking / opic 항목) — 개수는 data/meta.js 의 speaking 에서 (문제 파일을 읽지 않음) ---- */
(() => {
  const lastOf = exam => (TSStore.col("speaking_attempts").all().filter(a => a.exam === exam).reduce((m, a) => (a.created_at > m ? a.created_at : m), "")).slice(0, 10);
  const meta = () => (window.TS_META && window.TS_META.speaking) || null;
  Hub.register("toeic-speaking", () => {
    const est = Spk.tspEstimate(Spk.tspTaskStats());
    const lv = Spk.tspLevel(est);
    const m = meta();
    return {
      now: est !== null ? `${est}점` : "–", now_sub: lv ? lv[1] : "다섯 유형을 연습하면 계산",
      n: m ? `${m.tsp.total.toLocaleString()}문제` : "", last: lastOf("tsp"),
      links: [["유형 연습", "toeic-speaking.html#tasks"], ["모의고사", "tsp-mock.html"], ["답변 틀", "tsp-guide.html"]],
    };
  });
  Hub.register("opic", () => {
    const g = Spk.opicStats().grade;
    const m = meta();
    return {
      now: g || "–", now_sub: g ? Spk.OPIC_GRADE_NAME[g] : "답변 5개를 채점하면 계산",
      n: m ? `${(m.opic.questions + 3 * m.opic.roleplay).toLocaleString()}문항 · 롤플레이 ${m.opic.roleplay}세트` : "", last: lastOf("opic"),
      links: [["주제 연습", "opic.html#topics"], ["모의고사", "opic-mock.html"], ["설문", "opic-survey.html"]],
    };
  });
})();
