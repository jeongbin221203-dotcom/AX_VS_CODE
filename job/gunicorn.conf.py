# Render 등에서 `gunicorn wsgi:app` 실행 시 자동으로 읽는 설정.
# 정기 크롤링 예약 스레드가 앱 안에서 돌므로 워커는 1개 (여러 개면 DB 잠금 덕에 겹치진 않지만 의미 없이 늘어남).
# 화면 요청과 크롤링이 함께 돌도록 스레드를 여러 개 쓴다.
workers = 1
worker_class = "gthread"
threads = 8
timeout = 120
