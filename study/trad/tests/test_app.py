"""서버 테스트. 시험 기록·채점 흐름은 브라우저 쪽이라 tests/store.test.js (Node)가 맡는다 — 아래 마지막 테스트가 실행한다."""
import io
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import create_app


@pytest.fixture
def app():
    return create_app({'TESTING': True})


@pytest.fixture
def client(app):
    c = app.test_client()
    token = c.get('/api/bootstrap').json['csrf']
    c.environ_base['HTTP_X_CSRF_TOKEN'] = token
    return c


def keys(n=None, sub=None):
    """(id, correct) straight from the catalog file, independent of the endpoints under test."""
    sql, args = 'SELECT id,correct FROM questions', []
    if n is not None:
        sql += ' WHERE round=? AND subject=?'
        args = [n, sub]
    with sqlite3.connect(ROOT / 'data/catalog.sqlite3') as db:
        return db.execute(sql + ' ORDER BY id' if n is None else sql + ' ORDER BY number', args).fetchall()


def fake_response():
    return io.BytesIO(json.dumps({'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '핵심 개념: 테스트 해설입니다.'}]}]}).encode())


def test_catalog_lists_840_questions_without_the_answer_key(client):
    boot = client.get('/api/bootstrap').json
    r = client.get(f"/api/catalog?v={boot['catalog_v']}")
    items = r.json['items']
    assert len(items) == 840
    assert all('correct' not in q and 'parts' not in q for q in items)
    assert {'id', 'round', 'subject', 'number', 'title', 'body', 'context', 'page', 'subject_name', 'images', 'context_images', 'source'} <= set(items[0])
    groups = {}
    for q in items:
        groups.setdefault((q['round'], q['subject']), []).append(q['number'])
    assert len(groups) == 28 and all(len(v) == 30 for v in groups.values())
    assert 'immutable' in r.headers['Cache-Control']                       # 버전이 붙은 주소는 브라우저가 계속 보관
    assert 'immutable' not in client.get('/api/catalog').headers['Cache-Control']


def test_answers_match_the_official_key_and_reject_bad_requests(client):
    rows = keys()
    assert len(rows) == 840
    for i in range(0, 840, 120):
        chunk = rows[i:i + 120]
        got = client.post('/api/answers', json={'qids': [q for q, _ in chunk]}).json['answers']
        assert got == {str(q): c for q, c in chunk}
    post = lambda body: client.post('/api/answers', json=body).status_code
    assert post({}) == 400 and post({'qids': []}) == 400
    assert post({'qids': [q for q, _ in rows[:121]]}) == 400
    assert post({'qids': [59120]}) == 400 and post({'qids': ['59000']}) == 400 and post({'qids': [True]}) == 400
    assert post({'qids': [59000, 59000]}) == 200                              # 겹쳐 물어도 한 번만 답함
    assert client.post('/api/answers', json={'qids': [59000]}, headers={'X-CSRF-Token': 'bad'}).status_code == 403


def test_csrf_origin_and_host(client):
    assert client.post('/api/answers', json={'qids': [59000]}, headers={'X-CSRF-Token': 'bad'}).status_code == 403
    assert client.post('/api/answers', json={'qids': [59000]}, headers={'Origin': 'https://other.example'}).status_code == 403
    assert client.get('/api/catalog', base_url='http://evil.example').status_code == 400


def test_the_server_keeps_no_study_records(client):
    for url in ['/api/dashboard', '/api/wrong', '/api/backup', '/api/attempts/abc', '/api/questions/59000']:
        assert client.get(url).status_code == 404
    assert client.post('/api/attempts', json={'round': 59, 'subject': 0}).status_code == 404
    assert client.put('/api/questions/59000/note', json={'body': 'x'}).status_code in (404, 405)


def test_shared_material_and_images(client):
    items = {q['id']: q for q in client.get('/api/catalog').json['items']}
    for qid in [59000, 60012, 62087, 64090, 64104, 65118]:
        q = items[qid]
        assert all(c in q['body'] for c in '①②③④')
        if qid == 64090:
            assert q['context'] and q['context_images']
        for url in q['images'] + q['context_images']:
            r = client.get(url)
            assert r.status_code == 200 and r.data[:8] == b'\x89PNG\r\n\x1a\n'
    assert client.get('/sources/59/questions').mimetype == 'application/pdf'
    assert client.get('/sources/66/questions').status_code == 404


def test_ai_key_stays_on_the_server_and_explanations_are_returned_not_stored(app, client):
    key = 'sk-fake-testing-only-1234567890'
    with patch('urllib.request.urlopen', side_effect=lambda *a, **kw: fake_response()) as mocked:
        res = client.post('/api/ai/settings', json={'key': key, 'model': 'gpt-4.1-mini'})
        assert res.status_code == 200 and key not in res.text
        with client.session_transaction() as session:
            assert key not in str(dict(session))
        assert not app.test_client().get('/api/ai/settings').json['connected']
        exp = client.post('/api/questions/59000/explanation', json={})
        assert exp.status_code == 200 and exp.json['status'] == 'AI 초안' and exp.json['body'].startswith('핵심 개념')
        assert exp.json['model'] == 'gpt-4.1-mini' and exp.json['created_at'] > 0
        sent = json.loads(mocked.call_args[0][0].data)
        assert sent['store'] is False
        assert '정답: 3번' in sent['input'][0]['content'][0]['text']
        assert sent['input'][0]['content'][1]['type'] == 'input_image'
        before = mocked.call_count
        assert client.post('/api/questions/59000/explanation', json={}).status_code == 200
        assert mocked.call_count == before + 1                                # 저장은 브라우저가 한다 — 서버는 매번 새로 만든다
        # 제출 전 학습 연습: 정답을 빼고 힌트만
        assert client.post('/api/ai/chat', json={'qid': 59000, 'message': '힌트', 'reveal': False}).status_code == 200
        sent = json.loads(mocked.call_args[0][0].data)
        assert '공식 정답표의 정답:' not in sent['input'][0]['content'][0]['text'] and '힌트' in sent['instructions']
        assert client.post('/api/ai/chat', json={'qid': 59000, 'message': '설명', 'reveal': True}).status_code == 200
        assert '정답: 3번' in json.loads(mocked.call_args[0][0].data)['input'][0]['content'][0]['text']
        assert client.post('/api/ai/chat', json={'qid': 59000, 'message': '설명'}).status_code == 200   # reveal 없으면 숨김
        assert '공식 정답표의 정답:' not in json.loads(mocked.call_args[0][0].data)['input'][0]['content'][0]['text']
        assert client.post('/api/ai/chat', json={'qid': 1, 'message': 'x', 'reveal': True}).status_code == 400
        assert client.post('/api/ai/chat', json={'message': 'x', 'history': [{'role': 'system', 'content': 'x'}]}).status_code == 400
    assert client.delete('/api/ai/settings').status_code == 200
    assert not client.get('/api/ai/settings').json['connected']
    assert client.post('/api/questions/59001/explanation', json={}).status_code == 409


def test_behind_https_proxy(monkeypatch):
    monkeypatch.setenv('TRADE_PROXY', '1')
    monkeypatch.setenv('RENDER_EXTERNAL_HOSTNAME', 'trade.onrender.com')
    app = create_app({'TESTING': True})
    c = app.test_client()
    fwd = {'X-Forwarded-Proto': 'https', 'X-Forwarded-For': '1.2.3.4'}
    base = 'http://trade.onrender.com'
    assert c.get('/healthz', base_url=base).json == {'ok': True}
    token = c.get('/api/bootstrap', base_url=base, headers=fwd).json['csrf']
    r = c.post('/api/answers', base_url=base, json={'qids': [59000]},
               headers={**fwd, 'X-CSRF-Token': token, 'Origin': 'https://trade.onrender.com'})
    assert r.status_code == 200 and r.json['answers'] == {'59000': 3}
    assert c.get('/api/catalog', base_url='http://evil.example', headers=fwd).status_code == 400


# ---------- 예전 서버 방식(study-backup.sqlite3) 백업을 브라우저 기록으로 바꾸기 ----------
OLD_SCHEMA = '''
CREATE TABLE attempts (id TEXT PRIMARY KEY, title TEXT NOT NULL, round INTEGER, subject INTEGER, mode TEXT NOT NULL,
 started_at REAL NOT NULL, deadline REAL, submitted_at REAL, score REAL, last_index INTEGER NOT NULL DEFAULT 0);
CREATE TABLE responses (attempt_id TEXT NOT NULL, qid INTEGER NOT NULL, position INTEGER NOT NULL,
 choice INTEGER CHECK(choice BETWEEN 1 AND 4), flagged INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(attempt_id,qid));
CREATE TABLE notes (qid INTEGER PRIMARY KEY, body TEXT NOT NULL DEFAULT '', mastered INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL);
CREATE TABLE explanations (qid INTEGER PRIMARY KEY, body TEXT NOT NULL, model TEXT NOT NULL, created_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'AI 초안');
'''


def make_old_backup(path, bad_qid=None):
    db = sqlite3.connect(path)
    db.executescript(OLD_SCHEMA)
    rows = keys(59, 0)
    db.execute("INSERT INTO attempts VALUES('old1','제59회 무역규범',59,0,'exam',1000.0,NULL,1100.0,90.0,5)")
    for i, (qid, correct) in enumerate(rows):
        db.execute('INSERT INTO responses VALUES(?,?,?,?,?)', ('old1', qid, i, correct if i < 27 else correct % 4 + 1, int(i == 2)))
    db.execute("INSERT INTO attempts VALUES('old2','제60회 무역결제',60,1,'study',2000.0,4000000000.0,NULL,NULL,1)")
    for i, (qid, _) in enumerate(keys(60, 1)[:3]):
        db.execute('INSERT INTO responses VALUES(?,?,?,?,0)', ('old2', qid, i, None))
    db.execute("INSERT INTO notes VALUES(?, '옛 메모', 1, 5.0)", (rows[28][0],))
    db.execute("INSERT INTO explanations VALUES(?, '옛 해설', 'gpt-4.1-mini', 6.0, 'AI 초안')", (rows[28][0],))
    if bad_qid:
        db.execute("INSERT INTO notes VALUES(?, 'x', 0, 1.0)", (bad_qid,))
    db.commit()
    db.close()
    return Path(path).read_bytes()


def test_old_sqlite_backup_converts_to_browser_records(client, tmp_path):
    blob = make_old_backup(tmp_path / 'old.sqlite3')
    r = client.post('/api/convert-backup', data=blob, content_type='application/octet-stream')
    assert r.status_code == 200
    j = r.json
    assert j['app'] == 'trade-study' and j['version'] == 1
    done, going = j['attempts']['old1'], j['attempts']['old2']
    assert len(done['items']) == 30 and done['score'] == 90.0 and done['last_index'] == 5 and done['submitted_at'] == 1100.0
    assert [i['qid'] for i in done['items']] == [q for q, _ in keys(59, 0)]
    assert done['items'][2]['flagged'] is True and done['items'][3]['flagged'] is False
    assert done['correct'] == {str(q): c for q, c in keys(59, 0)}
    assert going['submitted_at'] is None and 'correct' not in going and going['deadline'] == 4000000000.0
    assert all(i['choice'] is None for i in going['items'])
    qid = keys(59, 0)[28][0]
    assert j['notes'][str(qid)] == {'body': '옛 메모', 'mastered': 1, 'updated_at': 5.0}
    assert j['explanations'][str(qid)]['body'] == '옛 해설'


def test_old_backup_conversion_rejects_bad_files(client, tmp_path):
    post = lambda data: client.post('/api/convert-backup', data=data, content_type='application/octet-stream')
    assert post(b'not a database').status_code == 400
    other = sqlite3.connect(':memory:')
    other.execute('CREATE TABLE notes(qid, body)')
    assert post(other.serialize()).status_code == 400
    assert post(make_old_backup(tmp_path / 'bad.sqlite3', bad_qid=1)).status_code == 400   # 이 앱의 문제가 아님
    assert client.post('/api/convert-backup', data=b'SQLite format 3\x00', content_type='application/octet-stream',
                       headers={'X-CSRF-Token': 'bad'}).status_code == 403
    assert post(b'SQLite format 3\x00' + b'0' * (51 * 1024 * 1024)).status_code == 413


# ---------- 브라우저 쪽 기록 저장소(static/store.js) — 실제 문항·정답으로 Node 테스트 실행 ----------
@pytest.mark.skipif(shutil.which('node') is None, reason='Node.js 가 필요합니다')
def test_browser_store_in_node(client, tmp_path):
    (tmp_path / 'catalog.json').write_text(client.get('/api/catalog').text, encoding='utf-8')
    (tmp_path / 'answers.json').write_text(json.dumps({str(q): c for q, c in keys()}), encoding='utf-8')
    old = client.post('/api/convert-backup', data=make_old_backup(tmp_path / 'old.sqlite3'), content_type='application/octet-stream')
    (tmp_path / 'converted.json').write_text(old.text, encoding='utf-8')
    run = subprocess.run(['node', '--test', str(ROOT / 'tests' / 'store.test.js')], capture_output=True, text=True, encoding='utf-8',
                         env={**__import__('os').environ, 'FIXTURE_DIR': str(tmp_path)})
    assert run.returncode == 0, run.stdout[-4000:] + run.stderr[-2000:]


# ---------- 서버 점검(2026-10-10)에서 찾은 문제의 재발 방지 ----------
def test_convert_backup_rejects_views_and_oversized_tables(client, tmp_path):
    """뷰를 표로 위장해 서버가 계속 행을 만들게 하는 파일(작은 크기로 서버를 붙잡음)을 즉시 거부."""
    post = lambda blob: client.post('/api/convert-backup', data=blob, content_type='application/octet-stream')
    evil = tmp_path / 'evil.sqlite3'
    db = sqlite3.connect(evil)
    db.executescript('''
    CREATE VIEW attempts AS WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c)
      SELECT 'a'||x AS id, 't' AS title, 59 AS round, 0 AS subject, 'exam' AS mode, 1.0 AS started_at, NULL AS deadline, NULL AS submitted_at, NULL AS score, 0 AS last_index FROM c;
    CREATE VIEW responses AS SELECT 'a1' AS attempt_id, 59000 AS qid, 0 AS position, NULL AS choice, 0 AS flagged;
    CREATE VIEW notes AS SELECT 59000 AS qid, '' AS body, 0 AS mastered, 1.0 AS updated_at WHERE 0;
    CREATE VIEW explanations AS SELECT 59000 AS qid, '' AS body, 'm' AS model, 1.0 AS created_at, 'x' AS status WHERE 0;''')
    db.commit(); db.close()
    started = time.monotonic()
    assert post(evil.read_bytes()).status_code == 400
    assert time.monotonic() - started < 2                                      # 끝없는 뷰를 읽지 않고 바로 거부
    many = tmp_path / 'many.sqlite3'
    db = sqlite3.connect(many); db.executescript(OLD_SCHEMA)
    db.executemany('INSERT OR IGNORE INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?)', [(f'a{i}', 't', 59, 0, 'exam', 1.0, None, None, None, 0) for i in range(2001)])
    db.commit(); db.close()
    assert post(many.read_bytes()).status_code == 400                          # 시험 기록 2,001개 > 한도 2,000
    ok = make_old_backup(tmp_path / 'ok.sqlite3')
    assert post(ok).status_code == 200                                         # 정상 백업은 그대로


def test_huge_question_numbers_are_404_not_500(client):
    for url in ['/question-image/99999999999999999999/body/0.png', '/question-image/59999/body/0.png']:
        assert client.get(url).status_code == 404
    assert client.post('/api/questions/99999999999999999999/explanation', json={}).status_code == 404


def test_openai_connection_failures_and_odd_replies_are_502_json(client):
    import http.client
    key = 'sk-fake-testing-only-1234567890'
    with patch('urllib.request.urlopen', side_effect=lambda *a, **k: fake_response()):
        assert client.post('/api/ai/settings', json={'key': key, 'model': 'gpt-4.1-mini'}).status_code == 200
    for exc in [http.client.IncompleteRead(b'x'), http.client.BadStatusLine('x'), http.client.RemoteDisconnected('closed'), ConnectionResetError('reset')]:
        with patch('urllib.request.urlopen', side_effect=exc):
            r = client.post('/api/ai/chat', json={'qid': 59000, 'message': '질문', 'reveal': True})
            assert r.status_code == 502 and r.json['error'].startswith('AI 서버에 연결하지 못했습니다'), type(exc).__name__
    for body in [b'[]', b'null', b'{"output":null}', b'{"output":[null]}', b'{"output":[{"type":"message","content":null}]}',
                 b'{"output":[{"type":"message","content":[{"type":"output_text","text":null}]}]}', b'{"output":[]}', b'']:
        with patch('urllib.request.urlopen', side_effect=lambda *a, b=body, **k: io.BytesIO(b)):
            r = client.post('/api/questions/59000/explanation', json={})
            assert r.status_code == 502 and r.is_json, body


def test_long_conversations_fit_and_oversized_ones_get_a_korean_message(client):
    key = 'sk-fake-testing-only-1234567890'
    with patch('urllib.request.urlopen', side_effect=lambda *a, **k: fake_response()):
        client.post('/api/ai/settings', json={'key': key, 'model': 'gpt-4.1-mini'})
        # 규칙상 허용되는 가장 긴 대화: 12개 × 한글 12,000자(브라우저는 한글을 3바이트로 보냄, 약 430KB)
        history = [{'role': 'user' if i % 2 == 0 else 'assistant', 'content': '가' * 12000} for i in range(12)]
        body = json.dumps({'qid': 59000, 'message': '질문', 'history': history, 'reveal': True}, ensure_ascii=False).encode('utf-8')
        assert 400_000 < len(body) < 500_000
        assert client.post('/api/ai/chat', data=body, content_type='application/json').status_code == 200
        huge = json.dumps({'message': 'x' * 1_100_000}).encode()
        r = client.post('/api/ai/chat', data=huge, content_type='application/json')
        assert r.status_code == 413 and '너무 커요' in r.json['error']


def test_error_messages_are_korean_json_and_unexpected_errors_do_not_leak(client, app):
    assert client.get('/api/nope').json['error'] == '찾을 수 없습니다.'
    assert client.post('/healthz', json={}).status_code == 405 and client.post('/healthz', json={}).json['error'] == '허용되지 않는 요청 방식입니다.'
    assert client.post('/api/answers', data='{"qids":[59000]}' + ' ' * 150_000, content_type='application/json').json['error'].startswith('보낸 내용이 너무 커요')
    assert client.post('/api/answers', json={'qids': []}).json['error'] == '문제 번호를 확인해 주세요.'     # 직접 쓴 문구는 그대로
    app.config['PROPAGATE_EXCEPTIONS'] = False
    with patch('fitz.open', side_effect=RuntimeError('내부 경로 C:/secret/path 가 보이면 안 됨')):
        r = client.get('/question-image/59000/body/0.png')
    assert r.status_code == 500 and r.is_json and 'secret' not in r.text and '문제가 생겼어요' in r.json['error']
