# 컴활 실기 형식 모의고사 정의 (`content/exams/<id>.json`)

실제 시험과 같은 구성·배점·지시문 형식으로 **새로 만든** 문제. 기출 문제를 옮겨 적지 않는다.
예시: `c2-01.json` (2급 1회). 채점 코드: `core/exam.py`, Excel 로 정답 파일을 만들어 검증: `tools/exam_answer.py`.

## 시험
| 필드 | 설명 |
|---|---|
| `id` | `c2-02`, `c1-01` … |
| `level` | `c2`(2급) / `c1`(1급) |
| `round` | 회차 번호 |
| `title`, `intro` | 제목, 안내 문장 |
| `minutes` | 2급 40, 1급(엑셀) 45 |
| `pass` | 70 |
| `sheets` | 문제 파일 시트(아래) — 순서대로 만들어진다 |
| `names` | (선택) 처음부터 있는 이름 정의 `{"이름": "'시트'!$A$1:$A$9"}` |
| `tasks` | 문제(아래) |

배점 합계 100. 2급: 기본작업 20 · 계산작업 40(5×8) · 분석작업 20(2×10) · 기타작업 20(매크로 10 + 차트 10).
1급(엑셀): 기본작업 15(5×3) · 계산작업 30(5×6) · 분석작업 20(2×10) · 기타작업 35(차트 10 + 매크로 10 + VBA 15).

## 시트
`{"name", "rows", "at"(기본 A1), "blocks": [{"at", "rows"}], "cells": {"H3": "…"}, "formats": {"E4:E15": "yyyy-mm-dd"},
"widths": {"A": 10}, "merge": ["A1:F1"], "bold": ["A3:F3"], "fills": {"A3:F3": "DDEBF7"}, "borders": ["A3:F15"],
"align": {"A3:F3": "center"}, "charts": [{"type": "col|bar|line|pie", "data": "B3:D9", "cats": "A4:A9", "at": "G3"}]}`
- 날짜는 `"@2026-09-01"`, 수식은 `"=SUM(B4:D4)"` (문제 파일에 수식으로 들어감).
- 계산작업 시트는 실제 시험처럼 [표1]~[표5] 를 `blocks` 로 한 시트에 배치하고, 참조표(<분류표> 등)도 같이 둔다.

## 문제(task)
`{"no": "1-1", "section": "기본작업|계산작업|분석작업|기타작업", "title", "sheet", "text", "items": ["①…", …],
"table": {"at", "rows"}(자료 입력 문제의 입력할 표), "checks": [...], "answer": [...]}`
- `text`·`items` 는 실제 시험 지시문 말투로. 함수 사용 조건은 items 마지막에 `'IF, AVERAGE 함수 사용'`.
- 배점은 `checks` 의 `points` 합.

## 채점 항목(check) — 모두 `kind`, `label`, `points`, (선택) `sheet`(다른 시트일 때)
| kind | 필드 |
|---|---|
| values | `range`, `expect`(2차원, 입력해야 할 값) |
| formula | `range`, `answer`(첫 칸 정답 수식 — 채우기로 복사해 기대 값 계산), `require`, `forbid`, `array`(배열 수식 필수), `allow_value`, `tol` |
| style | `range`, `merge`, `font{name,size,bold,italic,underline:"single",color:"RRGGBB"}`, `fill`, `halign`, `valign`, `wrap`, `numfmt`, `border:"all"`, `height` |
| comment | `cell`, `text` |
| name | `name`, `ref` (`'시트'!D4:D15`) |
| cf | `range`, `formula`(첫 행 기준 정답 규칙 — 행마다 계산해 서식 받을 행을 비교), `font_color`, `bold`, `italic`, `fill` |
| dv | `range`, `type`(whole/decimal/list/date/textLength/custom), `operator`, `formula1`, `formula2`, `error_title`, `error`, `prompt_title`, `prompt`, `style`(stop/warning/information) |
| filter | 고급 필터 결과: `source`(머리글 포함), `answer`(첫 데이터 행 기준 조건 수식), `out`(결과 첫 칸), `columns`(일부 필드만 추출할 때), `criteria_at`, `criteria_range`(조건 영역), `criteria_head`(필드명) |
| sorted | `range`(머리글 포함), `keys`: `[["부서","asc"], ["직급", ["부장","과장","대리","사원"]]]` |
| subtotal | `range`(원본 머리글 포함), `group`, `items`: `[{"func":"max","fields":["기본급"]}]` (sum·average·count·max·min) |
| pivot | `rows`, `cols`, `filters`, `values`: `[["금액","sum"]]`, `source`(원본 범위), `sheet`, `at`, `layout`(compact/outline/tabular), `no_grand_rows`, `no_grand_cols`, `group`, `check_total` |
| goalseek | `cell`(수식 셀), `value`(목표값), `changing` |
| scenario | `changing`, `scenarios`: `[["이름", [값…]]]`, `summary`(요약 시트 필요), `summary_before`(요약 시트가 바로 앞에 와야 할 시트) |
| datatable | `range`(결과 칸 — 머리 행·열 제외), `row_input`, `col_input` (모서리 칸 수식은 문제 파일에 미리 두거나 정답 단계에서 넣음) |
| chart | `index`, `type`(col/bar/line/pie), `title`, `series`(계열 이름 순서), `line_series`, `secondary`, `labels`, `trend`, `y_title`, `x_title`, `legend`(b/t/r/l), `grouping` |
| macro | `name`, `button`(양식 단추 글자) — **`needs_vba: true`** 필수 |
| vba | `proc`(프로시저 이름), `patterns`(정규식 — 공백은 한 칸으로 줄여 비교) — **`needs_vba: true`** 필수 |
| page | `orientation`(landscape/portrait), `center_h`, `center_v`, `print_area`, `title_rows`, `header_center`, `footer_center`, `header_right`, `footer_right`, `fit_width` |
| protect | `unlocked`(잠금 해제 범위), `hidden`, `allow_select_locked`, `allow_format_cells` |

사용자 정의 함수(1급): formula 항목에 `require: ["FN비고"]` + `needs_vba: true`, `answer` 는 같은 결과를 내는 일반 수식.
매크로 결과(합계 칸·서식)는 macro 와 별도의 formula/style 항목으로 채점한다(매크로 실행 결과는 자동 검증됨).

## 1급 폼·외부 데이터
- 시험 최상위 `forms: [{name, caption, width, height, controls: [{type: Label|TextBox|ComboBox|ListBox|CommandButton|OptionButton|CheckBox, name, left, top, width, height, caption}]}]`,
  `commands: [{sheet, name, caption, range}]`(시트의 ActiveX 명령 단추) → `python tools/exam_problem_files.py` 가 Excel 로 `content/exams/files/<id>.xlsm` 을 만든다
  (폼에는 코드 없이 컨트롤만). 정의를 바꾸면 다시 실행(테스트가 시트 내용이 같은지 확인).
- `data_files: {"이름.csv": {"rows": [[머리글…], …]}}` → 문제지의 [자료 파일] 단추로 받는 외부 데이터. 피벗 검사 `external: true` 는 원본이 시트 범위가 아닌지 본다.
  데이터 모델 피벗의 필드 이름('[쿼리].[지점].[지점]', '[Measures].[평균: 대여료]')은 채점 때 '지점'·'대여료'로 읽는다.

## 정답 단계(answer op) — `tools/exam_answer.py` 가 Excel 에서 실행
`values{range,values}` · `formula{range,formula,array}` · `style{…check 와 같은 필드}` · `comment{cell,text}` · `name{name,ref}` ·
`cf{range,formula,font_color,bold,italic,fill}` · `dv{range,type,operator,formula1,formula2,error_title,error,prompt_title,prompt,style}` ·
`advfilter{source,criteria:{at,rows},out,columns}` · `sort{range,keys}` · `subtotal{range,group_col(열 번호),func,cols:[열 번호],replace,current_region}` ·
`pivot{source_sheet,source,at,dest_sheet,rows,cols,filters,values:[[필드,함수,표시 이름]],layout,no_grand_rows,no_grand_cols}` ·
`scenario{changing,scenarios,result}` · `goalseek{cell,value,changing}` · `datatable{range(머리 포함 전체),row_input,col_input,corner,corner_at}` ·
`consolidate{target,sources:["'시트'!$A$3:$C$9"],func,top,left}` · `chart{source,type,at,title,line_series,secondary,labels,trend,y_title,x_title,legend}` ·
`page{…}` · `protect{unlocked,hidden,password}` · `textsplit{range,delimiter}` ·
`vba{code, module('sheet'=그 시트 모듈, 기본=새 표준 모듈), buttons:[{text,macro,range}](양식 단추), commands:[{name,caption,range}](ActiveX 명령 단추), run:[실행할 프로시저], activate(시트를 다시 활성화해 Worksheet_Activate 실행)}`
— 매크로·사용자 정의 함수·VBA 프로그래밍 문제의 모범 답안. `module: "form:<폼 이름>"` 은 그 폼의 코드,
  `test: "<VBA 문장>"` 은 임시 모듈에서 실행(폼에 값을 넣고 단추 프로시저를 부르는 식 — 폼 단추 프로시저는 Public 이어야 부를 수 있음).
`pivot{external_csv: "<data_files 이름>"}` 은 csv → Power Query → 데이터 모델 피벗(시험의 [외부 데이터 원본 사용]과 같은 형태). 실행하려면 Excel '보안 센터 > VBA 프로젝트 개체 모델에 안전하게 액세스'(AccessVBOM)가 켜져 있어야 한다.
op 에 `sheet` 를 주면 그 시트에서 실행(기본은 task 의 sheet).

## 검증
`python tools/exam_answer.py <id>` → `<id>: 점수/100 (매크로·VBA 포함)` 와 실패 항목. 모든 항목이 통과해야 한다(VBA 접근이 꺼져 있거나 `--no-vba` 면 needs_vba 항목은 빼고 보고).
