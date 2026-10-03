# 제3자 소프트웨어 고지

이 시스템은 아래 오픈소스를 사용한다. 버전은 `requirements.lock` 기준(2026-09-27 검증).
라이선스 원문은 각 패키지 배포본(설치 폴더의 LICENSE 파일) 또는 아래 링크에 있다.

## 파이썬 패키지 (pip로 설치, 소스에 포함하지 않음)

| 패키지 | 버전 | 라이선스 | 용도 |
|---|---|---|---|
| Flask / Werkzeug / Jinja2 | 3.0.0 / 3.1.8 / 3.1.6 | BSD-3-Clause | 웹 프레임워크 |
| pandas / NumPy | 3.0.5 / 2.5.3 | BSD-3-Clause (NumPy 일부 0BSD·MIT·Zlib) | 표 계산 |
| openpyxl | 3.1.5 | MIT | 엑셀 읽기·쓰기 |
| defusedxml | 0.7.1 | PSF License | 엑셀 XML 공격 방어 |
| cryptography | 50.0.1 | Apache-2.0 또는 BSD-3-Clause | joserfc(SSO 토큰 서명 검증)가 사용 |
| joserfc | 1.7.5 | BSD-3-Clause | 사내 SSO ID 토큰 검증 |
| **psycopg / psycopg-binary / psycopg-pool** | 3.3.6 / 3.3.6 / 3.3.3 | **LGPL-3.0-only** | PostgreSQL 연결 (여러 서버 운영 시) |
| boto3 | 1.43.103 | Apache-2.0 | S3 호환 파일 저장소 (선택) |
| waitress | 3.0.2 | ZPL 2.1 | 운영 WSGI 서버 |
| pytest · moto (개발용) | 9.1.1 · 5.2.3 | MIT · Apache-2.0 | 테스트 (운영 배포에는 넣지 않음) |

**LGPL 참고**: psycopg는 수정하지 않고 별도 설치되는 라이브러리로 동적 사용한다. 이 경우 LGPL은 이 시스템 소스 공개를
요구하지 않는 것이 일반적 해석이나, 배포 형태(컨테이너 이미지에 포함 등)에 따라 사내 법무 검토를 받는다.
SQLite만 쓰는 한 서버 구성에서는 psycopg가 필요 없다.

## 소스에 포함된 자바스크립트 (static/vendor/)

### Chart.js 4.4.1 — MIT License
https://github.com/chartjs/Chart.js · Copyright (c) 2014-2022 Chart.js Contributors

MIT License 전문:

```
Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
documentation files (the "Software"), to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of
the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```
