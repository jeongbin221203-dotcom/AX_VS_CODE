# TS (TS_word) — 영어 시험 학습, 서버 없는 HTML·JS 판

TS 영어 시험 앱(`../TS`, Flask)의 기능을 서버 없이 HTML·JS 로 돌아가게 옮긴 것입니다.
- **단어**: 토익 단어 1,800개 + 토플 학술 어휘 800개 (카드·시험·듣기·단어장)
- **토익**: 오늘 할 일, 파트 연습(Part 1~7, 3,554문항), 진단 테스트, 모의고사(실전 200문항·하프·미니), 풀이 엔진, 결과, 오답노트, 받아쓰기, 통계, 등급 가이드
- **토플**: 2026년 1월 개편 11개 과제(읽기·듣기·말하기·쓰기) 연습, 영역별 추정 밴드(1~6), 실전 모의고사(적응형), 오답노트, 기록, 학술 어휘 (문제 960묶음)
- **토익스피킹·오픽**: 11문항 구성·유형별 연습·실전 모의고사·점수(0~200)/레벨 추정, 오픽 설문·주제별 연습·15문항 모의고사·NL~AL 등급 추정, 약한 문항 다시, 기록, 답변 틀·채점 기준 (문제 589 + 698)

## 쓰는 법
- `index.html` 을 더블클릭하면 바로 열립니다 (서버·설치 필요 없음).
- **휴대폰에서 쓰려면** 폴더를 정적 호스팅(GitHub Pages 등)에 올리거나, PC에서 `python -m http.server 8000` 후 같은 와이파이로 접속합니다.
  서버로 열면 **홈 화면에 설치**할 수 있고, 한 번 열어 두면 **인터넷이 없어도** 공부됩니다(서비스 워커).
  `file://` 로는 휴대폰 브라우저가 막는 경우가 많습니다.

## Vercel 배포
빌드가 필요 없는 정적 사이트입니다. `vercel.json`(서비스 워커·매니페스트 헤더)과 `.vercelignore`(tests·tools 제외)가 들어 있습니다.

1. Vercel → **Add New → Project** → GitHub 저장소(`AX_VS_CODE`)를 가져옵니다.
2. **Root Directory** 를 `study/TS_word` 로 고릅니다.
3. **Framework Preset** 은 `Other`, Build Command·Output Directory 는 **비워 둡니다**(Install Command 도 비움).
4. Deploy 한 뒤 **Settings → Git → Production Branch** 를 `TS_word` 로 바꿉니다(그래야 이 브랜치가 운영 주소가 됩니다).

배포되면 https 라서 휴대폰에서 홈 화면에 설치·오프라인 사용이 됩니다. 이후에는 `TS_word` 브랜치에 푸시할 때마다 자동으로 다시 배포됩니다.
데이터·화면 파일을 고치면 `sw.js` 의 `CACHE` 이름(`ts-word-v2`)을 올려 푸시하세요.

## 화면
| 파일 | 내용 | 스크립트 |
|---|---|---|
| `index.html` | **허브**: 시험 4개 카드(지금 위치·목표·문제 수·D-day·바로가기·단어 버튼) | `hub.js`, `hub-extra.js` |
| `toeic.html` | 토익 오늘: 이어서 풀기, 오늘의 단어, 현재/목표/시험일, 등급 사다리, 오늘 할 일, 파트별 정답률, 점수 추이 | `toeic.js` |
| `practice.html` | 파트 연습 (`?start=1&part=5&level=2&type=품사&n=10` 이면 바로 시작) | `practice.js` |
| `diagnostic.html` | 진단 테스트 (약 45문항·12분) | `diagnostic.js` |
| `mock.html` | 모의고사: 실전(200문항, LC 자동 진행 + RC 75분)·하프·미니, 안 푼 문제로 볼 수 있는 횟수, 지난 기록 | `mock.js` |
| `solve.html?sid=N` | **풀이 화면** (연습·복습은 문제마다 채점, 진단·모의고사는 끝에 제출) | `engine.js`, `solve.js` |
| `result.html?sid=N` | 결과: 점수·파트별·틀린 유형·문제 다시 보기 | `engine.js`, `result.js` |
| `review.html` | 오답노트 (`?start=1&n=10&part=5` 이면 바로 복습 시작) | `review.js` |
| `dictation.html` | 받아쓰기 (단어 단위 LCS 비교) | `dictation.js` |
| `stats.html` / `history.html` | 통계(차트 2개·파트×등급 정답률·약한 유형·단어 진도) / 전체 기록 | `stats-page.js` / `history.js` |
| `guide.html?level=N` | 등급별 가이드 (Orange~Gold) | `guide.js` |
| `words.html` | 단어 홈 (예전 `index.html`) | `home.js` |
| `study.html` `quiz.html` `list.html` `listen.html` | 단어 카드·시험·단어장·듣기 | 같은 이름 `.js` |
| `settings.html` | 시험별 목표·시험일, 하루 학습량, 내 등급, 음성, **백업·복원·초기화** | `settings.js`, `backup.js` |
| `toefl.html` | **토플 홈**: 종합·영역별 추정 밴드, 밴드 표(예전 점수 대응), 과제 11개 연습 시작(난이도 고르기), 최근 기록, 목표 밴드 | `toefl-home.js` |
| `toefl-practice.html?task=r_words&level=3&n=3` | 과제 연습 (`review=1` 이면 틀린 문제만). 빈칸·읽기·듣기·응답·문장 만들기·이메일/토론(타이머+자기 채점)·따라 말하기·인터뷰(녹음) | `toefl-practice.js`, `toefl-speech.js` |
| `toefl-mock.html` → `toefl-mock-run.html?id=N` → `toefl-mock-result.html?id=N` | 모의고사: 안내·시작 → 진행(Reading → Listening → Writing → Speaking, 읽기·듣기 2단계 적응형) → 결과 | `toefl-mock.js`, `toefl-mock-run.js`, `toefl-mock-result.js` |
| `toefl-review.html` / `toefl-history.html` | 토플 오답노트 / 기록(날짜별·모의고사) | `toefl-review.js` / `toefl-history.js` |
| `toeic-speaking.html` `opic.html` | **토익스피킹 홈 / 오픽 홈** (추정 점수·등급, 유형·주제별 연습, 모의고사 기록, 약한 문항) | `spk-pages.js` |
| `tsp-practice.html` `opic-practice.html` `tsp-mock-run.html` `opic-mock-run.html` | 말하기 진행 화면(질문 듣기 → 준비 → 삐 → 녹음·음성 인식 → 스스로 채점) | `spk-run.js` |
| `tsp-mock.html` `opic-mock.html` `tsp-mock-result.html` `opic-mock-result.html` `tsp-guide.html` `opic-guide.html` `opic-survey.html` `speaking-history.html?exam=toeic\|opic` | 모의고사 시작·결과, 답변 틀·채점 기준, 오픽 설문·난이도, 답변 기록 | `spk-pages.js` |

위쪽 탭(토익·토플·토익스피킹·오픽)을 누르면 그 시험의 메뉴가 아래 줄에 나옵니다. 단어 화면에서는 한 줄 더(단어 홈·카드·시험·단어장·듣기, 토익 단어 ↔ 토플 학술 어휘)가 나옵니다.

## TS 앱에서 옮긴 것
| TS 앱 기능 | 여기서는 |
|---|---|
| 카드 학습·간격 반복(SM-2) | 그대로 (`js/store.js`, 같은 규칙) |
| 뜻 고르기 시험·단어장·듣기 모드 | 그대로 |
| 파트 연습·진단·모의고사(실전 포함)·오답 복습 | 그대로 (`js/sessions.js`, 파이썬 `core/study.py` 와 같은 규칙) |
| 점수 추정(난이도 가중·원점수 환산)·등급 | 그대로 (`js/scoring.js`) |
| 통계·연속 학습일·학습 계획·약한 파트 | 그대로 (`js/stats.js`, `js/planner.js`) |
| 풀이 엔진(`quiz.js`)·LC 자동 진행·RC 타이머·답안 백업·이어서 풀기 | `js/engine.js` (서버 호출만 저장소 호출로 바꿈) |
| 단어를 먼저(오늘의 단어 카드) | 오늘 화면·허브 |
| 1시간 MP3 만들기 | `tools/make_audio.py` (컴퓨터에서 한 줄 실행) |
| 학습 기록 | `tools/import_ts_db.py` 로 TS 앱 기록(단어+토익 풀이)을 가져옴 |
| 설치·오프라인 | 매니페스트 + 서비스 워커 |
| 기기 간 동기화 | **없음** — 서버가 없어서. 백업 파일로 옮깁니다 |

**다르게 한 것**: 서버 SQLite 대신 브라우저 localStorage(`ts:v1`) · 오답노트는 "연속 2번"이 아니라 코드대로 **서로 다른 날 2번** 맞혀야 졸업(화면 문구도 맞춤) · 정답이 문제 파일에 같이 들어 있어(서버 없음) 개발자 도구로는 볼 수 있음(풀이 화면에는 채점 전에 정답을 보내지 않음) · 시험 풀이 기록은 문항 25,000건이 넘으면 오래된 것부터 정리.

## 복습 일정
TS 앱(`core/srs.py`)과 같은 규칙입니다. 모름 → 내일 다시, 알면 1일 → 4일 → 이전 간격 × 계수, 간격 21일 이상 = 암기 완료.
파이썬과 자바스크립트의 반올림이 달라(6.5 → 6 / 7) 일정이 어긋나므로 파이썬 방식(`TSU.pyRound`)으로 맞춰 두었고,
`tests/store.test.js` 가 384개 경우를, `tests/exam.test.js` 가 점수 환산·통계·계획을 TS 앱 결과와 대조합니다.

## 기록은 어디에 저장되나
| 키 | 내용 | 코드 |
|---|---|---|
| `ward:v1` | 단어 카드·복습 기록·단어 시험 기록·단어 설정(하루 새 단어·내 등급·음성) | `js/store.js` (`Ward`) |
| `ts:v1` | 시험 목표·시험일, 풀이 세션, 문항별 기록, 오답노트, (다른 시험의 기록 `ext`) | `js/tsstore.js` (`TSStore`) |
| `ts:draft:<세션번호>` | 진행 중인 모의고사·진단의 임시 답안 (끝나면 지워짐) | `TSStore.getDraft/setDraft` |
| `ts:v1` 의 `ext.toefl_attempts` / `ext.toefl_mocks` | 토플 풀이 기록(문항마다) / 토플 모의고사 (계획=문제 번호, 결과) | `js/toefl-core.js` (`Toefl`) |
| `ts-tmock-<모의고사번호>` | 진행 중인 토플 모의고사에서 **끝낸 영역**의 결과 (끝나면 지워짐) | `js/toefl-mock-run.js` |

**기기끼리 합쳐지지 않으니** 브라우저 데이터를 지우거나 기기를 바꾸기 전에 설정 → 백업 파일을 내려받으세요.
백업 파일은 하나(단어+시험)입니다. 불러올 때 **합치기**(지금 기록을 지키며 더함, 같은 풀이는 중복 없음)와 **바꾸기**(통째로 교체)를 고를 수 있고,
단어만 든 예전 백업을 "바꾸기"로 불러도 시험 기록은 지워지지 않습니다.

## TS 앱 기록 가져오기
    python tools/import_ts_db.py                 # ../TS/data/ts.db → ts-word-backup.json (원본 DB 는 읽기만 함)

만든 파일을 설정 → 백업 불러오기(합치기)로 불러옵니다. 복습 카드·복습 기록·단어 시험·하루 새 단어 수·음성 설정, **토익 풀이 세션·문항 기록·오답노트·목표/시험일**이 옮겨집니다.
**토플 풀이 기록(`toefl_attempts`)과 모의고사(`toefl_mocks`)도 함께 옮겨집니다** (`ts.ext`). 모의고사 계획은 문제 전체 대신 번호만 남깁니다. 토익스피킹·오픽 답변(`speaking_attempts`)·모의고사(`speaking_mocks`)도 `ts.ext` 로 옮겨집니다(모의고사 계획은 문제 번호만).

## 긴 MP3 만들기 (화면을 끄고 듣기)
브라우저만으로는 음성 파일을 만들 수 없어서 컴퓨터에서 실행합니다.

    pip install edge-tts lameenc
    python tools/make_audio.py --set toeic --level 1 --tier core --minutes 60 --repeats 3
    python tools/make_audio.py --set toeic --which weak --backup ts-word-backup.json   # 자주 잊는 단어만

`audio/` 폴더에 `토익단어_Orange_필수_60분_01.mp3` 처럼 만들어집니다(이 폴더는 git 에 올리지 않음).
**듣기 화면(listen.html) 아래에 지금 고른 조건 그대로의 명령이 만들어져** 복사만 하면 됩니다.
음성은 Microsoft 온라인 음성(edge-tts)이라 인터넷이 필요하고, 처음엔 몇 분 걸립니다(만든 조각은 `audio/clips/` 에 남아 다음엔 빠름).

## 데이터를 바꾸려면
단어·문제 원본은 TS 앱의 `content/toeic/*.json`, `content/toefl/vocab*.json` 입니다. 고친 뒤:

    python tools/build_data.py            # ../TS 를 읽어 data/*.js 를 모두 다시 만듭니다

| 만들어지는 파일 | 내용 |
|---|---|
| `data/toeic.js` `data/toefl.js` | 단어 (`window.WARD_DATA.toeic / .toefl`) |
| `data/toefl-r_words.js` … `toefl-w_discussion.js` (11개) | 토플 문제 은행 과제별 (`window.TS_DATA.tf_<과제>`, 정답·해설·모범 답안 포함, `tools/build_toefl.py` 가 만듦, 필요한 과제만 읽음) |
| `data/toeic-p1.js` ~ `toeic-p7.js` | 토익 문제 은행, 파트별 (`window.TS_DATA.p1 ~ .p7`, 정답·해설·번역 포함) |
| `data/guide.js` | 등급별 학습 가이드·풀이 전략·목표 풀이 시간 (`window.TS_GUIDE`) |
| `data/meta.js` | 파트·등급·유형별 문항 수 요약 (`window.TS_META`) — 문제 데이터를 읽지 않고도 개수를 보여 줌 |

`data/*.js` 는 직접 고치지 마세요(자동 생성). `file://` 에서도 열리도록 JSON 대신 `<script>` 로 읽는 `.js` 로 만듭니다.
단어·문제를 고치거나 파일을 더하면 `sw.js` 의 `CACHE` 이름을 올려야 오프라인 복사본이 새로 만들어집니다.

---

# 공통 기반 API (2단계 — 토플·토익스피킹·오픽 — 가 쓰는 것)

모든 모듈은 `<script>` 로 읽는 일반 스크립트이고 전역(`window.이름`)에 붙습니다. 화면 파일은 `tools/make_pages.py` 로 만들기 때문에 스크립트 순서를 신경 쓰지 않아도 됩니다.
node 에서도 같은 파일이 돌아가도록 `document` 를 쓰지 않는 모듈이 많습니다(테스트가 그대로 읽음).

## 모듈 한눈에
| 전역 | 파일 | 역할 | 화면 필요 |
|---|---|---|---|
| `TSU` | `js/tsutil.js` | 날짜 문자열(`todayStr` `nowStr` `addDays` `daysBetween`), **`pyRound`(파이썬 반올림)**, `pct` `mmss` `fmtTime`, 시드 가능한 난수 `makeRng(seed)` `shuffle` | 아니오 |
| `Score` | `js/scoring.js` | 토익 등급·점수 환산·모의고사 구성표 (TS `core/scoring.py`) | 아니오 |
| `TSNav` | `js/exams.js` | **시험 4개 정의 `EXAMS`, 위쪽 메뉴 정의 `MENUS`** | 아니오 |
| `Ward` | `js/store.js` | 단어 저장소(`ward:v1`)·간격 반복. `Ward.setClock(fn)`(테스트용) `Ward.logEntries()` | 아니오 |
| `TSStore` | `js/tsstore.js` | **시험 저장소(`ts:v1`)**: 설정·세션·풀이 기록·오답노트·확장 컬렉션·백업 | 아니오 |
| `Bank` | `js/bank.js` | 토익 문제 은행 로딩·뽑기·정답 가리기 | 읽을 때만 |
| `Stats` | `js/stats.js` | 정답률·추이·학습량·연속 학습일·이어서 풀기 | 아니오 |
| `Planner` | `js/planner.js` | 현재 점수·학습 계획·오늘의 단어 카드 | 아니오 |
| `Sessions` | `js/sessions.js` | 세션 만들기·채점·마감 (TS `core/study.py`) | 아니오 |
| `Engine` | `js/engine.js` | 풀이 화면 엔진(토익 7개 파트 렌더) | 예 |
| `Charts` | `js/charts.js` | 점수 추이·일별 학습량 차트 (Chart.js `vendor/`) | 예 |
| `Backup` | `js/backup.js` | 단어+시험 백업 내보내기/불러오기 | 아니오 |
| `UI` | `js/ui.js` | **머리글·메뉴·`UI.boot`**, 도우미(`esc` `$` `params` `intParam` `url` `go` `seg` `flash` `toeicBadge` `ttsRaw`…) | 예 |
| `TTS` | `js/tts.js` | 브라우저 음성 합성(성별·억양 고르기, 긴 문장 나눠 읽기) | 예 |
| `Hub` | `js/hub.js` | 허브 카드 (확장은 `js/hub-extra.js`) | 예 |

## 새 화면(페이지) 만드는 법
1. `tools/make_pages.py` 의 `PAGES` 에 한 줄 추가 (`"toefl-mock.html": ("토플 모의고사 · TS", BASE + ["js/toefl-mock.js"], {})`) → `python tools/make_pages.py`.
   BASE 는 공통 스크립트 묶음이고, 토익 데이터·세션이 필요하면 `EXAM` 도 더합니다.
2. `js/toefl-mock.js` 를 만들고 시작합니다:
   ```js
   UI.boot({ exam: "toefl", page: "toefl-mock" }, async () => {
     UI.$("app").innerHTML = `...`;            // #app 안에 그리기. start 가 async 여도 되고, 던진 오류는 화면에 '열 수 없습니다' 로 보임
   });
   ```
   `UI.boot` 가 하는 일: 서비스 워커 등록(서버로 열었을 때) → 시험에 맞춰 단어 세트(토익/토플) 전환 → 단어 데이터 읽기 → 머리글·메뉴 그리기 → `start()` 실행 → 세부 메뉴의 `#구역` 링크 강조.
3. `js/exams.js` 의 `MENUS` 에서 해당 항목의 `ready: false` 를 지우고 `pages` 에 `"toefl-mock"` 을 넣으면 메뉴에서 "준비 중" 이 사라지고 이 화면에서 강조됩니다.
   홈이 완성되면 `EXAMS.toefl.ready = true`. 같은 파일 이름(`toefl.html` 등)을 쓰면 허브 카드·메뉴 링크가 그대로 이어집니다 — 안내용 `toefl.html` 은 새 화면으로 덮어쓰세요(`make_pages.py` 의 `soon.js` 줄을 바꿈).
4. `sw.js` 의 `ASSETS` 에 새 `.html`·`.js`·`data/*.js` 를 넣고 `CACHE` 이름을 올립니다 (`tests/exam.test.js` 가 "화면이 읽는 스크립트가 sw.js 목록에 있는지" 검사).
5. 풀이 기록을 남기려면 아래 `TSStore.col()`, 허브 카드는 `Hub.register()`.

**메뉴 정의** (`TSNav.MENUS[시험키]`): `{ label, href, pages:[강조할 page 이름], emph?, ready?, set? }`.
page 이름은 보통 HTML 파일 이름(`.html` 뺀 것), 풀이 화면은 `"solve:practice"` 식, `"toefl-practice:r_"` 처럼 `:` 가 있으면 앞부분 일치입니다. 시험 키: `toeic` `toefl` `toeic-speaking` `opic`.
**허브 카드**: `js/hub-extra.js` 에 `Hub.register("toefl", () => ({ now, now_sub, target, n, last, date, links:[[라벨, href]...], vocab }))` (항목 설명은 `js/hub.js` 맨 위).

## 데이터 읽기
- 단어: `Ward.init()` 이 현재 세트(토익/토플)를 읽음 → `Ward.words()`, `Ward.queue({level, tier, startLevel, dailyNew})`, `Ward.levelProgress()`. `UI.boot` 가 세트를 시험에 맞춤(`exam: "toefl"` 이면 토플 어휘).
- 토익 문제: `await Bank.load([5, 6])`(파트만) 또는 `await Bank.load()`(전부). 이미 읽었으면 즉시 끝남. 데이터를 읽지 않고 개수만: `Bank.count(part, level, qtype)`, `Bank.typeList(part)`, `Bank.meta()`.
- **새 데이터 파일 추가 패턴** (토플 문제 등): `tools/build_data.py` 에 파트 함수 하나를 더해 `data/toefl-xxx.js` 를 `(window.TS_DATA = window.TS_DATA || {}).키 = [...]` 모양으로 쓰고, 화면에서 `<script>` 를 동적으로 붙여 읽습니다 — `Bank.loadPart` 의 `document.createElement("script")` 가 그 예입니다(`file://` 에서도 동작). 정답을 같이 넣되 풀이 화면에는 채점 전에 넘기지 마세요(`Bank.publicItem`/`reveal` 패턴).
- 개수 요약이 필요하면 `data/meta.js` 에 항목을 더해 화면 첫 로딩을 가볍게 합니다.

## 저장소 `TSStore` (키 `ts:v1`)
```js
TSStore.settings()                    // {target_score:"800", exam_date:"", daily_questions:"40", toefl_target:"4.5", toefl_exam_date:"",
                                      //  tsp_target:"140", tsp_exam_date:"", opic_target:"IH", opic_level:"4", opic_survey:"", opic_exam_date:""} — 값은 모두 문자열
TSStore.setSettings({toefl_target: "5"})   // DEFAULT_SETTINGS 에 있는 키만 저장
```
하루 새 단어 수·음성(속도·억양)은 `Ward.settings()` (`daily_new`, `tts_rate`, `tts_accent`) 를 씁니다. 설정 화면은 이미 네 시험의 목표·시험일 칸을 모두 가지고 있습니다.

**세션**(`TSStore.sessions()` 의 항목): `{id, created_at, finished_at, mode:'practice'|'diagnostic'|'mock'|'review', variant, part, level, items:["5:p5-001",...], time_limit, seen_before, requested, total, correct, lc_total, lc_correct, rc_total, rc_correct, lc_est, rc_est, total_est, duration_sec}`
**문항 기록**(`TSStore.attempts()`): `{s:세션id, k:"파트:문제id:문항번호", c:고른 번호(-1=무응답), o:정답 0/1, m:걸린 ms, t:"YYYY-MM-DDTHH:MM:SS", l:등급, y:유형}` — 짧은 이름은 용량 때문. 파트·문제id·문항번호는 `TSStore.partOf(a)` `itemIdOf(a)` `qidxOf(a)`.
**오답노트**(`TSStore.notes()` = `{qkey: {part, item_id, qidx, level, qtype, wrong_count, right_streak, status:'open'|'cleared', first_wrong_at, last_wrong_at, last_seen_at, memo}}`): `recordAttempt(sid, q, chosen, ms, ts)` 가 갱신(무응답은 제외, 서로 다른 날 2번 맞히면 `cleared`), `wrongNotes(status, part, qtype)`, `setNoteMemo`, `setNoteStatus`.
**여러 변경을 한 번에 저장**: `TSStore.batch(() => {...})`. 쓰기 직전마다 다른 탭이 저장한 내용을 다시 읽어 서로 덮어쓰지 않습니다.

**다른 시험의 기록**: `const c = TSStore.col("toefl_attempts")` → `c.add({...})`(id·created_at 자동) `c.all()` `c.where(fn)` `c.update(id, patch)` `c.remove(id)`. `ts:v1` 의 `ext` 에 저장되고 **백업·합치기·TS 앱 변환 틀에 자동으로 포함**됩니다(합치기는 내용이 같은 레코드를 한 번만). 컬렉션 이름은 `toefl_attempts` `toefl_mocks` `speaking_attempts` `speaking_mocks` 처럼 시험별 접두어를 권장합니다.
TS 앱 DB 의 토플·말하기 표는 `tools/import_ts_db.py` 의 `toefl_ext()`·`speaking_ext()` 가 `ts.ext` 로 옮깁니다(참고용 예).

## 풀이 화면에 쓰는 흐름 (토익 예)
```js
const sid = await Sessions.startPractice(part, level, qtype, n)   // → 세션 번호. startMock(form) startDiagnostic() startReview(part, n)
UI.go(`solve.html?sid=${sid}`)                                      // location.replace — 뒤로 가기에 세션 만드는 주소가 남지 않게
// solve.js: Engine.run(P, { grade(ref, answers), submit(body) → 이동 주소, draft: {get,set,clear} })
Sessions.gradeItem(sid, ref, [{qidx, chosen, elapsed_ms}])         // 한 문제(세트) 채점 → {ref, translation, questions:[{answer, explanation..}], results:[{qidx, chosen, correct}]}
Sessions.submitSession(sid, {items: {ref: [...]}, duration_sec})   // 모의고사·진단 한 번에 제출, 연습·복습은 마감
```
채점은 `Bank` 의 정답으로 하고 `TSStore.recordAttempt` 가 풀이 기록·오답노트를 갱신합니다. 점수는 `Score.estimate` / `Score.estimateRaw`.
다른 시험은 자기 데이터 모양에 맞는 엔진을 따로 만들되 위 패턴(공개용 사본 / 채점 뒤 reveal / 임시 저장 draft / 세션 요약 저장)을 따르면 됩니다.
음성: `TTS.play([{text, gender:'male'|'female', nth, pause, onStart}], {rate, accent})` 는 끝나면 true(중간에 `TTS.stop()` 으로 끊기면 false), `UI.ttsRaw()` 가 설정의 속도·억양(`mix` 포함), 억양 섞기는 `TTS.accentFor(accent, 시드문자열)`.

## 통계·계획
`Stats.partAccuracy({days, lastN})`, `levelAccuracy()`, `typeAccuracy()`, `scoreHistory(n)`, `dailyCounts(n)`, `todayCounts()`, `streak()`(풀이 + 단어 복습한 날), `recentSessions(n)`, `unfinishedSessions()`.
`Planner.currentScore(settings)`, `Planner.build({...TSStore.settings(), daily_new_words: String(Ward.settings().daily_new)})`, `Planner.vocabHero(plan)`.
토플 밴드·스피킹 점수처럼 다른 척도는 각 시험이 자기 모듈을 두되, `Stats.streak()`·`daily_counts` 에 다른 시험의 공부한 날이 합쳐지게 하려면 `Stats.setVocabLog` 와 같은 방식으로 날짜 목록을 더해 주세요(지금은 토익 풀이+단어만 셈).

## 파이썬과 같은 결과를 보장하는 방법
- 파이썬 `round()` 는 .5 를 짝수 쪽으로 반올림 → 반드시 `TSU.pyRound` 를 쓰세요(`Math.round` 금지).
- `python tools/make_fixtures.py` 가 TS 앱의 실제 코드로 4주치 학습을 흉내 내 `tests/py_fixtures.json`(정답표)을 만듭니다. `tests/exam.test.js` 가 같은 값을 JS 로 계산해 비교합니다.
  토플·스피킹 로직을 옮길 때도 같은 방식(파이썬 결과를 JSON 으로 저장 → node 테스트가 대조)을 쓰면 안전합니다.
- 테스트에서 '오늘'을 고정: `Ward.setClock(() => new Date(2026,9,7,20))`, `Stats.setToday(() => "2026-10-07")`, `Planner.build(settings, {today})`. 난수는 `TSU.makeRng(seed)` 를 `rng` 로 넘깁니다.

## 검사
    node --test                           # tests/*.test.js 전부 (store.test.js 검사 20개 + exam.test.js 테스트 19개) — 노드만 있으면 됩니다
    python -m pytest tests -q             # 도구(데이터 만들기·MP3 계획·TS 기록 변환·화면 파일이 make_pages 와 일치)
    python tools/make_fixtures.py         # 정답표(py_fixtures.json) 다시 만들기 (TS 앱 코드가 바뀌었을 때)
    node tests/browser/smoke_pages.js     # 헤드리스 크롬으로 화면 열어 보기 (모든 화면 오류 없이 그려지는지)
    node tests/browser/flow_practice.js   # 연습 → 결과 → 오답노트 → 진단 → 통계 → 백업 왕복
    node tests/browser/flow_real_mock.js  # 실전 모의고사 LC 자동 진행 → RC → 제출 (음성은 가짜, 시간 100배속)
    node tests/browser/flow_resume.js     # 새로고침해도 연습·시험 답안이 이어지는지
    node tests/browser/flow_toefl.js      # 토플: 과제별 연습 → 기록·밴드 → 오답노트, 가짜 마이크 말하기, 모의고사 통째로(새로고침 이어 하기 포함)
    python tools/make_toefl_fixtures.py   # 토플 정답표(toefl_fixtures.json) 다시 만들기 — tests/toefl.test.js 가 대조
    node tests/browser/flow_speaking.js   # 토익스피킹·오픽: 유형별 연습 → 채점 → 새로고침 유지, 모의고사 11/15문항 → 결과, 설문 저장, 약한 문항 (가짜 마이크, 시간 100배속)
    python tools/make_spk_fixtures.py     # 토익스피킹·오픽 정답표(spk_fixtures.json) 다시 만들기 — tests/spk.test.js 가 대조
브라우저 점검 스크립트는 크롬/엣지가 필요하고 `node --test` 에는 포함되지 않습니다 (환경변수 `CHROME` 으로 경로 지정 가능).

---

# 토플 (js/toefl-*.js)
TS 앱의 `core/toefl.py`·`views/toefl.py`·`static/js/toefl*.js` 를 옮긴 것입니다.
- **로직** `js/toefl-core.js` (`window.Toefl`): 과제 정의(`TASKS` 11개, `SECTIONS` R·L·S·W), 밴드 추정(`bandFromLevels` 난이도별 정답률 65%/45% 규칙, `selfBand` 자기 평가, `sectionBands`, `overallBand`), **`halfUp`**(0.5 단위 반올림, .25 는 올림 — 파이썬 `round` 쓰지 말 것), 영역 순서 R→L→W→S(모의고사), 오답 모으기(`wrongItems`: 마지막 회차 기준, 자동 채점 80% 미만 / 자기 평가 3점 미만), 연습 뽑기(`pick`: 안 푼 문제 → 오래전에 푼 문제), 모의고사 구성(`buildMock`)·끝내기(`finishMock`).
  기록은 `TSStore.col("toefl_attempts")`(문항마다 한 줄, 같은 문제 한 번 풀이는 같은 `created_at`)와 `col("toefl_mocks")`. 풀이 기록은 6,000건을 넘으면 오래된 것부터 정리합니다.
- **모의고사** 적응형: 읽기·듣기 1모듈(밴드 4)에서 60% 이상이면 2모듈은 어려운 문제(밴드 5~6), 아니면 쉬운 문제(밴드 2~3). 계획은 문제 전체가 아니라 `{task, id}` 번호만 저장합니다. 영역 하나를 끝낼 때마다 `localStorage["ts-tmock-<id>"]` 에 저장해, 새로고침해도 끝낸 영역은 다시 보지 않습니다(보던 영역은 처음부터).
- **말하기** `js/toefl-speech.js`: 브라우저 음성 인식(Web Speech)으로 따라 말하기를 단어 단위(LCS)로 채점, MediaRecorder 로 인터뷰 답변 녹음(다시 듣기용 — **저장하지 않음**). 인식·마이크를 쓸 수 없으면 스스로 채점합니다. 마이크는 https 또는 localhost 에서만 켜집니다.
- **문제 데이터** `python tools/build_data.py`(→ `tools/build_toefl.py`) 가 `data/toefl-<과제>.js` 11개와 `data/meta.js` 의 `TS_META.toefl`(과제·난이도별 개수)을 만듭니다.
- **검사** `tests/toefl.test.js`(파이썬 정답표 대조: 반올림 규칙, 밴드 400건, 영역 밴드·최근 기록·오답·날짜별, 모의고사 끝내기, 구성 규칙), `tests/test_toefl_tools.py`, 브라우저 `tests/browser/flow_toefl.js`.

---

# 토익스피킹·오픽 (js/spk-*.js)
TS 앱의 `core/speaking.py`·`views/speaking.py`·`templates/speaking/*`·`static/js/speaking.js` 를 옮긴 것입니다.
- **로직** `js/spk-core.js` (`window.Spk`, 화면 없이 node 에서도 돔): 토익스피킹 5유형(`TSP_TASKS`, 11문항 Q1~10 0~3·Q11 0~5, 합 35 → 0~200 비례 환산 `tspScore` = 파이썬 반올림, 레벨 8단계 `tspLevel`), 오픽 주제 표(`OPIC_TOPICS`)·설문 묶음·등급(`opicGrade`: 자기 채점 평균 + 평균 단어 수로 IM1~3), 채점 기준(루브릭),
  말하기 '단계(steps)' 만들기(`tspSteps` `opicQStep` `opicRpSteps`), 연습 뽑기(`tspPractice` `opicPractice`: 안 푼 문제 → 오래전에 푼 문제, `weak` 이면 마지막 점수가 낮은 문제만), 모의고사 구성(`tspMockPlan` 11문항, `opicMockPlan` 15문항·난이도 1~2는 13문항), `record`(검증·점수 범위 자르기) / `finishMock`(토익스피킹은 안 한 문항 0점, 오픽은 1점·자기소개 제외) / 통계(`tspTaskStats` `tspEstimate` `opicStats` `weakItems` `trend` `history`).
  **기록**: `TSStore.col("speaking_attempts")`(답변 한 줄: exam·task·item_id·qidx·points·max_points·words·seconds·accuracy·mock_id·response)와 `col("speaking_mocks")`(모의고사: 문제 번호 `units` + 설정 + 결과 — 단계는 은행에서 다시 만들어 용량을 아낌). 녹음 파일은 **저장하지 않고** 음성 인식으로 받아 적은 글만 남깁니다. 답변이 쌓이면 localStorage 를 쓰므로 백업을 자주 내려받으세요(응답 글은 한 답변당 최대 4,000자).
  `Spk.setNow(fn)` 은 테스트용 시계.
- **진행 화면** `js/spk-run.js`: `<body data-spk>` 가 화면 종류(tsp-practice·tsp-mock-run·opic-practice·opic-mock-run). 연습은 문제 하나가 끝날 때마다 채점·저장, 모의고사는 끝난 뒤 한꺼번에. 읽기 정답 문장과 인식된 말을 단어 단위(LCS)로 비교하고 모범 답안 듣기·섀도잉(따라 말해 일치율)을 제공합니다. 마이크·음성 인식은 https 또는 localhost 에서만(파일로 열면 브라우저에 따라 막힘 — 그때는 녹음 없이 스스로 채점). 점검용으로 `window.SPK_SPEED`(시간 배속)가 있습니다(평소 1).
- **문제 데이터** `python tools/build_data.py`(→ `tools/build_speaking.py`) 가 `data/speaking-tsp.js`(`window.SPK_DATA.tsp`)·`data/speaking-opic.js`(`.opic`)를 만들고 개수 요약은 `data/meta.js` 의 `TS_META.speaking`(허브 카드가 씀)에 넣습니다. 화면이 필요할 때만 읽습니다(`Spk.loadTsp()` `Spk.loadOpic()`).
  `data/meta.js` 는 이제 `build_data.py` 의 `META` 에 시험별로 모아 `write_meta()` 가 한 번에 씁니다.
- **검사** `tests/spk.test.js`(파이썬 정답표 `tests/spk_fixtures.json` 대조: 상수·환산표·모든 문제의 단계 해시 1,157개·기록 재생 후 통계/약한 문항/추세/모의고사 결과·모의고사 구성 모양), `tests/test_spk_tools.py`, 브라우저 `node tests/browser/flow_speaking.js`. 정답표는 `python tools/make_spk_fixtures.py`.
