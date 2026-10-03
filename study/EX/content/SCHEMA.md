# 문제 형식

`content/problems/<이름>.json` — 파일 하나에 한 분류(category)를 둔다. 파일이 여러 개여도 같은 분류 가능(`lookup.json`, `lookup_2.json`).

```json
{"category": "lookup", "problems": [ ... ]}
```

## 공통 필드
| 필드 | 설명 |
|---|---|
| `id` | 전체에서 유일. `분류-번호` (예 `lookup-012`) |
| `type` | `formula`(수식 입력, 실제 계산으로 채점) / `choice`(보기 고르기) |
| `tracks` | `work`(실무) · `c2`(컴활 2급) · `c1`(컴활 1급) 중 하나 이상 |
| `level` | 1 기초 · 2 응용 · 3 심화 |
| `title` | 짧은 제목 |
| `prompt` | 문제 문장. 줄바꿈 `\n` 가능 |
| `functions` | 관련 함수 이름 목록(함수 사전 링크) |
| `hint` | 힌트(선택) |
| `explain` | 해설(필수) — 왜 그렇게 쓰는지, 자주 하는 실수 |

## formula
| 필드 | 설명 |
|---|---|
| `sheet.base` | 공용 데이터 `content/datasets.json` 의 키: `sales`(A1:G16) · `emp`(A1:H13) · `score`(A1:G13) · `stock`(A1:G13). 없으면 빈 시트 |
| `sheet.cells` | 추가 셀 `{"J1": "지역", "J2": "서울", "K5": 3}`. 날짜는 `"@2026-03-01"` |
| `sheet.rows` + `sheet.at` | 표를 한 번에 놓기(`at` 왼쪽 위, 기본 A1) |
| `sheet.formats` | 표시 형식 `{"K": "#,##0", "L2": "yyyy-mm-dd"}` |
| `target` | 수식을 입력할 첫 셀 |
| `fill` | 채우기 범위(예 `"H2:H16"`). 있으면 첫 셀 수식을 복사해 모든 셀을 채점 → `$` 고정을 확인 |
| `answer` | 정답 수식(`=`로 시작) — 채점 기준 값은 이 수식의 계산 결과 |
| `alts` | 다른 정답 예시(모두 정답으로 채점돼야 함) |
| `wrong` | 흔한 오답(오답으로 채점돼야 함, 예: $ 빠진 수식) |
| `require` | 꼭 써야 하는 함수(값만 맞히는 우회 방지) |
| `spill_rows` | 동적 배열 결과가 몇 행으로 펼쳐지는지(격자 크기) |

규칙: 데이터 열 오른쪽(빈 열, 보통 I~N)에 조건·결과 칸을 둔다. 원본 데이터는 바꾸지 않는다.
TODAY() 는 2026-10-01 로 고정 계산된다.

## choice
| 필드 | 설명 |
|---|---|
| `options` | 보기 문자열 2~5개 |
| `answer` | 정답 보기 번호(0부터) |
| `sheet` | (선택) 문제 이해용 표 — formula 와 같은 형식 |

검사: `python tools/validate_content.py`
