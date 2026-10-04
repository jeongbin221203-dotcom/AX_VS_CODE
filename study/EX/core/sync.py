"""학습 기록 맞추기: PC(원본) ↔ 배포 서버(개인 링크 기기 'owner').

무료 서버는 다시 시작하면 기록이 지워지므로, PC 앱이 주기적으로 자기 기록을 보내고(POST /api/sync)
서버는 그것을 'owner' 기록에 합친 뒤 합친 결과를 돌려준다. PC 는 받은 것을 자기 기록에 합친다.
- 풀이·실습·모의고사 기록: 같은 행(같은 문제·시각·결과)이 없을 때만 더한다(지우지 않음).
- 별표: 나중에 바꾼 쪽(stars_at)의 별표 목록을 따른다.
- 인증: 개인 링크 토큰(EX_OWNER_TOKEN, PC 는 data/owner.token)을 Bearer 로.
"""
import gzip
import json
import logging
import threading
import time
import urllib.request
from pathlib import Path

from . import backup

log = logging.getLogger(__name__)

KEYS = {'attempts': ('pid', 'created_at', 'ok', 'answer'),
        'build_results': ('task', 'created_at', 'score', 'file_name'),
        'exam_results': ('exam', 'created_at', 'score', 'file_name')}
MAX_BODY = 40 * 1024 * 1024            # 압축을 푼 크기


def merge(conn, user, data):
    """data(export 형식)를 user 기록에 합친다 → 새로 더한 행 수."""
    if not isinstance(data, dict) or data.get('version') != backup.VERSION:
        raise ValueError('기록 형식이 아닙니다.')
    added = {}
    for t, key in KEYS.items():
        cols = backup.COLS[t]
        have = {tuple(r) for r in conn.execute(f"SELECT {', '.join(key)} FROM {t} WHERE user=?", (user,))}
        n = 0
        rows = data.get(t) or []
        for row in rows[-backup.LIMITS[t]:] if isinstance(rows, list) else []:
            vals = backup.clean_row(t, row)
            if vals is None:
                continue
            k = tuple(vals[cols.index(c)] for c in key)
            if k in have:
                continue
            conn.execute(f"INSERT INTO {t}({', '.join(cols)}, user) VALUES({', '.join('?' for _ in cols)}, ?)",
                         (*vals, user))
            have.add(k)
            n += 1
        added[t] = n
    theirs = data.get('stars_at') or ''
    mine = backup.raw_setting(conn, user, 'stars_at', '') or ''
    stars = data.get('stars') if isinstance(data.get('stars'), list) else []
    if isinstance(theirs, str) and theirs > mine:
        conn.execute('DELETE FROM stars WHERE user=?', (user,))
        for row in stars:
            vals = backup.clean_row('stars', row)
            if vals is not None:
                conn.execute('INSERT OR IGNORE INTO stars(pid, created_at, user) VALUES(?, ?, ?)', (*vals, user))
        backup.set_raw_setting(conn, user, 'stars_at', theirs)
        added['stars'] = len(stars)
    elif not mine and stars:                         # 별표를 한 번도 바꾼 적 없는 쪽: 합치기만
        for row in stars:
            vals = backup.clean_row('stars', row)
            if vals is not None:
                conn.execute('INSERT OR IGNORE INTO stars(pid, created_at, user) VALUES(?, ?, ?)', (*vals, user))
    if data.get('track') in ('work', 'c2', 'c1') and not backup.raw_setting(conn, user, 'track', ''):
        backup.set_raw_setting(conn, user, 'track', data['track'])
    conn.commit()
    return added


def pack(data):
    return gzip.compress(json.dumps(data, ensure_ascii=False).encode('utf-8'))


def unpack(blob):
    if blob[:2] != b'\x1f\x8b':                      # 중간 프록시(Render)가 압축을 풀어 보낸 경우
        raw = blob[:MAX_BODY + 1]
    else:
        raw = gzip.GzipFile(fileobj=__import__('io').BytesIO(blob)).read(MAX_BODY + 1)
    if len(raw) > MAX_BODY:
        raise ValueError('기록이 너무 큽니다.')
    return json.loads(raw.decode('utf-8'))


# ---------------------------------------------------------------- PC 쪽
def config(data_dir):
    """data/sync.json {"url": "https://..."} + data/owner.token 이 있으면 (url, token)."""
    p, t = Path(data_dir) / 'sync.json', Path(data_dir) / 'owner.token'
    if not p.exists() or not t.exists():
        return None
    try:
        url = json.loads(p.read_text(encoding='utf-8')).get('url', '').rstrip('/')
    except (ValueError, OSError):
        return None
    token = t.read_text(encoding='utf-8').strip()
    return (url, token) if url.startswith('https://') and token else None


def sync_once(db_path, url, token, timeout=120):
    """PC 기록을 보내고, 서버가 합친 기록을 받아 PC 에 합친다 → {'sent': .., 'got': ..}"""
    from .db import connect
    conn = connect(db_path)
    try:
        mine = backup.export(conn, '')
        req = urllib.request.Request(url + '/api/sync', data=pack(mine), method='POST', headers={
            'Authorization': f'Bearer {token}', 'Content-Type': 'application/json', 'Content-Encoding': 'gzip',
            'Accept-Encoding': 'gzip'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(MAX_BODY + 1)
        theirs = unpack(body)
        got = merge(conn, '', theirs.get('data') or {})
        return {'server_added': theirs.get('added'), 'pc_added': got}
    finally:
        conn.close()


def start(app, every=1800, first=20):
    """PC 앱: 켠 뒤 first 초, 그 뒤 every 초마다 맞추기(설정이 없으면 아무것도 안 함)."""
    cfg = config(app.config['DATA_DIR'])
    if not cfg or app.config.get('PUBLIC'):
        return None

    def loop():
        time.sleep(first)
        while True:
            try:
                res = sync_once(app.config['DATABASE'], *cfg)
                log.warning('기록 맞추기 완료: %s', res)
                _note(app, f"{time.strftime('%Y-%m-%d %H:%M')} 완료")
            except Exception as e:  # noqa: BLE001 — 서버가 잠들었거나 인터넷이 끊겨도 다음에 다시
                log.warning('기록 맞추기 실패: %s', e)
                _note(app, f"{time.strftime('%Y-%m-%d %H:%M')} 실패: {type(e).__name__}")
            time.sleep(every)
    th = threading.Thread(target=loop, name='ex-sync', daemon=True)
    th.start()
    return th


def _note(app, text):
    from .db import connect
    conn = connect(app.config['DATABASE'])
    try:
        backup.set_raw_setting(conn, '', 'sync_last', text)
        conn.commit()
    finally:
        conn.close()
