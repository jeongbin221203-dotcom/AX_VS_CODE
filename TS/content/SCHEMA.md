# 토익 문제 데이터 형식

모든 파일은 `content/toeic/` 아래 UTF-8 JSON 배열이다. 검증: `python tools/validate_content.py` (TS 폴더에서).

공통 규칙
- `id`: 파일 안에서 겹치지 않는 문자열. 접두어 + 3자리 번호 (예: `p5-001`, `p7-012`, `v-0231`).
- `level`: 1~5 정수. 1=Orange(10~215), 2=Brown(220~465), 3=Green(470~725), 4=Blue(730~855), 5=Gold(860~990).
  "그 등급 학습자가 풀어야 할 난이도"를 뜻한다 (level 3 = 470~725점대가 절반 정도 맞히는 수준).
- `answer`: 정답 선택지의 0부터 시작하는 인덱스.
- `explanation`: 한국어 해설 (왜 정답인지 + 오답이 왜 틀렸는지 핵심, 2~4문장).
- `type`: 문제 유형 태그(한국어, 아래 목록에서 고른다).
- 영어 문장은 실제 토익처럼 비즈니스·일상 업무 상황(사무실, 회의, 출장, 제품, 고객, 채용, 행사, 공지 등).

## part1.json — 사진 묘사 (선택지 4개)
```json
{"id":"p1-001","level":1,"type":"1인 동작",
 "scene":"한 여성이 사무실 책상에서 노트북으로 타이핑을 하고 있다. 책상 위에 커피잔과 서류 더미가 있다.",
 "statements":["She is typing on a laptop.","She is drinking some coffee.","She is filing some documents.","She is talking on the phone."],
 "answer":0,"explanation":"..."}
```
type: 1인 동작 | 2인 이상 동작 | 사물·배경 | 상태(수동태 진행/완료)

## part2.json — 질의응답 (선택지 3개)
```json
{"id":"p2-001","level":2,"type":"Where 의문문",
 "question":"Where is the quarterly report?",
 "choices":["It's on your desk.","Every three months.","Yes, I reported it."],
 "answer":0,"explanation":"...","translation":"분기 보고서는 어디 있나요? / (A) 당신 책상 위에 있어요. ..."}
```
type: Who/When/Where/What·Which/Why/How 의문문 | 일반 의문문 | 부정·부가 의문문 | 선택 의문문 | 요청·제안 | 평서문 | 간접 응답

## part3.json — 짧은 대화 (세트, 문항 3개, 선택지 4개)
```json
{"id":"p3-001","level":3,"topic":"회의 일정 변경",
 "speakers":{"M":"male","W":"female"},
 "script":[{"s":"W","t":"Hi, Tom. ..."},{"s":"M","t":"..."}],
 "graphic":null,
 "questions":[{"q":"What are the speakers mainly discussing?","choices":["...","...","...","..."],"answer":1,"type":"주제·목적","explanation":"..."}],
 "translation":"(대화 전체 한국어 번역)"}
```
- speakers 키: `M`, `W`, 3인 대화면 `M2` 또는 `W2`. 값은 `male`/`female`.
- graphic: 시각 자료 연계 문제용. 없으면 null. 있으면 `{"title":"...","rows":[["헤더1","헤더2"],["값","값"]]}` (표).
question type: 주제·목적 | 화자 정보(직업·장소) | 세부 사항 | 요청·제안 | 다음 할 일 | 의도 파악 | 시각 자료 연계

## part4.json — 짧은 담화 (세트, 문항 3개, 선택지 4개)
```json
{"id":"p4-001","level":3,"topic":"공항 안내 방송","talk_type":"안내 방송","voice":"female",
 "script":"Attention passengers ...", "graphic":null,
 "questions":[...같은 형식...], "translation":"..."}
```
talk_type: 전화 메시지 | 안내 방송 | 회의 발췌 | 광고 | 방송·뉴스 | 연설·소개 | 관광·견학 안내

## part5.json — 단문 빈칸 (선택지 4개)
```json
{"id":"p5-001","level":2,"type":"품사",
 "question":"The new software allows employees to work more ------- .",
 "choices":["efficient","efficiently","efficiency","efficiencies"],
 "answer":1,"explanation":"...","translation":"새 소프트웨어는 직원들이 더 효율적으로 일할 수 있게 해 준다."}
```
- 빈칸은 반드시 `-------` (하이픈 7개)로 표시.
type: 품사 | 동사 시제·태 | 수 일치 | 대명사 | 전치사 | 접속사 | 관계사 | 준동사(to부정사·동명사·분사) | 비교 | 어휘

## part6.json — 장문 빈칸 (세트, 문항 4개, 선택지 4개)
```json
{"id":"p6-001","level":3,"doc_type":"이메일","title":"To: all staff / Subject: Office renovation",
 "passage":"Dear staff,\n\nThe third floor will be {1} next week. ... {2} ... {3} ... {4}",
 "questions":[{"q":"","choices":["...","...","...","..."],"answer":0,"type":"동사 시제·태","explanation":"..."}],
 "translation":"..."}
```
- 지문 안의 `{1}`~`{4}`가 questions[0]~[3]의 빈칸. 4개 중 1개는 문장 삽입 문제(type "문장 삽입", 선택지가 완전한 문장).
- `q`는 빈 문자열.
doc_type: 이메일 | 공지 | 기사 | 광고 | 편지 | 안내문

## part7.json — 독해 (세트)
```json
{"id":"p7-001","level":3,"kind":"single",
 "passages":[{"doc_type":"이메일","title":"From: ... / To: ... / Subject: ...","text":"..."}],
 "questions":[{"q":"Why was the e-mail sent?","choices":["...","...","...","..."],"answer":2,"type":"주제·목적","explanation":"..."}],
 "translation":"..."}
```
- kind: `single`(지문 1개, 문항 2~4) | `double`(지문 2개, 문항 5) | `triple`(지문 3개, 문항 5).
- 문장 삽입 문제는 지문에 `[1]` `[2]` `[3]` `[4]` 위치 표시 + 선택지 `"[1]"`..`"[4]"`.
question type: 주제·목적 | 세부 사항 | 사실 확인(NOT/TRUE) | 추론 | 동의어 | 문장 삽입 | 의도 파악 | 연계 문제

## vocab.json — 단어
```json
{"id":"v-0001","level":1,"word":"invoice","pos":"n.","meaning":"청구서, 송장",
 "example":"Please send the invoice by Friday.","example_ko":"금요일까지 청구서를 보내 주세요.",
 "tip":"issue an invoice (청구서를 발행하다)"}
```
- pos: n. | v. | adj. | adv. | prep. | conj. | phr.
- tip: 자주 나오는 짝꿍 표현·혼동 단어·파생어. 없으면 빈 문자열.
