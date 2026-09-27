# Render 등에서 `gunicorn wsgi:app` 실행 시 자동으로 읽는 설정.
# 음성 파일 만들기 작업 상태를 메모리에 두므로 워커는 1개, 긴 파일 재생(스트리밍)과
# 진행률 확인이 동시에 되도록 스레드 여러 개를 쓴다.
workers = 1
worker_class = "gthread"
threads = 8
timeout = 120
EOF
SP="C:/Users/user/AppData/Local/Temp/claude/c--Users-user-AX-VS-CODE/ef5e51ed-83a8-4332-9e5e-a0fbb48f84e0/scratchpad"; rm -f "$SP/ts_audio.db"; TS_DB_PATH="$SP/ts_audio.db" PYTHONIOENCODING=utf-8 python - <<'PYEOF'
import re, time
from app import create_app
from core import audio
c=create_app({'TESTING':True}).test_client()
tok=re.search(r'name="csrf-token" content="([^"]+)"', c.get('/').data.decode()).group(1)
t=time.time()
job=c.post('/api/vocab/audio/prepare', headers={'X-CSRF-Token':tok}, json={'level':3,'tier':'stretch','minutes':10,'repeats':3,'chunk':1}).json
while job['state']=='running':
    time.sleep(1); job=c.get(f"/api/vocab/audio/status/{job['key']}").json
print(job['state'], job.get('error'), 'build', round(time.time()-t,1),'s', job.get('minutes'),'min', job.get('mb'),'MB', job.get('name'))
r=c.get(job['download']); print('download', r.status_code, r.mimetype, len(r.data)//1024,'KB', r.headers['Content-Disposition'][:80])
r=c.get(job['url'], headers={'Range':'bytes=0-99'}); print('range', r.status_code, len(r.data))
