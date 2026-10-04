"""컴활 필기: 문제 은행 검사, 모의고사 구성, 과락·합격, 화면·기록."""
import re

from core import written


def test_bank_is_valid_and_balanced():
    for subj in written.SUBJECTS:
        qs = written.questions(subj)
        assert len(qs) >= 100, subj
        pos = [q['answer'] for q in qs]
        assert min(pos.count(i) for i in range(4)) >= len(qs) * 0.18, subj      # 정답 위치가 한쪽에 몰리지 않게
        longest = sum(1 for q in qs if max(range(4), key=lambda i: len(q['options'][i])) == q['answer'])
        assert longest <= len(qs) * 0.35, subj
        assert all(not written.validate_question({k: v for k, v in q.items() if k != 'subject'}, subj) for q in qs)
    assert all(q['levels'] == ['c1'] for q in written.questions('database'))
    assert not written.questions('database', 'c2')


def test_mock_composition():
    for level, spec in written.LEVELS.items():
        ids = written.build_mock(level, seed=1)
        assert len(ids) == 20 * len(spec['subjects']) and len(set(ids)) == len(ids)
        for subj in spec['subjects']:
            qs = [written.get(i) for i in ids if written.get(i)['subject'] == subj]
            assert len(qs) == 20 and all(level in q['levels'] for q in qs)
            assert len({q['topic'] for q in qs}) >= 5                       # 주제가 고르게


def test_grade_subject_cut_and_average():
    ids = written.build_mock('c2', seed=2)
    pc = [i for i in ids if i.startswith('pc-')]
    ss = [i for i in ids if i.startswith('ss-')]
    right = {i: written.get(i)['answer'] for i in ids}
    assert written.grade(ids, right)['passed']
    ans = dict(right)                                       # 컴퓨터 일반 7/20(35점) → 평균이 높아도 과락
    for i in pc[7:]:
        ans[i] = (right[i] + 1) % 4
    r = written.grade(ids, ans)
    assert r['subjects'][0]['score'] == 35 and r['average'] >= 60 and not r['passed']
    ans = dict(right)                                       # 두 과목 모두 50점 → 평균 미달
    for i in pc[10:] + ss[10:]:
        ans[i] = (right[i] + 1) % 4
    assert not written.grade(ids, ans)['passed']


def test_pages_and_records(client):
    assert client.get('/written/').status_code == 200
    page = client.get('/written/practice?level=c2&subject=computer').get_data(as_text=True)
    qid = re.search(r'data-wq="([^"]+)"', page).group(1)
    h = {'X-CSRF-Token': client.csrf}
    r = client.post('/written/api/answer', json={'id': qid, 'picked': written.get(qid)['answer']}, headers=h)
    assert r.json['ok'] and r.json['explain']
    assert client.post('/written/api/answer', json={'id': qid, 'picked': 9}, headers=h).status_code == 400
    mock = client.get('/written/mock/c1').get_data(as_text=True)
    qids = re.search(r'name="qids" value="([^"]+)"', mock).group(1)
    assert len(qids.split(',')) == 60
    form = {'_csrf': client.csrf, 'qids': qids, 'seconds': '1200'}
    for i in qids.split(',')[:30]:
        form['a_' + i] = str(written.get(i)['answer'])
    r = client.post('/written/mock/c1/submit', data=form)
    assert r.status_code == 302
    res = client.get(r.headers['Location']).get_data(as_text=True)
    assert '불합격' in res and '답을 고르지 않았습니다' in res
    data = client.get('/api/backup').json['data']
    assert len(data['written_results']) == 1 and len(data['written_attempts']) == 31
    assert client.get('/written/practice?level=c1&subject=computer&mode=wrong').status_code == 200
