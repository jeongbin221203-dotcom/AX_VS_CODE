# 대한사료 웹사이트 (Flask) + 포트폴리오

## 프로젝트
- 학습용 대한사료 기업 사이트 리뉴얼. 공식 사이트 아님 (포트폴리오에 이 안내 문구 유지).
- 실행: `pip install -r requirements.txt` → `python app.py` → http://127.0.0.1:5000
- 라우트 7개(`/`, `/sub1`, `/sub1/detail/<category>`, `/sub2`, `/sub3`, `/sub4`, `/privacy`), 템플릿 8개.

## 포트폴리오 (2026-09-17 개인 포트폴리오로 전환)
- 대상 프로젝트: `sales/`(영업관리) + `material_manager/`(자재관리). 2026-10-02 사용자 선택으로 포트폴리오에 자재관리 섹션을 다시 넣음(코드는 여전히 따로 작업). 대한사료는 사용자 요청으로 제외.
  - 2026-10-02 사용자 지시 "자재관리와 무관한 영업관리 데이터 삭제": 영업 섹션에서 대시보드·매출예측·영업기회·견적·결재함 탭, Stage Gate·할인 결재선 단, 견적→매출·부가세 단, 영업 전용 검증 줄을 뺌.
  - `portfolio_src/merge_later/`는 9/27 옛 보관본(이제 안 씀).
- 결과물: `portfolio.html` (스크린샷이 base64로 들어간 단일 파일, 약 1.8MB). 직접 수정하지 말 것.
- 수정은 `portfolio_src/portfolio.tpl.html`에서 하고 `python portfolio_src/build.py`로 다시 생성.
  - `imgs.json`: JPEG(1200x750) base64 이미지 12장 — sales_doc·sales_erp·sales_form·sales_audit + mm_dash·mm_check·mm_offline·mm_lot·mm_val·mm_po·mm_erp·mm_ops. 원본 PNG는 `portfolio_src/shots/` (뺀 sales_dash 등·대한사료 PNG도 남아 있음).
  - 자재관리 캡처: 세션 scratchpad `shot_mm.py`(임시 DB, seed(history=True), MM_ERP_MODE=mock, SAP 번호 매핑, MM_WORKER_ID=app-01 — PC 이름(실명) 노출 방지, CDP 9447·앱 5919).
  - 스크린샷: 샘플 DB 복사본(`MM_DB_PATH`, `SALES_DB_PATH`)으로 앱 실행 → CDP로 렌더링 완료를 기다린 뒤 1440x900 캡처. 원본 DB는 건드리지 말 것.
    - 겹치지 않는 포트 사용(다른 세션이 5902·5011을 쓸 수 있음). Git Bash에서 `/path` 인자는 `MSYS_NO_PATHCONV=1` 필요.
    - 영업관리: `SALES_AUTH_MODE=sso`, `SALES_SSO_USER=정임원|김영업|시스템관리자`로 실행 → `/login?next=/deals`. 증빙 화면은 `#sale-edit`로 스크롤.
    - 자재관리: 로그인 필수 — DB 복사본에 `core.auth.create_user`로 관리자 생성 + `core.seed.seed()` 후 로그인 폼 제출.
  - 2026-09-27 Version 4: 영업관리 단독판(Flask 스택, 6,020줄, 테스트 37, 감사 해시 체인·ERP·세금계산서 섹션).
  - 2026-10-02 Version 11(게시): 영업 13,584줄·테스트 108·메뉴 21 + 자재 9,696줄·테스트 121·메뉴 19 (합계 23,280줄+테스트 5,658줄, 229 통과). 자재관리 섹션 로고(인라인 SVG).
  - 2026-10-02 Version 9: 13,521줄+테스트 2,633줄, 테스트 89, 메뉴 20. (Version 8: 11,786줄·테스트 80·메뉴 19) (Version 5~7: 11,202줄·테스트 69·메뉴 18)
  - 2026-09-27 Version 5(대기업 제출용, 약 1.3MB): PostgreSQL. 이미지 9장(+sales_quote·sales_appr·sales_form).
    새 섹션: 견적·부가세, 회사 엑셀 양식, 여러 서버 운영·장애 대비, ERP(SAP·REST·파일·수신 API), 결재선 표. 캡처 DB는 세션 scratchpad `demo3`(prep_demo3.py), CDP 포트 9444~9446, 앱 5031~5033.

## 영업관리 `sales_manager/` (2026-09-27 Streamlit → Flask 전환)
- 2026-10-03 폴더 이름 `sales/` → `sales_manager/`(사용자), 브랜치 `sales` → `sales_manager`(커밋 12df649, CI 경로 함께), Render 브랜치·rootDir = `sales_manager`. 아래의 `sales/` 경로는 `sales_manager/` 로 읽을 것.
- 2026-10-03 사이드바 메뉴 = 자재관리와 같은 방식(사용자 "자재관리처럼 즐겨찾기 등"): 기본 순서 자주 쓰는 순(대시보드 고정·영업기회·영업활동·거래처·견적·수주…), 메뉴 편집 ☆ 즐겨찾기·▲▼·끌어 놓기, core/prefs.py·views/prefs.py·user_prefs(마이그레이션 0013), 시연 서버는 세션에 저장. 테스트 122, 브라우저 확인 scratchpad cdp_menu.py 11/11.
- 사용자 요청으로 Flask 구조로 바꾸고 Streamlit 전용 파일(app.py 화면, .streamlit/, run.bat/sh, auth.py)은 삭제. 전환 전 원본(`sales_streamlit_backup/`)은 2026-10-02 삭제 — git 기록에 남아 있음.
- 실행: `python app.py` → http://127.0.0.1:5001 (대한사료 5000번과 분리). 워커 `python manage.py worker`. 운영은 `manage.py db upgrade` 후 `python serve.py`. 테스트: `python -m pytest tests -q` (89개, 임시 DB·폴더).
- PostgreSQL 테스트: conda env `sales-pg`의 PG16을 127.0.0.1:5433(데이터 scratchpad/pgdata, trust)로 띄우고 `SALES_TEST_PG_URL=postgresql://postgres@127.0.0.1:5433/postgres`, `SALES_PG_DUMP`/`SALES_PG_RESTORE`=`<conda base>/envs/sales-pg/Library/bin/pg_dump.exe`/`pg_restore.exe` → 88 통과 + 1 SQLite 전용 skip.
- 구조: `core/`(database 연결계층, sales_db, enterprise, catalog, quotes, jobs, notify, storage, hr, oidc, api_keys, observability, dataio, excel_forms, auth, documents, erp) · `views/`(auth, reports, crm, catalog, finance, io, admin, api) · `migrations/`(Alembic 0001~0006, alembic.ini는 ASCII만 — Windows cp949로 읽힘) · `deploy/`(compose·nginx·Prometheus) · `docs/RUNBOOK.md` · CI는 저장소 루트 `.github/workflows/sales-ci.yml`.
- 2026-09-27 "모두 추가": PostgreSQL+Alembic, S3/로컬 저장소, DB 작업 큐·스케줄러·알림, 품목·특가·견적·부가세(공급가액/부가세/합계), 다단계 결재선·대결·독촉, HR 연동, OIDC(PKCE), REST API(/api/v1), 지표·readyz·JSON 로그·읽기 전용 모드, 회사 엑셀 양식(업로드 열 매핑·내려받기 서식 파일). Docker는 이 PC에 없어 compose는 기동 미검증.
- 2026-09-27 대기업 운영 보강: 담당자 owner_id 연결(동명이인 분리), 승인 후 조건 변경 시 승인 무효, 관리자 자기결재 금지, 삭제 대신 보존(매출은 '취소'), 감사로그 해시 체인+수정·삭제 트리거, password/SSO 인증·잠금·세션 만료, 개인정보 마스킹·수식 주입 차단·다운로드 감사, ERP(SAP OData/파일) 전송 대기열·입금 대사, 세금계산서·전자세금계산서 증빙(이미지/PDF/XML, `sale_documents` 테이블, 파일은 `data/documents/`) 등록·검증. 상세는 `sales/README.md`.
- 하지 않은 것: SAP·IdP·S3 실시스템 연결 검증(모의 서버로만 확인), 세금계산서 발행·OCR, 컨테이너 실기동.
- 2026-09-27 ERP 확장: `rest` 어댑터(Bearer/Basic/헤더 인증, HMAC 서명, 필드 이름 바꾸기, Idempotency-Key), 연결 테스트 버튼, ERP→CRM 수신 API `/api/v1/erp/{payments,acks,credit,products}`(scope erp:write). 테스트 69(SQLite) / 68+1 skip(PG).
- 2026-10-02 회사별 설정·데이터 안전: `core/company.py`(company_settings 테이블, 관리자 > 🏢 회사 설정 — 회사 정보·결재 한도·SLA·단계 확률·코드 목록·개인정보 보관기간, 업무 상수는 같은 객체를 제자리 갱신, 15초 캐시),
  `core/offline.py`(외부 연결 사내망/인터넷 분류), 비상 로그인(`SALES_BREAKGLASS_USERS`), 마이그레이션 0007·0008(sales.row_version, form_submissions).
  입금 조건부 갱신(동시 입금 유실 방지, ERP 대사는 expected_before), 매출 수정 충돌 검사, ERP 대기열 선점('전송중', recover_stuck), 폼 `_submit_id` 중복 제출 차단,
  app.js 입력 보관(localStorage `sales-draft:<user>:`)·연결 끊김 배너. 테스트 80(SQLite) / 79+1 skip(PG). 브라우저 동작은 CDP로 확인(scratchpad cdp_drafts.py).
- 2026-10-02 대기업 보완(마이그레이션 0009): 거래처 중복(biz_no_norm·name_key)·병합(merge_customers),
  감사로그 이관(core/retention.py, audit_archives — 트리거가 이관 범위만 삭제 허용, audit() 은 로그가 비면 이관 last_hash 에서 체인 잇기), 백업 세대(일·월말·연말),
  첨부(core/attachments.py), 회계연도(core/fiscal.py), 개인정보 요청(core/privacy.py, 관리자 메뉴), 법인·환율(core/entities.py), 전자세금계산서 발행(core/etax.py, SALES_ETAX_ADAPTER none|mock|file|rest, 실제 ASP 미검증).
  2단계 인증(OTP)은 사용자 요청으로 같은 날 제거(마이그레이션 0010 이 users OTP 열 삭제).
- 2026-10-02 자재관리와 맞춘 데이터(마이그레이션 0011, 사용자 요청 "자재관리와 비교해 필요한 데이터 추가"): payments(입금 내역·반제, 입금액=합계 불변식 — paid_amount 를 직접 UPDATE 하지 말 것),
  core/periods.py(매출 월 마감 sales_period_closes·ar_snapshots·거래처 원장), ERP 거래처 마스터 /api/v1/erp/customers(erp_synced_at 잠금·trade_blocked), products.spec, sales.created_by_id(ERP 전송 매출 취소는 팀장 이상·본인 불가), 감사로그 detail 에 접속IP.
  같은 날 material-manager 세션이 sales 대시보드(core/insights.py·views/reports.py·dashboard·seed 보정)를 동시에 고침 — 그 세션 작업과 섞여 있어 이 변경은 커밋하지 않음.
- 2026-10-02 채권·영업 실무 6종(마이그레이션 0012): core/returns.py(반품·정정 = 원매출 연결 음수 매출, 원매출에 '반품상계' 입금, 남으면 선수금 '반품초과'),
  core/credit.py(fin_requests: 대손·거래정지해제 결재, 자동 거래정지 job credit.autoblock), core/advances.py(선수금 원장·일괄 입금), core/contacts.py(customer_contacts, 대표=customers.manager),
  core/orders.py(sales_orders·items, 분할 납품, 매출 취소 시 잔량 복귀), 원장은 '반품상계'·'선수금' 입금을 숨기고 선수금 입금·환불을 표시. 로고 static/img/logo.svg(+favicon). 테스트 108 / PG 107+1.
- 사용자 `data/sales.db`에는 담당자 계정 없이 만든 샘플 데이터(9/27 17:54~56, 여러 번 생성)가 있어 담당자 '미연결' 상태(관리자만 조회).
- 확인용 서버는 SALES_PORT=5011 + DB 복사본 사용(Windows는 같은 포트에 여러 프로세스가 바인딩됨).
- 2026-10-04 점검(사용자 "점검 더 실행"): 검토 에이전트 결함 수정 — 큰 숫자·ValueError 전역 처리, 반제 고유 인덱스(마이그레이션 0017)·내부 입금(반품상계·선수금·대손) 반제 금지, 정정 후 반품 단가, 매출 수정은 입금액 불변(입금액=payments 합계)·전자세금계산서 잠금, 견적 상태 조건부 UPDATE(발송·수락·개정·전환), API 매출 값 검사, ERP 참조 CRM-<번호>만, 멱등 5분 이어받기, 개인정보 LIKE ESCAPE '!', 업로드 2만 행·압축 폭탄. 회귀 tests/test_review_fixes.py, 테스트 165 / PG 163+2 skip, 퍼징 82경로 0·순회 1,437화면 0. 상세 docs/SECURITY.md 2-1. 같은 날 남은 5종도 수정(사용자가 목록을 붙여 넣음): 영업기회 없는 할인 견적 발송 금지, etax claimed_at/sent_at·ux_etax_active(마이그레이션 0018)·recover_stuck(erp.send 작업에서)·늦은 회신 이중 발행 경고, database.transaction()(바깥 트랜잭션 + get_conn 저장점 — 납품·반품·선수금 receive/apply·대손 decide), SAP OData 생성 전 PurchaseOrderByCustomer 조회, jobs 실행 중 잠금 갱신(LEASE 60초·STALE 10분)·잠금 잃으면 결과 안 씀. 테스트 177 / PG 175+2 skip(tests/test_remaining_fixes.py). 이어서 사용자 "사용자 및 전문가 입장에서 오류·불편 찾아": 점검 에이전트 3개(실무·회계세무채권·운영) 64건 중 주요 수정 — 상세 docs/SECURITY.md '실무자·회계·운영 관점 점검'. company 설정 holidays(세금계산서 기한), database 아님. 이어서 사용자가 판단 3개·큰 작업 6개 목록을 붙여 넣음 → 판단은 Claude 기본안(특가=결재 기준가, 수주·매출 전환 시 영업기회 자동 수주, 반품초과는 '돌려줄 돈' 따로)으로 하고 6개 모두 구현: 수정세금계산서 01·04·05·06(0019), 세금계산서 잠금(종이 증빙 포함), 대손 법정 사유·대손세액·회수·신고 자료(0020), 외화 한 번 반올림·납품일 환율, core/bulk.py 백그라운드 업로드·추출·엑셀 백업(0021 bulk_tasks, 웹 서버 스레드 — 워커 불필요), 감사로그 검색(기간·대상번호·작업·상세)·요청ID 기록. 상세 docs/SECURITY.md. 2026-10-05 남은 6개도 구현(사용자가 목록 붙여 넣음): 순채권(credit_exposure·자동 거래정지·ar_snapshots.advance 0022·채권 탭 카드), ERP 대사 내부정리 제외, 견적 저장 실패 시 _quotes_page(submitted) 로 다시 그림, 결재 회수(ent.withdraw_approval·credit.withdraw, 상태 '취소'/'회수'), API 인증 실패 IP별(login_ip_failures 'api:<ip>') 429·감사로그 첫 실패·차단만, 면세 전자계산서(0301/0401, DOC_TYPES 전자계산서·계산서·수정계산서). 테스트 210. 2026-10-06 자재관리 비교 → 사용자 "중간 이상은 영업관리에 맞게 적용": 매출취소·입금반제 요청 결재(credit kinds, 0023 payment_id), ent.decide_many, core/hometax.py(매출 대사 탭), 품목·특가·거래처담당자 IMPORT_SPECS(role 검사), 품목 erp_synced_at 잠금·row_version(품목·사용자·조직 0023), erp.reconcile_ar_balances·master_pull(SALES_ERP_MASTER_URL, 작업 erp.master_sync), user_scopes(ent.add_user_scope), /sw.js·/offline, company status_labels·|label 필터. 테스트 221. 2026-10-06 이어서 자재관리 재비교(에이전트) 나머지 전부 적용(사용자가 목록 붙여 넣음): /setup 설정 코드(core_auth.setup_code·SALES_SETUP_CODE), 증빙·첨부 무효 직무 분리(documents.check_void_allowed·attachments.uploaded_by_id 0024)·열람 감사(record_view)·이미지 sandbox CSP·Permissions-Policy, 월 마감 점검(periods.close_checks·sales_period_closes.checks·db.lock)·마감 화면 점검 표, 변경 이력 패널(db.entity_history·_macros change_history), 증빙 파일 백업(core/backup_files.py — backup_database 가 호출, manage.py restore-files, 운영 점검, 관리자 zip), 데이터 점검 6종 추가, 목표 동시 수정(expected), werkzeug>=3.1.9, docs/OPERATIONS.md. 테스트 229. 참고: 보고서 10번(마감 해제 권한 분리)은 영업이 더 엄격해 보류. 2026-10-07 사용자 요청 "회사 엑셀파일을 업로드하면 나중에 회사 양식으로 다운로드": 일괄 등록 검증 화면(또는 표준 양식이 아니라 거부된 회사 파일)에서 '이 모양 기억하기'(영업지원↑) → xf.remember_upload 가 머리글을 읽어 열을 자동 연결하고 업로드·내려받기 양식을 함께 만듦(올린 파일에서 데이터 행만 비운 서식 파일 xf.clean_template, 합계 줄 SUM → {{합계:열}}, 병합 범위 보정, 내려받기 변환표 = 업로드 변환표 역방향), 기억 직후 /data/import/recheck 로 같은 파일을 이어서 검증, 받을 때는 데이터 추출의 '회사 양식으로 받기'. 시연 서버는 DEMO_LOCKED. tests/test_remember_format.py. 테스트 231 / PG 229+2, 커밋 68c7c09(원격 반영·시연 서버 /data/import/recheck 확인).
- 2026-10-10 사용자 "더미 데이터를 깔끔하게 적은 수량, 큰 금액 / 추가 데이터 목록을 넓혀 복잡하게": 기본 샘플 = core/sample_clean.py(거래처 12·영업기회 18·매출 약 77, 수량 ≤5·금액 ≥100만, 고정 표라 매번 같음; demo-init·관리자 '샘플 데이터 생성'이 사용, seed_demo_data 는 테스트·CLI용으로 유지). 업종별 복잡 데이터는 기본에서 빼고(SALES_DEMO_EXTRA=1 이면 demo-init 에 포함) 관리자 '추가 데이터'에서 선택 — 추가 업종 물류·식품·에너지·반도체(core/sample_extra.py, PRESETS 에 합침, INDUSTRY_KEYS 는 기본 8개 그대로). 포트폴리오의 옛 샘플 수치와 시연 화면이 달라짐. 테스트 232.
- 2026-10-03 Render 배포: 서비스 `sales`(srv-db06iqqd0e5s73aael40) https://sales-5j42.onrender.com — 브랜치 `sales_manager`, rootDir `sales_manager`, 자동 배포, free·singapore.
  운영 모드 + password 로그인. 2026-10-03 첫 접속 속도 개선(core/demo_data.py): **빌드** `pip install -r requirements.txt && python manage.py demo-build`(샘플 DB data/demo_template.db, 약 15초), **시작** `python serve.py`(빈 DB 에 샘플을 넣고 스키마 갱신까지 한 프로세스, 시작→live 약 25초 — 예전 demo-init 시작은 약 3분).
  샘플 기준일이 지나면(매일 새벽 4시, TZ=Asia/Seoul) 별도 프로세스가 새로 만들어 DB 경로를 바꿔 끼움. '샘플로 되돌리기' POST /demo/reset. bulk_load 는 연결 하나를 열어 두어 WAL 체크포인트 반복을 막음(84초→39초).
  사이드바 아래는 자재관리와 같은 모양(연체 미수·결재 대기·ERP 실패 상자, 시연 안내, 다른 역할로 보기, 마감·ERP·DB 상태), 대시보드 맨 위 '포트폴리오 시연 안내'.
  2026-10-03 추가(사용자 "모두실행" + 메신저): 시연 안내 접힘·배너 한 줄, 고정 샘플(SALES_DEMO_SEED=2026, PYTHONHASHSEED=0 — 배포마다 같은 이름·금액, 날짜만 오늘 기준),
  휴대폰(760px↓ ☰ 메뉴·'＋ 활동 기록'), 영업지원 역할 SUPPORT(전사 조회·데이터 점검·병합·품목, 결재 못 함, 시연 '다른 역할로 보기'에 윤지원 2008),
  회사 설정 '영업 단계'(진행 5단계 이름·확률, 수주·실주 고정, 이름 바꾸면 deals·이력도 변경), 메신저 알림 core/messenger.py(잔디·네이버웍스·카카오워크·Slack·Teams·웹훅, 관리자 > 🔔 알림 채널, 마이그레이션 0014, 실서비스 미검증),
  대량 데이터(거래처 1만·매출 10만, scratchpad bulk_make.py/bulk_measure.py): 선택지 300개↑ 서버 검색(/options/<kind>), 보드 열당 60장, 매출 탭별 계산, 대시보드·영업기회 60초 캐시(POST 시 무효), 인덱스 0015.
  영업사원 화면 대부분 0.1~0.5초, 시스템관리자 전사 화면 0.3~4초(PC 부하 높을 때). 테스트 137, CDP cdp_bulk.py 11/11·cdp_ux.py 20/20. 커밋 f2339f3.
  2026-10-03 밤 자재관리와 맞춘 보안·운영 13종(커밋 55b9bdc, 마이그레이션 0016): 세션 판(비번 변경·로그아웃 때 다른 세션 끊김, 관리자 '모든 세션 끊기'),
  IP 로그인 차단(15분 20회, SALES_IP_ALLOWLIST 예외), 실제 IP(SALES_CLIENT_IP_HEADER — Render 는 True-Client-IP 로 설정함), core/doctor.py(manage.py doctor·관리자 운영 점검),
  점검 모드·SSO 장애 모드(회사 설정 maintenance·sso_outage_until, manage.py read-only·sso-outage), 백업 검증·SQLite synchronous=FULL·같은 디스크 경고,
  core/customer_names.py(다른 이름·이름 정리 탭), 위험 버튼 확인 창(버튼별 data-confirm 지원), S3 SpoolingStorage(웹 서버마다 flush 스레드),
  users.messenger_id, SALES_AUDIT_STDOUT(Render 설정함, 샘플 생성 중엔 안 찍음), docs/SECURITY.md·CI bandit·pip-audit(High 0, 취약점 없음).
  밤샘 점검(사용자 지시 "5시간 혼자"): PostgreSQL 16(scratchpad pgdata, 포트 5437) 테스트, 코드 검토 12건 반영, 금액 칸 step 버그(1,234,567 입력 불가) 수정,
  시연 안내 순서 브라우저 따라 하기 scratchpad cdp_flows.py 13/13(수주 전 견적 필터 ?ready=1 · 샘플에 수락·수주 전 견적 2건).
  2026-10-03 사용성 5종: 목록 행 클릭 → 같은 화면 수정 칸(swapMain, data-edit-anchor, 조회 조건 유지), 긴 select 검색(app.js enhanceSelect, 10개 이상), 영업기회 보드(?view=board, POST /deals/stage), 관리자 🩺 데이터 점검(core/quality.py, 자동 수정은 담당자 연결·종료일 맞추기만). 테스트 130, 브라우저 확인 scratchpad cdp_ux.py 20/20. SALES_SESSION_MINUTES=60(사용자 지시). 모든 계정 비밀번호 = Render 환경변수 SALES_DEMO_PASSWORD.
  2026-10-03 대시보드 금액 카드 = 자재관리 형식(₩ 원 단위 `|krw`, 건수·전월 같은 기간 대비, 4×2: 매출·목표·미수(연체)·파이프라인 / 입금·반품정정·수주실주·영업활동, kpi_summary 에 sales_cnt·receipts·adj·late 추가). 화면 금액은 모두 `|krw`(그래프 축만 백만원).
  시연 샘플 현실화(sample_industry): 이번 달 정기 구매 포함, 수주는 지난 날짜·대형 설비 1대·분할 납품·입금, 수주/실주 마감, backdate_customers·realign_targets(demo-init). Render API 키는 사용자가 폐기함(2026-10-03, 이후 API 호출 401).
  SQLite 는 빌드 때 만든 시연 데이터 — 재시작·재배포하면 그 상태로 돌아감(무료 디스크는 휘발). Hobby 25개 한도라 일시중지된 AX_VS_CODE-1 을 사용자 승인으로 삭제하고 만듦.
  사용자 요청(포트폴리오용)으로 `SALES_DEMO_AUTOLOGIN=9999` — 로그인 화면 없이 시스템관리자로 자동 접속, 사이드바 "다른 역할로 보기"(/demo/as/<사번>, config.DEMO_ROLES), 시연 안내 문구. SALES_DEMO=1 이 없으면 꺼짐.
  자재관리(core/demo.py)와 같은 흐름: 공개 화면(로그인) 밖에서만 자동 로그인 → 로그아웃하면 로그인 화면에 "시연 관리자로 들어가기" + 사번 로그인, 시연 계정이 중지·강등돼도 자동 로그인 때 복구(views/helpers.demo_user).
  사용자 선택 "관리자 설정만 잠그기": 시연 서버에서 admin 블루프린트·회사 엑셀 양식·비밀번호 변경 POST 를 막음(helpers.DEMO_LOCKED), 업무 데이터는 저장됨(재시작 시 초기화).
- 2026-10-02 대시보드에 자재관리와 맞춘 항목 추가(사용자 요청 "서로 추가해야 될 항목", 코드는 서로 연결하지 않음):
  `core/insights.py`(최근 30일 일별 매출 · 품목군별 매출 · 견적 만료 임박 7일) + '즉시 확인' 탭에 연체 미수금(ent.ar_aging) ·
  견적 만료 임박 · 내 결재 대기(ent.pending_for) · ERP 전송 실패(팀장 이상), 빈 DB 안내. 테스트 `tests/test_dashboard_extra.py`(7개, 전체 97 통과).
  2026-10-02 데이터 결함 수정: 연체구간 이름(1~30/31~60/61~90/90일 초과, AR_BUCKETS), 미수 판정을 합계(VAT 포함) 기준, '매출액'→'청구액(VAT포함)',
  kpi_summary 이번 달은 전월 같은 날짜까지(mom_partial), 대시보드에 마감 지난 기회·신규 거래처, 거래처 사업자번호 검증번호 확인(새로·바꿀 때만),
  seed_demo_data 끝 `_seed_fixups`(부분입금 입금액+payments 행, 품목 연결, 수주 기회 금액=매출 합계, 매출 없는 수주에 매출), 샘플 사업자번호 검증번호 맞춤.
  사용자 data/sales.db(샘플 12번 생성분)는 손대지 않음 — 중복 거래처 24×12, 검증번호 오류 252, 수주인데 매출 없음 112, 종료일<생성일 170 등.
- 게시된 Artifact: https://claude.ai/artifact/CTxZ4PEpwjc7xMdtveRMNQ (제목 "업무 시스템 포트폴리오". 다른 대화/폴더에서 수정하려면 이 URL을 `url`로 넘겨 업데이트)
- 디자인: 회사 서류(전표·결재란) 형식. 먹색 잉크 + 청색 #2747A3 + 인주 빨강 #C23A2E, Hahmlet(제목) / IBM Plex Sans KR / IBM Plex Mono. 라이트·다크 테마 지원.
- 구성: 요약전표 → 기술 → 영업관리(탭 4장, 권한, 회사 엑셀 양식, 감사로그, ERP, 여러 서버 운영·장애 대비, 세금계산서, 검증) → 자재관리(탭 8장, 재고=거래 합계·동시 출고, 인터넷 끊김 대비, 중복·덮어쓰기 방지, ERP 7방식, 구매 결재·3자 대조, 검증) → 일하는 방식.

## 자재관리 `material_manager/` (2026-09-27 Streamlit → Flask 전환)
- 2026-10-03 폴더 이름 `material-manager/` → `material_manager/`(사용자). 브랜치 이름도 2026-10-03 `material_manager` 로 바꿈(사용자 "브랜치명을 폴더 이름으로"), 브랜치 커밋 5417166 에서 이름 변경, Render rootDir = `material_manager`(API 로 변경). 커밋 도우미는 옛 경로도 같은 프로젝트로 봄(ALIASES).
- 사용자 요청으로 Flask 구조로 전환. 전환 전 원본(`material_manager_streamlit_backup/`)은 2026-10-02 삭제 — git 기록에 남아 있음. `core/`(업무 로직)는 그대로 두고 화면 계층만 교체.
- 실행: `MM_SECRET_KEY=... python app.py` → http://127.0.0.1:5002 (대한사료 5000, 영업관리 5001과 분리). 테스트: `python -m pytest tests -q` (2026-10-03: 168 통과 + 1 skip(pg_dump 전용)). PostgreSQL은 `MM_DATABASE_URL=postgresql://...mm_test MM_PG_DUMP=... MM_PG_RESTORE=...`로 120 + 2 skip(SQLite 전용).
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
- 2026-09-27 추가: 사내 SSO(core/sso.py, OIDC+PKCE, joserfc 검증, 그룹→역할, JIT), 2단계 인증은 2026-10-02 사용자 지시로 기능 전체 삭제(core/mfa.py·/mfa·/login/mfa 없음, 예전 DB의 totp 칸은 남기고 값은 시작할 때 비움),
  재고 평가(core/valuation.py, MAVG/FIFO replay + 마감 스냅샷), 로트·유효기한(lots 테이블, 스냅샷 PK에 lot_no, FEFO 자동 배정),
  구매(core/purchasing.py + views/purchase.py, 금액별 결재 단계, 요청자 결재·발주 금지, 발주 대비 입고 검사, 3자 대조),
  SAP 마스터 동기화(core/master_sync.py, 자재·원가센터, 동기화 자재 SAP 항목 잠금).
- 엑셀 양식(core/excel_forms.py, 관리자 → 엑셀 양식): 내려받기는 회사 .xlsx에 채움(열 연결·서식·자리표시 {{제목}} 등), 올리기는 열 이름 별칭·머리글 행.
  모든 엑셀 내려받기는 views/helpers.form_response(양식키, df, 파일명)를 거친다. 새 내려받기를 만들면 EXPORT_FORMS에 양식키·기본 열을 추가.
- 2026-09-27 ERP·SAP 연결 방식(core/erp.py, 사용자 요청 "ERP, SAP 모두 가능하게"): MM_ERP_MODE(예전 MM_SAP_MODE, 내부 변수는 config.SAP_MODE) =
  off|mock|http(EAI)|sap_odata(S/4 API_MATERIAL_DOCUMENT_SRV)|sap_rfc(pyrfc BAPI)|rest(기타 ERP, erp_maps/*.json 매핑)|file(공유 폴더+ack).
  대기열·멱등키·재시도는 core/sap.py 그대로, 연결마다 send/ping/fetch_master/fetch_stock. 실제 SAP·ERP 미검증(tests/test_erp.py의 가짜 서버·가짜 pyrfc).
- PostgreSQL 테스트: 세션 scratchpad에 conda로 PG16 설치(포트 55432, DB mm_test). 새 세션이면 다시 띄워야 함.
  (2026-10-02 세션은 `miniforge3/envs/sales-pg`의 initdb·pg_ctl로 scratchpad pgdata, 포트 55433)
- 로그인이 생겼으므로 스크린샷은 로그인 세션이 필요. 사용자가 없으면 `/setup`으로 이동. 캡처 시 DB 복사본에 사용자를 만들고 CDP로 세션 쿠키(이름 mm_session)를 넣는다.
  CDP 포트는 9333 말고 다른 번호 사용(다른 세션이 9333을 씀). Git Bash에서는 MSYS_NO_PATHCONV=1(인자 '/..' 경로 변환 방지).
- `data/materials.db`는 비어 있음(자재 0종).
- 2026-10-02 장애·실수 대비(사용자 요청 "인터넷이 멈춰도 사용 가능, 데이터 변경·저장 날아감 확인"): 중복 제출 방지(core/once.py, csrf() 매크로에 `_once`,
  app.py before/after_request — 3xx면 DONE, 그 외 표 삭제), 엑셀 업로드는 빈 칸·없는 열이면 기존 값 유지(`_blank` 열)·SAP 항목 보호, 자재 수정 updated_at 충돌 검사,
  자동 백업(core/backup.py, 배치 db_backup, `flask backup`), S3 장애 임시 보관(storage.SpoolingStorage, 배치 storage_flush, /health degraded=200),
  SSO 장애 모드(app_settings sso_outage_until, 사용자 화면·`flask sso-outage`), SQLite synchronous=FULL, 브라우저 입력 임시 저장·끊김 알림·두 번 클릭 방지(static/js/app.js).
  ERP: OData since는 UTC로, SAP 취소 재시도 전 기존 취소 문서 확인, 재고 대사는 창고 범위 사용자에게 권한 밖 (플랜트, 저장위치) 제외. CDP 확인 10/10(포트 5917·9447).
- 2026-10-02 추가(사용자 "실행"): 오프라인 입출고 대기열(입출고·이동 폼 data-offline → 브라우저 localStorage `mm-queue`, 사용자별,
  연결되면 순서대로 POST /transactions/queue — 서버가 같은 규칙으로 재판정, 거부는 사유와 함께 남김, `_once`=브라우저가 만든 표),
  서비스 워커 /sw.js(static/js/sw.js, 입출고 화면·정적 파일 보관, HTTPS·localhost에서만 동작, 로그아웃·로그인 화면에서 비움),
  동시 수정 판(core/version.py, `_ver` — 플랜트·창고·사용자·범위·발주 SAP 번호), PostgreSQL 백업(pg_dump `MM_PG_DUMP`, pg_restore --list 검사),
  백업 같은 디스크 경고, 운영 점검(core/doctor.py, `flask doctor`, 배치 화면 버튼). CDP 오프라인 13/13 + 입력 보호 10/10.
  CDP 확인 시 Chrome 프로필은 짧은 경로(예: %TEMP%\mm9447)로 — scratchpad 긴 경로는 260자 제한으로 CacheStorage 오류.
- 2026-10-02 거래명세서 입출고(core/statements.py · views/statements.py · statements 테이블, transactions.statement_id): 엑셀·PDF·스캔(OCR은 Tesseract 있을 때, core/statement_reader.py)·직접 입력 → 미리보기 → 한 트랜잭션 등록, 명세서 전체 취소(services._reverse), 미리보기 최근 10개. services._register/_reverse 는 트랜잭션 안에서 쓰는 본문.
  제조 샘플 core/seed_mfg.py (데이터 관리 '제조 샘플 추가', 창원 공장 P-CW). 브랜치 `material-manager`(main 위 커밋 0872465, 작업 폴더는 job-fit 그대로) — 이후 변경은 아직 브랜치에 없음.
- 2026-10-02 대시보드에 영업관리와 맞춘 항목 추가(코드는 서로 연결하지 않음): 기준월(?ym=) · 입고/출고 금액 전월 대비 ·
  월별 입출고 금액 12개월 · 창고별 재고금액 · 출고 금액 상위 자재 · 소진 예상(최근 30일 평균 출고, 14일) · 차트마다 '수치 보기' ·
  '즉시 확인' 탭(안전재고 미달 · 유효기한 · 소진 임박 · 90일 미사용 · 결재 대기 · 입고 예정/지연 발주 · ERP 전송 실패). 쿼리는 `core/insights.py`.
  2026-10-02 데이터 결함 수정: 30일 그래프는 수량 대신 금액(단위 섞임), 실사조정 금액 카드·그래프, 이번 달은 전월 같은 날짜까지 비교(insights.month_to_date),
  재고금액은 '(기준단가)'로 밝히고 재고평가(이동평균) 금액 함께 표시. seed.seed(history=True)=화면 '샘플 데이터 생성'(지난 11개월·장기 미사용·로트/유효기한·WH2 이동·늦은 발주·결재 대기). seed.seed()는 테스트용 그대로.
- 2026-10-03 Render 배포: 서비스 `material`(srv-davsejugekts73f9stlg, https://material-7gzj.onrender.com, 무료·싱가포르), 브랜치 material_manager 자동 배포, 시작 `waitress-serve --listen=0.0.0.0:$PORT --call app:create_app`, 상태 확인 /health.
  포트폴리오용 시연 모드 `MM_DEMO=1`(core/demo.py): 방문자 자동 시스템관리자(demo) 로그인 + 빈 DB면 seed(history=True)+seed_mfg+core/seed_demo.seed_large(4플랜트·9창고·자재 74·거래 약 7천·사용자 8·PR 301). 무료 플랜이라 재시작·재배포 때 데이터 초기화. Render 환경변수를 바꾸면 수동 재배포 필요(진행 중 배포엔 반영 안 됨).
  사용자 선택(2026-10-03): 방문자 변경은 저장하지 않고 '샘플로 되돌리기'(POST /demo/reset)·매일 4시(KST) 첫 요청 자동 초기화, 감사로그는 표준출력 `AUDIT {json}`(MM_AUDIT_STDOUT)으로 Render 로그에 남김.
  Render env: MM_DEMO=1, MM_IDLE_MINUTES=60(사용자 요청 1시간), MM_CLIENT_IP_HEADER=True-Client-IP(실제 IP), TZ=Asia/Seoul, MM_SECRET_KEY 등. "로그 확인해줘" = Render API `GET /v1/logs?ownerId=tea-dakgjqoae00c73bcq770&resource=<srv>`(키는 사용자에게 받음, 파일에 저장 안 함)로 오류·AUDIT 확인 후 수정·배포.
  2026-10-03 Render가 'live 1f423e7'이라 표시하면서 이전 빌드(219e0e9)를 계속 서빙한 일이 있음 → 배포 뒤 화면·static 파일의 새 표시(또는 last-modified)로 확인하고, 옛것이면 `clearCache: clear`로 다시 배포.
  사이드바 메뉴(2026-10-03): 기본 순서 = 자주 쓰는 순(대시보드 고정), 사용자별 순서·즐겨찾기(core/prefs.py, user_prefs, 시연 모드는 세션), 브라우저 확인 scratchpad cdp_menu.py(11/11, 끌어 놓기는 미확인).
  PC 저장 방식(deploy/pc_portfolio.ps1 + MM_FORWARD_URL 앞단 전달, cloudflared 설치됨)은 코드만 있고 미가동 — 키 파일 저장·상시 터널·자동 시작이 권한 시스템에 막혀 사용자 결정 대기.
- 2026-10-03 사용자 요청(면접관·실무자·데이터 관리자 관점 개선 5종 + BOM): 커밋 0c62737·2f3faa0(브랜치 material_manager).
  생산 투입 BOM(core/production.py, boms·bom_items(부품별 issue_wh_id)·productions, transactions.production_id, 소요량→부품 출고+완제품 입고 한 트랜잭션, 부족분 구매요청, 전체 취소),
  여러 줄·스캔 입출고(/transactions/batch, services.register_lines·cancel_group, transactions.batch_no, static/js/picker.js — 자재 찾기·스캐너 Enter·카메라 BarcodeDetector, Permissions-Policy camera=(self)),
  materials.barcode(다른 자재 코드·바코드·SAP 번호와 겹침 금지), /materials/lookup.json, 거래 이력 검색어 q,
  거래처 마스터(core/partners.py, partners·partner_aliases, transactions/statements.partner_id·purchase_orders.supplier_id, 원가센터 출고는 거래처 아님, MM_PARTNER_REQUIRED),
  결재 알림 메일(core/notify.py, notifications, 배치 notify_send, MM_NOTIFY_MODE off|log|smtp, users.email), 시연 안내 띠(base.html .demo-banner)·대시보드 안내, 휴대폰 메뉴 가로 한 줄.
  테스트 168(+test_features.py 17). 브라우저 확인 scratchpad cdp_check.py 20/20(포트 5931·CDP 9461, 새 DB·새 프로필이어야 함 — 생산 샘플이 베어링을 써서 반복 실행하면 부족).
- 2026-10-03 2차(사용자 "필요한 내용 추가", 커밋 c28f6fd): 작업지시(productions 확장 status PLANNED→RELEASED→DONE, production_lines 계획·투입·반납, wo_operations 공정 실적, 백플러시, 실제 원가=최근 180일 입고 가중평균 ÷ 양품, 재공품 탭) ·
  MRP(core/mrp.py, mrp_demands·runs·plans, BOM 다단계·리드타임 역산·MOQ·배수·안전재고는 그 플랜트에서 다룬 자재만, 계획→구매요청·작업지시, 메뉴 📅 MRP) ·
  단위 환산(core/uom.py, material_units, transactions.entry_unit/entry_qty, 상자 바코드) · 스캔 수량 유지·`24*코드` · 여러 줄 화면 오프라인 대기열(kind BATCH) ·
  Code128 라벨(core/barcode.py, /materials/labels) · ZXing(static/vendor, 아이폰 카메라, 가짜 카메라로 라벨 인식 확인) · 잔디·네이버웍스(notify channel, users.messenger_id, MM_JANDI_*·MM_NAVERWORKS_*) ·
  DB 버전 관리 core/migrate.py + migrations/versions(0001 기준선, 0002; `flask db ...`, 앱 시작 때 자동 upgrade — 새 표·칸은 리비전으로만) ·
  /metrics(core/metrics.py, MM_METRICS_TOKEN) + deploy/prometheus · docs/OPERATIONS.md·SECURITY.md · 거래처·BOM 엑셀 일괄(core/bulk.py) · 자재 많으면 /materials/search.json(MM_LOOKUP_MAX) ·
  CI 저장소 루트 .github/workflows/material-ci.yml(material_manager 브랜치) · requirements: flask>=3.1.3, pdfplumber·pymupdf·pytesseract 추가(전에는 Render에서 PDF 거래명세서 읽기가 안 됐음).
  테스트 SQLite 192+1 skip, PostgreSQL 191+2 skip(conda sales-pg 의 PG16, scratchpad pgdata, 포트 55434). 브라우저 확인 scratchpad cdp_check.py 20 + cdp_check2.py 17(새 DB 필요).
  heredoc 으로 긴 파이썬을 넘기면 Bash 도구가 잘라먹는 일이 잦음 → 패치 스크립트는 scratchpad 파일로 써서 실행.
- 2026-10-03 3차(사용자 "영업관리 프로젝트 확인해서 UI, 명칭, 부족한거 추가"): 영업관리(sales_manager 브랜치)는 읽기만(scratchpad salesref 에 git archive), 코드 연결 없이 자재관리에 같은 개념을 새로 구현.
  리비전 0003(notifications.read_at·link, approval_delegations, approval_reminders, partners.merged_into, api_keys).
  🔔 알림함(notify channel 'inbox', 메일 설정과 무관, /notifications) · 결재함 재구성(core/workflow.py: 📥 내 결재 대기=내 차례만·대결 포함, 📤 내 요청 현황, 🗃️ 결재 이력, 🔁 대결 지정 core/delegation.py, 📋 실사 조정 전체, 배치 approval_remind 독촉) ·
  🏢 회사 설정(core/company.py, app_settings 'company', 15초 캐시, 저장한 항목만 config 덮어씀) · 🛠️ 점검 모드(core/maintenance.py, `flask read-only on|off`) · /readyz · X-Request-ID · MM_LOG_JSON · 모든 내려받기 감사(DOWNLOAD) ·
  🩺 데이터 점검(core/quality.py, /quality) · 🔀 거래처 병합(partners.merge, merged_into 로 지난 거래 실적 합침) · 🔑 API(/api/v1 materials·stock·transactions, Idempotency-Key, 키 해시·범위·창고·IP·분당 120) ·
  화면(static/js/ui.js: tr[data-href] 행 누르기, 10개 넘는 select 찾기, data-confirm, data-check-all, 구매요청 결재 단계 미리 보기, 작업지시 ▦ 보드) · 이름(그림 글자 탭, 💾 저장, 🚨 즉시 확인이 필요한 항목, 내 결재 대기, 비밀번호 변경, 접근 권한 없음, ⏱️ 배치 작업, |krw |won 필터).
  하지 않음: 모든 기록 첨부파일, 개인정보 요청, 감사로그 해시 체인, 회계연도, 통합 데이터 등록·추출 화면. 브라우저 확인 scratchpad cdp_check3.py 17/17.
- 2026-10-03 4차(사용자 "수정" — 데이터 운용 불편 5종, 커밋 9d5bfbd): 자재 마스터·단위 환산·BOM 내려받기(올리기 양식과 같은 열) · 단위 환산 일괄 등록(배수 0=삭제) ·
  목록 엑셀(구매·결재함·작업지시·재공품·MRP 계획·데이터 점검 전부·증빙) · 거래 취소 요청(approval_requests kind 'CANCEL', 거래 이력 📨 → 관리자 승인 시 services._reverse) ·
  결재함 한꺼번에 승인(workflow.decide_many, form="bulk-approve" 체크박스) · 여러 줄 화면 줄마다 현재고(/transactions/stock.json)·📦 발주 불러오기(/transactions/po-lines.json, line_po="발주|품목", LineIn.po_no/po_item) ·
  MRP 수요 엑셀 일괄(바꾸기 선택) · 배치 mrp_nightly(MM_MRP_HOUR 기본 2, -1 끔, 실행자 'MRP 자동 실행') · 변경 이력(audit.entity_history, 자재 수정·거래처 상세, 엑셀 반영도 자재·거래처마다 기록).
  테스트 SQLite 221+1, PG 220+2. 브라우저 scratchpad cdp_check4.py 27/27(ERP off 서버 — mock 이면 SAP 번호 없는 시연 자재 입고가 거부됨).
- 2026-10-04 밤샘 점검(사용자 "5시간 혼자 오류 확인·수정"): 점검 에이전트 3개(권한·범위 / 재고·데이터 / 화면) 결과를 확인해 수정 — 생산 거래 한 줄 취소 금지(services.single_cancel_problem),
  로트 부품 반납은 투입 로트로(production._return_lots), 작업지시 완료·취소·공정 실적·MRP 수요 닫기 창고 범위(_scope_problem), 거래처 상세·목록 실적 창고 범위,
  거래처 일괄 구분 빈 칸=그대로, 단위 바코드≠자기 자재 코드, BOM 일괄 미리보기에서 순환, 엑셀 날짜 숫자(_excel_date), MRP 입고 창고 없는 작업지시는 공급 아님,
  sw.js 가 메뉴(fetch)로 연 입출고 화면도 보관, 결재함 줄마다 입력 보관 키(draftKey 에 숨은 id), 확인 창 '취소' 뒤 두 번 클릭 막기 풀림·끊김 때 취소해도 전송되던 문제, 리비전 0004(감사로그 entity 색인).
  테스트 tests/test_review2.py(10). 브라우저 cdp_check5.py 7/7.
- 2026-10-04 다시 점검(사용자 "다시 점검", 에이전트 3개: 수정 재확인 / 미점검 업무 로직 / 실행·업로드·잘못된 입력): 취소 요청은 결재 때 다시 확인(이미 취소·생산 거래면 자동 종료, 이동은 두 줄 모두로 중복 확인),
  BOM 일괄 기준수량 빈 칸=그대로, MRP 계획→요청은 그 플랜트 최신 실행에서만, 거래명세서 유효기한 날짜 숫자, 실사 조정 결재 대기 중이면 같은 자재·창고·로트 새 조정 거부(두 번 반영 방지),
  SAP 대기열: 한 번이라도 보낸 원거래는 취소 때 버리지 않고 원거래 결과를 기다려 취소 전송, 재고 대사: 같은 SAP 번호·저장위치 여러 줄은 합계 비교, 월 마감: 결재 대기 실사 조정 있으면 거부,
  API 본문이 배열이면 400(멱등키 잠김 방지), 데이터 점검 '결재 기한 넘김' 창고 범위, 시연 안내 링크는 역할별, CSV cp949 읽기, 자재 업로드 '1,000' 숫자, 큰 번호·글자 번호 500 → 안내(as_id/f_id).
  테스트 test_review2.py 22개 + test_demo 1개.
- 증빙(세금계산서·전자세금계산서 이미지) 기능: `core/documents.py`, `views/documents.py`, `documents` 테이블, 파일은 DB 옆 `attachments/`.
- 주의: Windows에서 개발 서버는 같은 포트(5002)에 여러 프로세스가 동시에 바인딩된다. 2026-09-27 다른 프로세스가 5002에서 원본 DB로 떠 있어
  확인 요청이 원본에 들어간 적 있음(원본은 빈 상태로 복구, 오염본은 세션 scratchpad에 보관). 확인용 서버는 `MM_PORT=5902` 등 다른 포트 + DB 복사본 사용.
- 포트폴리오: 2026-10-02 자재관리 섹션 다시 넣음(Version 11). 로고 `static/logo.svg`(탭 아이콘·사이드바·/favicon.ico). 배치 서버 이름은 MM_WORKER_ID로 바꿀 수 있음.
- 2026-10-03 시연 모드 보강(sales 세션, 커밋 ce0fd11, 사용자 지시): 관리자 설정 저장 잠금(core/demo.locked — admin 블루프린트 POST + auth.password + data_admin.make_seed/make_seed_mfg, /demo/reset·업무 화면은 허용),
  사이드바 '다른 역할로 보기'(GET /demo/as/<role>, demo.ROLE_VIEWS = demo·park.jh·kim.mj(인천)·kang.dy, 중지·강등돼 있으면 되돌림), 안내 문구에 잠금 설명. Render https://material-7gzj.onrender.com 반영 확인.
  PC 영구 저장(MM_FORWARD_URL + deploy/pc_portfolio.ps1)은 코드만 있고 미설정(2026-10-03 확인: mm-portfolio 폴더·작업 스케줄러·터널·Render MM_FORWARD_URL 없음).
- 2026-09-27 같은 시각 다른 세션이 5977번·CDP 9333으로 자재관리 앱을 캡처 중이었음(포트폴리오 작업으로 추정). 이 세션의 로그인 도입으로 그쪽 캡처 흐름이 로그인 화면에 막힐 수 있음.

- **📌 다음 작업 6개 — 2026-10-04 자재관리 세션에서 완료** (sales 가 적은 목록, 코드는 서로 연결하지 않고 따로 구현):
  1) 🔔 알림 채널(core/messenger.py, /admin/channels, 리비전 0005 messenger_channels·notifications.channel_id/event): 잔디·네이버웍스·카카오워크·Slack·Teams·웹훅, 시험 보내기·발송 기록,
     채널별 알림 종류(REQUEST/RESULT/REMIND)·받는 곳(room/user), 비밀값 Fernet 암호화(MM_NOTIFY_KEY→MM_SECRET_KEY). 환경변수 방식도 유지.
  2) 🏷️ 이름 설정(core/names.py, 회사 설정 화면): 거래 구분·작업지시·구매요청·발주·결재 상태 이름(코드로 저장 → 보이는 이름만, dict 제자리 갱신·15초),
     자재 분류 이름 바꾸기·합치기(materials 를 한 트랜잭션에서).
  3) 데이터 관리 역할 DATA(리비전 0006 — users.role CHECK 를 SQLite writable_schema/PG 제약 재생성으로 넓힘): 전사 조회 + 자재 마스터·단위·거래처·데이터 점검(helpers.can_master/master_required/role_or_data), 입출고·결재 없음. 시연 '다른 역할로 보기'에 윤지원(yoon.jw).
  4) 메뉴 편집·즐겨찾기: 이미 있음 확인.
  5) 대량 데이터(scratchpad bulk_measure.py·bulk_profile.py, 자재 1만·거래 10만·거래처 3천): 덮는 색인 idx_tx_stock(리비전 0007, 0.4→0.02초), core/cache.py(사이드바 30초·대시보드 60초·데이터 점검 60초,
     저장 POST 성공 시 비움, 'slow' 키(재고평가 합계 5분)는 유지, 테스트는 끔), 거래처·수불부·재고평가 100건씩, 대시보드 표 200건, 거래처 300↑이면 datalist 입력해서 찾기, 병합 후보 부분 글자 사전(15초→1초 안).
  6) 전자세금계산서: 자재관리는 매입 쪽 → 발행 대신 🧾 홈택스 매입 대사(core/hometax.py, 증빙 탭): 홈택스 매입 목록 엑셀 ↔ 증빙(승인번호)·입고(±10일 후보) — 일치/금액 다름/증빙 없음/홈택스에 없음.
  같은 날: 오프라인 대기열 중복 방지 90일(MM_OFFLINE_KEEP_DAYS, once.OFFLINE), 전체 백업 모든 표(비밀값 열 제외)·write-only 엑셀·CSV 묶음(/data/backup-csv.zip). 테스트 tests/test_round5.py.
- 2026-10-04 사용자·전문가 점검(사용자 "사용자 및 전문가 입장에서 오류·불편함"): 에이전트 3개(현장 담당자 / 관리자·시스템관리자 / 원가·재고 회계·SAP 전문가) 결과로 수정 —
  재고 평가: 완제품 입고 = 작업지시가 실제로 쓴 부품 평가 원가(valuation Engine.wo_cost), 부품 반납은 출고 되돌림(매입 아님)·SAP 202(IN_RETURN), 발주 입고는 발주 단가(purchasing.po_line_price, 다르면 경고),
  발주 잔량 종결(SHORT_CLOSED, short_close_po), 수량 표시 지수 표기 제거(core/utils.fmt_qty, Jinja |qty |plain, Table fmt "qty") — 회사 설정 저장 불가(1e+06) 해결,
  오류 뒤 발주 품목 선택 유지·발주 고르면 단가·잔량 채움, 한 건 입력 화면 상자 바코드 단위·'24*코드' 수량, 오프라인 자재 바꾸면 단가·단위 비움, 로트·재고 부족 안내 문구(남은 로트 표시),
  거래 이력 '취소 요청 중', 발주 입고 거래처 경고 생략, 데이터 범위 없는 사용자 대시보드 안내·등록 시 경고, 결재 목록 종류 칸(거래 취소), MRP 구매요청 같은 자재 한 줄,
  홈택스 '증빙 등록' 내용 채움, 작업지시 투입·완료 확인 창, 이름 설정을 표 머리글에도(|relabel). 테스트 test_round5.py·test_review2.py 추가.
- 2026-10-04 전문가 과제 7개 구현(사용자 "추가 후 다시 확인", 리비전 0008): core/costing.py — 작업 달력(work_calendar, WEEKEND_OFF, sub_workdays: MRP·작업지시 착수일 근무일 역산),
  표준원가 롤업(standard_costs, 구매품=기준단가, 제품=부품 표준×소요량+공정 표준시간×임률·배부율, 모든 창고 관리자만), 작업지시 차이(재료 가격·수량·노무·경비·기타, BOM/공정이 산정 뒤 바뀌면 정산 금지),
  오더 정산(productions.settled_at·settlement, 정산 뒤 취소 금지), 구매 가격차이 · 노무비/경비(LABOR_RATE·OVERHEAD_RATE 회사 설정, 완료 때 productions.labor_cost/overhead_cost → 완제품 원가·재고 평가)
  · 투입 단가 = valuation.unit_cost(그 플랜트·날짜 평가 단가) · SAP: MM_SAP_PRODUCTION_MODE=order(261/262/101+productions.sap_order_no, GM 02, OData ManufacturingOrder·RFC ORDERID), payload amount·unitPrice·currency(RFC AMOUNT_LC 501·561)
  · 발주 delivery_date(MRP 공급일·대시보드 입고 예정) · 3자 대조 지급 보류(payment_status, MATCH_PRICE/QTY_TOL_PCT, 발주자 아닌 관리자 해제, 구매 → 💳 지급 대조)
  · 마감 전 점검(periods.close_checks 월말 기준 재공품·GR/IR·음수 재고·지급 보류 → period_closes.checks). 테스트 test_round6.py. 브라우저 cdp_check7.py 14/14.
  재점검 에이전트 2개(회계·SAP / 화면·권한) 결과 반영. SQLite 277+1, PG 276+2.
- 2026-10-05 사용자·전문가 점검 7차(사용자 "문제점 확인해봐 사용자 및 전문가", 리비전 0009 standard_cost_history + 완료 작업지시 원가 잠금 트리거 prod_done_locked):
  평가 — 부품 반납은 투입 단가로, 마감 전 출고 취소는 원거래 단가로, 선입선출 투입은 valuation.issue_cost(여러 층 실제 금액). 생산 — 완료 수량 110% 상한, 반납한 부품은 백플러시 안 함, 투입 칸 비움+'남은 계획 수량 채우기'.
  통제 — 지급 보류 해제는 입고·계산서 등록자도 불가, SOD_PO_RECEIPT(회사 설정, 발주자 입고 금지), 표준원가 이력·변경 감사·임률/부품 단가 변경 감지, 임률 없던 때 완료한 작업지시는 차이 비교 불가,
  rollup(update_price) 제품 기준단가를 표준원가로. SAP 원가센터 방식 완제품 입고 521. 홈택스 품목 여러 줄 합산·범위 밖 증빙 숨김, 일부 창고 권한 안전재고 미달은 그 창고에서 다룬 자재만,
  수불부 카드 수량 합 제거, 이름 설정이 카드·기본 엑셀 머리글에도, 발주 배지=지급 상태, 계산서 공급자≠거래처 경고, ERP 켜짐+SAP 번호 없는 자재 발주 금지, 거래처 병합 사업자번호 다르면 금지,
  대결 대상에서 DATA 제외, 여러 줄 입고: 단위 바꾸면 수량 환산·발주 불러온 줄에 스캔 세기(첫 스캔이 예정 수량 대체)·발주 목록에 납기. 테스트 test_round7.py(13). SQLite 290+1, PG 289+2, 커밋 eb229d7·CI 성공·Render 스키마 0009 확인. 관리자 점검의 대결 담당자 제외는 하지 않음(대결 취지 — 데이터 관리 역할만 제외).
- 2026-10-06 영업관리 비교 적용(사용자 "영업관리 확인해서 자재관리에 적용할 항목", 목록 중 비밀번호 정책은 사용자 지시로 제외, 리비전 0010·0011, 코드는 읽기만 하고 따로 구현):
  감사로그 해시 체인(core/audit.py seal/verify — 기록할 때 잠그지 않고 따로 '봉인': 업무 트랜잭션 안 기록이라 체인 잠금은 서버 여러 대에서 교착 위험. seq·prev_hash·hash 열, 트리거는 봉인 열 채우기만 허용, 배치 audit_seal 1분 + 요청 뒤 1분 + 검증 화면 '무결성 검증'),
  감사로그 request_id 열·검색, 배치 임대 갱신(jobs._Lease), API 인증 실패 IP별 429(api_auth_failures, MM_API_FAIL_MAX), ValueError·OverflowError 전역 400,
  백그라운드 작업(core/tasks.py·views/tasks.py·bg_tasks: 업로드 반영 500줄↑·거래이력/백업 엑셀 1만 행↑, 결과 파일 uploads/task… 저장소 키는 영문만 허용(KEY_RE), 서버 재시작 땐 10분 뒤 중단 표시),
  월말 미지급·GR/IR 스냅샷(ap_snapshots, 마감 때 고정·이전 마감 달은 '보충' 버튼). 테스트 tests/test_round8.py(13). SQLite 303+1, PG 301+3.
- 2026-10-06 2차(사용자 "감사 해시 서버 밖 보관, 결재 요청 회수, 데이터 범위 기한·사유" 후 검증, 리비전 0012): 감사로그 앵커(audit.anchor_text/parse_anchor/check_anchor — 관리자 > 감사로그에서 내려받기·붙여넣어 대조, 배치 audit_anchor 하루 1회 표준출력 `AUDIT_ANCHOR`, 운영 점검 '감사로그 무결성'이 35일 넘으면 경고),
  결재 요청 회수(approvals.withdraw, status WITHDRAWN, 내 요청 현황 탭 '회수'·구매요청은 cancel_pr), 임시 데이터 범위(user_scopes.valid_to·reason, org.add_temp_scope/revoke_temp_scope/temp_scopes — 기한 당일 포함·자동 만료, 기본 범위 저장은 임시 범위 유지, 전체 창고로 바꾸면 정리, 최대 MM_SCOPE_TEMP_MAX_DAYS=365).
  검증: 점검 에이전트 결함 4건 수정 — 거래이력 백그라운드 추출 audit import 누락, 작업 화면 역할(VIEWER·DATA 포함), mark_stale 읽기 먼저·이 서버 작업 제외, cancel_pr 잠금+조건부 UPDATE. 테스트 tests/test_round9.py(14). SQLite 317+1, PG 315+3.
  자동 승인이 프로세스 종료(pytest 중단)를 거부함 — 테스트를 돌리는 중에는 끝나길 기다린 뒤 수정할 것. 영업관리에 더 있는 것: 비밀번호 정책(사용자가 제외 지시)·운영 점검 항목(DB 구조·SQLite 설정·디스크·메일·메신저·점검 모드, 미적용).
- 2026-10-07 회사 엑셀 양식 기억(사용자 "회사 엑셀파일을 업로드하면 나중에 다운로드 받을 때 회사 양식으로, 영업관리도 동시에"): 자재관리 core/excel_forms.py learn/adopt/stage_learn/adopt_staged — 업로드 화면(자재·거래처·BOM·단위)의 '이 엑셀의 양식을 기억'(관리자만, 반영 때 저장, 임시 보관 uploads/<token>-form.*) 또는 관리자 > 엑셀 양식 > '회사 엑셀로 자동 설정'.
  머리글 행 자동 추정·열 이름 연결(동의어·올리기 별칭)·열 위치(col) 보존·데이터 행 비우기·없던 항목 오른쪽 덧붙임(export 가 col·new 지원). 시연 서버는 잠금. 테스트 tests/test_round10.py(8). 영업관리는 sales 세션이 같은 시각에 구현(remember_upload·/import/remember, 21:23 이후) — 이 세션은 sales_manager 를 건드리지 않음.
## 미확정 사항
- 이름·한 줄 소개: 사용자가 비워두기로 함(성명 칸은 빈칸 표시). 제목 문구 "현장의 업무 규칙을 코드의 규칙으로 옮깁니다"는 Claude가 쓴 문안.
- 연락처는 사용자 선택으로 넣지 않음.
- 요약전표의 결재란(설계·개발·검증)은 가정값 — 사용자 확인 필요.
- 페이지의 수치(메뉴 19, 코드 13,521줄+테스트 2,633줄, 테스트 89/89, 영업기회 조회 건수 7/33/55·매출 17/94/160 — 2026-10-02 재측정)는 코드·README 기준. 프로젝트를 고치면 함께 갱신할 것.

## 영어 시험 학습 `study/TS/` (2026-09-27 시작, 개인 공부용 — 배포 안 함, 문제 저작권 무관)
- 2026-10-03 폴더 이동 `TS/` → `study/TS/`. TS 브랜치도 경로를 `study/TS/`로 바꿔 커밋(963ad3c, 내용 변경 없음), 커밋 도우미는 `study/TS`=TS(옛 `TS/`는 별칭).
  Render ax-vs-code-3(srv-dasgfnojo6nc73bm5nfg) rootDir = `study/TS` 로 변경 후 푸시, 배포 live 확인(2026-10-03).
- **git: TS 작업은 `TS` 브랜치에만 커밋·푸시** (2026-10-02 사용자 결정, Render ax-vs-code-3 배포 브랜치 = TS). 공용 폴더(브랜치 workspace)에서 브랜치를 바꾸지 말고 TS 파일만 `git add` 후 `python .git/hooks/project_branches.py commit -m ...`.
- 메인 `/` = 네 시험 카드(지금 위치·목표·문제 수·바로가기), 토익 '오늘'은 `/toeic`, 위쪽 시험 탭 항상 표시, 로고 static/favicon.svg.
- 범위: 토익·토플·토익스피킹·오픽. 메뉴는 ☰ 사이드바(평소 숨김, `views/nav.py` MENUS): 카테고리 토익 → 토플 → 토익스피킹 → 오픽, 카테고리 이름 = 그 시험 첫 화면, ▾ = 탭 펼치기(사용자 지시). `/exam/<key>` 는 각 시험 홈으로 이동(`core/exams.py`).
- 말하기 `/speaking/toeic`·`/speaking/opic`(2026-10-02, 사용자 요청 "스피킹 목록들 제작"): `core/speaking.py`(과제·주제 정의, 문제 → '단계' 목록, `speaking_attempts`·`speaking_mocks`, 점수 추정), `views/speaking.py`, 공용 엔진 `static/js/speaking.js`(준비·답변 타이머, MediaRecorder 녹음 + Web Speech 인식, 자기 채점 — 녹음 파일은 서버에 저장 안 함), 문제 `content/speaking/{toeic,opic}/*.json`(형식 SCHEMA.md, 검사 `tools/validate_speaking.py`).
  토익스피킹: 2022.6 개편 11문항, Q1~10 0~3·Q11 0~5 합 35 → 200 비례 환산(추정). 사진 묘사는 장면 설명. 오픽: 설문(`opic_survey`·`opic_level`·`opic_target` 설정), 모의고사 15문항(자기소개·설문 콤보 2·돌발 콤보·롤플레이·비교/이슈 2), 등급 = 자기 채점 평균 + 평균 단어 수(IM1~3). 문제 수(2026-10-02): 토익스피킹 590(읽기 140·사진 140·질문 100세트·표 90세트·의견 120), 오픽 698(설문 21·돌발 19 주제, 롤플레이 65세트). 약한 문항 다시(`weak=1`), 채점 화면 모범 답안 듣기·섀도잉. 테스트 `tests/test_speaking.py`(표본 은행). 브라우저 확인은 scratchpad cdp_speaking.py(가짜 마이크, 모의고사는 시간 20배속).
- 토플 `/toefl`(2026-09-27): 2026년 1월 개편 형식 11개 과제, 밴드 1~6. `core/toefl.py`(과제 정의·밴드 추정·`toefl_attempts` 테이블), `views/toefl.py`(API `/toefl/api/attempt`), `static/js/toefl.js`, 문제 `content/toefl/*.json`(형식 SCHEMA.md, 검사 `tools/validate_toefl.py`). 말하기는 Web Speech 인식 + MediaRecorder, 쓰기·인터뷰는 자기 평가.
- 토플 모의고사 `/toefl/mock`: R→L→S→W, 읽기·듣기 2단계 적응형(`core/toefl.build_mock`, 기준 60%), `static/js/toefl-mock.js`, 결과 `toefl_mocks`. 토플 어휘 `/toefl/vocab`: vocab 블루프린트를 url_prefix="/toefl", name="tvocab" 로 재등록(템플릿은 `url_for('.x')`, JS는 `TS.vbase`), 은행은 `ToeflBank.vocab`, 등급 표시는 `VOCAB_GRADES`(밴드 2~6). 등급 = 토익 인증 색상(Orange 10~215 / Brown ~465 / Green ~725 / Blue ~855 / Gold 860~).
- 실행: `python app.py` → http://127.0.0.1:5003. PC 서버를 다시 켤 때는 5003 을 듣는 TS 프로세스를 **모두** 끈다 (Windows 는 같은 포트에 여러 개가 붙고 Get-NetTCPConnection 이 하나만 보여 줄 때가 있음 — 2026-10-03 옛 서버가 남아 새 화면이 404, psutil 로 cwd=study/TS·포트 5003 인 것 전부 종료). 테스트 `python -m pytest tests -q` (175개, 2026-10-07). 문제 검사 `python tools/validate_content.py` · `validate_toefl.py` · `validate_speaking.py`.
- 2026-10-07 사용자·전문가·학원강사 점검(에이전트 4갈래 + 정답 가린 재풀이 638문항, 불일치는 Part 6 5건만 → 수정 뒤 재확인): 토플 반올림 `toefl.half_up`(5.25→5.5)·영역 순서 R→L→W→S·0~120 대응표(ETS), 실전 모의고사 Part 7 단일 29문항 고정(200문항), `db.connect(write=True)`(BEGIN IMMEDIATE)로 겹친 제출 중복 저장 방지 + 토플·말하기 finish 잠금, 빈 답안(-1)은 오답노트·통계 제외·절반 미만이면 점수 추정 안 함, 오픽 미응답=1점, 첫 학습 '다시'도 lapses+1(시작 때 옛 카드 보정), 새 단어는 현재 등급부터(`srs.queue start_level`), 오답노트 졸업은 서로 다른 날 2번, 이어서 풀기 카드(`stats.unfinished_sessions`), sessions.requested, 토플 모의고사 영역 단위 저장(localStorage `ts-tmock-<id>`), 문장 만들기 `alts`, 한국어 404, 진단 듣기 28문항, 풀이 화면 메뉴 강조(세션 종류별), 500 → 400/404(`JsonDictRequest`·`SafeIntConverter`), 서버 TZ=Asia/Seoul, 음성 파일 동시 2개.
  콘텐츠: '가장 긴 보기=정답' 비율 Part 2 68→35%·토플 응답 81→35%·듣기 75→26%·Part 3/4/6/7 40~70→27~38%(오답을 늘림), 토익스피킹 고득점 답안을 말할 수 있는 길이로(단어 ≤ 초×2.3, SCHEMA·validate_speaking 갱신), Part 6 문장 삽입 오답을 '연결만 어긋남' 스타일로, 중복 문항 삭제(op-027, p1-051). 패치를 bash heredoc 으로 넘기면 `\0` 같은 제어 문자가 파일에 들어간다 — 적용 뒤 NUL 검사.
- 구조: `core/`(db·content·scoring·study·srs·planner·stats·guide) · `views/`(main·quiz·vocab) · `static/js/quiz.js`(풀이 엔진) · `content/toeic/*.json`(형식은 content/SCHEMA.md).
- 문제 은행(모두 새로 작성, `partN.json`+`partN_2/_3.json`… 합쳐 읽음, 파일 변경 시 자동 반영): P1 180, P2 550, P3 140세트, P4 125세트, P5 800, P6 130세트, P7 170세트(합 3,555문항, 2026-10-02 `_4`·`part7_5` 추가), 단어 1,800(vocab·vocab_2·vocab_3 + 도전 vocab_s1~s5). 단어는 tier: core(필수)/stretch(도전). 2026-09-27 오타 전수 검사(문제 영어 드문 단어 3,420개 + 단어 1,600개) 완료.
- 실전 모의고사(`full`, real=True): 실제 구성 200문항, LC 음성 흐름대로 자동 진행(P1·2 5초, P3·4 문항 읽기 후 8초/표 12초, 되돌아가기 없음), RC 75분, 원점수 환산(`scoring.estimate_raw`), 시험 중 등급 숨김, 답안 localStorage 백업, 전에 푼 문제 수(`sessions.seen_before`) 경고.
- 단어 듣기(운전 모드) `/vocab/listen`: 영어 N번(미국→영국→호주)→한국어 뜻 반복 재생(Web Speech). 1시간 MP3는 `core/audio.py`: edge-tts 조각(`data/audio/clips/`) + lameenc 무음을 이어 붙임, 백그라운드 작업(`/api/vocab/audio/prepare`·`status`), 리눅스(Render)에서도 동작. 1시간 약 21MB·반복 3번 약 395단어.
- 단어 시험 `/vocab/quiz`: 범위(전체·자주 잊는=틀린 2번 이상·최근 틀린·별표·학습 중) × 등급 × 필수/도전, 15/30/50문제. 답은 `vocab_quiz_log`에 기록, 오답은 `srs.review(id, 0)`로 복습 카드(내일). 다시 풀기 회차는 기록 안 함.
- 듣기 = 브라우저 음성 합성(Web Speech). Part 1 사진은 한국어 장면 설명. 추정 점수는 난이도 가중 정답률 → 구간 보간(실제 점수와 다를 수 있음).
- CSP: script-src 'self'(인라인 스크립트 금지, 데이터는 `<script type="application/json">`), style은 인라인 허용. Jinja에서 `.75` 대신 `0.75`.
- `data/ts.db`는 사용자 학습 기록. 확인용 서버는 `TS_PORT=5913` 등 다른 포트 + `TS_DB_PATH`로 임시 DB 사용.

## 폴더 정리 (2026-10-02)
- 삭제함: `__pycache__`·`.pytest_cache`(자동 생성), `fonts_1/`, `sales_streamlit_backup/`, `material_manager_streamlit_backup/` (뒤의 셋은 git 기록에 있음, 삭제는 아직 미커밋).
- **`forwardus_back/`, `forwardus_back9-21/`(171MB)는 사용자 지시로 그대로 유지** — git 에 없는 다른 프로젝트 백업이므로 지우거나 옮기지 말 것.

## git 브랜치 규칙 (2026-10-02 사용자 지시 "각각의 브랜치에만 커밋")
- 프로젝트별 전용 브랜치 = 폴더 이름 (2026-10-03 사용자 지시로 sales→sales_manager, material-manager→material_manager, jobfit→job 로 이름 변경, 옛 GitHub 브랜치 삭제): `job/`→`job`, `study/TS/`→`TS`, `sales_manager/`→`sales_manager`, `material_manager/`→`material_manager`, `study/trad_study/`→`trad_study`(2026-10-03 추가, 도우미 project_of 가 하위 폴더 경로도 인식). 한 커밋에 프로젝트를 섞지 않음.
- 공용 폴더(AX_VS_CODE)의 브랜치는 `workspace`(여러 프로젝트가 섞인 작업용, 예전 이름 job-fit). 여기서 브랜치를 바꾸면 다른 세션 파일이 바뀌므로 바꾸지 말 것.
- 커밋: 한 프로젝트 변경만 `git add` → `python .git/hooks/project_branches.py commit -m "메시지"` (폴더 브랜치를 안 바꾸고 그 프로젝트 브랜치에 커밋). `.git/hooks/pre-commit` 이 다른 브랜치로 가는 프로젝트 커밋을 거부(ALLOW_ANY_BRANCH=1 로 해제). 훅은 버전 관리 안 됨.
- 업로드도 각 브랜치에만 (2026-10-02 사용자 "각각의 브랜치에만 업로드"): `git push origin <브랜치>` 로 같은 이름 GitHub 브랜치에. `.git/hooks/pre-push` 가 workspace 업로드와, 프로젝트 브랜치에 다른 프로젝트 파일이 섞인 커밋 업로드를 거부. job·TS·sales_manager·material_manager·trad_study 모두 GitHub 에 올라가 있음. main 합치기는 사용자 확인 후.

## 채용공고 적합성 `job/` (2026-09-30 시작, 개인 구직용)
- 실행: `python app.py` → http://127.0.0.1:5004 (5000~5003과 분리). 테스트 `python -m pytest tests -q` (113개, 네트워크 없이 fixture·가짜 사이트). 상세는 `job/README.md`.
- 구조: `core/`(db·normalize·salary·fit·postings·applications·profile·collect) · `core/sources/`(saramin·work24·wanted·linkimport·fileimport) · `views/`(main·jobs·collect·profile·applications).
- 사이트: 사람인(공식 API, `JOB_SARAMIN_KEY`)·고용24(`JOB_WORK24_KEY`)·원티드(비공식 JSON, `JOB_WANTED_ENABLED=1`) 자동 수집. 잡코리아·링커리어·자소설닷컴·잡플래닛·리멤버(+사람인·원티드·고용24)는 **공고 링크 붙여넣기**(JSON-LD JobPosting → og:description 요약 → og:title, 8개 도메인·공고 주소 모양만 허용, 첫 화면 주소 거부) 또는 CSV/엑셀. 목록 대량 크롤링은 하지 않음(사용자 요청 2026-09-30에 맞춰 이 방식으로 추가).
- 2026-09-30 정기 크롤링(사용자 요청 "4시간 간격", "리멤버 13,829건 전체"): `core/crawler.py`·`core/scheduler.py`·`crawl.py`. 사람인·잡코리아·링커리어 검색 결과 → 새 공고만 상세, 리멤버는 sitemap-jobs.xml 비교(sitemap_ids 테이블, 새 번호만 상세, 빠지면 마감, 최근부터 회당 500건), 저장 공고 하루 1회 갱신. robots.txt·3초 간격·403/429 중단·DB 잠금(settings.crawl_lock). 전체 재수집을 매번 하지 않는 이유는 13,829×3초 ≈ 11.5시간 > 4시간. 실제 1회 실행 확인(22요청·71초·14건). 공고 source_id = 사이트 공고번호(rec_idx 등)라 API·크롤링·링크가 합쳐짐.
- 2026-10-02 사용자 실제 DB(`job/data/job.db`)에 자동 수집 켬: 검색어 없음 = "모든 직무"(사용자 선택) → 사람인은 `jobs/list/domestic`(검색어 없는 검색은 페이지가 안 넘어감), 잡코리아 검색 Ord=RegDtDesc, 링커리어 목록, 목록 5페이지·사이트당 새 공고 100·리멤버 500/회·4시간. 앱은 miniforge python으로 숨김 실행(Start-Process, 로그 `job/data/app.log`·`app.err.log`), PC 재시작 시 다시 켜야 함. 앱을 강제 종료하면 crawl_lock 이 남으므로 settings.crawl_lock 을 '' 로 지운 뒤 재시작.
- 2026-10-02 "모든 직무를 각각 조사해서 추가"(사용자): 검색어 없으면 직무별 순회(`by_category`, `LIST_SITES[*].categories`). 사람인 `jobs/list/job-category?cat_mcls=2~22`(21개, 페이지 넘어감), 잡코리아 `recruit/joblist?menucode=duty&dutyCtgr=10026~10046`(21개, 페이지가 거의 안 바뀌어 1페이지·약 170건; Search 의 duty 파라미터는 무시됨), 링커리어 `filterBy_categoryIDs=100001~100014`(14개, 20건/페이지). `pick_round_robin` 으로 직무마다 돌아가며 새 공고 선택, 실제 DB 는 목록 5페이지·사이트당 210건/회. 3개 이상 직무 목록에 나오는 광고(TOP100)는 직무 비움. 공고 목록에 직무 필터.
- 2026-10-02 사용자 요청 "직무 선택 시 상세조건 선택, 마감된 건 저장해서 다시 보기, 저장 안 한 공고는 삭제": `core/jobgroups.py`(사이트 직무명 → 19개 통합 직무, 세부 직무는 제목·키워드 단어, 못 맞추면 '기타·미분류'), 공고 화면 직무 선택 → 세부 직무 체크(건수 표시), `postings.saved` 열(기존 DB는 db._migrate 로 추가), ★저장 버튼·저장한 공고 탭, `postings.purge_closed()` = 마감 지남 & 저장 안 함 & 지원 기록 없음 → 삭제 (크롤링 끝·스케줄러 1시간마다).
- 2026-10-02 "모두 실행": 내 조건 = 사용자 답("전국 모두", "모든 경력과 신입", "3000~8000만원 500만원 단위", "모든 직무") → 실제 DB 프로필은 지역 없음·career_type '모두'·연봉 0·직무 없음·학력 무관·고용형태 무관으로 저장(학력·고용형태 무관은 Claude 판단). 연봉 500만원 단계 선택(내 조건·공고 필터)·연봉 구간 통계, 희망 직무(job_groups/job_subs, fit 직무·기술 점수 절반), 새로 들어온 맞는 공고(settings.seen_at, NEW 표시), 로그인 자동 시작(`job/start_app.ps1`, 작업 스케줄러 'JobFit 앱 자동 시작', 스크립트는 PS 5.1 때문에 UTF-8 BOM).
- 2026-10-02 제외 항목(사용자 "헤드헌팅, 수리기자(→수리기사로 해석)"): `core/exclude.py` 프리셋+동의어, 제목·회사·키워드·직무·flags만 비교(띄어쓰기 무시), 걸리면 목록에서 숨김(show_excluded). 헤드헌팅은 `post_flags` 테이블 — 크롤링 때 사람인 jobs/list/headhunting(10쪽)·잡코리아 /Headhunting/·joblist?menucode=headhunting 을 읽어 표시, 제외면 상세도 안 읽음. 실제 DB exclude = 헤드헌팅·수리기사. 로고 `job/static/favicon.svg`(서류가방+체크, #2f5bd3), /favicon.ico 도 같은 파일. 크롤링 연결 끊김 재시도(10·30초, 3연속 실패 시 그 사이트 중단, BlockedError).
- 2026-10-03 수집 일정 개편(사용자 승인 1~5): `crawl_queue`(못 읽은 공고 번호, 새 묶음이 앞), 목록 전체는 `list_every_hours`(4)마다, 갱신은 저장·지원(하루)·상시(주)만, 60일 지난 미저장 상시 공고 삭제(`purge_stale`), 점수·지원가능·제외·직무를 `fit_score/fit_ok/fit_excl/grp/subgrp` 열에 미리 계산(저장·내 조건 변경·실행 끝·앱 시작 시) → `postings.query` 는 DB 에서 거르고 한 쪽만 상세 계산. 5만 건 기준 목록 0.8초·통계 0.5초. 수집 화면에 사이트별 밀린 공고·하루 새 공고(실측, `list_seen`)·예상 완료. 제외 항목에 '계약직'(계약직·기간제로만 뽑는 공고, 정규직 병행이면 제외 안 함).
- 2026-10-03 Render 배포 (사용자: "내 vs코드에 저장", 로그인 필요 없음): 서비스 `job`(srv-davse2dg1s2s73bnaerg) https://job-916s.onrender.com — 브랜치 `job`(2026-10-03 jobfit 에서 이름 변경), rootDir `job/`, free·singapore, 자동 배포, start `gunicorn wsgi:app`, health `/healthz`. **원본은 내 PC** — Render 는 사본(JOB_MIRROR=1, JOB_SCHEDULER=0, 수집·등록 POST 막음). 내 PC 앱이 한 시간마다·수집 끝날 때 `core/sync.py` push: 사본의 변경(저장·숨김·지원 기록·회사 평균연봉·내 조건·확인함)을 받아 반영 → 원본 DB gzip 업로드(`/api/sync/upload`, Bearer JOB_SYNC_TOKEN). 내 PC 설정 `job/data/sync.json`(url·token, 깃 제외). free 라 배포·잠듦 뒤 사본 DB 가 비므로 배포 후 `sync.push()` 한 번. 로그인 기능은 사용자 요청으로 제거(사본이 공개 — 누구나 보고 바꾼 것이 원본으로 옴). Render API 키는 Windows 사용자 환경변수 RENDER_API_KEY(채팅에 노출돼 재발급 권장함).
- 2026-10-03 수집은 사람인·잡코리아만(사용자 요청, 링커리어·리멤버 끔). 제외 항목에 배송·운전(운전 직무만: 운전기사·수행기사 등, 시운전·설비운전은 아님)·식당·주방 추가.
- 커밋 도우미(.git/hooks/project_branches.py)는 프로젝트 브랜치에만 있던 파일을 폴더에서 지우면 브랜치에서도 지움(2026-10-03 수정). job 브랜치와 폴더 비교는 `git diff` 대신 파일 해시로 (공용 브랜치에 없는 파일은 diff 에 '삭제'로 보임).
- 고용24 상세는 infoTypeCd 등이 붙은 원래 주소여야 내용이 나옴 — `_work24_fields`(라벨→값). 링커리어 JSON-LD는 연봉을 MONTH로 잘못 적어 둬 1,500만원/월 이상이면 연봉으로 봄.
- 적합성 100점 = 지역·연봉·경력·직무키워드 각 25, 학력 −15·고용형태 −10, 지원 불가 사유 별도. 연봉은 연 만원 환산(월×12, 시급×209×12).
- 자동 지원 제출 없음 — 원문 링크 + 지원 상태·일정·메모 관리. CSP script-src 'self'(인라인 스크립트 금지, `static/js/app.js`의 data-autosubmit·data-confirm).
- 2026-09-30 실제 공고 페이지 확인: 잡코리아·리멤버는 JSON-LD 있음, 사람인은 og:description 요약만(근무지 없음), 링커리어는 제목·설명만. 자소설닷컴·잡플래닛·고용24 공고 페이지와 API 키 호출은 미검증. 확인용 서버는 `JOB_PORT=5914` + `JOB_DB_PATH`로 임시 DB 사용.

## 국제무역사 1급 학습 `study/trad_study/` (2026-10-03 GitHub 브랜치 `trad_study`에 올림)
- Flask, 제59~65회 840문항. 테스트 `python -m pytest tests -q` (37개). 학습 기록 복원(POST /api/restore, 사이드바 버튼 — 백업 파일로 통째 교체, 2026-10-03). 기본 포트 5090(2026-10-03 변경, `--port`·TRADE_PORT).
- 2026-10-03 Render 배포: 서비스 `trad-study`(srv-db08t0dg1s2s73d2d430) https://trad-study.onrender.com — 브랜치 trad_study, rootDir study/trad_study, free·singapore, 자동 배포,
  start `gunicorn --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:$PORT wsgi:app`(워커 1개 필수 — API 키 보관함이 프로세스 메모리), health /healthz.
  env TRADE_PROXY=1(ProxyFix·보안 쿠키, 없으면 https Origin 검사로 모든 저장 요청 403), TRADE_SECRET_KEY(생성값), PYTHON_VERSION 3.12.7. 로그인 없음·학습 기록은 재시작 시 사라짐.
  배포 공간 마련으로 사용자 확인 후 일시중지 서비스 16개 삭제(AX_VS_CODE, AX_VS_CODE-2, project_0916-1~13, flask).
- 공개 저장소에 기출 PDF·catalog.sqlite3까지 올림(사용자 선택 "전부 올리기"). 학습 기록 instance/ 는 git 제외.

## 엑셀 연습장 `study/EX/` (2026-10-03 시작, 개인 학습용 — 브랜치 `EX`, GitHub·Render 배포됨)
- Flask, 실행 `python app.py` → http://127.0.0.1:5005 (EX_PORT·EX_DATA_DIR·EX_DB_PATH). 테스트 `python -m pytest tests -q`, 문제 검사 `python tools/validate_content.py`. 상세 `study/EX/README.md`.
- 네 부분: 함수·기능 학습(실무·컴활 2급·1급, 문제 239·함수 사전 135) / 파일 → 대시보드 / 대시보드 만들기 실습(매출·인사·재고, 피벗은 필드까지 채점) / 컴활 실기 모의고사(사용자 요청 "컴활 실제문제" → 기출은 비공개·저작권 문제로 실제 구성·배점·지시문 형식의 새 문제로 만듦).
- 수식 채점은 자체 계산기 `core/formula.py`(엑셀 15자리, OFFSET·INDIRECT·이름 정의·D함수 계산 조건·SUBTOTAL·한글 사용자 정의 함수 이름 지원, 문제의 TODAY 는 2026-10-01 고정).
- 모의고사: `content/exams/*.json`(형식 SCHEMA.md), 채점 `core/exam.py`(+pivots.py·vba.py), 검증 `python tools/exam_answer.py <id>` = 실제 Excel(COM, pywin32 — 2026-10-03 이 PC 에 설치)로 정답 파일을 만들어 채점.
  매크로·VBA 는 Excel '보안 센터 > VBA 프로젝트 개체 모델에 안전하게 액세스'가 꺼져 있어(레지스트리 AccessVBOM 없음) 자동 검증 못 함 — 설정은 사용자 허락 없이 바꾸지 말 것.
  테스트 고정 자료 tests/fixtures/*_excel.xlsx 는 Excel 로 만든 파일.
- 2026-10-03 EX 브랜치 GitHub 업로드(사용자 지시). 컴활 실기 실습(/practice, core/compare.py·official.py·library.py): 실습↔정답 파일 비교 채점.
  대한상공회의소 공식 예제(2024~2026 1·2급 엑셀 A·B형, 2015 예제)는 앱 버튼으로 license.korcham.net 에서 받아 data/official 에만(기출은 비공개).
  사용자 교재 자료 ~/Downloads/MYBOX(에듀윌 2025 컴활 2급 실기 그대로 따라하기·기출예제/변형 15회, 초단기 2급 부록, 함수기초 마스터 — 65쌍)를 data/library 로 가져옴.
  출판사·대한상공회의소 파일은 저작물 — 저장소에 올리지 말 것. 사용자 "에듀윌 교재 크롤링해서 문제 만들어" → 크롤링·교재 지문 복제는 하지 않고,
  core/describe.py 가 실습↔정답 차이로 시험 말투 지문을 자동 생성(data/library/<id>/problem.json, 65쌍 중 63쌍·798문장). 채점기 점검: 정답→100·실습→0 (65쌍·공식 4세트 모두 확인).
- 수식 입력창: 함수 자동 완성(= 없이 av 도)·인수 도움말, 셀 클릭·드래그 주소 넣기, F4. 정적 파일은 `asset()` 으로 ?v=수정시각(캐시 무효화).
- PC 서버(5005)는 숨김 프로세스로 실행 중일 수 있음 — 코드 바꾸면 5005 프로세스를 끄고 `Start-Process python app.py`(작업 폴더 study/EX, 로그 data/app.log)로 다시 켤 것(디버그 모드 아니라 자동 재시작 안 됨).
- 커밋: `git add study/EX` → `python .git/hooks/project_branches.py commit -m ...` (도우미 PROJECTS 에 "study/EX": "EX" 추가함, 2026-10-03).
- 2026-10-03 Render 배포(사용자 요청): 서비스 `excel`(srv-db0dekid0e5s73b55htg) https://excel-y91o.onrender.com — 브랜치 EX, rootDir study/EX, free·singapore, 자동 배포,
  start `gunicorn --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT wsgi:app`, health /healthz. env EX_PUBLIC=1(공식 예제 받기·교재 가져오기 끔 — 저작물 공개 방지),
  EX_PROXY=1, EX_SECRET_KEY(생성값), PYTHON_VERSION 3.12.7, TZ=Asia/Seoul, EX_VAULT_KEY·EX_OWNER_TOKEN(값은 PC data/vault.key·data/owner.token).
  개인 자료(교재 65쌍·공식 예제)는 사용자 요청(다른 PC·모바일에서 보기)으로 content/private/vault.bin 에 암호화해 저장소에 두고 서버 시작 때 복원, 개인 링크 /me/<owner.token> 를 연 기기에서만 보임(공개 방문자는 못 봄).
  자료를 더하면 PC 에서 `python tools/vault_pack.py` 후 커밋·푸시. 무료라 재시작 시 학습 기록·올린 파일 초기화. Render API 키는 채팅에 노출됨 — 재발급 권장(파일에 저장 안 함).
- 2026-10-04 밤샘 점검(사용자 "5시간 혼자 수정·오류 확인"): 계산기 Excel 대조(140문제 2,336칸 중 불일치 1 = 이 PC Excel 2019 의 동적 배열 한계) + 함수 30개 추가,
  웹 보안 9건(압축 폭탄·동시 등록·</script>·토큰·JSON·깊은 수식·CSV), 학습 기록 백업 검증, 채점 검토 18건 반영(테마 색·회계/쉼표 서식 같게·용지 방향 기본=세로·
  조건부 서식 조건/유효성 두 번째 값/시나리오 값 비교·연결된 차트 제목·시험의 계산작업은 수식만·서식 항목은 (속성, 정답 값)별·매크로는 2급 매크로작업 시트·이름 정의는 가리키는 시트·
  VBA 는 문장 일치율+핵심 값(다룬 셀·문자열·색)·부분합 그룹 값·배점 반올림 합=100·차트 시트 건너뜀·차이 없는 짝은 BadFile). 테스트 180.
  할 일·지문 캐시는 views/practice.CACHE_VER(tasks.v2.json·problem.v2.json) — 채점·지문 규칙을 바꾸면 올릴 것.
- 2026-10-04 사용자 승인으로 Excel VBA 접근 켬(HKCU\Software\Microsoft\Office\16.0\Excel\Security AccessVBOM=1). 모의고사 매크로·사용자 정의 함수·VBA 문제에 모범 답안 `vba` 단계
  (코드·양식 단추·ActiveX 명령 단추·실행·Activate) → tools/exam_answer.py 4회분 모두 100/100(매크로·VBA 포함). 고정 자료 tests/fixtures/c1-01_answer_excel.xlsm.
  같은 때 문제 오류 수정: 1급 1회 계산작업 [표1] 한 행 어긋남(자료 3~14행 ↔ 문제 G4:G15), 1급 2회 4-3 Activate 날짜를 표 머리글 F3 → I1.
  실습 VBA: 수식 글자가 달라도(+ ↔ SUM, R1C1/A1) 매크로가 다루는 칸에서 결과가 같으면 맞음(compare._same_result_formulas).
- 2026-10-04 학습 기록 PC ↔ 배포 서버 맞추기(사용자 선택 "내 PC로 보내기"): core/sync.py, 서버 POST /api/sync(Bearer = EX_OWNER_TOKEN, 공개 서버만, gzip),
  PC 앱(`python app.py`)이 data/sync.json {"url"} + data/owner.token 이 있으면 켠 뒤 20초·30분마다 PC 기록('') ↔ 서버 'owner'(개인 링크 기기) 합침(같은 행은 안 늘림, 별표는 stars_at 나중 쪽). 첫 화면 '기록 백업'에 마지막 시각.
  Render API 키: Windows 환경변수 RENDER_API_KEY 는 옛 키(401). 사용자가 채팅에 다시 준 키는 동작(파일에 저장 안 함).
- 2026-10-04 사용자 요청 "사용자 및 전문가 입장에서 오류·불편함 찾아내": 점검 에이전트 3개(학습자 화면 CDP / 컴활 전문가 내용 / 엑셀 전문가 COM 대조) 결과 반영.
  계산기: 중간 값은 15자리로 자르지 않음(_exact, INT(10/3*3)=10), 비교·최종 값·ROUND 만 15자리, 끝 괄호 자동으로 닫음, 전각 영문 NFKC(따옴표 밖), 인수 개수 오류는 ArgError(#VALUE!)→ 안내,
  CHOOSE({1,2},…)·CONCATENATE 배열, 왼쪽 오류 우선, LARGE k 올림, ROUND 자릿수 0 쪽 버림, (-8)^(1/3), 함수 추가(AGGREGATE·CLEAN·T·MAXA·MINA·GCD·LCM·COMBIN·PERMUT·RADIANS·DEGREES·PERCENTILE/QUARTILE.EXC).
  채점(content.check): 모르는 함수·#NAME?·인수 오류·순환 참조·6초 넘는 계산은 기록하지 않는 error, '배열 수식으로' 문제는 {=…} 필요(_needs_array), 근사 VLOOKUP 오답, 문자/숫자·대소문자 안내,
  require 함수는 문제 글에 "(X 함수 사용)" 으로 표시(41문제), require_any·forbid 지원. 시험: 테마 색=그 파일 테마 RGB(same_color), 표시 형식은 실제 칸 값으로(_same_fmt), 다른 파일은 BadFile, 피벗 value_numfmt.
  화면: 자동 완성 Enter=고르기·한글 조합 중 무시·F4 범위, 시험 시간은 결과 화면(done=1)에서만 지움, 넓은 화면 문제/시트 좌우 배치, 휴대폰 안내, 사전 keywords(한국어 검색), 테마 깜빡임(theme.js).
  내용: 1급 1회 [표1] 행 어긋남·1급 2회 F3 날짜, logic-010(=text-004 중복) → logic-016 출생 연도, 사전 DATE·TIME·TRIM 팁, Excel 2021 배열 설명. 테스트 219. (뒤에 모두 처리)
- 2026-10-04 사용자 "모두 적용하고 다시 확인 후 필기 시험 내용 추가": 보기 99문제 다시 씀(가장 긴 보기=정답 46→7, 정답 위치 25/25/25/24), 새 연습 문제 37(math_2·lookup_2·array_2·date_2·logic_2·basic_2.json:
  재무 PMT·FV·PV·NPV·NPER·RATE, OFFSET, INDIRECT, FREQUENCY, 시간, ISERROR·ISNUMBER·IFNA 등 — 실제 Excel 대조 0 불일치, 함수 사전 147, ISNUMBER 등은 오류 인수에 FALSE),
  1급 4-3 을 실제 시험처럼 사용자 정의 폼(<수강신청>·<대여등록>, tools/exam_problem_files.py → content/exams/files/c1-0N.xlsm, 내려받기 .xlsm),
  1급 2회 3-1 은 외부 데이터 csv(<렌터카대여.csv>, /exam/<id>/data/<이름>) → 데이터 모델 피벗(pivots.clean_name·external). 정답 도구는 외부 데이터 피벗을 먼저 만든다(UDF 수식 뒤면 Excel 멈춤).
  모의고사 4회 모두 Excel 로 만든 정답 100점(폼·외부 피벗 포함), 고정 자료 tests/fixtures/c1-01·c1-02_answer_excel.xlsm.
  컴활 필기 /written(core/written.py, views/written.py, content/written/*.json 550문항: 컴퓨터 200·스프레드시트 200·DB 150, 에이전트가 작성·검사, 표본 확인), 대시보드 카드, 메뉴 '필기'. 테스트 226.
- 2026-10-05 사용자 "문제점 확인해봐 사용자 및 전문가": 점검 5갈래(학습자 CDP·필기 3과목 전수·실기 전문가). 필기 550문항 정답 오류 0 — 해설·문구·급 조정(content/written, 도구 scratchpad wfix.py 식).
  필기 모의고사: 문제지·답·시간을 localStorage(ex-written-mock:<급>)에 저장해 새로 고쳐도 이어 풀기(?q=, written.valid_paper), [새 문제지](?new=1), 시간 끝나면 자동 제출(data-timer-autosubmit), 떠날 때 경고,
  안 푼 문제도 오답 기록, 주제 비율대로 뽑아 섞기, 제출값 검사(중복·다른 급·음수 시간), 연습 1~4 키, 표 문제(| 줄) 표로(wtext 필터). 시험 막대 top = 머리글 높이(--top-h).
  1급: 문제지 번호 겹침(views/exam._items), 매크로 '차단 해제' 안내, values 검사는 숫자 칸에 텍스트면 틀림, VBA 패턴 완화(With …Font, Format$), 소수 자릿수 0 문구, csv 가져오기 경로 안내.
  계산기: TEXT h…m 분, OFFSET 음수 높이, SIGN·N 배열, COUNTIF 범위 조건 배열, A1:INDEX() 안내, 범위 한 번에 넣는 배열 수식(FREQUENCY) 채점, ROW()/ROWS($H$2:H2) 순환 참조 아님. 테스트 230.
- 2026-10-06 사용자 "문제점 확인해봐 사용자 및 전문가"(2차): 점검 4갈래(학습자 전체 흐름·함수 사전 147·2급/실습 채점 변형 145+69·보안). 함수 사전 사실 오류 3 + 설명 15 수정, 15개 추가(MAXA·MINA·RAND·RANDBETWEEN·ADDRESS·MMULT·FACT·DAYS360·GEOMEAN·HARMEAN·ISNONTEXT·TYPE·CLEAN·AGGREGATE·MROUND → 162).
  실습 지문(describe.py): 추출 결과 없는 조건 칸은 고급 필터 아님, 부분합 '최대값', 조건 칸 값은 따로 '입력하시오', 칸마다 다른 수식은 칸별, 반올림 자릿수·뒤에 붙일 글자 안내, 피벗 그룹 단위·차트 데이터·이름의 시트·조사, 빈 칸 서식(맞춤 등)은 과제·채점에서 제외.
  채점(compare/exam): 행 높이는 customHeight=1 로 저장된 행만(xlsx.custom_height_rows), 조건부 서식 수식은 $ 가 다르면 칸마다 결과 비교, TODAY·NOW·RAND 정답은 같은 함수를 쓴 수식이면 맞음, 매크로 `Module1.이름`, 기록한 매크로 끝의 .Select·ColorIndex 6=65535.
  보안: 엔진 시간 제한(fx.time_limit·TimeUp, 채점 25초·연습 6초 → BadFile 안내), <int> 큰 수 404(SafeInt), 시험·실습 seconds 범위, 복원 detail 모양·답 2000자·빈 복원 거부, 공개 서버 DB·분석 업로드 300MB 한도, /track next 안전한 경로만, 404·413·400 한국어 오류 화면, VBA 소스 찾기 512KB.
  파일 분석: 합계·소계·※ 메모 행 제외(합계 두 배 방지), 두 줄 머리글, xlsx 글자 숫자·날짜 변환, 60열 초과·제외한 행 안내, 3년 넘는 기간은 연별, (빈칸) 조건은 "". 테스트 231.
  - 2026-10-06 3차(사용자 "문제점 확인해봐"): 건너뛰기 링크·인쇄 CSS(@media print, beforeprint 로 힌트 펼침)·대시보드 '맞힌 문제'+실기 실습 카드·차트 힌트/규칙 종류 한글(_chart_show·_rule_hint)·서식 메시지 현재→정답.
  2급 시뮬레이션 반영: 정답이 보이던 예시(c2-01 2-3, c2-02 2-5)·모호한 지시(c2-02 1-3 고급 필터, 3-2 통합 [A13:C19])·H3 안내 셀 삭제, 매크로 check 에 near(단추 위치 2칸 안, vba.button_cells)·patterns(코드 내용),
  부분합 check 에 ordered(오름차순)·above(적용 순서). 다른 문제의 파일 거부는 글자 겹침 40% 미만이면 BadFile(compare._same_origin·exam._same_origin, 공식 예제·모의고사 포함).
  파일 분석: 출처·주1)·비고 메모 행 제외, (개)·(원) 단위 둘째 머리글 줄, (3) 음수·10개·2 500 숫자. 필기 모의고사: 시간 지난 문제지는 새로/제출 선택(이동 때 타이머 자동 제출과 겹치지 않게 __exRedirecting). 사전 검색은 공백 무시·범주 이름 제외. 테스트 233.
**주의: 파이썬 패치를 bash heredoc 으로 넘기면 ``→백스페이스 같은 문자가 파일에 들어간다 — 패치는 Write 도구로 만든 파일로, 적용 뒤 제어 문자 검사(fix_bs.py 식).**
- 2026-10-06 필기 확장(사용자 "오류 확인 후 필기시험도 추가해"): 컴활 필기 `/written` 문제 1,100(컴퓨터 일반 400·스프레드시트 400·데이터베이스 300, `content/written/*.json` 한 줄 한 문제, `tools/validate_written.py` 오류 0), 독립 검토 에이전트 6명이 정답 오류 0건 확인·표현/급수 수정 반영. 약점 주제(마지막에 맞힌 비율 70% 미만·3문제↑) 연습 `?mode=weak`. 테스트 234. 필기 단일 문제 수정은 scratchpad wfix.py 방식.
- 2026-10-07 필기·실기 전수 재검증(사용자 "하나씩 다 확인"): 필기 1,100문항을 정답·해설 가린 시험지 11장으로 에이전트 11명이 따로 풀어 대조 — 불일치 3건 모두 에이전트 오답, 표시된 82건 중 표현 6건 수정(ss-0237·0034·0261·0062·0324, pc-0326). 실제 Excel: 모의고사 4회 100/100, 실습 69쌍 정답100·실습0, 연습 수식 177문제 2,945칸 불일치 1(Excel 2019 동적 배열 한계). 채점 빈틈 5종 보완(tests/test_gap_fixes.py): 피벗 원본 범위(spec.source, c1-01·c2-01), 고급 필터 조건 영역(criteria_range·criteria_head), 보이지 않는 차트 계열(채우기·선 없음), 실습 조건부 서식은 지문의 '함수 사용' 함수 필수, 서식 코드를 '코드 (예: …)'로 표시. views/practice.CACHE_VER=7. 테스트 241. 필기 정답 대조 도구는 scratchpad make_blind.py·compare_blind.py. 사람이 푼 검증·실제 기출 대조·Access 화면 메뉴 이름은 못 함.
- 2026-10-10 엑셀 단축키 모음(사용자 "그대로 유지하고 엑셀 단축키 모음 추가", 정적 HTML 전환은 안 하기로 함): /shortcuts(views/shortcuts.py, content/shortcuts.json 7묶음 117개 — 입력·선택·서식·수식·데이터 도구(Alt 순차 키)·파일·VBA, ★ 자주 쓰는 42개, 검색·★만 보기는 app.js sc-search), 메뉴 '단축키'. Windows Excel 2016↑ 기준, 키 표기는 동시(+)·순차(,) 구분. tests/test_shortcuts.py, 브라우저 scratchpad lrev/verify_shortcuts.py 13/13. 테스트 245.
