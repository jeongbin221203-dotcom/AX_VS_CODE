# 자재관리 시스템 (Streamlit + SQLite)

창고 자재의 **입고 / 출고 / 실사조정**을 기록하고, 재고·안전재고·재고금액을
실시간으로 집계하는 단일 서버 웹 애플리케이션.

## 빠른 시작

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

브라우저에서 `http://localhost:8501` 접속.
첫 실행 시 `data/materials.db`가 자동 생성된다.
**데이터 관리 → 샘플 데이터 생성**으로 데모 데이터를 넣을 수 있다.

## 폴더 구조

```
material-manager/
├── app.py              진입점 — 페이지 라우팅과 사이드바만 담당
├── config.py           경로 · 상수 · 라벨 (환경변수 MM_DB_PATH로 DB 경로 override)
├── requirements.txt
├── README.md
├── .gitignore
│
├── core/               업무 로직 — Streamlit 비의존, 단독 테스트 가능
│   ├── db.py           커넥션 · 스키마 · 트랜잭션 컨텍스트
│   ├── repository.py   SQL 전담 (SQL은 이 파일 밖으로 나가지 않는다)
│   ├── services.py     업무 규칙 (재고 판정, 조정 계산, 업로드 정제)
│   ├── utils.py        문자열 정규화 · 엑셀 변환
│   └── seed.py         데모용 샘플 데이터
│
├── views/              화면 — 위젯 렌더링만, 판정은 services에 위임
│   ├── dashboard.py    KPI · 30일 추이 · 분류별 재고금액 · 미달 목록
│   ├── materials.py    자재 마스터 등록/수정/사용중지
│   ├── transactions.py 입고 · 출고 · 실사조정 등록
│   ├── stock.py        재고 현황 조회 · 필터 · 엑셀
│   ├── history.py      거래 이력 조회 · 엑셀 · 삭제
│   └── data_admin.py   일괄 업로드 · 백업 · 샘플
│
├── data/               런타임 데이터 (git 제외)
│   └── materials.db
└── tests/
    └── test_core.py    업무 규칙 단위 테스트 (Streamlit 불필요)
```

### 폴더명 주의
`views/`를 **`pages/`로 바꾸면 안 된다.** Streamlit은 `pages/` 디렉터리를
멀티페이지 앱으로 자동 인식해, 사이드바에 파일 목록을 강제로 노출한다.

## 설계 원칙

| 원칙 | 이유 |
|---|---|
| 재고는 컬럼이 아니라 **거래의 합계로 도출** | 재고 필드를 따로 두면 반드시 장부와 어긋난다 |
| 판정은 `services`, SQL은 `repository`, 위젯은 `views` | 화면을 바꿔도 업무 규칙이 흔들리지 않는다 |
| `core`는 Streamlit을 import하지 않는다 | 서버 없이 `python tests/test_core.py`로 검증 가능 |
| 자재는 삭제하지 않고 **사용중지** | 과거 거래 이력의 참조 무결성 보존 |
| 출고는 **등록 직전 재고를 다시 조회**해 판정 | 두 명이 동시에 출고해도 음수 재고가 생기지 않는다 |

## 테스트

```bash
python tests/test_core.py    # 또는 pytest tests/
```

## 운영 메모

- **백업**: `data/materials.db` 파일 복사만으로 완전 백업. 화면에서도 `.db` / 엑셀 백업 제공.
- **동시 사용**: SQLite는 WAL 모드로 동작하며 소규모 팀(동시 10명 내외)까지 무난.
  그 이상이거나 다지점 운영이면 `core/db.py`의 커넥션만 PostgreSQL로 교체하면 된다.
- **DB 경로 변경**: `MM_DB_PATH=/mnt/nas/materials.db streamlit run app.py`
