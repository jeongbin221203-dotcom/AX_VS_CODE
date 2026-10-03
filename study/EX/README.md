# 엑셀 연습장 (Flask)

개인 학습용 엑셀 연습 사이트. 네 부분으로 나뉜다.

1. **함수·기능 학습** — 실무 · 컴활 2급 · 컴활 1급 범위 문제. 수식 문제는 시트에 직접 수식을 넣으면 작은 엑셀 계산기(`core/formula.py`)로 계산해 채점한다. 채우기 범위가 있으면 첫 셀 수식을 복사해 모든 칸을 비교하므로 `$` 고정 실수도 잡는다. 기능(피벗·서식·데이터 도구·차트·매크로·단축키)은 보기 고르기.
   - 수식 입력: `=av` → AV 로 시작하는 함수 목록(↑↓, Tab), 인수 도움말, 셀 클릭·드래그·Shift+클릭으로 주소 넣기, F4 로 `$` 바꾸기, Enter 채점 / Ctrl+Enter 계산해 보기.
   - 대시보드: 진도·첫 시도 정답률·최근 14일·분류별 진도·약한 분류·다시 볼 문제. 오답 노트, 별표, 함수 사전.
2. **파일 → 대시보드** — `.xlsx/.xlsm/.csv` 를 올리면 머리글 행을 찾아 열 종류를 판정하고 핵심 지표·분류별 집계·월별 추이·피벗 표·열 정보를 만든다. 같은 값을 내는 `SUMIFS` 수식과 피벗 설정도 보여 주고, 요약을 엑셀(차트 포함)로 내려받을 수 있다.
3. **대시보드 만들기 실습** — 연습 파일(데이터·대시보드·과제 시트)을 내려받아 엑셀에서 수식·차트·조건부 서식을 넣고 올리면 칸마다 채점한다. 저장된 계산값이 없으면 계산기로 직접 계산하고, 값만 쳐 넣은 칸은 오답 처리. 완성 예시 파일도 내려받을 수 있다.
4. **컴활 실기 모의고사** — 실제 시험과 같은 구성·배점·지시문 형식으로 새로 만든 2급·1급(엑셀) 모의고사. 문제 파일을 내려받아 Excel 에서 풀고 올리면 항목별로 채점한다(타이머, 응시 기록, 영역별 점수).
   - 수식(지시 함수·배열 수식 확인), 서식·이름·메모, 조건부 서식(규칙을 칸마다 계산해 비교), 고급 필터·정렬·부분합, 피벗(필드 배치·위치·레이아웃·총합계),
     목표값 찾기·시나리오·데이터 표·통합, 차트(계열·종류·보조 축·제목·범례·레이블), 페이지 설정·시트 보호, 매크로(.xlsm 의 VBA 코드·양식 단추), VBA(코드 패턴).
   - 기출 문제를 옮긴 것이 아니며 실제 채점 기준과 다를 수 있다.

## 실행

```
pip install -r requirements.txt
python app.py            # http://127.0.0.1:5005
```

환경 변수: `EX_PORT`(기본 5005), `EX_DATA_DIR`(기본 `data/`), `EX_DB_PATH`, `EX_SECRET_KEY`, `EX_PROXY=1`(프록시 뒤 HTTPS).
학습 기록은 `data/ex.db`, 분석한 파일 요약은 `data/uploads/`(최근 20개).

## 구조

- `core/formula.py` 계산기(토큰·파서·평가, 함수 약 140개, 배열 수식, 다른 시트 참조, 채우기 복사 `shift`, `unparse`)
- `core/content.py` 문제 은행 읽기·채점·시트 격자 · `core/study.py` 기록·대시보드 통계
- `core/analyze.py` 파일 → 표 → 요약 · `core/build.py` 실습 과제·연습 파일·채점 · `core/xlsx.py` 안전한 엑셀 읽기
- `views/` main · learn · functions · analyze · build
- `content/problems/*.json` 문제(형식 `content/SCHEMA.md`), `content/functions.json` 함수 사전, `content/datasets.json` 공용 데이터
- CSP `script-src 'self'` — 인라인 스크립트 금지, 데이터는 `<script type="application/json">`

## 검사

```
python -m pytest tests -q
python tools/validate_content.py   # 정답 계산·다른 정답·오답 예시·함수 사전 연결
pip install -r requirements-dev.txt   # pytest, (Windows) pywin32 — 모의고사 검증 도구용
```

## 모의고사 만들기·검증

- 정의: `content/exams/<id>.json` (형식 `content/exams/SCHEMA.md`), 채점 `core/exam.py`, 피벗 읽기 `core/pivots.py`, VBA 읽기 `core/vba.py`.
- 검증: `python tools/exam_answer.py <id>` — 각 문제의 정답 단계를 실제 Excel(COM, pywin32)로 실행해 정답 파일을 만들고 채점한다.
  매크로·VBA 는 Excel 보안 설정('VBA 프로젝트 개체 모델에 안전하게 액세스')이 꺼져 있으면 만들 수 없어 그 항목은 빼고 보고한다.
- 테스트 고정 자료 `tests/fixtures/*_excel.xlsx` 는 Excel 로 만든 파일이다(시험 내용을 바꾸면 도구로 다시 만들 것).

## 알려진 한계

- 계산기는 엑셀 일부만 흉내 낸다: 교차 연산자, INDEX 를 범위 끝으로 쓰는 참조(INDEX(...):INDEX(...)), FREQUENCY 등은 지원하지 않는다.
  (OFFSET·INDIRECT·이름 정의·D함수 계산 조건·SUBTOTAL 은 지원)
- 숫자는 엑셀처럼 15자리 정밀도로 다룬다.
- 1급 VBA 문제는 실제 시험의 사용자 정의 폼을 제공할 수 없어 시트 프로시저로 바꿨고, 코드에 지시한 문장이 들어 있는지만 본다.
