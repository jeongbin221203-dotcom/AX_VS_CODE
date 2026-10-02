# 말하기 시험(토익스피킹·오픽) 문제 데이터 형식

UTF-8 JSON 배열. 검사: `python tools/validate_speaking.py` (TS 폴더에서).
추가 파일은 `이름_2.json`, `이름_b.json` 처럼 붙이면 함께 읽는다 (예: `opinion_2.json`, `questions_c.json`).

공통 원칙
- 모두 새로 쓴 문제 (기출·교재 문장 복사 금지). 영어는 자연스러운 미국 영어.
- 질문·지문은 브라우저 음성 합성으로 읽으므로 괄호 지시문·이모지 금지.
- 모범 답안은 **실제로 말하는 문장**: 짧은 문장 위주, 구어 연결어(Well, Actually, So, Also, That's why …) 사용.
  - `sample` = 기본 답변 (IM2~IM3 / 토익스피킹 130~150 목표): 쉬운 단어, 정확한 문장.
  - `sample_adv` = 고득점 답변 (IH~AL / 160 이상 목표): 더 길고, 구체적 예시·다양한 시제·연결어.
  - `_ko` = 바로 앞 영어의 자연스러운 한국어 번역.
- `tips`: 한국어 1~3개. 그 문제에 바로 쓸 수 있는 표현·전략 (예: "장소 묘사 순서: 전체 → 가운데 → 양옆 → 배경").
- `id` 는 파일 종류 안에서 겹치지 않게 (접두어 + 3자리).

---

## 토익스피킹 `content/speaking/toeic/`

### read_aloud.json — Q1~2 문장 읽기 (준비 45초 · 읽기 45초)
```json
{"id":"ra-001","type":"안내 방송",
 "text":"Attention, shoppers. Starting this Friday, Green Valley Market will be open until ten p.m. every night. ...",
 "marked":"Attention, *shoppers*. / Starting this *Friday*, / Green Valley *Market* / will be open until *ten* p.m. / every *night*. ...",
 "tricky":["schedule — 미국식 [스케줄]","produce — 명사는 앞에 강세 [PRO-duce]"],
 "translation":"(한국어 번역)", "tips":["나열 A, B, and C 는 ↗ ↗ ↘ 억양"]}
```
- `text` 45~70단어, 문장 3~5개. 실제 시험처럼 **나열(A, B, and C)**, 고유명사, 숫자·시간, 의문문 중 2가지 이상 포함.
- `marked` = text 와 같은 문장에 끊어 읽기 ` / ` 와 강세 단어 `*단어*` 표시. `/` 와 `*` 를 지우면 text 와 같아야 한다.
- `tricky` 2~4개: "단어 — 한국어 발음 설명".
- type: 안내 방송 | 광고 | 뉴스 | 자동 응답 메시지 | 소개 | 일기 예보 | 교통 정보 | 행사 안내

### describe_picture.json — Q3~4 사진 묘사 (준비 45초 · 답변 30초)
사진을 넣을 수 없어 **장면 설명**으로 대신한다.
```json
{"id":"dp-001","setting":"사무실",
 "scene_ko":"밝은 사무실. 네 명이 회의 탁자에 둘러앉아 있다. ...(2~3문장)",
 "elements":[
   {"where":"가운데","ko":"여자가 서서 화이트보드의 그래프를 가리키고 있다","en":"A woman is standing and pointing at a graph on a whiteboard."},
   {"where":"왼쪽","ko":"...","en":"..."}],
 "sample":"(50~70단어)","sample_ko":"...","sample_adv":"(75~95단어)","sample_adv_ko":"...","tips":["..."]}
```
- elements 4~6개, where: 가운데 | 왼쪽 | 오른쪽 | 앞쪽 | 뒤쪽 | 배경 | 전체 중 하나. `en` 은 현재진행형·there is·위치 표현을 쓴 완전한 문장.
- 모범 답안 구성: 장소 한 문장 → 가장 눈에 띄는 사람 → 다른 사람·사물 → 배경 → 느낌·추측 한 문장.
- setting 은 다양하게: 사무실 | 회의실 | 식당 | 카페 | 거리 | 시장 | 공원 | 공항 | 기차역 | 공사장 | 상점 | 도서관 | 주방 | 창고 | 강의실 | 병원 대기실 | 호텔 로비 | 버스 정류장 | 항구 | 농장 등.

### respond_questions.json — Q5~7 질문에 답하기 (준비 3초 · 15/15/30초)
```json
{"id":"rq-001","topic":"영화관",
 "intro":"Imagine that a marketing firm is doing research in your area. You have agreed to participate in a telephone interview about going to the movies.",
 "intro_ko":"...",
 "questions":["When was the last time you went to a movie theater, and who did you go with?",
              "How do you usually find out about new movies?",
              "Would you prefer to watch a new movie at a theater or at home? Why?"],
 "questions_ko":["...","...","..."],
 "samples":["(Q5 25~35단어)","(Q6 25~35단어)","(Q7 50~70단어)"],
 "samples_ko":["...","...","..."],
 "samples_adv":["(Q5 35~45단어)","(Q6 35~45단어)","(Q7 70~90단어)"],
 "samples_adv_ko":["...","...","..."],
 "tips":["Q5·Q6 는 질문의 단어를 그대로 살려 첫 문장을 만든다", "..."]}
```
- intro 는 전화 설문(marketing firm / research) 또는 친구·동료와의 전화 대화 상황. 한 주제로 이어지는 질문 3개.
- Q5·6 은 의문사 2개를 묻는 질문(When … and who …?)이 흔하다. Q7 은 선택·의견·추천 + 이유.

### respond_info.json — Q8~10 표 보고 답하기 (표 45초 · 준비 3초 · 15/15/30초)
```json
{"id":"ri-001","doc_type":"행사 일정표","voice":"male",
 "info":{"title":"Regional Marketing Conference","subtitle":"Saturday, May 14 · Harbor Hotel, Room 302 · Registration fee: $50",
         "rows":[["9:00 – 9:30 A.M.","Opening Remarks","Linda Park"],["9:30 – 10:30 A.M.","Workshop: Social Media Trends","Tom Rivers"]],
         "notes":["Lunch will be provided in the hotel restaurant."]},
 "intro":"Hi, this is Jason. I'm going to attend the marketing conference, but I lost my schedule. Could you help me with a few questions?",
 "questions":["What time does the conference start, and where is it?",
              "I heard the registration fee is forty dollars. Is that right?",
              "Can you tell me about all the sessions in the afternoon?"],
 "questions_ko":["...","...","..."],
 "samples":["(Q8)","(Q9: 잘못 안 정보를 바로잡기)","(Q10: 두 개 이상 항목을 순서대로)"],
 "samples_ko":["...","...","..."],
 "tips":["Q9 는 보통 잘못 알고 있는 정보 — 'Actually, ...' 로 바로잡는다"]}
```
- doc_type: 행사 일정표 | 면접 일정 | 출장 일정 | 이력서 | 수업 시간표 | 예약표 | 여행 일정 | 강연 일정 | 회의 안건 | 교육 일정 중 하나.
- rows 5~8줄, 각 줄은 2~4칸 (시간·내용·담당자/장소). 이력서는 ["2019 – Present","Sales Manager, Bright Tech"] 처럼.
- Q8 = 기본 정보(날짜·장소·시간), Q9 = 질문자가 잘못 알고 있는 정보 바로잡기, Q10 = 조건에 맞는 항목 2~3개를 모두 설명.
- 질문의 답이 반드시 표 안에 있어야 한다. samples 만 있으면 된다(고득점 답안 불필요).

### opinion.json — Q11 의견 말하기 (준비 45초 · 답변 60초)
```json
{"id":"op-001","topic":"직장",
 "question":"Do you agree or disagree with the following statement? Employees should be allowed to work from home at least two days a week. Give specific reasons and examples to support your opinion.",
 "question_ko":"...",
 "outline":["의견: 찬성","이유 1: 출퇴근 시간 절약 → 업무 집중","예시: 친구 회사 사례","마무리: 그래서 찬성"],
 "sample":"(110~140단어)","sample_ko":"...","sample_adv":"(140~170단어)","sample_adv_ko":"...","tips":["..."]}
```
- 질문 형식을 섞는다: 찬반(Do you agree or disagree …) | 선택(Which do you prefer, A or B?) | 장단점(What are the advantages of …?) | 중요한 것(What is the most important …?).
- topic: 직장 | 교육 | 기술 | 생활 | 사회 | 리더십 | 소비 | 건강 | 환경 | 지역 사회 중 하나.

---

## 오픽 `content/speaking/opic/`

주제 key 는 `core/speaking.py` 의 `OPIC_TOPICS` 에 있는 것만 쓴다.
- 설문 주제: home movie concert park beach cafe shopping tv games music cooking pets jogging walking gym bike swimming hiking travel_dom travel_abroad staycation
- 돌발 주제: recycling bank hotel phone tech transport furniture weather holiday health food friends appointment fashion housework neighborhood library geography industry
- 자기소개: intro

### questions.json · questions_*.json — 단일 문항
```json
{"id":"oq-movie-001","topic":"movie","kind":"describe","level":1,
 "question":"You indicated in the survey that you like to go to the movies. What kinds of movies do you like to watch? Who are your favorite actors? Why do you like them?",
 "question_ko":"...",
 "sample":"(IM: 90~130단어)","sample_ko":"...","sample_adv":"(IH~AL: 170~230단어)","sample_adv_ko":"...",
 "tips":["..."]}
```
- kind: `intro`(자기소개, topic=intro) | `describe`(묘사: 장소·사람·물건) | `routine`(습관: 보통 무엇을 어떻게 하는지) |
  `past`(경험: 기억에 남는 일·처음 했던 때·최근에 한 일 — 과거 시제 이야기) | `compare`(과거와 현재 비교·변화) |
  `issue`(사회 이슈·뉴스·사람들의 걱정 — 고난도).
- level: 그 문항이 나오는 최소 설문 난이도 (describe·routine 1~2, past 3, compare·issue 5).
- 실제 오픽처럼 한 질문 안에 소질문 2~3개를 이어 붙인다 ("Tell me about … What … Why …?").
- 설문 주제 질문은 "You indicated in the survey that …" / "I would like to know …" 로, 돌발은 설문 언급 없이 시작.
- 주제마다 최소: describe 2 · routine 1 · past 2 · compare 1 · issue 1 (= 7문항).
- 고득점 답안은 서론 한 문장(질문 받아 말하기) → 본론(구체적 이름·장소·시간·감정) → 마무리 한 문장 구조, 말하듯 자연스럽게.

### roleplay.json · roleplay_*.json — 롤플레이 3문항 세트
```json
{"id":"rp-001","topic":"movie","level":3,
 "situation_ko":"영화를 보러 가려고 극장에 전화해 상영 정보를 묻는 상황 → 표에 문제가 생김 → 비슷한 경험",
 "steps":[
  {"kind":"ask","question":"I'd like to give you a situation and ask you to act it out. You want to see a movie with a friend this weekend. Call the theater and ask three or four questions to get the information you need.",
   "question_ko":"...","sample":"...","sample_ko":"...","sample_adv":"...","sample_adv_ko":"...","tips":["..."]},
  {"kind":"solve","question":"I'm sorry, but there is a problem I need you to resolve. ... Call your friend, explain the situation, and give two or three alternatives.", ...},
  {"kind":"exp","question":"That's the end of the situation. Have you ever had a similar problem ...? Tell me what happened ...", ...}]}
```
- steps 정확히 3개, 순서 ask → solve → exp.
- ask 답안은 인사 → 상황 설명 → 질문 3~4개 → 인사. solve 는 사과/상황 설명 → 대안 2~3개 → 마무리.
- level 3~6. topic 은 설문·돌발 주제 어느 것이든.
