# 토플(2026 개편) 문제 데이터 형식

`content/toefl/` 아래 UTF-8 JSON 배열. 검증: `python tools/validate_toefl.py` (TS 폴더에서).
추가 파일은 `이름_2.json` 처럼 붙이면 함께 읽는다 (예: `r_daily_2.json`).

공통
- `id`: 파일 종류 안에서 겹치지 않는 문자열 (접두어 + 3자리, 예 `rw-001`).
- `level`: 1~5. 목표 밴드: 1=밴드 2(A2) · 2=밴드 3(B1) · 3=밴드 4(B2) · 4=밴드 5(C1) · 5=밴드 6(C2).
  "그 밴드 학습자가 절반 정도 맞히는 난이도".
- 객관식 `answer`: 0부터 시작하는 인덱스, 선택지 4개. `explanation`: 한국어 2~3문장.
- 대학 생활·학술 강의·캠퍼스 공지·일상 이메일 등 토플 상황. 영어는 자연스러운 미국 대학 영어.
- 듣기 스크립트는 브라우저 음성 합성으로 읽으므로 지문 안에 무대 지시문 금지.

## Reading

### r_words.json — Complete the Words (빈칸 단어 완성)
```json
{"id":"rw-001","level":3,"topic":"생물학",
 "text":"Bees play a central role in pollination. When a bee visits a flower, it [[col|lects]] nectar and [[pol|len]] ...",
 "translation":"(한국어 번역)"}
```
- `[[보이는부분|채울부분]]` 이 빈칸. 정확히 10개. 첫 문장은 빈칸 없이 온전하게.
- 실제 시험처럼 단어의 뒤쪽 절반쯤을 지운다 (보이는 부분 1글자 이상, 채울 부분 1글자 이상, 영문자만).
- 70~110단어의 학술 문단.

### r_daily.json — Read in Daily Life (일상 글)
```json
{"id":"rd-001","level":2,"doc_type":"공지","title":"Library Hours Update",
 "text":"...", "questions":[{"q":"...","choices":["","","",""],"answer":1,"type":"세부 사항","explanation":"..."}],
 "translation":"..."}
```
- 문항 2~3개. 40~150단어 (공지·이메일·문자·메뉴·일정표·광고·안내문).
- doc_type: 공지 | 이메일 | 문자 메시지 | 안내문 | 광고 | 일정표 | 웹페이지
- type: 주제·목적 | 세부 사항 | 추론 | 어휘 | 사실 확인

### r_academic.json — Read an Academic Passage (학술 지문)
```json
{"id":"ra-001","level":4,"title":"The Origins of Agriculture","text":"...(180~260단어, 문단 2~3개)",
 "questions":[... 정확히 5개 ...],"translation":"..."}
```
- type: 주제·목적 | 세부 사항 | 추론 | 어휘 | 사실 확인 | 글의 구조

## Listening

### l_response.json — Listen and Choose a Response (응답 고르기)
```json
{"id":"lr-001","level":1,"voice":"female","prompt":"Do you know where the chemistry lab is?",
 "choices":["It's on the third floor of Hall B.","I studied chemistry last year.","Yes, the lab report is due Friday.","Chemistry is my favorite."],
 "answer":0,"explanation":"...","translation":"질문: ... / (A) ... / (B) ... / (C) ... / (D) ..."}
```
- 선택지는 글자로 보여 주고 prompt 만 음성으로 들려준다.

### l_conversation.json — Conversations (대화)
```json
{"id":"lc-001","level":2,"topic":"기숙사 문제","speakers":{"M":"male","W":"female"},
 "script":[{"s":"W","t":"..."},{"s":"M","t":"..."}],
 "questions":[{"q":"...","choices":[4개],"answer":2,"type":"주제·목적","explanation":"..."}],
 "translation":"W: ...\nM: ..."}
```
- 6~12턴, 문항 2개. type: 주제·목적 | 세부 사항 | 추론 | 화자 의도 | 다음 할 일

### l_talk.json — Announcements & Academic Talks (안내·강의)
```json
{"id":"lt-001","level":3,"kind":"academic","topic":"천문학","voice":"male",
 "script":"...", "questions":[...], "translation":"..."}
```
- kind: `announcement`(캠퍼스 안내, 80~140단어, 문항 2개) | `academic`(교수 강의, 180~280단어, 문항 4개)
- type: 주제·목적 | 세부 사항 | 추론 | 화자 의도 | 글의 구조

## Writing

### w_sentence.json — Build a Sentence (문장 만들기)
```json
{"id":"ws-001","level":2,"context":"Did you finish the group project?",
 "answer":"We still need to add the final chart.",
 "chunks":["We","still need","to add","the final","chart"],
 "explanation":"...","translation":"A: 프로젝트 끝냈어? / B: 아직 마지막 차트를 넣어야 해."}
```
- context = 상대의 말, answer = 그에 대한 응답 문장. chunks 를 이어 붙이면(공백으로) answer 에서 끝 문장부호를 뺀 것과 정확히 같아야 한다.
- chunks 4~8개 (단어 또는 2~3단어 덩어리), 정답 순서대로 저장 (화면에서 섞는다).
- 의문문·간접의문문·수동태·관계절·비교 구문 등 어순이 중요한 문장.

### w_email.json — Write an Email (이메일 쓰기, 7분)
```json
{"id":"we-001","level":3,"situation":"You recently joined a campus hiking club, but ...",
 "to":"Club president, Ms. Rivera",
 "tasks":["explain the problem","describe what you have tried","ask for a specific solution"],
 "sample":"Dear Ms. Rivera, ... (120~160단어)","sample_ko":"(한국어 번역)",
 "tips":["공손한 요청 표현: Would it be possible to ...?", "..."]}
```

### w_discussion.json — Academic Discussion (학술 토론, 10분)
```json
{"id":"wd-001","level":4,"course":"Environmental Science",
 "professor":"(교수 질문 글 60~100단어)",
 "students":[{"name":"Kelly","post":"(40~70단어)"},{"name":"Andrew","post":"(40~70단어)"}],
 "sample":"(자기 의견 + 이유 + 예시, 110~150단어)","sample_ko":"...","tips":["..."]}
```

## Speaking

### s_repeat.json — Listen and Repeat (듣고 따라 말하기)
```json
{"id":"sr-001","level":2,"topic":"캠퍼스 투어","voice":"female",
 "sentences":["Welcome to the campus tour.","...", "... (점점 길게, 정확히 7문장)"],
 "translations":["캠퍼스 투어에 오신 것을 환영합니다.", "..."]}
```
- 4단어 → 20단어 안팎까지 점점 길어지는 7문장. 한 상황(안내자 멘트)으로 이어짐.

### s_interview.json — Take an Interview (인터뷰, 답변 45초)
```json
{"id":"si-001","level":3,"topic":"온라인 수업 연구 인터뷰",
 "intro":"(인터뷰어 소개 한두 문장)",
 "questions":["(쉬운 개인 질문)","...","...","(의견·가정 질문)"],
 "samples":["(질문별 모범 답변 60~100단어)","...","...","..."],
 "samples_ko":["...","...","...","..."],
 "tips":["..."]}
```
- 질문 정확히 4개, 뒤로 갈수록 추상적(개인 경험 → 의견 → 가정·사회적 쟁점).
