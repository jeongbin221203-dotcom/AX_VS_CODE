# TS — 토익 학습 (개인 공부용)

등급(Orange·Brown·Green·Blue·Gold)과 점수에 맞춰 토익을 공부하는 Flask 앱. 배포하지 않는 개인용.

## 실행

```
pip install -r requirements.txt
python app.py            # http://127.0.0.1:5003
python -m pytest tests -q
python tools/validate_content.py   # 문제 데이터 형식 검사
```

환경변수: `TS_PORT`(기본 5003), `TS_DB_PATH`(기본 data/ts.db), `TS_CONTENT_DIR`, `TS_SECRET_KEY`, `TS_DEBUG=1`.

## 기능

| 화면 | 내용 |
|---|---|
| 오늘 | 현재·목표 점수, D-day, 주당 필요 점수, 등급 사다리, 오늘 할 일(약한 파트 우선), 파트별 정답률, 점수 추이 |
| 등급 가이드 | 등급마다 흔한 모습, 다음 목표, 핵심 과제, 문법 체크리스트, 하루 루틴, 파트별 목표 정답률·풀이 전략 |
| 파트 연습 | Part 1~7 × 등급 × 유형 필터. 안 푼 문제 먼저. 답하면 바로 채점·해설·해석 (듣기는 스크립트) |
| 진단 테스트 | 약 15분, 여러 등급 섞음 → 추정 점수가 현재 점수가 됨 |
| 모의고사 | 미니(40문항)·하프(약 100)·실전(200). 듣기 1회 재생, RC 제한 시간, 끝에 추정 점수 |
| 오답노트 | 틀린 문항 자동 수집, 복습에서 연속 2번 맞히면 졸업, 문항별 메모 |
| 단어 | 등급별 1,600단어, SM-2 간격 반복 카드, 뜻 고르기 퀴즈, 별표, 운전용 듣기·음성 파일 |
| 받아쓰기 | Part 1~4 문장을 듣고 입력 → 단어 단위 비교 |
| 통계 | 점수 추이, 30일 학습량, 파트×등급 정답률, 약한 유형, RC 문항당 시간 |

- 듣기 음성은 브라우저 음성 합성(Web Speech API). 크롬·엣지 권장, 화자 성별·억양(미국·영국·호주)을 나눔.
- Part 1 사진은 한국어 장면 설명으로 대신함.
- 추정 점수는 난이도 가중 정답률을 일반적인 환산 구간에 맞춘 값 (`core/scoring.py`). 실제 점수와 다를 수 있음.

## 구조

```
app.py  config.py
core/     db(SQLite) · content(문제 은행) · scoring(등급·점수) · study(세션·채점·오답노트)
          srs(단어 간격 반복) · planner(오늘 할 일) · stats · guide(등급별 가이드 문구)
views/    main(대시보드·가이드·통계·설정) · quiz(연습·진단·모의고사·오답·받아쓰기) · vocab
static/js quiz.js(풀이 엔진) · tts.js(음성) · vocab.js · vocab-extra.js · dictation.js · charts.js
content/toeic/  part1~7.json, vocab.json — 형식은 content/SCHEMA.md
```

## 문제 은행 (content/toeic)

기본 파일(`partN.json`)과 추가 파일(`partN_2.json`, `vocab_2.json` …)을 합쳐 읽는다. 파일이 바뀌면 서버를 다시 켜지 않아도 자동 반영.

| 파트 | 개수 | 문항 |
|---|---|---|
| Part 1 | 70 | 70 |
| Part 2 | 250 | 250 |
| Part 3 | 60세트 | 180 |
| Part 4 | 50세트 | 150 |
| Part 5 | 350 | 350 |
| Part 6 | 50세트 | 200 |
| Part 7 | 70세트 (단일·이중·삼중) | 281 |
| 단어 | 1,600 (등급별 320) | |

문제는 모두 새로 작성한 연습용 문제. 추가할 때는 SCHEMA.md 형식으로 새 JSON(`part5_3.json` 등)을 만들고 `python tools/validate_content.py`로 검사(파일 간 id·단어 중복 포함).

## 단어 듣기 (운전 모드)

`/vocab/listen` — 영어 단어 N번(기본 3) → 한국어 뜻 → (예문) → 다음 단어, 정지할 때까지 반복. 화면 꺼짐 방지, 마지막 위치 기억.
엣지 권장(자연스러운 한국어 음성). 휴대폰에서 화면을 끄고 들으려면 같은 화면의 **음성 파일 받기**(50단어씩 WAV, Windows 내장 음성으로 오프라인 생성, `data/audio/`에 캐시).
