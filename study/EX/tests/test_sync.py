"""PC ↔ 배포 서버 학습 기록 맞추기(core/sync.py, POST /api/sync)."""
import json

import pytest

from app import create_app
from core import backup, db, sync


def _app(tmp_path, name, **extra):
    d = tmp_path / name
    return create_app({'DATA_DIR': str(d), 'DATABASE': str(d / 'ex.db'), 'SECRET_KEY': 'test', 'TESTING': True,
                       **extra})


def _add(app, user, pid, ok, at, star=None, stars_at=None):
    conn = db.connect(app.config['DATABASE'])
    conn.execute('INSERT INTO attempts(pid, category, ok, answer, created_at, user) VALUES(?, ?, ?, ?, ?, ?)',
                 (pid, 'basic', ok, '=A1', at, user))
    if star:
        conn.execute('INSERT OR IGNORE INTO stars(pid, user) VALUES(?, ?)', (star, user))
    if stars_at:
        backup.set_raw_setting(conn, user, 'stars_at', stars_at)
    conn.commit()
    conn.close()


def _export(app, user):
    conn = db.connect(app.config['DATABASE'])
    try:
        return backup.export(conn, user)
    finally:
        conn.close()


@pytest.fixture
def server(tmp_path):
    return _app(tmp_path, 'server', PUBLIC=True, OWNER_TOKEN='tok-123')


def _post(server, data, token='tok-123'):
    return server.test_client().post('/api/sync', data=sync.pack(data), headers={
        'Authorization': f'Bearer {token}', 'Content-Encoding': 'gzip'})


def test_sync_needs_token_and_public(server, tmp_path):
    assert _post(server, {'version': 1}, token='wrong').status_code == 404
    pc = _app(tmp_path, 'pc', OWNER_TOKEN='tok-123')
    assert pc.test_client().post('/api/sync', data=sync.pack({'version': 1}), headers={
        'Authorization': 'Bearer tok-123', 'Content-Encoding': 'gzip'}).status_code == 404
    assert _post(server, {'version': 99}).status_code == 400


def test_round_trip_merges_both_sides_without_duplicates(server, tmp_path):
    pc = _app(tmp_path, 'pc')
    _add(pc, '', 'basic-001', 1, '2026-10-04 09:00:00')
    _add(server, 'owner', 'basic-002', 0, '2026-10-04 10:00:00')
    _add(server, 'stranger', 'basic-003', 1, '2026-10-04 10:00:00')     # 다른 방문자 기록은 섞이지 않음
    for _ in range(2):                                                    # 두 번 맞춰도 늘어나지 않음
        r = _post(server, _export(pc, ''))
        assert r.status_code == 200
        back = sync.unpack(r.data)
        conn = db.connect(pc.config['DATABASE'])
        sync.merge(conn, '', back['data'])
        conn.close()
    pids = lambda app, u: sorted(x['pid'] for x in _export(app, u)['attempts'])  # noqa: E731
    assert pids(pc, '') == ['basic-001', 'basic-002']
    assert pids(server, 'owner') == ['basic-001', 'basic-002']
    assert pids(server, 'stranger') == ['basic-003']


def test_stars_follow_the_side_changed_last(server, tmp_path):
    pc = _app(tmp_path, 'pc')
    _add(pc, '', 'basic-001', 1, '2026-10-04 09:00:00', star='basic-001', stars_at='2026-10-04T09:00:00')
    _add(server, 'owner', 'basic-002', 1, '2026-10-04 09:00:00', star='basic-002', stars_at='2026-10-04T11:00:00')
    back = sync.unpack(_post(server, _export(pc, '')).data)
    conn = db.connect(pc.config['DATABASE'])
    sync.merge(conn, '', back['data'])
    conn.close()
    assert [s['pid'] for s in _export(pc, '')['stars']] == ['basic-002']
    assert [s['pid'] for s in _export(server, 'owner')['stars']] == ['basic-002']


def test_bad_rows_and_gzip_bomb_rejected(server):
    data = {'version': 1, 'attempts': [{'pid': ['x'], 'category': 'basic', 'ok': 1}, 'junk']}
    r = _post(server, data)
    assert r.status_code == 200 and sync.unpack(r.data)['added']['attempts'] == 0
    bomb = __import__('gzip').compress(b'[' + b'0,' * (sync.MAX_BODY // 2 + 10) + b'0]')
    r = server.test_client().post('/api/sync', data=bomb, headers={
        'Authorization': 'Bearer tok-123', 'Content-Encoding': 'gzip'})
    assert r.status_code == 400


def test_pc_config(tmp_path):
    assert sync.config(tmp_path) is None
    (tmp_path / 'owner.token').write_text('abc', encoding='utf-8')
    (tmp_path / 'sync.json').write_text(json.dumps({'url': 'http://x'}), encoding='utf-8')
    assert sync.config(tmp_path) is None                                 # https 만
    (tmp_path / 'sync.json').write_text(json.dumps({'url': 'https://x.onrender.com/'}), encoding='utf-8')
    assert sync.config(tmp_path) == ('https://x.onrender.com', 'abc')
