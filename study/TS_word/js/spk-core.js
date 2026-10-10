/* 말하기 시험(토익스피킹·오픽) 핵심 로직 (window.Spk) — TS 앱 core/speaking.py 를 브라우저에서 돌린다.
   과제 정의·채점 기준·점수/등급 추정·말하기 '단계(steps)' 만들기·연습/모의고사 구성·기록·통계·약한 문항.
   document 를 쓰지 않아 node 에서도 돈다(문제 읽기 loadTsp/loadOpic 만 <script> 를 붙임).

   저장: TSStore.col("speaking_attempts") — 채점한 답변 한 줄 {exam:'tsp'|'opic', task:'tsp:read_aloud'…|'opic_q'|'opic_rp',
          item_id, qidx, points, max_points, words, seconds, accuracy, mock_id, response, created_at}
         TSStore.col("speaking_mocks")   — {exam, units:[{task,item_id}], settings, finished_at, result, score}
   모의고사 계획은 문제 번호(units)만 저장하고, 단계(steps)는 문제 은행에서 다시 만든다(저장 용량을 줄이려고; 결과는 TS 앱과 같음).
   녹음 파일은 저장하지 않는다(음성 인식으로 받아 적은 글만 남음).

   점수 환산은 주관사 공식 방식이 아니다(공개되지 않음). 스스로 채점한 결과로 대략의 위치를 보여 주는 추정치다. */
(function (root) {
  "use strict";
  const U = root.TSU || require("./tsutil.js");
  const store = () => root.TSStore || require("./tsstore.js");
  let nowFn = U.nowStr;                                       // 테스트에서 시각을 고정할 때 setNow 로 바꾼다

  // ---- 토익스피킹 --------------------------------------------------------------------
  // 2022년 6월 개편 형식: 11문항 약 20분. 문항 점수 Q1~10 0~3, Q11 0~5 (합 35) → 0~200 환산(추정).
  const TSP_TASKS = {
    read_aloud: { q: "Q1~2", name: "문장 읽기", en: "Read a text aloud", n: 2, prep: 45, speak: [45], max: 3,
                  desc: "화면의 안내문·광고를 45초 준비 후 45초 동안 소리 내어 읽는다. 발음·억양·강세를 본다." },
    describe_picture: { q: "Q3~4", name: "사진 묘사", en: "Describe a picture", n: 2, prep: 45, speak: [30], max: 3,
                        desc: "사진을 45초 보고 30초 동안 묘사한다. (이 앱은 사진 대신 장면 설명을 보여 줍니다)" },
    respond_questions: { q: "Q5~7", name: "질문에 답하기", en: "Respond to questions", n: 1, prep: 3, speak: [15, 15, 30], max: 3,
                         desc: "전화 설문 같은 상황에서 일상 질문 3개에 답한다 (준비 3초 · 15/15/30초)." },
    respond_info: { q: "Q8~10", name: "표 보고 답하기", en: "Respond using information provided", n: 1, prep: 3, speak: [15, 15, 30], max: 3,
                    desc: "일정표·예약표를 45초 읽은 뒤, 전화로 묻는 질문 3개에 표를 보고 답한다 (10번은 두 번 들려줌)." },
    opinion: { q: "Q11", name: "의견 말하기", en: "Express an opinion", n: 1, prep: 45, speak: [60], max: 5,
               desc: "찬반·선택 질문에 45초 준비 후 60초 동안 이유와 예시를 들어 의견을 말한다." },
  };
  const TSP_ORDER = Object.keys(TSP_TASKS);
  const TSP_RAW_MAX = TSP_ORDER.reduce((a, t) => a + TSP_TASKS[t].max * TSP_TASKS[t].n * TSP_TASKS[t].speak.length, 0);     // 35

  const TSP_LEVELS = [                                        // [레벨, 이름, 최저 점수] — 점수는 10점 단위
    [8, "Advanced High", 200], [7, "Advanced Mid", 180], [6, "Advanced Low", 160], [5, "Intermediate High", 140],
    [4, "Intermediate Mid 3", 130], [3, "Intermediate Mid 2", 120], [2, "Intermediate Mid 1", 110],
    [1, "Intermediate Low", 90], [0, "Novice High", 60], [-1, "Novice Mid / Low", 0],
  ];
  const TSP_TARGETS = [110, 120, 130, 140, 150, 160, 170, 180];

  // 문항 유형별 자기 채점 기준 [점수, 설명] — ETS 공개 채점 기준을 학습용으로 풀어 쓴 것
  const TSP_RUBRIC = {
    read_aloud: [[3, "발음·억양·강세가 자연스럽고 막힘이 거의 없다"],
                 [2, "대체로 알아듣기 쉽지만 발음·끊어 읽기 실수가 몇 군데 있다"],
                 [1, "자주 막히거나 틀린 발음이 많아 알아듣기 어려운 곳이 많다"],
                 [0, "읽지 못했거나 거의 알아들을 수 없다"]],
    describe_picture: [[3, "주요 대상과 배경을 알맞은 어휘와 문법으로 묘사했다 (사람·동작·위치)"],
                       [2, "묘사는 했지만 어휘·문법 실수가 있거나 내용이 적다"],
                       [1, "한두 가지만 말했거나 실수가 많아 이해가 어렵다"],
                       [0, "답하지 못함 / 사진과 무관"]],
    respond_questions: [[3, "질문에 맞게 완전한 문장으로 답하고 (30초 문항은) 이유까지 덧붙였다"],
                        [2, "질문에 답했지만 문장이 어색하거나 내용이 부족하다"],
                        [1, "일부만 답했거나 실수가 많아 뜻이 흐리다"],
                        [0, "답하지 못함 / 질문과 무관"]],
    respond_info: [[3, "표의 정보를 정확히 찾아 자연스러운 문장으로 전달했다"],
                   [2, "정보는 대체로 맞지만 일부 빠지거나 문장이 어색하다"],
                   [1, "정보가 틀렸거나 단어만 나열했다"],
                   [0, "답하지 못함 / 표와 무관"]],
    opinion: [[5, "의견이 분명하고 이유·예시가 논리적으로 이어지며 실수가 거의 없다"],
              [4, "의견과 이유가 잘 전달되지만 작은 실수나 머뭇거림이 있다"],
              [3, "의견은 있으나 이유·예시가 부족하거나 실수가 눈에 띈다"],
              [2, "생각을 일부만 전달했고 문장이 단순·부정확하다"],
              [1, "짧은 말 몇 마디로 끝났다"],
              [0, "답하지 못함 / 질문과 무관"]],
  };

  const TSP_DIRECTIONS = {
    read_aloud: "In this part of the test, you will read aloud the text on the screen. You will have 45 seconds to prepare. " +
                "Then you will have 45 seconds to read the text aloud.",
    describe_picture: "In this part of the test, you will describe the picture on your screen in as much detail as you can. " +
                      "You will have 45 seconds to prepare your response. Then you will have 30 seconds to speak about the picture.",
    respond_questions: "In this part of the test, you will answer three questions. You will have three seconds to prepare after you " +
                       "hear each question. You will have 15 seconds to respond to Questions 5 and 6, and 30 seconds to respond to Question 7.",
    respond_info: "In this part of the test, you will answer three questions based on the information provided. You will have 45 seconds " +
                  "to read the information before the questions begin. You will have three seconds to prepare and 15 seconds to respond " +
                  "to Questions 8 and 9. You will hear Question 10 two times. You will have three seconds to prepare and 30 seconds to respond.",
    opinion: "In this part of the test, you will give your opinion about a specific topic. Be sure to say as much as you can in the time " +
             "allowed. You will have 45 seconds to prepare. Then you will have 60 seconds to speak.",
  };

  /** 문항 점수 합(0~35) → 0~200 (10점 단위, 추정). */
  function tspScore(raw) {
    return U.pyRound(Math.max(0, Math.min(raw, TSP_RAW_MAX)) / TSP_RAW_MAX * 20) * 10;
  }
  /** 점수 → [레벨 번호, 이름] (없으면 null) */
  function tspLevel(score) {
    if (score === null || score === undefined) return null;
    for (const [lv, name, low] of TSP_LEVELS) if (score >= low) return [lv, name];
    return [TSP_LEVELS[TSP_LEVELS.length - 1][0], TSP_LEVELS[TSP_LEVELS.length - 1][1]];
  }

  // ---- 오픽 ------------------------------------------------------------------------------
  // 주제 key → [이름, 묶음, 설문 주제 여부]. 설문=true 는 Background Survey 에서 고르는 주제, false 는 돌발 주제.
  const OPIC_TOPICS = {
    home: ["사는 곳·집", "거주", true],
    movie: ["영화 보기", "여가", true], concert: ["공연·콘서트 보기", "여가", true],
    park: ["공원 가기", "여가", true], beach: ["해변 가기", "여가", true],
    cafe: ["카페 가기", "여가", true], shopping: ["쇼핑하기", "여가", true],
    tv: ["TV·리얼리티 쇼 보기", "여가", true], games: ["게임하기", "여가", true],
    music: ["음악 감상", "취미", true], cooking: ["요리하기", "취미", true], pets: ["반려동물 기르기", "취미", true],
    jogging: ["조깅", "운동", true], walking: ["걷기", "운동", true],
    gym: ["헬스", "운동", true], bike: ["자전거 타기", "운동", true],
    swimming: ["수영", "운동", true], hiking: ["하이킹·등산", "운동", true],
    travel_dom: ["국내 여행", "휴가", true], travel_abroad: ["해외 여행", "휴가", true],
    staycation: ["집에서 보내는 휴가", "휴가", true],
    recycling: ["재활용", "돌발", false], bank: ["은행", "돌발", false], hotel: ["호텔", "돌발", false],
    phone: ["휴대폰", "돌발", false], tech: ["인터넷·기술", "돌발", false], transport: ["교통", "돌발", false],
    furniture: ["가구·가전", "돌발", false], weather: ["날씨·계절", "돌발", false],
    holiday: ["명절·기념일", "돌발", false], health: ["건강", "돌발", false],
    food: ["음식·외식", "돌발", false], friends: ["가족·친구", "돌발", false],
    appointment: ["약속", "돌발", false], fashion: ["패션", "돌발", false],
    housework: ["집안일", "돌발", false], neighborhood: ["동네·이웃", "돌발", false],
    library: ["도서관", "돌발", false], geography: ["지형·나라", "돌발", false], industry: ["산업·회사", "돌발", false],
    intro: ["자기소개", "자기소개", false],
  };
  const SURVEY_GROUPS = { "여가": 2, "취미": 1, "운동": 1, "휴가": 1 };       // 묶음별 최소 선택 수 (실제 설문은 합계 12개)
  const OPIC_KINDS = {
    intro: "자기소개", describe: "묘사", routine: "습관·루틴", past: "경험", compare: "비교·변화", issue: "사회 이슈",
    ask: "롤플레이: 질문하기", solve: "롤플레이: 문제 해결", exp: "롤플레이: 관련 경험",
  };
  // 등급 (점수 1~5 자기 채점 → 등급). IM 은 답변 길이로 IM1~3 을 나눈다(추정).
  const OPIC_GRADES = ["NL", "NM", "NH", "IL", "IM1", "IM2", "IM3", "IH", "AL"];
  const OPIC_GRADE_NAME = { AL: "Advanced Low", IH: "Intermediate High", IM3: "Intermediate Mid 3", IM2: "Intermediate Mid 2",
                            IM1: "Intermediate Mid 1", IL: "Intermediate Low", NH: "Novice High", NM: "Novice Mid", NL: "Novice Low" };
  const OPIC_RUBRIC = [
    [5, "AL 수준: 일어난 일을 시간 순서대로 이야기하고, 문단으로 길게 말하며 시제·연결어가 정확하다"],
    [4, "IH 수준: 문단으로 말하고 현재·과거·미래를 오가며 설명·비교한다. 가끔 실수가 있다"],
    [3, "IM 수준: 문장을 여러 개 이어 질문에 답한다. 기본 시제는 대체로 맞고 내용이 이어진다"],
    [2, "IL 수준: 짧고 단순한 문장 몇 개, 자주 멈추고 같은 말을 반복한다"],
    [1, "NH 이하: 단어·외운 표현 위주, 문장을 거의 만들지 못했다"],
  ];
  const OPIC_LEVELS = { 1: "1 · 아주 쉬움", 2: "2 · 쉬움", 3: "3 · 보통", 4: "4 · 보통+", 5: "5 · 어려움", 6: "6 · 아주 어려움" };
  const OPIC_TARGETS = ["IM1", "IM2", "IM3", "IH", "AL"];
  const OPIC_MINUTES = 40;

  /** 자기 채점 평균(1~5)과 답변 평균 단어 수 → 등급 (추정) */
  function opicGrade(avgPoints, avgWords) {
    if (avgPoints === null || avgPoints === undefined) return null;
    const w = avgWords || 0;
    if (avgPoints >= 4.5) return "AL";
    if (avgPoints >= 3.6) return "IH";
    if (avgPoints >= 2.6) return w >= 110 ? "IM3" : w >= 70 ? "IM2" : "IM1";
    if (avgPoints >= 1.8) return "IL";
    return avgPoints >= 1.3 ? "NH" : "NM";
  }

  // ---- 문제 은행 -----------------------------------------------------------------------
  let bank = { tsp: null, opic: null };               // tsp: {task: [item]}, opic: {questions, roleplay}
  const byId = new Map();                             // "task|id" -> item  (task: 토익스피킹 5유형 | opic_q | opic_rp)
  const loading = {};
  const key2 = (task, id) => task + "|" + id;

  function setTsp(obj) {
    bank.tsp = {};
    for (const t of TSP_ORDER) {
      bank.tsp[t] = (obj && obj[t]) || [];
      for (const it of bank.tsp[t]) byId.set(key2(t, it.id), it);
    }
  }
  function setOpic(obj) {
    const questions = ((obj && obj.questions) || []).filter(q => q.topic in OPIC_TOPICS);
    const roleplay = (obj && obj.roleplay) || [];
    bank.opic = { questions, roleplay };
    for (const q of questions) byId.set(key2("opic_q", q.id), q);
    for (const r of roleplay) byId.set(key2("opic_rp", r.id), r);
  }
  function loadScript(file, ok) {
    if (loading[file]) return loading[file];
    loading[file] = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "data/" + file;
      s.onload = () => { try { ok(); resolve(); } catch (e) { reject(e); } };
      s.onerror = () => { delete loading[file]; reject(new Error(`data/${file} 를 읽을 수 없습니다 (tools/build_data.py 로 만들어야 합니다)`)); };
      document.head.appendChild(s);
    });
    return loading[file];
  }
  /** 토익스피킹 문제를 읽는다 (이미 읽었으면 바로 끝). */
  function loadTsp() {
    if (bank.tsp) return Promise.resolve();
    if (root.SPK_DATA && root.SPK_DATA.tsp) { setTsp(root.SPK_DATA.tsp); return Promise.resolve(); }
    return loadScript("speaking-tsp.js", () => { if (!root.SPK_DATA || !root.SPK_DATA.tsp) throw new Error("data/speaking-tsp.js 에 문제가 없습니다"); setTsp(root.SPK_DATA.tsp); });
  }
  function loadOpic() {
    if (bank.opic) return Promise.resolve();
    if (root.SPK_DATA && root.SPK_DATA.opic) { setOpic(root.SPK_DATA.opic); return Promise.resolve(); }
    return loadScript("speaking-opic.js", () => { if (!root.SPK_DATA || !root.SPK_DATA.opic) throw new Error("data/speaking-opic.js 에 문제가 없습니다"); setOpic(root.SPK_DATA.opic); });
  }
  const tspItems = task => (bank.tsp && bank.tsp[task]) || [];
  const opicQ = () => (bank.opic && bank.opic.questions) || [];
  const opicRp = () => (bank.opic && bank.opic.roleplay) || [];
  const item = (task, id) => byId.get(key2(task, id)) || null;

  /** 주제 → 종류별 문항 수 (롤플레이는 "roleplay") */
  function topicCounts() {
    const out = {};
    for (const q of opicQ()) { const o = (out[q.topic] = out[q.topic] || {}); o[q.kind] = (o[q.kind] || 0) + 1; }
    for (const r of opicRp()) { const o = (out[r.topic] = out[r.topic] || {}); o.roleplay = (o.roleplay || 0) + 1; }
    return out;
  }

  // ---- 난수 도우미 (파이썬 random.Random 의 shuffle/choice/sample 에 해당) ------------------------
  const defRng = r => r || U.makeRng();
  const shuffle = (arr, rng) => U.shuffle(arr, defRng(rng));
  const choice = (arr, rng) => arr[Math.floor(defRng(rng).random() * arr.length)];
  function sample(arr, k, rng) {
    const a = arr.slice(), r = defRng(rng);
    for (let i = 0; i < Math.min(k, a.length); i++) { const j = i + Math.floor(r.random() * (a.length - i)); [a[i], a[j]] = [a[j], a[i]]; }
    return a.slice(0, Math.min(k, a.length));
  }

  // ---- 기록 저장소 ----------------------------------------------------------------------
  const attemptsCol = () => store().col("speaking_attempts");
  const mocksCol = () => store().col("speaking_mocks");
  const taskKey = (exam, task) => (exam === "tsp" ? "tsp:" + task : task);

  /** 문제별 마지막으로 푼 시각 {item_id: 'YYYY-MM-DDTHH:MM:SS'} (task 는 저장된 이름: tsp:read_aloud / opic_q / opic_rp) */
  function lastSeen(task) {
    const out = {};
    for (const a of attemptsCol().all()) if (a.task === task && (!(a.item_id in out) || a.created_at > out[a.item_id])) out[a.item_id] = a.created_at;
    return out;
  }
  /** 마지막으로 답한 회차의 점수 비율(세트는 평균)이 낮은 문제 → {item_id: 비율}. 토익스피킹은 만점의 60% 미만, 오픽은 3점(IM) 이하. */
  function weakItems(exam, task) {
    const rows = attemptsCol().all().filter(a => a.exam === exam && a.task === task);
    const latest = {};
    for (const a of rows) if (!(a.item_id in latest) || a.created_at > latest[a.item_id]) latest[a.item_id] = a.created_at;
    const sum = {}, cnt = {};
    for (const a of rows) {
      if (a.created_at !== latest[a.item_id]) continue;
      sum[a.item_id] = (sum[a.item_id] || 0) + a.points / a.max_points;
      cnt[a.item_id] = (cnt[a.item_id] || 0) + 1;
    }
    const cut = exam === "opic" ? r => r <= 0.6 : r => r < 0.6;
    const out = {};
    for (const id of Object.keys(sum)) { const r = sum[id] / cnt[id]; if (cut(r)) out[id] = r; }
    return out;
  }
  /** 안 푼 문제 → 오래전에 푼 문제 순 (같은 조건이면 무작위) */
  function freshFirst(pool, task, rng) {
    pool = shuffle(pool.slice(), rng);
    const seen = lastSeen(task);
    return pool.map((it, i) => [it, i]).sort((a, b) => {
      const x = seen[a[0].id] || "", y = seen[b[0].id] || "";
      return x < y ? -1 : x > y ? 1 : a[1] - b[1];
    }).map(p => p[0]);
  }

  // ---- 단계(steps) 만들기 ---------------------------------------------------------------
  // 단계 공통 키:
  //   ref {task, item_id, qidx} · label · title · directions · show {kind, ...} · intro(말할 소개, 첫 단계만)
  //   say [{text, gender}] 들려줄 질문 · repeat 질문을 몇 번 들려줄지 · prep 준비 초 · speak 답변 초(0 = 제한 없음, 최대 180초)
  //   replay 다시 듣기 허용 횟수 · rubric 채점 기준 키 · max 만점 · samples [{label, text, ko}] · tips · target(읽기 정답 문장)
  /** 모범 답안 두 단계. i 가 있으면 samples[i] 처럼 질문별 배열에서 꺼낸다. */
  function samples(it, i) {
    const get = key => {
      if (i === undefined || i === null) return it[key];
      const arr = it["samples" + key.slice("sample".length)] || [];             // sample_ko → samples_ko
      return i < arr.length ? arr[i] : null;
    };
    const pairs = [["기본 답변 (IM 목표)", get("sample"), get("sample_ko")], ["고득점 답변 (IH~AL 목표)", get("sample_adv"), get("sample_adv_ko")]];
    return pairs.filter(p => p[1]).map(([label, text, ko]) => ({ label, text, ko: ko || "" }));
  }

  /** 토익스피킹 문제 하나 → 단계 목록. qno = 모의고사 문항 번호(1~11). */
  function tspSteps(task, it, qno) {
    const info = TSP_TASKS[task];
    const ref = { task, item_id: it.id };
    const base = { title: info.en, directions: TSP_DIRECTIONS[task], rubric: task, max: info.max, tips: it.tips || [] };
    const lab = k => (qno ? `Question ${qno + k} of 11` : (info.speak.length > 1 ? `질문 ${k + 1} / 3` : info.name));
    if (task === "read_aloud") {
      return [{ ...base, ref: { ...ref, qidx: 0 }, label: lab(0), show: { kind: "text", text: it.text }, say: [], prep: 45, speak: 45, target: it.text,
                samples: [{ label: "끊어 읽기·강세 표시 ( / 쉼, *강세* )", text: it.marked || "", ko: it.translation || "" }], notes: it.tricky || [] }];
    }
    if (task === "describe_picture") {
      return [{ ...base, ref: { ...ref, qidx: 0 }, label: lab(0),
                show: { kind: "scene", setting: it.setting || "", scene_ko: it.scene_ko, elements: it.elements.map(e => ({ where: e.where, ko: e.ko })) },
                say: [], prep: 45, speak: 30, samples: samples(it), notes: it.elements.map(e => `${e.where} · ${e.en}`) }];
    }
    if (task === "respond_questions") {
      return it.questions.map((q, i) => ({ ...base, ref: { ...ref, qidx: i }, label: lab(i),
        show: { kind: "question", intro: it.intro, question: q },
        intro: i === 0 ? [{ text: it.intro, gender: "female" }] : [],
        say: [{ text: q, gender: "female" }], prep: 3, speak: info.speak[i], samples: samples(it, i), question_ko: (it.questions_ko || ["", "", ""])[i] }));
    }
    if (task === "respond_info") {
      return it.questions.map((q, i) => ({ ...base, ref: { ...ref, qidx: i }, label: lab(i),
        show: { kind: "table", info: it.info }, read_first: i === 0 ? 45 : 0,
        intro: i === 0 ? [{ text: it.intro, gender: it.voice || "male" }] : [],
        say: [{ text: q, gender: it.voice || "male" }], repeat: i === 2 ? 2 : 1, prep: 3, speak: info.speak[i], hide_question: true,
        samples: samples(it, i), question_ko: (it.questions_ko || ["", "", ""])[i], question_text: q }));
    }
    if (task === "opinion") {
      return [{ ...base, ref: { ...ref, qidx: 0 }, label: lab(0), show: { kind: "question", question: it.question },
                say: [{ text: it.question, gender: "female" }], prep: 45, speak: 60, samples: samples(it),
                question_ko: it.question_ko || "", notes: it.outline || [] }];
    }
    throw new Error("알 수 없는 유형: " + task);
  }

  /** 연습: 문제 n개 → [{task, item_id, steps}]. weak=true 면 마지막 점수가 낮았던 문제만 (낮은 것부터). */
  function tspPractice(task, n, rng, weak) {
    const pool = tspItems(task);
    if (weak) {
      const ids = weakItems("tsp", "tsp:" + task);
      const list = pool.filter(it => it.id in ids).sort((a, b) => ids[a.id] - ids[b.id]);
      return list.slice(0, n).map(it => ({ task, item_id: it.id, steps: tspSteps(task, it) }));
    }
    return freshFirst(pool, "tsp:" + task, rng).slice(0, n).map(it => ({ task, item_id: it.id, steps: tspSteps(task, it) }));
  }

  /** 모의고사 11문항: 읽기 2 · 사진 2 · 질문 1세트 · 표 1세트 · 의견 1. units = [{task, item_id, steps}] */
  function tspMockPlan(rng) {
    const units = [];
    let qno = 1;
    for (const task of TSP_ORDER) {
      const info = TSP_TASKS[task];
      for (const it of freshFirst(tspItems(task), "tsp:" + task, rng).slice(0, info.n)) {
        const steps = tspSteps(task, it, qno);
        qno += steps.length;
        units.push({ task, item_id: it.id, steps });
      }
    }
    return units;
  }

  function opicQStep(q, label, showText) {
    return { ref: { task: "opic_q", item_id: q.id, qidx: 0 }, label, title: `${OPIC_TOPICS[q.topic][0]} · ${OPIC_KINDS[q.kind]}`,
             show: { kind: "opic", question: showText ? q.question : "" }, say: [{ text: q.question, gender: "female" }], prep: 0, speak: 0, replay: 1,
             rubric: "opic", max: 5, samples: samples(q), question_text: q.question, question_ko: q.question_ko || "", tips: q.tips || [] };
  }
  function opicRpSteps(r, startNo, showText) {
    return r.steps.map((s, i) => ({
      ref: { task: "opic_rp", item_id: r.id, qidx: i }, label: startNo ? `Question ${startNo + i}` : `롤플레이 ${i + 1} / ${r.steps.length}`,
      title: `${OPIC_TOPICS[r.topic][0]} · ${OPIC_KINDS[s.kind]}`,
      show: { kind: "opic", question: showText ? s.question : "", situation_ko: i === 0 ? (r.situation_ko || "") : "" },
      say: [{ text: s.question, gender: "female" }], prep: 0, speak: 0, replay: 1, rubric: "opic", max: 5, samples: samples(s),
      question_text: s.question, question_ko: s.question_ko || "", tips: s.tips || [] }));
  }

  /** 한 주제 3문항 콤보: 묘사 → 습관/비교 → 경험 (난이도가 높으면 비교·이슈가 섞인다) */
  function combo(topic, level, rng, used) {
    // 경험(level 3)은 어느 난이도에서나 나온다. 비교·이슈(level 5)는 난이도 5 이상에서만 콤보에 섞인다.
    const qs = opicQ().filter(q => q.topic === topic && !used.has(q.id) && (q.level ?? 1) <= Math.max(level, 3));
    const seen = lastSeen("opic_q");
    const take = kinds => {
      const pool = shuffle(qs.filter(q => kinds.includes(q.kind) && !used.has(q.id)), rng);
      const sorted = pool.map((q, i) => [q, i]).sort((a, b) => {
        const x = seen[a[0].id] || "", y = seen[b[0].id] || "";
        return x < y ? -1 : x > y ? 1 : a[1] - b[1];
      }).map(p => p[0]);
      if (sorted.length) { used.add(sorted[0].id); return sorted[0]; }
      return null;
    };
    const second = level >= 5 && defRng(rng).random() < 0.5 ? ["compare", "routine"] : ["routine", "compare"];
    const picks = [take(["describe"]), take(second) || take(["describe", "routine"]), take(["past"]) || take(["routine", "describe"])];
    return picks.filter(Boolean);
  }

  /** 실제 시험처럼 15문항: 자기소개 1 · 설문 콤보 2(3+3) · 돌발 콤보 1(3) · 롤플레이 1(3) · 마무리 2(비교·이슈). 난이도 1~2 는 마무리 2문항 없이 13문항. */
  function opicMockPlan(survey, level, rng) {
    const used = new Set();
    const units = [];
    const stepsSoFar = () => units.reduce((a, u) => a + u.steps.length, 0);
    const add = qs => { for (const q of qs) units.push({ task: "opic_q", item_id: q.id, steps: [opicQStep(q, `Question ${stepsSoFar() + 1}`, false)] }); };
    const intro = opicQ().filter(q => q.kind === "intro");
    if (intro.length) add([choice(intro, rng)]);
    const hasQ = new Set(opicQ().map(q => q.topic));                 // 문항이 있는 주제만 (없는 주제를 뽑으면 시험이 짧아짐)
    const surveyTopics = (survey || []).filter(t => t in OPIC_TOPICS && OPIC_TOPICS[t][2] && hasQ.has(t));
    const topics = surveyTopics.length ? surveyTopics : Object.keys(OPIC_TOPICS).filter(t => OPIC_TOPICS[t][2] && hasQ.has(t));
    let first;
    if (topics.includes("home") && defRng(rng).random() < 0.6) {
      const others = topics.filter(t => t !== "home");
      first = ["home"].concat(sample(others.length ? others : ["home"], 1, rng));
    } else first = sample(topics, Math.min(2, topics.length), rng);
    for (const t of first) add(combo(t, level, rng, used));
    const unexpected = shuffle(Object.keys(OPIC_TOPICS).filter(t => !OPIC_TOPICS[t][2] && t !== "intro"), rng);
    for (const t of unexpected) {
      const c = combo(t, level, rng, used);
      if (c.length >= 2) { add(c); break; }
    }
    let rps = opicRp().filter(r => (r.level ?? 3) <= Math.max(level, 3));
    if (!rps.length) rps = opicRp();
    if (rps.length) {
      const seen = lastSeen("opic_rp");
      rps = shuffle(rps.slice(), rng).map((r, i) => [r, i]).sort((a, b) => {
        const x = seen[a[0].id] || "", y = seen[b[0].id] || "";
        return x < y ? -1 : x > y ? 1 : a[1] - b[1];
      }).map(p => p[0]);
      const r = rps[0];
      units.push({ task: "opic_rp", item_id: r.id, steps: opicRpSteps(r, stepsSoFar() + 1, false) });
    }
    if (level >= 3) {                                                // 마무리 2문항: 설문 주제의 비교·사회 이슈 (난이도 3 이상)
      const pool = shuffle(opicQ().filter(q => topics.includes(q.topic) && ["compare", "issue"].includes(q.kind) && !used.has(q.id)), rng);
      add(pool.slice(0, 2));
    }
    return units;
  }

  const KIND_ORDER = { intro: 0, describe: 1, routine: 2, past: 3, compare: 4, issue: 5 };
  /** 오픽 연습 (topic/kind 는 null 가능). kind === "roleplay" 면 롤플레이 세트. */
  function opicPractice(topic, kind, n, showText, rng, weak) {
    const qUnit = (q, i, total) => ({ task: "opic_q", item_id: q.id, steps: [opicQStep(q, `질문 ${i + 1} / ${total}`, showText)] });
    if (weak) {                                                      // 마지막 점수가 낮았던 문항 (점수 낮은 것부터)
      const ids = weakItems("opic", "opic_q");
      const pool = opicQ().filter(q => q.id in ids && (!topic || q.topic === topic)).sort((a, b) => ids[a.id] - ids[b.id]).slice(0, n);
      return pool.map((q, i) => qUnit(q, i, pool.length));
    }
    if (kind === "roleplay") {
      const pool = freshFirst(opicRp().filter(r => !topic || r.topic === topic), "opic_rp", rng).slice(0, Math.max(1, Math.min(n, 3)));
      return pool.map(r => ({ task: "opic_rp", item_id: r.id, steps: opicRpSteps(r, null, showText) }));
    }
    let pool;
    if (topic && !kind) {                                            // 주제 하나 → 콤보처럼 묘사·습관·경험 순서
      pool = freshFirst(opicQ().filter(q => q.topic === topic), "opic_q", rng).slice(0, n);
      pool = pool.map((q, i) => [q, i]).sort((a, b) => ((KIND_ORDER[a[0].kind] ?? 9) - (KIND_ORDER[b[0].kind] ?? 9)) || (a[1] - b[1])).map(p => p[0]);
    } else {
      pool = freshFirst(opicQ().filter(q => (!topic || q.topic === topic) && (!kind || q.kind === kind)), "opic_q", rng).slice(0, n);
    }
    return pool.map((q, i) => qUnit(q, i, pool.length));
  }

  // ---- 기록 ------------------------------------------------------------------------------
  function validItem(exam, task, itemId) {
    if (exam === "tsp" && task in TSP_TASKS) return item(task, itemId);
    if (exam === "opic" && (task === "opic_q" || task === "opic_rp")) return item(task, itemId);
    return null;
  }
  /** 파이썬 _num: 숫자로 바꾸고 NaN 은 null, lo/hi 로 자른다 */
  function num(v, lo, hi) {
    if (v === null || v === undefined || v === "" || typeof v === "boolean") return null;
    let x = typeof v === "string" ? (v.trim() === "" ? NaN : Number(v)) : Number(v);
    if (typeof v === "object") return null;
    if (Number.isNaN(x)) return null;
    if (lo !== undefined && lo !== null) x = Math.max(lo, x);
    if (hi !== undefined && hi !== null) x = Math.min(hi, x);
    return x;
  }
  const iint = x => Math.trunc(x);
  const intNum = (v, lo, hi) => { const x = num(v, lo, hi); return x === null ? 0 : iint(x) || 0; };

  /** rows = [{task, item_id, qidx, points, words, seconds, accuracy, response}] → 저장한 행(검증된 것만) */
  function record(exam, rows, mockId) {
    const saved = [];
    const ts = nowFn();
    const col = attemptsCol();
    store().batch(() => {
      for (const r of Array.isArray(rows) ? rows : []) {
        if (!r || typeof r !== "object" || Array.isArray(r)) continue;
        const task = String(r.task ?? ""), iid = String(r.item_id ?? "");
        const it = validItem(exam, task, iid);
        if (!it) continue;
        const qi = intNum(r.qidx, 0, 20);
        if (exam === "opic" && qi >= (task === "opic_rp" ? (it.steps || []).length : 1)) continue;     // 없는 단계 번호는 저장하지 않는다
        const mx = exam === "tsp" ? TSP_TASKS[task].max : 5;
        const pts = num(r.points, exam === "tsp" ? 0 : 1, mx);
        if (pts === null) continue;
        const w = num(r.words);
        const row = { task, item_id: iid, qidx: intNum(r.qidx, 0, 20), points: pts, max: mx,
                      words: w === null ? null : iint(num(r.words, 0, 2000)), seconds: num(r.seconds, 0, 600), accuracy: num(r.accuracy, 0, 1),
                      response: String(r.response ?? "").slice(0, 4000) };
        col.add({ exam, task: taskKey(exam, task), item_id: iid, qidx: row.qidx, points: pts, max_points: mx, words: row.words, seconds: row.seconds,
                  accuracy: row.accuracy, mock_id: mockId ?? null, response: row.response, created_at: ts });
        saved.push(row);
      }
    });
    return saved;
  }

  const attemptsOf = exam => attemptsCol().all().filter(a => a.exam === exam);
  const byIdDesc = rows => rows.slice().sort((a, b) => b.id - a.id);

  /** 과제별 최근 lastN 답변 평균 (점수 비율·단어 수) */
  function tspTaskStats(lastN = 20) {
    const out = {};
    const all = attemptsCol().all();
    for (const t of TSP_ORDER) {
      const mine = byIdDesc(all.filter(a => a.task === "tsp:" + t));
      const rows = mine.slice(0, lastN);
      if (!rows.length) continue;
      const w = rows.filter(r => r.words !== null && r.words !== undefined).map(r => r.words);
      out[t] = { n: mine.length, ratio: rows.reduce((a, r) => a + r.points / r.max_points, 0) / rows.length, words: w.length ? w.reduce((a, b) => a + b, 0) / w.length : null };
    }
    return out;
  }
  /** 다섯 유형을 모두 연습했으면 유형별 평균 비율로 11문항 점수를 채워 환산 */
  function tspEstimate(stats) {
    if (TSP_ORDER.some(t => !(t in stats))) return null;
    const raw = TSP_ORDER.reduce((a, t) => a + stats[t].ratio * TSP_TASKS[t].max * TSP_TASKS[t].n * TSP_TASKS[t].speak.length, 0);
    return tspScore(raw);
  }
  function opicStats(lastN = 30) {
    const all = attemptsOf("opic");
    const rows = byIdDesc(all).slice(0, lastN);
    if (!rows.length) return { n: 0, avg: null, words: null, grade: null };
    const w = rows.filter(r => r.words !== null && r.words !== undefined).map(r => r.words);
    const avg = rows.reduce((a, r) => a + r.points, 0) / rows.length;
    const words = w.length ? w.reduce((a, b) => a + b, 0) / w.length : null;
    return { n: all.length, avg, words, grade: rows.length >= 5 ? opicGrade(avg, words) : null };
  }
  /** 오픽 주제별 답변 수·평균: [{task,item_id,points}] */
  const topicProgress = (exam = "opic") => attemptsOf(exam).map(a => ({ task: a.task, item_id: a.item_id, points: a.points }));
  /** 최근 답변 (최근 순) */
  const recent = (exam, limit = 15) => byIdDesc(attemptsOf(exam)).slice(0, limit);
  /** 답변 기록 (최근 순) 과 전체 개수 */
  function history(exam, limit = 40, offset = 0) {
    const rows = byIdDesc(attemptsOf(exam));
    return [rows.slice(offset, offset + limit), rows.length];
  }
  /** 날짜별 평균 (점수 비율·단어 수) — 시간이 지나며 늘었는지 본다. today 는 테스트에서 고정 */
  function trend(exam, task, days = 60, today) {
    const cut = U.addDays(today || U.todayStr(), -days);
    const by = {};
    for (const a of attemptsOf(exam)) {
      if (task && a.task !== task) continue;
      const d = a.created_at.slice(0, 10);
      if (d < cut) continue;
      (by[d] = by[d] || []).push(a);
    }
    return Object.keys(by).sort().map(d => {
      const rs = by[d], w = rs.filter(r => r.words !== null && r.words !== undefined).map(r => r.words);
      return { d, n: rs.length, r: rs.reduce((a, r) => a + r.points / r.max_points, 0) / rs.length, w: w.length ? w.reduce((a, b) => a + b, 0) / w.length : null };
    });
  }
  /** 기록 화면에 보여 줄 질문 (없으면 빈 문자열) */
  function questionOf(task, itemId, qidx) {
    const t = task.replace(/^tsp:/, "");
    const it = item(t, itemId);
    if (!it) return "";
    if (t === "read_aloud") return it.text || "";
    if (t === "describe_picture") return "사진 묘사 · " + (it.scene_ko || "");
    if (t === "respond_questions" || t === "respond_info") return (it.questions || [])[qidx] || "";
    if (t === "opic_rp") { const st = it.steps || []; return qidx < st.length ? st[qidx].question : ""; }
    return it.question || "";
  }

  // ---- 모의고사 -------------------------------------------------------------------------
  /** 계획(units: [{task,item_id}…])에서 단계(steps)를 다시 만든다 — 저장한 계획과 같은 번호 매김 */
  function expandPlan(exam, units) {
    const out = [];
    let qno = 1, no = 1;
    for (const u of units) {
      const it = item(u.task, u.item_id);
      if (!it) continue;
      let steps;
      if (exam === "tsp") { steps = tspSteps(u.task, it, qno); qno += steps.length; }
      else if (u.task === "opic_rp") { steps = opicRpSteps(it, no, false); no += steps.length; }
      else { steps = [opicQStep(it, `Question ${no}`, false)]; no += 1; }
      out.push({ task: u.task, item_id: u.item_id, steps });
    }
    return out;
  }
  const compactPlan = plan => plan.map(u => ({ task: u.task, item_id: u.item_id }));

  function createMock(exam, plan, settings) {
    return mocksCol().add({ exam, units: compactPlan(plan), settings: settings || {}, finished_at: null, result: null, score: null, created_at: nowFn() }).id;
  }
  /** 모의고사 하나 (계획은 단계까지 풀어서). 문제 은행을 먼저 읽어 둬야 한다. */
  function getMock(mid) {
    const m = mocksCol().all().find(x => x.id === mid);
    if (!m) return null;
    return { ...m, plan: expandPlan(m.exam, m.units) };
  }
  /** 끝난 모의고사 목록 (최근 순) — 계획은 풀지 않는다 */
  function listMocks(exam, limit = 20) {
    return byIdDesc(mocksCol().all().filter(m => m.exam === exam && m.finished_at))
      .slice(0, limit).map(m => ({ id: m.id, created_at: m.created_at, finished_at: m.finished_at, score: m.score, result: m.result || {} }));
  }
  const plannedOf = plan => {
    const out = new Map();
    for (const u of plan) for (const s of u.steps) out.set(`${s.ref.task}|${s.ref.item_id}|${s.ref.qidx}`, [s.ref.task, s.ref.item_id, s.ref.qidx]);
    return out;
  };

  function finishMock(mid, rows, duration) {
    const m = getMock(mid);
    if (!m) throw new Error("모의고사가 없습니다.");
    if (m.finished_at) return m;
    const planned = plannedOf(m.plan);
    rows = (Array.isArray(rows) ? rows : []).filter(r => r && typeof r === "object" && planned.has(`${r.task}|${r.item_id}|${intNum(r.qidx, 0, 20)}`));
    const saved = record(m.exam, rows, mid);
    if (!saved.length) throw new Error("채점한 답변이 없습니다.");
    duration = num(duration, 0, 6000);                                         // TS 앱 api_mock_finish: _num(duration, 0, 6000)
    const wl = saved.filter(r => r.words !== null).map(r => r.words);
    const avgWords = wl.length ? wl.reduce((a, b) => a + b, 0) / wl.length : null;
    const got = new Map(saved.map(r => [`${r.task}|${r.item_id}|${r.qidx}`, r.points]));
    let result, label;
    if (m.exam === "tsp") {
      let raw = 0;
      for (const k of planned.keys()) raw += got.get(k) || 0;                 // 채점 안 한 문항은 0점
      const score = tspScore(raw);
      const byTask = {};
      for (const r of saved) (byTask[r.task] = byTask[r.task] || []).push(r.points / r.max);
      const bt = {};
      for (const [t, v] of Object.entries(byTask)) bt[t] = v.reduce((a, b) => a + b, 0) / v.length;
      result = { raw, raw_max: TSP_RAW_MAX, score, level: tspLevel(score), by_task: bt, words: avgWords, answered: saved.length, total: planned.size, duration: duration ?? null };
      label = String(score);
    } else {
      // 자기소개는 채점 비중 낮음 → 제외. 답하지 않은 문항은 최저점(1점)으로 센다 — 3문항만 답하고 AL 이 나오지 않게
      let pts = [...planned.entries()].filter(([, k]) => !k[1].startsWith("oq-intro")).map(([key]) => (got.has(key) ? got.get(key) : 1.0));
      if (!pts.length) pts = saved.map(r => r.points);
      const avg = pts.reduce((a, b) => a + b, 0) / pts.length;
      const grade = opicGrade(avg, avgWords);
      result = { avg, grade, grade_name: OPIC_GRADE_NAME[grade] || "", words: avgWords, answered: saved.length, total: planned.size,
                 duration: duration ?? null, level: m.settings.level ?? null };
      label = grade || "";
    }
    mocksCol().update(mid, { finished_at: nowFn(), result, score: label });
    return getMock(mid);
  }
  /** 모의고사 결과 화면용: 이 모의고사에서 저장된 답변 */
  const mockAnswers = mid => attemptsCol().all().filter(a => a.mock_id === mid).sort((a, b) => a.id - b.id);

  // ---- 설정 (TSStore.settings 의 tsp_*, opic_*) ------------------------------------------------
  function tspTarget() {
    const v = parseInt(store().settings().tsp_target || "", 10);
    return Number.isFinite(v) ? v : 140;
  }
  function opicSettings() {
    const st = store().settings();
    const survey = (st.opic_survey || "").split(",").filter(t => t in OPIC_TOPICS && OPIC_TOPICS[t][2]);
    let level = parseInt(st.opic_level || "4", 10);
    if (!Number.isFinite(level)) level = 4;
    const target = st.opic_target || "IH";
    return { survey, level: level in OPIC_LEVELS ? level : 4, target: OPIC_TARGETS.includes(target) ? target : "IH" };
  }

  root.Spk = {
    TSP_TASKS, TSP_ORDER, TSP_RAW_MAX, TSP_LEVELS, TSP_TARGETS, TSP_RUBRIC, TSP_DIRECTIONS, tspScore, tspLevel,
    OPIC_TOPICS, SURVEY_GROUPS, OPIC_KINDS, OPIC_GRADES, OPIC_GRADE_NAME, OPIC_RUBRIC, OPIC_LEVELS, OPIC_TARGETS, OPIC_MINUTES, opicGrade,
    setTsp, setOpic, loadTsp, loadOpic, tspItems, opicQ, opicRp, item, topicCounts,
    samples, tspSteps, tspPractice, tspMockPlan, opicQStep, opicRpSteps, opicMockPlan, opicPractice, expandPlan,
    setNow: fn => { nowFn = fn || U.nowStr; }, record, weakItems, lastSeen, tspTaskStats, tspEstimate, opicStats, topicProgress, recent, history, trend, questionOf,
    createMock, getMock, listMocks, finishMock, mockAnswers, tspTarget, opicSettings, num,
  };
  if (typeof module !== "undefined") module.exports = root.Spk;
})(typeof window !== "undefined" ? window : globalThis);
