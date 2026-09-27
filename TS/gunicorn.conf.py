# Render 등에서 `gunicorn wsgi:app` 실행 시 자동으로 읽는 설정.
# 음성 파일 만들기 작업 상태를 메모리에 두므로 워커는 1개, 긴 파일 재생(스트리밍)과
# 진행률 확인이 동시에 되도록 스레드 여러 개를 쓴다.
workers = 1
worker_class = "gthread"
threads = 8
timeout = 120
