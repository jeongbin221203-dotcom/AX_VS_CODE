/* 시험 4개(토익·토플·토익스피킹·오픽)와 위쪽 메뉴 정의 (window.TSNav) — TS 앱 core/exams.py + views/nav.py 를 데이터로 옮겼다.

   ★ 2단계(토플·토익스피킹·오픽)가 할 일
     1. 새 화면 파일(예: toefl-mock.html)을 만들고 UI.boot({ exam: "toefl", page: "toefl-mock" }, 시작함수) 로 시작한다.
     2. 아래 MENUS 의 해당 항목에서 ready: false 를 지우고(또는 true), pages 에 그 화면의 page 이름을 넣는다.
        (ready: false 인 항목은 위쪽 메뉴에 "준비 중" 으로 흐리게 보인다.)
     3. EXAMS[키].ready 를 true 로 바꾸면 홈 카드의 "준비 중" 표시가 사라진다.
   메뉴 항목 모양: { label, href, pages:[이 항목으로 볼 page 이름들], emph?, ready?, set? }
     - page 이름: HTML 파일 이름에서 .html 뺀 것 ("practice"). 풀이 화면은 "solve:practice" 처럼 세션 종류를 붙인다.
     - set: "toeic" | "toefl" — 이 항목을 누르면 단어 세트도 그쪽으로 바꾼다(단어 화면은 토익/토플 단어를 따로 보여 줌). */
(function (root) {
  "use strict";

  const EXAMS = {
    toeic: { name: "토익", en: "TOEIC", ready: true, href: "toeic.html", set: "toeic", mark: "LC·RC" },
    toefl: { name: "토플", en: "TOEFL iBT", ready: true, href: "toefl.html", set: "toefl", mark: "R·L·S·W",
             summary: "읽기·듣기·말하기·쓰기 네 영역 · 2026년 1월 개편 형식 · 밴드 1~6 (0.5 단위)" },
    "toeic-speaking": {
      name: "토익스피킹", en: "TOEIC Speaking", ready: true, href: "toeic-speaking.html", mark: "11문항",
      summary: "컴퓨터로 11문항에 영어로 말하는 시험 · 약 20분 · 0~200점 · 레벨 8단계",
      scale_title: "등급 (점수 → 레벨)",
      scale: [["Advanced High", "200"], ["Advanced Mid", "180~190"], ["Advanced Low", "160~170"],
              ["Intermediate High", "140~150"], ["Intermediate Mid 1~3", "110~130"],
              ["Intermediate Low", "90~100"], ["Novice High", "60~80"], ["Novice Mid / Low", "0~50"]],
      sections_title: "문항 구성",
      sections: [["Q1~2", "문장 읽기 (Read a text aloud)", "준비 45초 · 답변 45초"],
                 ["Q3~4", "사진 묘사 (Describe a picture)", "준비 45초 · 답변 30초"],
                 ["Q5~7", "질문에 답하기 (Respond to questions)", "준비 3초 · 답변 15~30초"],
                 ["Q8~10", "표 정보로 답하기 (Respond using information)", "표 읽기 45초 · 답변 15~30초"],
                 ["Q11", "의견 말하기 (Express an opinion)", "준비 45초 · 답변 60초"]],
    },
    opic: {
      name: "오픽", en: "OPIc", ready: true, href: "opic.html", mark: "NL~AL",
      summary: "1:1 인터뷰형 말하기 시험 · 약 40분 · 12~15문항 · 사전 설문(Background Survey)으로 주제가 정해짐",
      scale_title: "등급 (높음 → 낮음, 최고 AL)",
      scale: [["AL", "Advanced Low"], ["IH", "Intermediate High"], ["IM3", "Intermediate Mid 3"], ["IM2", "Intermediate Mid 2"],
              ["IM1", "Intermediate Mid 1"], ["IL", "Intermediate Low"], ["NH", "Novice High"], ["NM", "Novice Mid"], ["NL", "Novice Low"]],
      sections_title: "문항 유형",
      sections: [["자기소개", "첫 문항 (채점 비중 낮음)", ""], ["묘사", "좋아하는 장소·사람·물건 묘사", "설문 주제"],
                 ["경험", "과거 경험·최근 있었던 일", "설문 주제"], ["롤플레이", "상황극: 질문하기·문제 해결", "난이도 5~6"],
                 ["돌발", "설문에 없는 주제·비교·사회 이슈", ""]],
    },
  };

  const WORD_PAGES = ["words", "study", "quiz", "list", "listen"];       // 단어 화면들 (어느 시험의 단어인지는 Ward.currentSet())

  const MENUS = {
    toeic: [
      { label: "홈", href: "toeic.html", pages: ["toeic"] },
      { label: "단어", href: "words.html", pages: WORD_PAGES, emph: true, set: "toeic" },
      { label: "등급 가이드", href: "guide.html", pages: ["guide"] },
      { label: "파트 연습", href: "practice.html", pages: ["practice", "solve:practice"] },
      { label: "모의고사", href: "mock.html", pages: ["mock", "diagnostic", "solve:mock", "solve:diagnostic", "result"] },
      { label: "오답노트", href: "review.html", pages: ["review", "solve:review"] },
      { label: "받아쓰기", href: "dictation.html", pages: ["dictation"] },
      { label: "통계", href: "stats.html", pages: ["stats", "history"] },
    ],
    toefl: [
      { label: "홈", href: "toefl.html", pages: ["toefl"] },
      { label: "학술 어휘", href: "words.html", pages: WORD_PAGES, emph: true, set: "toefl" },
      { label: "읽기", href: "toefl.html#sec-R", pages: ["toefl-practice:r_"] },
      { label: "듣기", href: "toefl.html#sec-L", pages: ["toefl-practice:l_"] },
      { label: "말하기", href: "toefl.html#sec-S", pages: ["toefl-practice:s_"] },
      { label: "쓰기", href: "toefl.html#sec-W", pages: ["toefl-practice:w_"] },
      { label: "모의고사", href: "toefl-mock.html", pages: ["toefl-mock", "toefl-mock-run", "toefl-mock-result"] },
      { label: "오답노트", href: "toefl-review.html", pages: ["toefl-review"] },
      { label: "기록", href: "toefl-history.html", pages: ["toefl-history"] },
    ],
    "toeic-speaking": [
      { label: "홈", href: "toeic-speaking.html", pages: ["toeic-speaking"] },
      { label: "유형별 연습", href: "toeic-speaking.html#tasks", pages: ["tsp-practice"] },
      { label: "모의고사", href: "tsp-mock.html", pages: ["tsp-mock", "tsp-mock-run", "tsp-mock-result"] },
      { label: "기록", href: "speaking-history.html?exam=toeic", pages: ["speaking-history:toeic"] },
      { label: "답변 틀·채점 기준", href: "tsp-guide.html", pages: ["tsp-guide"] },
    ],
    opic: [
      { label: "홈", href: "opic.html", pages: ["opic"] },
      { label: "설문·난이도", href: "opic-survey.html", pages: ["opic-survey"] },
      { label: "주제별 연습", href: "opic.html#topics", pages: ["opic-practice"] },
      { label: "모의고사", href: "opic-mock.html", pages: ["opic-mock", "opic-mock-run", "opic-mock-result"] },
      { label: "기록", href: "speaking-history.html?exam=opic", pages: ["speaking-history:opic"] },
      { label: "답변 틀·등급 기준", href: "opic-guide.html", pages: ["opic-guide"] },
    ],
  };

  /** page 이름이 메뉴 항목의 pages 에 해당하는가. "a:b" 는 접두어 비교 ("toefl-practice:r_" ← "toefl-practice:r_reading") */
  function matches(item, page) {
    return item.pages.some(p => (p.includes(":") && !p.startsWith("solve:") ? page.startsWith(p) : p === page));
  }

  root.TSNav = { EXAMS, MENUS, WORD_PAGES, matches, EXAM_KEYS: Object.keys(EXAMS) };
  if (typeof module !== "undefined") module.exports = root.TSNav;
})(typeof window !== "undefined" ? window : globalThis);
