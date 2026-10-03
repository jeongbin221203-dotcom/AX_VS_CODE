# 영업관리 보안 점검

적용한 통제, 자동 보안 검사 결과, 남은 한계를 정리한다. 모의해킹(외부 침투 시험)은 하지 않았다.
장애 대응·운영 절차는 [RUNBOOK.md](RUNBOOK.md).

## 1. 적용한 통제

| 위협 | 대응 | 위치 |
|---|---|---|
| 비인가 접근 | 로그인 필수(비밀번호 · 사내 SSO 헤더 · OIDC), 역할별 메뉴·서버 검사(403), 담당자·조직 데이터 범위 | `core/auth.py`, `views/helpers.py`, `core/enterprise.visible_owners` |
| 비밀번호 추측 | PBKDF2-SHA256 31만 회, 계정 5회 실패 시 15분 잠금, **IP 단위 15분 20회 실패 시 차단** (회사 NAT 처럼 여럿이 한 IP 면 `SALES_IP_ALLOWLIST` 로 IP 차단만 예외 — 계정 잠금은 그대로) | `core/auth.py` (`login_ip_failures`) |
| 계정 존재 확인 | 없는 계정도 같은 시간 해시 계산, 실패 문구 통일 | `core/auth.authenticate_password` |
| 세션 탈취 | HttpOnly·SameSite 쿠키, 로그인 때 세션 교체, 무활동 만료 + 최대 시간, **비밀번호 변경·로그아웃 때 다른 세션 모두 무효**(`users.session_version`), 관리자 '모든 세션 끊기' | `views/auth.py`, `views/helpers.load_context` |
| 위조 요청(CSRF) | 모든 POST 에 세션 토큰, 같은 제출 두 번 처리 안 함(`_submit_id`) | `app.py` |
| XSS | Jinja 자동 이스케이프, CSP(`default-src 'self'`, 외부 CDN 없음 — Chart.js 는 static/vendor) | `app.py` |
| 클릭재킹 | `X-Frame-Options: DENY`, CSP `frame-ancestors 'none'` | `app.py` |
| 열린 리다이렉트 | 로그인 후 이동은 사이트 내부 경로만 | `views/auth._next_url` |
| 엑셀 수식 주입 | 내려받는 엑셀·CSV 에서 `= + - @` 로 시작하면 앞에 `'` | `core/dataio.py` |
| 악성 업로드 | 증빙은 파일 앞부분으로 형식 판정, XML 은 defusedxml, 업로드 크기·행 수 제한 | `core/documents.py`, `core/dataio.py` |
| 정보 노출 | 운영(production)에서는 위험한 설정으로 기동 거부, 500 은 일반 문구 + 문의 번호(X-Request-ID), 로그인 화면 외 `Cache-Control: no-store`, 개인정보 마스킹·다운로드 감사 | `config.py`, `core/observability.py` |
| SQL 주입 | 값은 모두 파라미터 바인딩. 문자열로 붙이는 부분은 코드에 고정된 표·열 이름뿐(사용자 입력 아님) | 전역 |
| 비밀값 | 메신저 토큰·키는 DB 에 Fernet 암호화(키: `SALES_NOTIFY_KEY` → `SALES_SECRET_KEY`), 화면에는 끝 4자리 | `core/messenger.py` |
| 외부 연동 | API 키(해시 저장·범위·허용 IP·분당 제한·폐기), ERP 멱등키, 웹훅은 https 만 | `core/api_keys.py`, `core/erp.py` |
| 부인 방지 | 감사로그 해시 체인 + 수정·삭제 트리거, 접속 IP(앞단 헤더 `SALES_CLIENT_IP_HEADER`), 열람·다운로드·운영 상태 변경 기록 | `core/sales_db.audit` |
| 사내 로그인 장애 | 비상 계정(`SALES_BREAKGLASS_USERS`), 시간 제한 SSO 장애 모드(최대 24시간, 감사로그·관리자 알림) | `core/auth.py` |

## 2. 자동 보안 검사 (2026-10-03)

CI(`.github/workflows/sales-ci.yml`)가 push 마다 실행한다.

| 도구 | 범위 | 결과 |
|---|---|---|
| bandit 1.8 | `core views app.py manage.py config.py serve.py` | **High 0** · Medium 121 · Low 18 — CI 는 High 가 있으면 실패 |
| pip-audit 2.9 | `requirements.txt` | **알려진 취약점 없음** |

Medium 검토 결과 (모두 수용):

| 항목 | 건수 | 판단 |
|---|---|---|
| B608 문자열로 만든 SQL | 115 | 붙이는 것은 코드 상수(표 이름·열 이름·`?` 자리표시 개수·권한 범위 절)뿐이고 사용자 값은 모두 바인딩. 테스트에 SQL 주입 문자열 입력 포함 |
| B310 `urllib.urlopen` | 4 | 관리자가 설정한 ERP·인사·메신저·전자세금계산서 주소로만 요청. 웹훅은 https 만 허용 |
| B323 TLS 검증 끄기 | 2 | `SALES_SAP_VERIFY_TLS=0` 일 때만(사설 인증서 시험 서버용). 기본은 검증 |
| Low (subprocess · assert · try/except/pass · random) | 18 | subprocess 는 고정 인자(pg_dump·pg_restore·manage.py), random 은 샘플 데이터 생성용 |

다시 돌리기:
```bash
pip install bandit pip-audit
bandit -r core views app.py manage.py config.py serve.py --severity-level high -q
PYTHONUTF8=1 pip-audit -r requirements.txt     # Windows 는 PYTHONUTF8=1 (requirements 의 한글 주석)
```

## 3. 운영에서 꼭 설정할 것

- `SALES_ENV=production`, `SALES_SECRET_KEY`(긴 무작위 값 — 바꾸면 모든 세션과 메신저 비밀값을 다시 넣어야 함, 따로 두려면 `SALES_NOTIFY_KEY`)
- HTTPS 리버스 프록시 + 쿠키 Secure(운영에서 자동). 프록시 뒤면 실제 IP: `SALES_PROXY_FIX=1`(X-Forwarded-For 1단) 또는
  `SALES_CLIENT_IP_HEADER=True-Client-IP`(앞단이 반드시 덮어쓰는 헤더만 — 아니면 IP 를 꾸밀 수 있다)
- `SALES_AUTH_MODE` = password · sso · oidc (simple 은 운영에서 기동 거부). SSO 면 `SALES_TRUSTED_PROXIES`
- DB·`data/`·백업 폴더는 서버 계정만 읽게 권한 제한. 백업은 다른 디스크·NAS(`SALES_BACKUP_DIR`) — 운영 점검이 같은 디스크면 경고

## 4. 남은 과제 (하지 않은 것)

- 외부 모의해킹 · 시큐어 코딩 점검(외부 기관)
- 저장 데이터 암호화(디스크/DB 수준) — 메신저 비밀값만 앱에서 암호화
- 증빙 파일 악성코드 검사(백신 연동), 감사로그 외부 전송(SIEM)
- 2단계 인증(OTP)은 사용자 요청으로 제거 — 사내 SSO(IdP)의 2단계 인증에 맡긴다
- 비밀번호 재사용 이력(최근 N개) 금지 — 지금은 직전 비밀번호만 막음
