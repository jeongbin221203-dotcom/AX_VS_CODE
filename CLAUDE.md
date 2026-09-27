# 대한사료 웹사이트 (Flask) + 포트폴리오

## 프로젝트
- 학습용 대한사료 기업 사이트 리뉴얼. 공식 사이트 아님 (포트폴리오에 이 안내 문구 유지).
- 실행: `pip install -r requirements.txt` → `python app.py` → http://127.0.0.1:5000
- 라우트 7개(`/`, `/sub1`, `/sub1/detail/<category>`, `/sub2`, `/sub3`, `/sub4`, `/privacy`), 템플릿 8개.

## 포트폴리오 (2026-09-17 개인 포트폴리오로 전환)
- 대상 프로젝트: 현재는 `sales/`(영업관리)만. 자재관리와 영업관리는 따로 만들고 **최종 단계에서 합친다**(사용자 지시 2026-09-27) — 그 전까지 서로 없는 것으로 보고 작업. 대한사료는 사용자 요청으로 제외.
  - 떼어 낸 자재관리 섹션·캡처는 `portfolio_src/merge_later/`(mm_section.tpl.html, mm_imgs.json)에 보관 — 합칠 때 사용.
- 결과물: `portfolio.html` (스크린샷이 base64로 들어간 단일 파일, 약 0.9MB). 직접 수정하지 말 것.
- 수정은 `portfolio_src/portfolio.tpl.html`에서 하고 `python portfolio_src/build.py`로 다시 생성.
  - `imgs.json`: JPEG(1200x750) base64 이미지 6장 — sales_dash·sales_fc·sales_deal·sales_doc·sales_erp·sales_audit. 원본 PNG는 `portfolio_src/shots/` (대한사료·옛 sales_an PNG도 남아 있음).
  - 스크린샷: 샘플 DB 복사본(`MM_DB_PATH`, `SALES_DB_PATH`)으로 앱 실행 → CDP로 렌더링 완료를 기다린 뒤 1440x900 캡처. 원본 DB는 건드리지 말 것.
    - 겹치지 않는 포트 사용(다른 세션이 5902·5011을 쓸 수 있음). Git Bash에서 `/path` 인자는 `MSYS_NO_PATHCONV=1` 필요.
    - 영업관리: `SALES_AUTH_MODE=sso`, `SALES_SSO_USER=정임원|김영업|시스템관리자`로 실행 → `/login?next=/deals`. 증빙 화면은 `#sale-edit`로 스크롤.
    - 자재관리: 로그인 필수 — DB 복사본에 `core.auth.create_user`로 관리자 생성 + `core.seed.seed()` 후 로그인 폼 제출.
  - 2026-09-27 Version 4: 영업관리 단독판(Flask 스택, 6,020줄, 테스트 37, 감사 해시 체인·ERP·세금계산서 섹션).

## 영업관리 `sales/` (2026-09-27 Streamlit → Flask 전환)
- 사용자 요청으로 Flask 구조로 바꾸고 Streamlit 전용 파일(app.py 화면, .streamlit/, run.bat/sh, auth.py)은 삭제. 전환 전 원본은 `sales_streamlit_backup/`.
- 실행: `python app.py` → http://127.0.0.1:5001 (대한사료 5000번과 분리). 워커 `python manage.py worker`. 운영은 `manage.py db upgrade` 후 `python serve.py`. 테스트: `python -m pytest tests -q` (65개, 임시 DB·폴더).
- PostgreSQL 테스트: conda env `sales-pg`의 PG16을 127.0.0.1:5433(데이터 scratchpad/pgdata, trust)로 띄우고 `SALES_TEST_PG_URL=postgresql://postgres@127.0.0.1:5433/postgres`, `SALES_PG_DUMP`/`SALES_PG_RESTORE`=`<conda base>/envs/sales-pg/Library/bin/pg_dump.exe`/`pg_restore.exe` → 64 통과 + 1 SQLite 전용 skip.
- 구조: `core/`(database 연결계층, sales_db, enterprise, catalog, quotes, jobs, notify, storage, hr, oidc, api_keys, observability, dataio, excel_forms, auth, documents, erp) · `views/`(auth, reports, crm, catalog, finance, io, admin, api) · `migrations/`(Alembic 0001~0006, alembic.ini는 ASCII만 — Windows cp949로 읽힘) · `deploy/`(compose·nginx·Prometheus) · `docs/RUNBOOK.md` · CI는 저장소 루트 `.github/workflows/sales-ci.yml`.
- 2026-09-27 "모두 추가": PostgreSQL+Alembic, S3/로컬 저장소, DB 작업 큐·스케줄러·알림, 품목·특가·견적·부가세(공급가액/부가세/합계), 다단계 결재선·대결·독촉, HR 연동, OIDC(PKCE), REST API(/api/v1), 지표·readyz·JSON 로그·읽기 전용 모드, 회사 엑셀 양식(업로드 열 매핑·내려받기 서식 파일). Docker는 이 PC에 없어 compose는 기동 미검증.
- 2026-09-27 대기업 운영 보강: 담당자 owner_id 연결(동명이인 분리), 승인 후 조건 변경 시 승인 무효, 관리자 자기결재 금지, 삭제 대신 보존(매출은 '취소'), 감사로그 해시 체인+수정·삭제 트리거, password/SSO 인증·잠금·세션 만료, 개인정보 마스킹·수식 주입 차단·다운로드 감사, ERP(SAP OData/파일) 전송 대기열·입금 대사, 세금계산서·전자세금계산서 증빙(이미지/PDF/XML, `sale_documents` 테이블, 파일은 `data/documents/`) 등록·검증. 상세는 `sales/README.md`.
- 하지 않은 것: SAP·IdP·S3 실시스템 연결 검증(모의 서버로만 확인), 세금계산서 발행·OCR, 컨테이너 실기동.
- 포트폴리오(Version 4)는 아직 옛 수치(6,020줄·테스트 37·메뉴 14·SQLite) — 제출 전 갱신 필요. 현재: 코드 10,895줄(테스트 제외)+테스트 1,890줄, 테스트 65, 메뉴 18.
- 사용자 `data/sales.db`에는 담당자 계정 없이 만든 샘플 데이터(9/27 17:54~56, 여러 번 생성)가 있어 담당자 '미연결' 상태(관리자만 조회).
- 확인용 서버는 SALES_PORT=5011 + DB 복사본 사용(Windows는 같은 포트에 여러 프로세스가 바인딩됨).
- 게시된 Artifact: https://claude.ai/artifact/CTxZ4PEpwjc7xMdtveRMNQ (제목 "업무 시스템 포트폴리오". 다른 대화/폴더에서 수정하려면 이 URL을 `url`로 넘겨 업데이트)
- 디자인: 회사 서류(전표·결재란) 형식. 먹색 잉크 + 청색 #2747A3 + 인주 빨강 #C23A2E, Hahmlet(제목) / IBM Plex Sans KR / IBM Plex Mono. 라이트·다크 테마 지원.
- 구성: 요약전표 → 기술 → 자재관리(탭 갤러리, 설계, 계층 구조, 테스트) → 영업관리(탭 갤러리, 권한, Stage Gate, 할인 결재, 예측, 검증) → 일하는 방식.

## 자재관리 `material-manager/` (2026-09-27 Streamlit → Flask 전환)
- 사용자 요청으로 Flask 구조로 전환. 전환 전 원본은 `material_manager_streamlit_backup/`. `core/`(업무 로직)는 그대로 두고 화면 계층만 교체.
- 실행: `MM_SECRET_KEY=... python app.py` → http://127.0.0.1:5002 (대한사료 5000, 영업관리 5001과 분리). 테스트: `python -m pytest tests -q` (90개, SQLite). `MM_DATABASE_URL=postgresql://...mm_test`로 PostgreSQL에서도 같은 90개.
- 구조: `core/`(db·storage·repository·services·approvals·org·periods·reconcile·sap·jobs·auth·audit·documents) · `views/`(블루프린트 14개 + helpers.py) · `templates/` · `static/` · `batch.py`(`sap_sync.py`는 호환용).
- 2026-09-27 대기업 대응 추가(사용자 요청 "모두 실행"): 로그인·역할 4단계(조회/담당자/관리자/시스템관리자)·감사로그(트리거로 수정·삭제 차단),
  거래 삭제 폐지 → 취소 거래(역분개), 월 마감 + 월말 재고 스냅샷, SAP 전송 대기열(mock/http, 실제 SAP 미검증).
- 2026-09-27 보안 강화: 디버그 기본 off, CSP(인라인 스크립트 금지 — onchange 대신 data-autosubmit, Chart.js는 static/vendor),
  세션 쿠키 이름 `mm_session`, 비밀번호 변경 시 이전 세션 무효·30분 무활동 로그아웃, IP 단위 로그인 차단, 최초 설정은 콘솔 코드 필요,
  엑셀 수식 주입 방지, 업로드 압축 폭탄·행 수 제한. 새 화면에 인라인 script/style/onchange를 쓰면 CSP에 막힘.
- 2026-09-27 여러 서버·대기업 기능: DB 어댑터(core/db.py, SQLite/PostgreSQL 같은 SQL, '?' 자리표시자), 파일 저장소(core/storage.py local/s3),
  배치(batch.py + core/jobs.py 임대 잠금), /health, 플랜트·창고(재고는 창고 단위, SAP 플랜트·저장위치는 창고에), 창고 간 이동(311/301),
  데이터 범위(user_scopes), 직무 분리(본인 거래 취소·본인 증빙 삭제 금지, 큰 실사 조정 결재), 증빙 열람·엑셀 내보내기 감사로그,
  수불부·재고 대사 보고서, 목록 100건 페이지. 여러 서버 통합 확인(앱 2대+배치 2개+PG) 통과.
- 2026-09-27 추가: 사내 SSO(core/sso.py, OIDC+PKCE, joserfc 검증, 그룹→역할, JIT), 2단계 인증(core/mfa.py, TOTP, ADMIN 필수 — 테스트는 config.MFA_REQUIRED_ROLES=set()),
  재고 평가(core/valuation.py, MAVG/FIFO replay + 마감 스냅샷), 로트·유효기한(lots 테이블, 스냅샷 PK에 lot_no, FEFO 자동 배정),
  구매(core/purchasing.py + views/purchase.py, 금액별 결재 단계, 요청자 결재·발주 금지, 발주 대비 입고 검사, 3자 대조),
  SAP 마스터 동기화(core/master_sync.py, 자재·원가센터, 동기화 자재 SAP 항목 잠금). QR은 static/vendor/qrcode.min.js.
- 엑셀 양식(core/excel_forms.py, 관리자 → 엑셀 양식): 내려받기는 회사 .xlsx에 채움(열 연결·서식·자리표시 {{제목}} 등), 올리기는 열 이름 별칭·머리글 행.
  모든 엑셀 내려받기는 views/helpers.form_response(양식키, df, 파일명)를 거친다. 새 내려받기를 만들면 EXPORT_FORMS에 양식키·기본 열을 추가.
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
- 페이지의 수치(메뉴 14, 코드 6,020줄, 테스트 37/37, 조회 건수 7/33/55 등)는 코드·README 기준. 프로젝트를 고치면 함께 갱신할 것.

## 영어 시험 학습 `TS/` (2026-09-27 시작, 개인 공부용 — 배포 안 함, 문제 저작권 무관)
- 범위: 토익부터 상세 제작(사용자 지시). 토익스피킹·토플은 나중. 등급 = 토익 인증 색상(Orange 10~215 / Brown ~465 / Green ~725 / Blue ~855 / Gold 860~).
- 실행: `python app.py` → http://127.0.0.1:5003. 테스트 `python -m pytest tests -q` (52개). 문제 검사 `python tools/validate_content.py`.
- 구조: `core/`(db·content·scoring·study·srs·planner·stats·guide) · `views/`(main·quiz·vocab) · `static/js/quiz.js`(풀이 엔진) · `content/toeic/*.json`(형식은 content/SCHEMA.md).
- 문제 은행(모두 새로 작성, `partN.json`+`partN_2.json` 합쳐 읽음, 파일 변경 시 자동 반영): P1 70, P2 250, P3 60세트, P4 50세트, P5 350, P6 50세트, P7 70세트, 단어 1,600(vocab·vocab_2·vocab_3). 등급별 균등.
- 단어 듣기(운전 모드) `/vocab/listen`: 영어 N번→한국어 뜻 반복 재생(Web Speech, 엣지 한국어 음성). 화면 끄고 듣기용 WAV는 `core/audio.py`(PowerShell System.Speech, 오프라인) → `data/audio/` 캐시.
- 듣기 = 브라우저 음성 합성(Web Speech). Part 1 사진은 한국어 장면 설명. 추정 점수는 난이도 가중 정답률 → 구간 보간(실제 점수와 다를 수 있음).
- CSP: script-src 'self'(인라인 스크립트 금지, 데이터는 `<script type="application/json">`), style은 인라인 허용. Jinja에서 `.75` 대신 `0.75`.
- `data/ts.db`는 사용자 학습 기록. 확인용 서버는 `TS_PORT=5913` 등 다른 포트 + `TS_DB_PATH`로 임시 DB 사용.
