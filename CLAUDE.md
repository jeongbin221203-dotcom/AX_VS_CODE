# 대한사료 웹사이트 (Flask) + 포트폴리오

## 프로젝트
- 학습용 대한사료 기업 사이트 리뉴얼. 공식 사이트 아님 (포트폴리오에 이 안내 문구 유지).
- 실행: `pip install -r requirements.txt` → `python app.py` → http://127.0.0.1:5000
- 라우트 7개(`/`, `/sub1`, `/sub1/detail/<category>`, `/sub2`, `/sub3`, `/sub4`, `/privacy`), 템플릿 8개.

## 포트폴리오 (2026-09-17 개인 포트폴리오로 전환)
- 대상 프로젝트: 현재는 `sales/`(영업관리)만. 자재관리와 영업관리는 따로 만들고 **최종 단계에서 합친다**(사용자 지시 2026-09-27) — 그 전까지 서로 없는 것으로 보고 작업. 대한사료는 사용자 요청으로 제외.
  - 떼어 낸 자재관리 섹션·캡처는 `portfolio_src/merge_later/`(mm_section.tpl.html, mm_imgs.json)에 보관 — 합칠 때 사용.
- 결과물: `portfolio.html` (스크린샷이 base64로 들어간 단일 파일, 약 1.3MB). 직접 수정하지 말 것.
- 수정은 `portfolio_src/portfolio.tpl.html`에서 하고 `python portfolio_src/build.py`로 다시 생성.
  - `imgs.json`: JPEG(1200x750) base64 이미지 9장 — sales_dash·sales_fc·sales_deal·sales_quote·sales_appr·sales_doc·sales_erp·sales_form·sales_audit. 원본 PNG는 `portfolio_src/shots/` (대한사료·옛 sales_an PNG도 남아 있음).
  - 스크린샷: 샘플 DB 복사본(`MM_DB_PATH`, `SALES_DB_PATH`)으로 앱 실행 → CDP로 렌더링 완료를 기다린 뒤 1440x900 캡처. 원본 DB는 건드리지 말 것.
    - 겹치지 않는 포트 사용(다른 세션이 5902·5011을 쓸 수 있음). Git Bash에서 `/path` 인자는 `MSYS_NO_PATHCONV=1` 필요.
    - 영업관리: `SALES_AUTH_MODE=sso`, `SALES_SSO_USER=정임원|김영업|시스템관리자`로 실행 → `/login?next=/deals`. 증빙 화면은 `#sale-edit`로 스크롤.
    - 자재관리: 로그인 필수 — DB 복사본에 `core.auth.create_user`로 관리자 생성 + `core.seed.seed()` 후 로그인 폼 제출.
  - 2026-09-27 Version 4: 영업관리 단독판(Flask 스택, 6,020줄, 테스트 37, 감사 해시 체인·ERP·세금계산서 섹션).
  - 2026-10-02 Version 8: 11,786줄+테스트 2,329줄, 테스트 80, 메뉴 19. (Version 5~7: 11,202줄·테스트 69·메뉴 18)
  - 2026-09-27 Version 5(대기업 제출용, 약 1.3MB): PostgreSQL. 이미지 9장(+sales_quote·sales_appr·sales_form).
    새 섹션: 견적·부가세, 회사 엑셀 양식, 여러 서버 운영·장애 대비, ERP(SAP·REST·파일·수신 API), 결재선 표. 캡처 DB는 세션 scratchpad `demo3`(prep_demo3.py), CDP 포트 9444~9446, 앱 5031~5033.

## 영업관리 `sales/` (2026-09-27 Streamlit → Flask 전환)
- 사용자 요청으로 Flask 구조로 바꾸고 Streamlit 전용 파일(app.py 화면, .streamlit/, run.bat/sh, auth.py)은 삭제. 전환 전 원본은 `sales_streamlit_backup/`.
- 실행: `python app.py` → http://127.0.0.1:5001 (대한사료 5000번과 분리). 워커 `python manage.py worker`. 운영은 `manage.py db upgrade` 후 `python serve.py`. 테스트: `python -m pytest tests -q` (80개, 임시 DB·폴더).
- PostgreSQL 테스트: conda env `sales-pg`의 PG16을 127.0.0.1:5433(데이터 scratchpad/pgdata, trust)로 띄우고 `SALES_TEST_PG_URL=postgresql://postgres@127.0.0.1:5433/postgres`, `SALES_PG_DUMP`/`SALES_PG_RESTORE`=`<conda base>/envs/sales-pg/Library/bin/pg_dump.exe`/`pg_restore.exe` → 79 통과 + 1 SQLite 전용 skip.
- 구조: `core/`(database 연결계층, sales_db, enterprise, catalog, quotes, jobs, notify, storage, hr, oidc, api_keys, observability, dataio, excel_forms, auth, documents, erp) · `views/`(auth, reports, crm, catalog, finance, io, admin, api) · `migrations/`(Alembic 0001~0006, alembic.ini는 ASCII만 — Windows cp949로 읽힘) · `deploy/`(compose·nginx·Prometheus) · `docs/RUNBOOK.md` · CI는 저장소 루트 `.github/workflows/sales-ci.yml`.
- 2026-09-27 "모두 추가": PostgreSQL+Alembic, S3/로컬 저장소, DB 작업 큐·스케줄러·알림, 품목·특가·견적·부가세(공급가액/부가세/합계), 다단계 결재선·대결·독촉, HR 연동, OIDC(PKCE), REST API(/api/v1), 지표·readyz·JSON 로그·읽기 전용 모드, 회사 엑셀 양식(업로드 열 매핑·내려받기 서식 파일). Docker는 이 PC에 없어 compose는 기동 미검증.
- 2026-09-27 대기업 운영 보강: 담당자 owner_id 연결(동명이인 분리), 승인 후 조건 변경 시 승인 무효, 관리자 자기결재 금지, 삭제 대신 보존(매출은 '취소'), 감사로그 해시 체인+수정·삭제 트리거, password/SSO 인증·잠금·세션 만료, 개인정보 마스킹·수식 주입 차단·다운로드 감사, ERP(SAP OData/파일) 전송 대기열·입금 대사, 세금계산서·전자세금계산서 증빙(이미지/PDF/XML, `sale_documents` 테이블, 파일은 `data/documents/`) 등록·검증. 상세는 `sales/README.md`.
- 하지 않은 것: SAP·IdP·S3 실시스템 연결 검증(모의 서버로만 확인), 세금계산서 발행·OCR, 컨테이너 실기동.
- 2026-09-27 ERP 확장: `rest` 어댑터(Bearer/Basic/헤더 인증, HMAC 서명, 필드 이름 바꾸기, Idempotency-Key), 연결 테스트 버튼, ERP→CRM 수신 API `/api/v1/erp/{payments,acks,credit,products}`(scope erp:write). 테스트 69(SQLite) / 68+1 skip(PG).
- 2026-10-02 회사별 설정·데이터 안전: `core/company.py`(company_settings 테이블, 관리자 > 🏢 회사 설정 — 회사 정보·결재 한도·SLA·단계 확률·코드 목록·개인정보 보관기간, 업무 상수는 같은 객체를 제자리 갱신, 15초 캐시),
  `core/offline.py`(외부 연결 사내망/인터넷 분류), 비상 로그인(`SALES_BREAKGLASS_USERS`), 마이그레이션 0007·0008(sales.row_version, form_submissions).
  입금 조건부 갱신(동시 입금 유실 방지, ERP 대사는 expected_before), 매출 수정 충돌 검사, ERP 대기열 선점('전송중', recover_stuck), 폼 `_submit_id` 중복 제출 차단,
  app.js 입력 보관(localStorage `sales-draft:<user>:`)·연결 끊김 배너. 테스트 80(SQLite) / 79+1 skip(PG). 브라우저 동작은 CDP로 확인(scratchpad cdp_drafts.py).
- 사용자 `data/sales.db`에는 담당자 계정 없이 만든 샘플 데이터(9/27 17:54~56, 여러 번 생성)가 있어 담당자 '미연결' 상태(관리자만 조회).
- 확인용 서버는 SALES_PORT=5011 + DB 복사본 사용(Windows는 같은 포트에 여러 프로세스가 바인딩됨).
- 게시된 Artifact: https://claude.ai/artifact/CTxZ4PEpwjc7xMdtveRMNQ (제목 "업무 시스템 포트폴리오". 다른 대화/폴더에서 수정하려면 이 URL을 `url`로 넘겨 업데이트)
- 디자인: 회사 서류(전표·결재란) 형식. 먹색 잉크 + 청색 #2747A3 + 인주 빨강 #C23A2E, Hahmlet(제목) / IBM Plex Sans KR / IBM Plex Mono. 라이트·다크 테마 지원.
- 구성: 요약전표 → 기술 → 영업관리(탭 갤러리 9장, 권한, Stage Gate·결재선, 견적·부가세, 회사 엑셀 양식, 감사로그, ERP, 여러 서버 운영·장애 대비, 세금계산서, 검증) → 일하는 방식. 자재관리는 합칠 때 다시 넣음.

## 자재관리 `material-manager/` (2026-09-27 Streamlit → Flask 전환)
- 사용자 요청으로 Flask 구조로 전환. 전환 전 원본은 `material_manager_streamlit_backup/`. `core/`(업무 로직)는 그대로 두고 화면 계층만 교체.
- 실행: `MM_SECRET_KEY=... python app.py` → http://127.0.0.1:5002 (대한사료 5000, 영업관리 5001과 분리). 테스트: `python -m pytest tests -q` (100개, SQLite). `MM_DATABASE_URL=postgresql://...mm_test`로 PostgreSQL에서도 같은 100개.
- 구조: `core/`(db·storage·repository·services·approvals·org·periods·reconcile·sap·jobs·auth·audit·documents) · `views/`(블루프린트 14개 + helpers.py) · `templates/` · `static/` · `cli.py`(Flask CLI: `flask --app app init-db | batch [--loop|run|list] | erp status|test|send|master-sync`). batch.py·sap_sync.py는 2026-09-27 삭제.
- 2026-09-27 대기업 대응 추가(사용자 요청 "모두 실행"): 로그인·역할 4단계(조회/담당자/관리자/시스템관리자)·감사로그(트리거로 수정·삭제 차단),
  거래 삭제 폐지 → 취소 거래(역분개), 월 마감 + 월말 재고 스냅샷, SAP 전송 대기열(mock/http, 실제 SAP 미검증).
- 2026-09-27 보안 강화: 디버그 기본 off, CSP(인라인 스크립트 금지 — onchange 대신 data-autosubmit, Chart.js는 static/vendor),
  세션 쿠키 이름 `mm_session`, 비밀번호 변경 시 이전 세션 무효·30분 무활동 로그아웃, IP 단위 로그인 차단, 최초 설정은 콘솔 코드 필요,
  엑셀 수식 주입 방지, 업로드 압축 폭탄·행 수 제한. 새 화면에 인라인 script/style/onchange를 쓰면 CSP에 막힘.
- 2026-09-27 여러 서버·대기업 기능: DB 어댑터(core/db.py, SQLite/PostgreSQL 같은 SQL, '?' 자리표시자), 파일 저장소(core/storage.py local/s3),
  배치(flask batch + core/jobs.py 임대 잠금), /health, 플랜트·창고(재고는 창고 단위, SAP 플랜트·저장위치는 창고에), 창고 간 이동(311/301),
  데이터 범위(user_scopes), 직무 분리(본인 거래 취소·본인 증빙 삭제 금지, 큰 실사 조정 결재), 증빙 열람·엑셀 내보내기 감사로그,
  수불부·재고 대사 보고서, 목록 100건 페이지. 여러 서버 통합 확인(앱 2대+배치 2개+PG) 통과.
- 2026-09-27 추가: 사내 SSO(core/sso.py, OIDC+PKCE, joserfc 검증, 그룹→역할, JIT), 2단계 인증(core/mfa.py, TOTP, ADMIN 필수 — 테스트는 config.MFA_REQUIRED_ROLES=set()),
  재고 평가(core/valuation.py, MAVG/FIFO replay + 마감 스냅샷), 로트·유효기한(lots 테이블, 스냅샷 PK에 lot_no, FEFO 자동 배정),
  구매(core/purchasing.py + views/purchase.py, 금액별 결재 단계, 요청자 결재·발주 금지, 발주 대비 입고 검사, 3자 대조),
  SAP 마스터 동기화(core/master_sync.py, 자재·원가센터, 동기화 자재 SAP 항목 잠금). QR은 static/vendor/qrcode.min.js.
- 엑셀 양식(core/excel_forms.py, 관리자 → 엑셀 양식): 내려받기는 회사 .xlsx에 채움(열 연결·서식·자리표시 {{제목}} 등), 올리기는 열 이름 별칭·머리글 행.
  모든 엑셀 내려받기는 views/helpers.form_response(양식키, df, 파일명)를 거친다. 새 내려받기를 만들면 EXPORT_FORMS에 양식키·기본 열을 추가.
- 2026-09-27 ERP·SAP 연결 방식(core/erp.py, 사용자 요청 "ERP, SAP 모두 가능하게"): MM_ERP_MODE(예전 MM_SAP_MODE, 내부 변수는 config.SAP_MODE) =
  off|mock|http(EAI)|sap_odata(S/4 API_MATERIAL_DOCUMENT_SRV)|sap_rfc(pyrfc BAPI)|rest(기타 ERP, erp_maps/*.json 매핑)|file(공유 폴더+ack).
  대기열·멱등키·재시도는 core/sap.py 그대로, 연결마다 send/ping/fetch_master/fetch_stock. 실제 SAP·ERP 미검증(tests/test_erp.py의 가짜 서버·가짜 pyrfc).
- PostgreSQL 테스트: 세션 scratchpad에 conda로 PG16 설치(포트 55432, DB mm_test). 새 세션이면 다시 띄워야 함.
- 로그인이 생겼으므로 스크린샷은 로그인 세션이 필요. 사용자가 없으면 `/setup`으로 이동. 캡처 시 DB 복사본에 사용자를 만들고 CDP로 세션 쿠키(이름 mm_session)를 넣는다.
  CDP 포트는 9333 말고 다른 번호 사용(다른 세션이 9333을 씀). Git Bash에서는 MSYS_NO_PATHCONV=1(인자 '/..' 경로 변환 방지).
- `data/materials.db`는 비어 있음(자재 0종).
- 증빙(세금계산서·전자세금계산서 이미지) 기능: `core/documents.py`, `views/documents.py`, `documents` 테이블, 파일은 DB 옆 `attachments/`.
- 주의: Windows에서 개발 서버는 같은 포트(5002)에 여러 프로세스가 동시에 바인딩된다. 2026-09-27 다른 프로세스가 5002에서 원본 DB로 떠 있어
  확인 요청이 원본에 들어간 적 있음(원본은 빈 상태로 복구, 오염본은 세션 scratchpad에 보관). 확인용 서버는 `MM_PORT=5902` 등 다른 포트 + DB 복사본 사용.
- 포트폴리오에서는 최종 합치기 전까지 빠져 있음(`portfolio_src/merge_later/` 보관본은 2026-09-27 기준 — 합칠 때 새 기능 반영해 갱신).
- 2026-09-27 같은 시각 다른 세션이 5977번·CDP 9333으로 자재관리 앱을 캡처 중이었음(포트폴리오 작업으로 추정). 이 세션의 로그인 도입으로 그쪽 캡처 흐름이 로그인 화면에 막힐 수 있음.

## 미확정 사항
- 이름·한 줄 소개: 사용자가 비워두기로 함(성명 칸은 빈칸 표시). 제목 문구 "현장의 업무 규칙을 코드의 규칙으로 옮깁니다"는 Claude가 쓴 문안.
- 연락처는 사용자 선택으로 넣지 않음.
- 요약전표의 결재란(설계·개발·검증)은 가정값 — 사용자 확인 필요.
- 페이지의 수치(메뉴 19, 코드 11,786줄+테스트 2,329줄, 테스트 80/80, 영업기회 조회 건수 7/33/55·매출 17/94/160 — 2026-10-02 재측정)는 코드·README 기준. 프로젝트를 고치면 함께 갱신할 것.

## 영어 시험 학습 `TS/` (2026-09-27 시작, 개인 공부용 — 배포 안 함, 문제 저작권 무관)
- 범위: 토익·토플 제작. 메뉴는 ☰ 사이드바(평소 숨김, `views/nav.py` MENUS): 카테고리 토익 → 토플 → 토익스피킹 → 오픽, 카테고리 이름 = 그 시험 첫 화면, ▾ = 탭 펼치기(사용자 지시). 토익스피킹·오픽은 카테고리만(`/exam/<key>` 준비 중 화면, `core/exams.py`).
- 토플 `/toefl`(2026-09-27): 2026년 1월 개편 형식 11개 과제, 밴드 1~6. `core/toefl.py`(과제 정의·밴드 추정·`toefl_attempts` 테이블), `views/toefl.py`(API `/toefl/api/attempt`), `static/js/toefl.js`, 문제 `content/toefl/*.json`(형식 SCHEMA.md, 검사 `tools/validate_toefl.py`). 말하기는 Web Speech 인식 + MediaRecorder, 쓰기·인터뷰는 자기 평가.
- 토플 모의고사 `/toefl/mock`: R→L→S→W, 읽기·듣기 2단계 적응형(`core/toefl.build_mock`, 기준 60%), `static/js/toefl-mock.js`, 결과 `toefl_mocks`. 토플 어휘 `/toefl/vocab`: vocab 블루프린트를 url_prefix="/toefl", name="tvocab" 로 재등록(템플릿은 `url_for('.x')`, JS는 `TS.vbase`), 은행은 `ToeflBank.vocab`, 등급 표시는 `VOCAB_GRADES`(밴드 2~6). 등급 = 토익 인증 색상(Orange 10~215 / Brown ~465 / Green ~725 / Blue ~855 / Gold 860~).
- 실행: `python app.py` → http://127.0.0.1:5003. 테스트 `python -m pytest tests -q` (72개). 문제 검사 `python tools/validate_content.py`.
- 구조: `core/`(db·content·scoring·study·srs·planner·stats·guide) · `views/`(main·quiz·vocab) · `static/js/quiz.js`(풀이 엔진) · `content/toeic/*.json`(형식은 content/SCHEMA.md).
- 문제 은행(모두 새로 작성, `partN.json`+`partN_2/_3.json`… 합쳐 읽음, 파일 변경 시 자동 반영): P1 120, P2 400, P3 100세트, P4 85세트, P5 600, P6 90세트, P7 120세트, 단어 1,800(vocab·vocab_2·vocab_3 + 도전 vocab_s1~s5). 단어는 tier: core(필수)/stretch(도전). 2026-09-27 오타 전수 검사(문제 영어 드문 단어 3,420개 + 단어 1,600개) 완료.
- 실전 모의고사(`full`, real=True): 실제 구성 200문항, LC 음성 흐름대로 자동 진행(P1·2 5초, P3·4 문항 읽기 후 8초/표 12초, 되돌아가기 없음), RC 75분, 원점수 환산(`scoring.estimate_raw`), 시험 중 등급 숨김, 답안 localStorage 백업, 전에 푼 문제 수(`sessions.seen_before`) 경고.
- 단어 듣기(운전 모드) `/vocab/listen`: 영어 N번(미국→영국→호주)→한국어 뜻 반복 재생(Web Speech). 1시간 MP3는 `core/audio.py`: edge-tts 조각(`data/audio/clips/`) + lameenc 무음을 이어 붙임, 백그라운드 작업(`/api/vocab/audio/prepare`·`status`), 리눅스(Render)에서도 동작. 1시간 약 21MB·반복 3번 약 395단어.
- 단어 시험 `/vocab/quiz`: 범위(전체·자주 잊는=틀린 2번 이상·최근 틀린·별표·학습 중) × 등급 × 필수/도전, 15/30/50문제. 답은 `vocab_quiz_log`에 기록, 오답은 `srs.review(id, 0)`로 복습 카드(내일). 다시 풀기 회차는 기록 안 함.
- 듣기 = 브라우저 음성 합성(Web Speech). Part 1 사진은 한국어 장면 설명. 추정 점수는 난이도 가중 정답률 → 구간 보간(실제 점수와 다를 수 있음).
- CSP: script-src 'self'(인라인 스크립트 금지, 데이터는 `<script type="application/json">`), style은 인라인 허용. Jinja에서 `.75` 대신 `0.75`.
- `data/ts.db`는 사용자 학습 기록. 확인용 서버는 `TS_PORT=5913` 등 다른 포트 + `TS_DB_PATH`로 임시 DB 사용.

## 채용공고 적합성 `job/` (2026-09-30 시작, 개인 구직용)
- 실행: `python app.py` → http://127.0.0.1:5004 (5000~5003과 분리). 테스트 `python -m pytest tests -q` (91개, 네트워크 없이 fixture·가짜 사이트). 상세는 `job/README.md`.
- 구조: `core/`(db·normalize·salary·fit·postings·applications·profile·collect) · `core/sources/`(saramin·work24·wanted·linkimport·fileimport) · `views/`(main·jobs·collect·profile·applications).
- 사이트: 사람인(공식 API, `JOB_SARAMIN_KEY`)·고용24(`JOB_WORK24_KEY`)·원티드(비공식 JSON, `JOB_WANTED_ENABLED=1`) 자동 수집. 잡코리아·링커리어·자소설닷컴·잡플래닛·리멤버(+사람인·원티드·고용24)는 **공고 링크 붙여넣기**(JSON-LD JobPosting → og:description 요약 → og:title, 8개 도메인·공고 주소 모양만 허용, 첫 화면 주소 거부) 또는 CSV/엑셀. 목록 대량 크롤링은 하지 않음(사용자 요청 2026-09-30에 맞춰 이 방식으로 추가).
- 2026-09-30 정기 크롤링(사용자 요청 "4시간 간격", "리멤버 13,829건 전체"): `core/crawler.py`·`core/scheduler.py`·`crawl.py`. 사람인·잡코리아·링커리어 검색 결과 → 새 공고만 상세, 리멤버는 sitemap-jobs.xml 비교(sitemap_ids 테이블, 새 번호만 상세, 빠지면 마감, 최근부터 회당 500건), 저장 공고 하루 1회 갱신. robots.txt·3초 간격·403/429 중단·DB 잠금(settings.crawl_lock). 전체 재수집을 매번 하지 않는 이유는 13,829×3초 ≈ 11.5시간 > 4시간. 실제 1회 실행 확인(22요청·71초·14건). 공고 source_id = 사이트 공고번호(rec_idx 등)라 API·크롤링·링크가 합쳐짐.
- 2026-10-02 사용자 실제 DB(`job/data/job.db`)에 자동 수집 켬: 검색어 없음 = "모든 직무"(사용자 선택) → 사람인은 `jobs/list/domestic`(검색어 없는 검색은 페이지가 안 넘어감), 잡코리아 검색 Ord=RegDtDesc, 링커리어 목록, 목록 5페이지·사이트당 새 공고 100·리멤버 500/회·4시간. 앱은 miniforge python으로 숨김 실행(Start-Process, 로그 `job/data/app.log`·`app.err.log`), PC 재시작 시 다시 켜야 함. 앱을 강제 종료하면 crawl_lock 이 남으므로 settings.crawl_lock 을 '' 로 지운 뒤 재시작.
- 고용24 상세는 infoTypeCd 등이 붙은 원래 주소여야 내용이 나옴 — `_work24_fields`(라벨→값). 링커리어 JSON-LD는 연봉을 MONTH로 잘못 적어 둬 1,500만원/월 이상이면 연봉으로 봄.
- 적합성 100점 = 지역·연봉·경력·직무키워드 각 25, 학력 −15·고용형태 −10, 지원 불가 사유 별도. 연봉은 연 만원 환산(월×12, 시급×209×12).
- 자동 지원 제출 없음 — 원문 링크 + 지원 상태·일정·메모 관리. CSP script-src 'self'(인라인 스크립트 금지, `static/js/app.js`의 data-autosubmit·data-confirm).
- 2026-09-30 실제 공고 페이지 확인: 잡코리아·리멤버는 JSON-LD 있음, 사람인은 og:description 요약만(근무지 없음), 링커리어는 제목·설명만. 자소설닷컴·잡플래닛·고용24 공고 페이지와 API 키 호출은 미검증. 확인용 서버는 `JOB_PORT=5914` + `JOB_DB_PATH`로 임시 DB 사용.
