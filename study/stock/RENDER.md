# 휴대폰용 오늘의 신호 (Render)

- 주소: Render 서비스 `stock` (브랜치 `stock`, rootDir `study/stock`, 푸시하면 자동 배포). 접속하면 비밀번호 창이 뜬다(아이디는 아무거나, 비밀번호는 서비스 환경변수 `STOCK_SNAPSHOT_PASSWORD`).
- 올라가는 것은 `snapshot_app.py` 와 `snapshot/` 의 JSON 뿐이다. DB·AI 모델·차트 화면은 PC 에만 있다(데이터 약 660MB, 거래 키).
- 갱신: PC 에서 `python manage.py publish-snapshot` — 최신 신호를 `snapshot/signals.json` 과 `snapshot/days/날짜.json` 에 넣고 `stock` 브랜치에 커밋·푸시한다.
  `.env` 에 `STOCK_PUBLISH=1` 이 있으면 평일 16:10 일봉 갱신·신호 훑기 뒤에 자동으로 한다(PC 와 `python app.py` 가 켜져 있어야 함).
- 날짜별 보관은 최근 90거래일. 무료 플랜은 15분 쓰지 않으면 잠들어 첫 접속에 20~50초 걸린다.
