# ERP 매핑 파일 (`MM_ERP_MODE=rest`)

SAP가 아닌 ERP(Oracle · Microsoft Dynamics · 더존 · 영림원 · 자체 ERP 등)의 REST API에 붙일 때 쓴다.
코드는 그대로 두고 이 JSON 파일에서 주소·필드 이름·코드 값만 회사 ERP에 맞춘다.

```bash
export MM_ERP_MODE=rest
export MM_ERP_REST_URL=https://erp.사내                       # https만 (같은 PC 테스트 서버 제외)
export MM_ERP_REST_MAP=erp_maps/우리회사.json                  # 이 폴더 기준 상대 경로도 됨
export MM_ERP_REST_AUTH=bearer:토큰                            # 또는 basic:아이디:비밀번호 / header:X-API-KEY:값
flask --app app erp test                                        # 연결 확인
```

## 항목

| 키 | 뜻 |
|---|---|
| `post` | 입고·출고·조정·이동 한 건 전기. `method`, `path`, `body` (body가 없으면 중립 JSON 그대로 보냄) |
| `cancel` | 취소 전기. `{originalDocument}`(원거래 ERP 문서번호), `{originalKey}`(원거래 멱등키) 사용 가능 |
| `lookup` | (선택) 재시도 전에 멱등키로 이미 전기된 문서를 찾는 요청. 찾으면 다시 보내지 않는다 |
| `document_field` · `year_field` | 응답에서 문서번호·연도를 꺼내는 점 경로 (`data.docNo`, `result.0.id`) |
| `success_field` · `error_field` | (선택) 응답 본문의 성공 여부·오류 문구 경로. 성공이 아니면 '실패(조치 필요)' |
| `headers` · `idempotency_header` | 모든 요청에 붙일 헤더, 멱등키 헤더 이름(기본 `Idempotency-Key`) |
| `ping` | 연결 확인 요청 |
| `master.materials` · `master.cost_centers` | 마스터 동기화. `list_field`(목록 경로)와 `fields`(이 시스템 항목 ← ERP 필드) |
| `stock` | 재고 대사용 ERP 재고. `{plants}` = 쉼표로 이은 플랜트 코드 |

## 값 틀

| 틀 | 결과 |
|---|---|
| `"{quantity}"` | 값 그대로 (숫자는 숫자로) |
| `"MM {reference}"` | 문자열에 끼워 넣기 (경로에서는 URL 인코딩) |
| `{"$map": "movementType", "values": {"101": "PO-GR"}, "default": "ETC"}` | 코드 변환 |
| `{"$date": "postingDate", "format": "%Y%m%d"}` | 날짜 형식 |
| `{"field": "useYn", "equals": "N"}` | (마스터·재고 응답에서) 참/거짓으로 읽기 |

## 쓸 수 있는 값 (중립 JSON)

`idempotencyKey` `action`(POST/CANCEL) `postingDate` `documentDate` `transactionType`(IN/OUT/ADJ) `movementType`(101·201…)
`material`(ERP 품목코드 = 자재 마스터의 SAP자재번호 칸) `itemCode`(이 시스템 자재코드) `plant` `storageLocation` `warehouseCode`
`quantity` `unit` `batch` `purchaseOrder` `purchaseOrderItem` `costCenter` `receivingPlant` `receivingStorageLocation`
`transferNo` `reference` `headerText` `enteredBy` · 취소: `originalKey` `originalDocument` `originalYear` `reason`

마스터 항목: `material` `description` `unit` `materialGroup` `standardPrice` `deleted` `batchManaged` `shelfLifeManaged` ·
원가센터 `costCenter` `name` `active` · 재고 `material` `plant` `storageLocation` `quantity`

`generic_example.json`은 형식 예시다(특정 제품의 실제 API가 아님). 실제 필드 이름은 ERP 담당 팀의 API 문서로 맞춘다.
