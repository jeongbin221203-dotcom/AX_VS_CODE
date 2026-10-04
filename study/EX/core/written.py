"""컴활 필기: 문제 은행(content/written/*.json), 과목·주제별 연습, 실제 구성 모의고사, 채점(과목 40점·평균 60점)."""
import json
import random
import threading
from pathlib import Path

CONTENT = Path(__file__).resolve().parent.parent / 'content' / 'written'

SUBJECTS = {
    'computer': {'name': '컴퓨터 일반', 'prefix': 'pc-',
                 'topics': ['운영체제', '시스템 설정', '하드웨어', '소프트웨어', '자료 표현', '네트워크·인터넷', '멀티미디어',
                            '정보 보안', '최신 기술']},
    'spreadsheet': {'name': '스프레드시트 일반', 'prefix': 'ss-',
                    'topics': ['환경·파일', '입력·편집', '서식', '수식·함수', '데이터 관리', '데이터 분석', '차트', '출력',
                               '매크로·VBA']},
    'database': {'name': '데이터베이스 일반', 'prefix': 'db-',
                 'topics': ['DB 개념', '테이블', '관계·키', '쿼리·SQL', '폼', '보고서', '매크로·모듈']},
}
# 실제 시험: 2급 2과목 40문항 40분, 1급 3과목 60문항 60분. 과목마다 100점 환산 40점 이상 + 평균 60점 이상 합격
LEVELS = {'c2': {'name': '2급', 'subjects': ['computer', 'spreadsheet'], 'minutes': 40},
          'c1': {'name': '1급', 'subjects': ['computer', 'spreadsheet', 'database'], 'minutes': 60}}
PER_SUBJECT = 20
SUBJECT_CUT = 40
AVERAGE_CUT = 60

_lock = threading.Lock()
_cache = {'stamp': None, 'data': None}


def _stamp():
    return tuple((p.name, p.stat().st_mtime_ns) for p in sorted(CONTENT.glob('*.json')))


def _load():
    items, order = {}, []
    for p in sorted(CONTENT.glob('*.json')):
        doc = json.loads(p.read_text(encoding='utf-8'))
        subj = doc.get('subject')
        if subj not in SUBJECTS:
            continue
        for q in doc.get('questions', []):
            if not validate_question(q, subj):
                q = dict(q, subject=subj)
                if q['id'] not in items:
                    order.append(q['id'])
                items[q['id']] = q
    return {'items': items, 'order': order}


def data():
    stamp = _stamp()
    with _lock:
        if _cache['stamp'] != stamp:
            _cache['data'] = _load()
            _cache['stamp'] = stamp
        return _cache['data']


def get(qid):
    return data()['items'].get(qid)


def questions(subject=None, level=None, topic=None):
    d = data()
    out = []
    for qid in d['order']:
        q = d['items'][qid]
        if subject and q['subject'] != subject:
            continue
        if level and level not in q['levels']:
            continue
        if topic and q.get('topic') != topic:
            continue
        out.append(q)
    return out


def validate_question(q, subject):
    """문제 하나 검사 → 오류 목록(없으면 빈 목록)."""
    errs = []
    if not isinstance(q, dict):
        return ['문제가 객체가 아님']
    spec = SUBJECTS[subject]
    if not str(q.get('id', '')).startswith(spec['prefix']):
        errs.append(f"id 는 {spec['prefix']} 로 시작")
    levels = q.get('levels')
    if not isinstance(levels, list) or not levels or not set(levels) <= {'c2', 'c1'}:
        errs.append('levels 는 ["c2","c1"] 또는 ["c1"]')
    elif subject == 'database' and levels != ['c1']:
        errs.append('데이터베이스는 1급만(["c1"])')
    if q.get('topic') not in spec['topics']:
        errs.append(f"topic {q.get('topic')!r} — {', '.join(spec['topics'])}")
    if not isinstance(q.get('q'), str) or len(q['q'].strip()) < 5:
        errs.append('q(문제 글) 없음')
    opts = q.get('options')
    if not isinstance(opts, list) or len(opts) != 4 or not all(isinstance(o, str) and o.strip() for o in opts):
        errs.append('options 는 글자 4개')
    elif len({o.strip() for o in opts}) != 4:
        errs.append('같은 보기가 있음')
    elif any(o.strip()[:1] in '①②③④' for o in opts):
        errs.append('보기 앞 번호는 빼기')
    if not isinstance(q.get('answer'), int) or not 0 <= q['answer'] <= 3:
        errs.append('answer 는 0~3')
    if not isinstance(q.get('explain'), str) or not q['explain'].strip():
        errs.append('explain(해설) 없음')
    return errs


def counts(level=None):
    out = {}
    for s in SUBJECTS:
        qs = questions(s, level)
        out[s] = {'total': len(qs), 'topics': {t: sum(1 for q in qs if q.get('topic') == t) for t in SUBJECTS[s]['topics']}}
    return out


def build_mock(level, seed=None, avoid=()):
    """실제 구성 모의고사: 과목마다 20문항, 주제가 고르게(되도록 최근에 푼 문제는 피함)."""
    rnd = random.Random(seed)
    avoid = set(avoid)
    picked = []
    for subj in LEVELS[level]['subjects']:
        pool = questions(subj, level)
        fresh = [q for q in pool if q['id'] not in avoid]
        if len(fresh) >= PER_SUBJECT:
            pool = fresh
        by_topic = {}
        for q in pool:
            by_topic.setdefault(q.get('topic'), []).append(q)
        for qs in by_topic.values():
            rnd.shuffle(qs)
        # 주제마다 문제 은행 크기에 비례해(최대 나머지 방식), 문제가 많은 운영체제·함수 쪽이 더 나오게
        n = min(PER_SUBJECT, len(pool))
        want = {t: n * len(qs) / len(pool) for t, qs in by_topic.items()}
        take = {t: int(w) for t, w in want.items()}
        for t in sorted(want, key=lambda t: (want[t] - take[t], rnd.random()), reverse=True)[:n - sum(take.values())]:
            take[t] += 1
        chosen = [q for t, qs in by_topic.items() for q in qs[:take[t]]]
        rnd.shuffle(chosen)
        picked += [q['id'] for q in chosen]
    return picked


def valid_paper(level, qids):
    """주소(?q=)로 받은 문제지가 이 급의 실제 구성인지(과목마다 20문항, 겹침 없음)."""
    if len(set(qids)) != len(qids):
        return False
    for subj in LEVELS[level]['subjects']:
        qs = [get(i) for i in qids if get(i) and get(i)['subject'] == subj]
        if len(qs) != PER_SUBJECT or any(level not in q['levels'] for q in qs):
            return False
    return len(qids) == PER_SUBJECT * len(LEVELS[level]['subjects'])


def grade(qids, answers):
    """qids 순서의 문제, answers {qid: 고른 번호} → 과목별 점수(100점 환산)·합격."""
    per = {}
    rows = []
    for qid in qids:
        q = get(qid)
        if q is None:
            continue
        pick = answers.get(qid)
        ok = isinstance(pick, int) and pick == q['answer']
        s = per.setdefault(q['subject'], {'n': 0, 'ok': 0})
        s['n'] += 1
        s['ok'] += ok
        rows.append({'id': qid, 'subject': q['subject'], 'picked': pick, 'answer': q['answer'], 'ok': ok})
    subjects = []
    for subj, s in per.items():
        score = round(100 * s['ok'] / s['n']) if s['n'] else 0
        subjects.append({'subject': subj, 'name': SUBJECTS[subj]['name'], 'n': s['n'], 'ok': s['ok'], 'score': score,
                         'cut': score >= SUBJECT_CUT})
    subjects.sort(key=lambda x: list(SUBJECTS).index(x['subject']))
    avg = round(sum(x['score'] for x in subjects) / len(subjects), 1) if subjects else 0
    return {'subjects': subjects, 'average': avg, 'passed': bool(subjects) and avg >= AVERAGE_CUT and
            all(x['cut'] for x in subjects), 'rows': rows}
