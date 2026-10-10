# 주식 차트분석 (study/stock)

개인 학습용. 투자 권유가 아니며 수익을 보장하지 않습니다. 계획 전체는 [PLAN.md](PLAN.md).

## 실행
```bash
pip install -r requirements.txt
python manage.py update --top 50      # 종목 목록 + 시총 상위 50 일봉 (또는 --codes 005930 000660)
python app.py                         # http://127.0.0.1:5006
python -m pytest tests -q             # 22개
```
환경변수: `STOCK_PORT`, `STOCK_DB_PATH`(기본 data/stock.db), `STOCK_DATA_DIR`.
검색창에서 아직 없는 종목을 열면 그 종목 일봉을 자동으로 받습니다.

## 토스증권 Open API (시세 조회 전용)
`.env`(git 제외)에 `toss=<client_id>:<client_secret>` 또는 `TOSS_CLIENT_ID` / `TOSS_CLIENT_SECRET` 두 줄.
키가 있으면 일봉을 토스증권(`/api/v1/candles`, 수정주가)에서 받고, 실패하면 FinanceDataReader로 대신 받습니다(`STOCK_SOURCE=auto|toss|fdr`).
`python manage.py toss-check` 로 키가 동작하는지 확인(키 값은 출력하지 않음). 주문 API는 쓰지 않습니다.

## 구현 범위 (PLAN 1~4단계 + 관심종목)
- 수집: FinanceDataReader, 마지막 날짜 다음날부터 증분, 결측·이상값 제거 (`core/collector.py`)
- 지표: SMA·EMA·RSI(Wilder, SMA 시드)·MACD·볼린저(모집단 σ)·스토캐스틱·ATR·OBV, 주봉·월봉 (`core/indicators.py`)
- 신호 9종(골든/데드크로스·RSI·MACD·거래량 급증·52주 신고가·볼린저 수축)과 근거 문장, 캔들 패턴 6종, 지지/저항 (`core/signals.py`, `core/patterns.py`)
- 화면: 캔들+거래량+이동평균·볼린저, 보조 패널 2개(RSI/MACD/스토캐스틱), 일·주·월, 기간, 로그, 신호 마커·내역, 관심종목(★), 데이터 갱신

## 단테 노트 · 기법 적용
- `/notes` 유튜브(주식단테·1분주식) 제목·설명·자막 검색, 기법 태그 필터. 수집: `python manage.py yt-list` → `yt-meta` → `yt-captions` → `yt-clean` (이어 받기). 쇼츠는 메타데이터만. 데이터는 `data/yt.db`(git 제외, 개인 학습용).
- 기법 태그를 누르면(`/technique/<기법>`) 규칙·관련 영상·종목 선택이 나오고, 차트(`/chart/<코드>?tech=눌림목`)의 '기법' 버튼으로 켜고 끈다.
- 적용 가능(일반적 정의로 구현, 규칙은 `core/techniques.py`·기법 화면에 표시): 이평선·지지·저항·눌림목·돌파·신고가·거래량(기본 켜짐), 캔들·손익비·단타(켜서 사용).
- 준비 중: 역매공파·256기법(단테 고유 규칙 — 자막에서 조건 확인 후 구현). 재료·테마·초보·기초는 차트 대상 아님.

## 기법 정확도 · 성공/실패 이유 (`/analysis`, `/analysis/why`)
- `python manage.py backtest` (전 종목 약 1시간+, 6프로세스) → `data/backtest.json`(기법별 성과), `data/why.json`(성공·실패 이유), `data/backtest_acc.pkl`·`why_rows.pkl`(중간 결과: 집계만 다시 하려면 이 파일에서).
- 신호 다음날 시가 진입(미래 정보 금지) · 하루·이틀 늦춘 진입 · −3% 눌림 지정가 · 손절 4종(기법/기법+1×ATR/2×ATR/3×ATR) · 목표 2R·3R·5R·자연 목표 · 매집봉 유무별 비교, 모두 같은 날 시장 평균을 뺀 초과수익과 날짜 묶음 t 값으로 판정.
- 성공·실패 이유: 신호일 종가까지 알 수 있던 특징 20개를 성공/실패 그룹에서 비교(d, 분위별 성공률), 손실 원인 분류, 시장 상황별·연도별 성공률.
- 기법 추가(자막): 매집봉(장대양봉·윗꼬리형·장기 이평 찌름 + 거래량 2배, 224일선 아래 바닥권), 역매공파(역배열 + 매집봉 + 공구리), 세력선(7·15·33일선 지지).

## 아직 안 한 것 (PLAN 5~8단계)
일일 자동 갱신 스케줄러, 스크리너, 백테스트, 알림, 그리기 도구, 분봉.

## 주의
- 차트 라이브러리는 `static/vendor/lightweight-charts.js`(v4.2.0)로 보관, CSP 때문에 인라인 스크립트 금지.
- 한국식 색상(상승 빨강·하락 파랑). 데이터 출처 구조가 바뀌면 `core/collector.py` 한 곳만 고치면 됩니다.
- `.env`는 git 제외.

## AI 분석 (`/ai`)
- `python manage.py ai-train` (약 4분) → `data/ai.json`, `data/ai_model.joblib`. 20거래일 뒤 +20% 이상 오른 종목을 그래디언트 부스팅으로 학습(특징 43개: 추세·이평·모멘텀·변동성·거래량·시장), 시간 순 분할(학습 ≤2018, 검증 ~2021, 시험 2022~)로 정확도(AUC·상위 k% 적중·보정)와 AI 상위 N개 보유 성과·중요한 특징·거래량 구간별 확률·오늘 점수 상위를 보여 줌.

## 진입 시점 (`/entry`)
- `python manage.py entry-train` (약 10~30분) → `data/entry.json`. 오를지 내릴지 모를 때를 위한 분석: 방향 예측 한계(오를 것인가·크게 오를/내릴 것인가), 시장 상황·종목 상태별 상승 확률, 진입 방식 비교(전액·3분할·늦춤·확인 후·눌림 지정가·2×ATR 손절), 종목 고유 모델(시장 효과 제외)·보정 확률로 본 상승 가능성 상위 종목과 이유.

## 거래량 급증 (`/surge`)
- `python manage.py surge-train` (약 1~2분) → `data/surge.json`. 거래량 3배↑ 급증(첫 급증, 거래 가능 종목) 다음날 시가 진입 후 120거래일 동안 +10·20·30·50·100%에 처음 닿은 날·확률·닿기 전 하락·−10% 먼저 여부를 아무 날 기준과 비교하고, 급증일 모습·위치·직전 거래량·가격대·시장·갭·시기별로 나눔. 최근 급증 종목에 비슷한 과거 그룹의 결과를 붙임.

## 종합 전략 (`/combined`)
- `python manage.py combined-train` (약 30분) → `data/combined.json`. 피함 7개 규칙 제외 → 앞선 연구에서 좋았던 진입 신호 후보(미리 정함) 중 학습(~2021)·시험(2022~) 모두 시장보다 좋은 것만 채택 → 갭 +3% 이하 진입 → 3분할(또는 2×ATR 손절) → AI 상위 10% 추가, 단계마다 20·60·120일 성과·날짜별 t·해마다·월별·시장 상황별, 오늘 걸리는 종목.
