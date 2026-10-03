import io
import json
import sqlite3
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import create_app


@pytest.fixture
def app(tmp_path):
    return create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'study.sqlite3')})


@pytest.fixture
def client(app):
    c = app.test_client()
    token = c.get('/api/bootstrap').json['csrf']
    c.environ_base['HTTP_X_CSRF_TOKEN'] = token
    return c


def start(c, **kwargs):
    r = c.post('/api/attempts', json={'round':59, 'subject':0, **kwargs})
    assert r.status_code == 201, r.json
    return r.json['id']


def keys(n, sub):
    with sqlite3.connect(ROOT / 'data/catalog.sqlite3') as db:
        return db.execute('SELECT id,correct FROM questions WHERE round=? AND subject=? ORDER BY number',(n,sub)).fetchall()


@pytest.mark.parametrize('n', range(59,66))
@pytest.mark.parametrize('sub', range(4))
def test_all_28_subject_tests_grade_from_official_key(client, n, sub):
    aid = start(client, round=n, subject=sub)
    questions = client.get(f'/api/attempts/{aid}').json['questions']
    assert len(questions)==30
    assert all('correct' not in q and 'is_correct' not in q for q in questions)
    for qid, correct in keys(n,sub):
        assert client.patch(f'/api/attempts/{aid}/answer',json={'qid':qid,'choice':correct}).status_code==200
    result = client.post(f'/api/attempts/{aid}/submit',json={}).json
    assert result['score']==100 and all(q['is_correct'] for q in result['questions'])
    assert not client.get('/api/wrong').json['items']


def test_resume_wrong_notes_and_retry(app, client):
    aid=start(client)
    client.patch(f'/api/attempts/{aid}/answer',json={'qid':59000,'choice':1,'flagged':True})
    resumed=client.get(f'/api/attempts/{aid}').json
    assert resumed['questions'][0]['choice']==1 and resumed['questions'][0]['flagged']
    first=client.post(f'/api/attempts/{aid}/submit',json={}).json
    second=client.post(f'/api/attempts/{aid}/submit',json={}).json
    assert first['submitted_at']==second['submitted_at'] and first['score']==0
    assert client.patch(f'/api/attempts/{aid}/answer',json={'qid':59000,'choice':3}).status_code==409
    assert len(client.get('/api/wrong').json['items'])==30
    assert client.put('/api/questions/59000/note',json={'body':'신용장 <script>alert(1)</script> 다시 보기','mastered':True}).status_code==200
    # New client and new app process must still see records, while API key is unrelated.
    fresh=create_app({'TESTING':True,'DATABASE':app.config['DATABASE']}).test_client()
    note=fresh.get('/api/questions/59000').json['note']
    assert note['mastered']==1 and '<script>' in note['body']
    retry=start(client,wrong=True,subject=0,mode='study')
    assert len(client.get(f'/api/attempts/{retry}').json['questions'])==29
    assert client.get('/api/dashboard').json['completed']==1
    backup=client.get('/api/backup')
    restored=sqlite3.connect(':memory:');restored.deserialize(backup.data)
    assert restored.execute('SELECT body FROM notes WHERE qid=59000').fetchone()[0]==note['body']


def test_expiry_and_validation(app,client):
    aid=start(client,minutes=30)
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute('UPDATE attempts SET deadline=? WHERE id=?',(time.time()-1,aid))
    assert client.patch(f'/api/attempts/{aid}/answer',json={'qid':59000,'choice':3}).status_code==409
    a=client.get(f'/api/attempts/{aid}').json
    assert a['submitted_at'] and a['score']==0
    assert client.post('/api/attempts',json={'round':59,'subject':9}).status_code==400
    b=start(client)
    assert client.patch(f'/api/attempts/{b}/answer',json={'qid':60000,'choice':1}).status_code==400
    assert client.patch(f'/api/attempts/{b}/answer',json={'qid':59000,'choice':True}).status_code==400
    assert client.patch(f'/api/attempts/{b}/answer',json={'qid':59000,'choice':5}).status_code==400


def test_csrf_origin_and_host(client):
    assert client.post('/api/attempts',json={},headers={'X-CSRF-Token':'bad'}).status_code==403
    assert client.post('/api/attempts',json={},headers={'Origin':'https://other.example'}).status_code==403
    assert client.get('/api/dashboard',base_url='http://evil.example').status_code==400


def test_shared_material_and_images(client):
    for qid in [59000,60012,62087,64090,64104,65118]:
        q=client.get(f'/api/questions/{qid}').json
        assert all(c in q['body'] for c in '①②③④')
        if qid==64090:
            assert q['context'] and q['context_images']
        for url in q['images']+q['context_images']:
            r=client.get(url)
            assert r.status_code==200 and r.data[:8]==b'\x89PNG\r\n\x1a\n'


def fake_response():
    return io.BytesIO(json.dumps({'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'핵심 개념: 테스트 해설입니다.'}]}]}).encode())


def test_ai_keys_private_cache_and_context(app,client):
    key='sk-fake-testing-only-1234567890'
    with patch('urllib.request.urlopen',side_effect=lambda *a,**kw:fake_response()) as mocked:
        res=client.post('/api/ai/settings',json={'key':key,'model':'gpt-4.1-mini'})
        assert res.status_code==200 and key not in res.text
        with client.session_transaction() as session:
            assert key not in str(dict(session))
        assert not app.test_client().get('/api/ai/settings').json['connected']
        exp=client.post('/api/questions/59000/explanation',json={})
        assert exp.status_code==200 and exp.json['status']=='AI 초안'
        sent=json.loads(mocked.call_args[0][0].data)
        assert sent['store'] is False
        assert '정답: 3번' in sent['input'][0]['content'][0]['text']
        assert sent['input'][0]['content'][1]['type']=='input_image'
        before=mocked.call_count
        assert client.post('/api/questions/59000/explanation',json={}).status_code==200
        assert mocked.call_count==before
        aid=start(client)
        assert client.post('/api/ai/chat',json={'qid':59000,'attempt_id':aid,'message':'힌트'}).status_code==409
        study=start(client,mode='study')
        assert client.post('/api/ai/chat',json={'qid':59000,'attempt_id':study,'message':'힌트'}).status_code==200
        sent=json.loads(mocked.call_args[0][0].data)
        assert '공식 정답표의 정답:' not in sent['input'][0]['content'][0]['text']
    assert key.encode() not in client.get('/api/backup').data
    assert client.delete('/api/ai/settings').status_code==200
    assert not client.get('/api/ai/settings').json['connected']
    assert client.post('/api/questions/59001/explanation',json={}).status_code==409


def test_full_round(client):
    aid=start(client,round=65,subject=-1)
    a=client.get(f'/api/attempts/{aid}').json
    assert len(a['questions'])==120 and len({q['subject'] for q in a['questions']})==4


def test_behind_https_proxy(tmp_path, monkeypatch):
    monkeypatch.setenv('TRADE_PROXY', '1')
    monkeypatch.setenv('RENDER_EXTERNAL_HOSTNAME', 'trade.onrender.com')
    app = create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'study.sqlite3')})
    c = app.test_client()
    fwd = {'X-Forwarded-Proto': 'https', 'X-Forwarded-For': '1.2.3.4'}
    base = 'http://trade.onrender.com'
    assert c.get('/healthz', base_url=base).json == {'ok': True}
    token = c.get('/api/bootstrap', base_url=base, headers=fwd).json['csrf']
    r = c.post('/api/attempts', base_url=base, json={'round': 59, 'subject': 0},
               headers={**fwd, 'X-CSRF-Token': token, 'Origin': 'https://trade.onrender.com'})
    assert r.status_code == 201
    assert c.get('/api/dashboard', base_url='http://evil.example', headers=fwd).status_code == 400
