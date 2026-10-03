# 운영 절차서 (Runbook) — 영업관리 시스템

경보(`deploy/alerts.yml`)의 runbook 링크가 이 문서의 절을 가리킨다. 모든 명령은 앱 컨테이너(또는 앱 서버) 안에서 실행한다.

## 목표 수준

| 항목 | 목표 | 근거 |
|---|---|---|
| RPO (잃어도 되는 데이터) | 24시간 → PostgreSQL WAL 아카이빙 적용 시 5분 | 매일 02:00 `pg_dump` 자동 백업 (+ 관리형 DB의 PITR) |
| RTO (복구 시간) | 1시간 | 백업 복구 + 스키마 확인 + 점검 모드 해제 |
| 앱 서버 1대 장애 | 무중단 | nginx 가 다른 서버로 넘김 (`proxy_next_upstream`) |
| 워커 장애 | 업무 입력은 계속, 배치만 지연 | 작업은 DB 큐에 남고 워커 재기동 시 이어서 처리 |

## 배포

1. `docker compose build` (또는 CI 이미지)
2. **스키마 먼저**: `docker compose run --rm migrate` → `python manage.py db upgrade`
   - 마이그레이션은 **확장 → 전환 → 축소** 순서로 쓴다: 컬럼 추가(옛 코드도 동작) → 새 코드 배포 → 다음 배포에서 옛 컬럼 제거.
     그래야 앱 서버를 한 대씩 바꾸는 동안 옛/새 코드가 같은 DB 를 써도 깨지지 않는다.
3. 앱 서버를 **한 대씩** 교체: `docker compose up -d --no-deps app1` → `/readyz` 200 확인 → `app2`
4. 워커 교체: `docker compose up -d --no-deps worker` (실행 중이던 작업은 잠금 만료 후 다른 워커가 이어받는다)
5. 확인: **`python manage.py doctor`**(실패면 종료 코드 1 — 배포 스크립트에서 멈춤) 또는 관리자 > ⏱️ 배치 작업 > 🩺 운영 점검,
   워커 '정상', Prometheus `sales_schema_current == 1`

되돌리기: 이전 이미지로 앱을 되돌린다. 스키마는 되돌리지 않는 것이 원칙(확장형 변경이므로 옛 코드가 새 스키마에서 동작).
꼭 필요하면 `python manage.py db downgrade <리비전>` — **먼저 백업**.

## 운영 점검

설치 직후·설정 변경 뒤·장애 의심 때 한 번에 확인한다 (`core/doctor.py`). 업무 데이터는 보내지 않는다.
```bash
python manage.py doctor     # DB·구조·SQLite 안전 설정·저장소 쓰기·디스크·백업(최근·같은 디스크)·ERP·로그인·메일·메신저·워커·보안 설정
```
화면: 관리자 > ⏱️ 배치 작업 > 🩺 운영 점검 (실행 기록은 감사로그 '운영점검').

## 점검 모드

DB 전환·대량 이관·복구 중에는 쓰기를 막는다 (조회만 가능, 쓰기·API 쓰기는 503, 워커는 쉼).
```bash
python manage.py read-only on --reason "DB 서버 교체"   # 서버 여러 대가 15초 안에 함께 (재기동 없음)
python manage.py read-only off
SALES_READ_ONLY=1                                        # 환경변수 — 화면에서 끌 수 없는 강제 모드 (재기동 필요)
```
화면: 관리자 > 🩺 운영 점검 > 점검 모드 (켜면 관리자 알림, 모든 화면 위에 띠). 2시간 넘게 켜져 있으면 `SalesReadOnlyTooLong` 경보.

## 1. 앱 서버 장애 (`SalesAppDown`)
1. `docker compose ps`, `docker compose logs --tail 200 app1` — 요청 ID 로 nginx 로그와 대조
2. `/readyz` 로 원인 구분: `db` / `schema` / `storage` 중 무엇이 실패인지
3. 재기동: `docker compose restart app1`. 반복되면 이전 이미지로 되돌린다.
4. 한 대만 죽었으면 사용자 영향 없음(nginx 가 넘김). 두 대 모두면 아래 DB·저장소 절 확인.

## 2. DB 장애 (`SalesDatabaseDown`)
1. `pg_isready -h <DB>`; 관리형 DB 면 콘솔에서 장애 조치(failover) 진행 상태 확인
2. 대기 서버로 넘어가면 `SALES_DATABASE_URL` 이 가리키는 주소(보통 DNS/VIP 그대로)를 확인하고 앱·워커 재기동(커넥션 풀 재생성)
3. 데이터 손상 시 → **복구**:
   ```bash
   # 점검 모드로 전환 후
   python manage.py restore /app/data/backups/sales_YYYYMMDD_HHMMSS.dump --yes   # 복구 직전 현재 DB 를 안전 백업
   python manage.py db upgrade && python manage.py check
   ```
   관리자 > 감사로그 '무결성 검증'으로 해시 체인을 확인한 뒤 점검 모드 해제.
4. 복구 후 ERP 대사: 관리자 > ERP 연동에서 '확인필요'·미전송 건 확인(백업 이후 ERP 로 나간 전표가 CRM 에 없을 수 있다).

## 3. 워커·배치 장애 (`SalesWorkerStopped`, `SalesJobBacklog`, `SalesJobsFailed`)
1. 관리자 > 배치 작업: 워커 목록(마지막 신호), 실패 작업의 오류 메시지
2. 워커 재기동 `docker compose restart worker`. 적체가 크면 `docker compose up -d --scale worker=3` (중복 처리 없음)
3. 실패 작업은 원인(ERP·SMTP·인사 원천)을 고친 뒤 화면에서 '재시도'
4. 인사 연동이 "피드가 잘렸을 수 있다"로 멈췄으면: 원천 파일 건수를 인사팀에 확인. 정상적인 대량 퇴사면
   `SALES_HR_MIN_RATIO` 를 일시적으로 낮춰 화면에서 미리보기 → 반영

## 4. 오류율 증가·응답 지연 (`SalesHighErrorRate`, `SalesSlowResponses`)
1. Prometheus: `sales_http_requests_total{status=~"5.."}` 를 endpoint 별로 → 어느 화면인지
2. 앱 로그에서 해당 endpoint 의 `exc` 필드, 요청 ID 로 추적
3. DB 느림: `pg_stat_activity`(오래 걸리는 쿼리·잠금 대기), 커넥션 풀 고갈이면 앱 서버 수·`SALES_THREADS` 조정
4. 특정 기능 장애면 점검 모드 대신 해당 기능만 안내하고 수정 배포

## 5. 외부 시스템 장애 (`SalesErpSendFailed`, `SalesApprovalsOverdue`)
- **ERP**: 영업 입력은 계속된다(Outbox). ERP 복구 후 워커가 자동 재시도, 5회 넘으면 관리자 > ERP 연동에서 '재시도'.
- **IdP(OIDC) 장애**: 새 로그인이 안 된다(기존 세션은 유지). 긴급 시 `SALES_AUTH_MODE=password` 로 바꾸고
  관리자가 비상 계정 비밀번호로 로그인(인증 방식이 바뀌면 기존 세션은 모두 끊긴다). 복구 후 되돌린다.
- **메일(SMTP)**: 알림은 화면 알림함에 남아 있다. 복구 후 실패한 notify.deliver 작업 재시도.
- **파일 저장소(S3/MinIO)**: `/readyz` 의 storage 실패. 증빙 열람·업로드만 실패하고 나머지는 동작. 버전 관리가 켜져 있어 실수로 지운 파일은 이전 버전으로 복구.
- **결재 지연**: 기한 지난 단계는 자동 독촉·윗선 보고. 부재자는 결재함 > 대결 지정으로 대결자를 지정.

## 인터넷 장애
- 화면은 사내망만으로 열린다. 관리자 > 🏢 회사 설정의 '인터넷이 끊겨도 쓸 수 있는지' 표에서 '외부 인터넷' 연결만 영향을 받는다.
- 사내 인증(IdP)이 클라우드라 로그인이 안 되면 → 로그인 화면의 '비상 계정 로그인'(`SALES_BREAKGLASS_USERS` 에 지정한 관리자, 비밀번호는 미리 설정).
  사용 즉시 감사로그·관리자 알림이 남는다. 복구 후 비상 계정 비밀번호를 바꾼다.
- 장애가 길어 직원도 들어와야 하면 **SSO 장애 모드**: 관리자 > 🩺 운영 점검 또는 `python manage.py sso-outage --hours 4 --reason ...`
  → 그 시간(최대 24시간) 동안 비밀번호가 설정된 사용자 누구나 비밀번호로 로그인. 시간이 지나면 저절로 꺼진다(`--off` 로 바로 끔).
  SSO 만 쓰던 직원은 비밀번호가 없으므로 영업 담당별 장애 대비 비밀번호를 미리 만들어 둔다.
- 파일 저장소(S3)가 안 되면 증빙·첨부는 이 서버의 `SALES_STORAGE_SPOOL_DIR`(기본 data/spool)에 임시 보관되고 업무는 계속된다.
  복구되면 배치 `storage.flush`(5분)가 올린다. 수동: `python manage.py storage-flush`. 운영 점검의 '저장소'에 남은 개수가 보인다.
- ERP·메일·웹훅은 대기열에 쌓였다가 연결이 돌아오면 자동 전송. 관리자 > ERP 연동에서 '전송중'으로 멈춘 건은 30분 뒤 '실패'로 바뀌므로
  ERP 에 전표가 생겼는지 확인한 뒤 재시도한다(자동 재전송하지 않는 이유: 중복 전표 방지).

## 감사로그 보관 이관
- **감사로그 이관 실패**: '해시 체인이 끊겨 이관하지 않았다'는 경보는 변조 의심이다. 감사로그 화면의 무결성 검증으로 위치를 찾고, 백업과 대조한 뒤 조치한다.
- **이관 파일 확인**: 감사로그 화면의 '파일 검증'(이관 id)으로 SHA-256·체인을 다시 확인한다. 파일은 저장소의 `audit_archive/` 에 있으며 지우지 않는다.

## 전자세금계산서 발행
- 발행 요청이 '발행요청'에 오래 머물면 배치 작업 화면에서 `etax.issue` 실패 사유(ASP 연결·인증)를 확인한다. 큐가 다시 시도한다.
- 파일 방식은 ASP 에이전트가 `SALES_ETAX_OUT_DIR` 의 XML 을 가져가고 `/api/v1/etax/acks` 로 승인번호를 회신해야 '발행완료'가 된다.

## 6. 보안 사고 (`SalesLoginFailureSpike`)
1. 감사로그에서 '로그인실패'·'API인증실패'의 IP·사번 분포 확인
2. 한 계정 대상이면 잠금이 자동 적용됨(5회/15분). 한 IP 에서 여러 계정을 시도하면 **IP 자동 차단**(15분에 20회, `SALES_IP_MAX_FAILURES`).
   여러 IP 에서 오면 nginx·WAF 에서 막는다. 접속 IP 는 `SALES_CLIENT_IP_HEADER`(예: True-Client-IP) 또는 `SALES_PROXY_FIX=1` 로 실제 IP 를 받게 둔다
3. 계정 탈취 의심: 관리자 > 조직·사용자 > 그 사용자 > **모든 세션 끊기**(다른 PC·복사된 쿠키 포함) + 임시 비밀번호. 비밀번호를 바꿔도 다른 세션은 끊긴다
4. API 키 유출 의심: 관리자 > API 연동에서 즉시 **폐기**(바로 401), 새 키 발급·허용 IP 지정
5. 데이터 변조 의심: 감사로그 '무결성 검증' → 끊긴 지점 이후를 백업과 대조

## 정기 점검 (분기)
- [ ] 백업 파일로 **복구 리허설** (별도 DB 에 `pg_restore` → `manage.py doctor` → 건수 대조). 백업은 만들 때마다 검증된다(SQLite quick_check · `pg_restore --list`, 실패하면 파일을 남기지 않음)
- [ ] 백업 폴더가 DB 와 **다른 디스크·NAS** 인지 (운영 점검 '백업'이 같은 디스크면 주의)
- [ ] 감사로그 무결성 검증
- [ ] 사용하지 않는 API 키·대결 지정 정리, 퇴직자 계정 비활성 확인(인사 연동 결과)
- [ ] 인증서 만료일, 의존성 보안 업데이트(`pip list --outdated`), 이미지 재빌드
