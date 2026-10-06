"""컴활 필기: 과목·주제별 연습(바로 채점), 실제 구성 모의고사(시간 제한·과목별 과락), 오답 다시 풀기."""
import json
import random

from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for
from markupsafe import Markup, escape

from core import db, written

bp = Blueprint('written', __name__, url_prefix='/written')


@bp.app_template_filter('wtext')
def wtext(text):
    """문제 글: ' | ' 로 나눈 줄이 이어지면 표로, 나머지는 줄 바꿈 그대로."""
    out, rows = [], []

    def flush():
        if rows:
            head, *body = rows
            out.append('<div class="table-wrap"><table class="t wq-table"><thead><tr>' +
                       ''.join(f'<th>{escape(c)}</th>' for c in head) + '</tr></thead><tbody>' +
                       ''.join('<tr>' + ''.join(f'<td>{escape(c)}</td>' for c in r) + '</tr>' for r in body) +
                       '</tbody></table></div>')
            rows.clear()
    for line in str(text).split('\n'):
        if ' | ' in line:
            rows.append([c.strip() for c in line.split('|')])
        else:
            flush()
            out.append(f'<span class="wq-line">{escape(line)}</span>')
    flush()
    return Markup(''.join(out))


def _level():
    """학습 범위(2급·1급) — 위쪽 선택이 2급이면 2급, 그 밖은 1급(필기는 실무 범위가 없음)."""
    lv = request.args.get('level')
    if lv in written.LEVELS:
        return lv
    from app import current_track
    return 'c2' if current_track() == 'c2' else 'c1'


def _stats(level):
    """문제별 마지막 풀이 결과 → 과목·주제별 푼 수·정답률."""
    conn = db.get()
    last = {}
    for r in conn.execute('SELECT qid, ok FROM written_attempts WHERE user=? ORDER BY id', (db.user_id(),)):
        last[r['qid']] = r['ok']
    out = {}
    for subj in written.LEVELS[level]['subjects']:
        qs = written.questions(subj, level)
        topics = []
        for t in written.SUBJECTS[subj]['topics']:
            tq = [q for q in qs if q.get('topic') == t]
            if not tq:
                continue
            done = [q for q in tq if q['id'] in last]
            topics.append({'name': t, 'total': len(tq), 'done': len(done),
                           'ok': sum(1 for q in done if last[q['id']]),
                           'wrong': sum(1 for q in done if not last[q['id']])})
        done = [q for q in qs if q['id'] in last]
        out[subj] = {'name': written.SUBJECTS[subj]['name'], 'total': len(qs), 'done': len(done),
                     'ok': sum(1 for q in done if last[q['id']]), 'wrong': sum(1 for q in done if not last[q['id']]),
                     'topics': topics}
    return out, last


WEAK_MIN = 3          # 약점으로 보려면 그 주제를 이만큼은 풀어 봐야 함
WEAK_BELOW = 0.7      # 마지막에 맞힌 비율이 이보다 낮으면 약점


def weak_topics(stats):
    """[(과목 키, 주제, 맞힌 비율, 푼 수)] 약한 순 — 마지막 풀이 기준."""
    out = []
    for key, s in stats.items():
        for t in s['topics']:
            if t['done'] >= WEAK_MIN and t['ok'] / t['done'] < WEAK_BELOW:
                out.append((key, t['name'], t['ok'] / t['done'], t['done']))
    return sorted(out, key=lambda x: (x[2], -x[3]))


@bp.route('/')
def index():
    level = _level()
    stats, _ = _stats(level)
    rows = db.get().execute('SELECT id, level, average, passed, seconds, created_at FROM written_results WHERE user=? '
                            'ORDER BY id DESC LIMIT 10', (db.user_id(),)).fetchall()
    return render_template('written.html', level=level, L=written.LEVELS, stats=stats, history=rows,
                           weak=weak_topics(stats)[:6], cut=(written.SUBJECT_CUT, written.AVERAGE_CUT))


@bp.route('/practice')
def practice():
    """과목·주제 연습: mode=new(안 푼 것 먼저)·wrong(마지막에 틀린 것)·all, n 문항."""
    level = _level()
    subj = request.args.get('subject')
    if subj and subj not in written.LEVELS[level]['subjects']:
        if subj in written.SUBJECTS:                  # 2급에서 데이터베이스(1급 과목)를 열면 1급으로
            return redirect(url_for('.practice', **{**request.args.to_dict(), 'level': 'c1'}))
        abort(404)
    topic = request.args.get('topic') or None
    mode = request.args.get('mode', 'new')
    n = max(5, min(50, request.args.get('n', 20, type=int) or 20))
    stats, last = _stats(level)
    pool = written.questions(subj, level, topic) if subj else \
        [q for s in written.LEVELS[level]['subjects'] for q in written.questions(s, level)]
    if mode == 'wrong':
        pool = [q for q in pool if q['id'] in last and not last[q['id']]]
    if mode == 'weak':                                # 약점 주제의 문제만(틀린 것·안 푼 것 먼저), 약점이 없으면 안 푼 문제
        wk = {(k, t) for k, t, *_ in weak_topics(stats)}
        weak_pool = [q for q in pool if (q['subject'], q.get('topic')) in wk]
        pool = weak_pool or pool
        if not weak_pool:
            mode = 'new'
        else:
            pool.sort(key=lambda q: (0 if q['id'] in last and not last[q['id']] else 1 if q['id'] not in last else 2))
    rnd = random.Random()
    if mode != 'weak':
        rnd.shuffle(pool)
    else:                                             # 약점: 우선순위(틀린 것 → 안 푼 것 → 맞힌 것)는 지키고 그 안에서 섞기
        groups = {}
        for q in pool:
            groups.setdefault(0 if q['id'] in last and not last[q['id']] else 1 if q['id'] not in last else 2, []).append(q)
        pool = []
        for g in sorted(groups):
            rnd.shuffle(groups[g])
            pool += groups[g]
    if mode == 'new':
        pool.sort(key=lambda q: q['id'] in last)          # 안 푼 문제 먼저(같은 무리 안에서는 섞인 순서)
    qs = pool[:n]
    title = (written.SUBJECTS[subj]['name'] if subj else '전 과목') + (f' · {topic}' if topic else '') + \
        {'wrong': ' · 틀린 문제 다시', 'weak': ' · 약점 주제', 'new': '', 'all': ''}.get(mode, '')
    return render_template('written_practice.html', level=level, L=written.LEVELS, qs=qs, title=title,
                           S=written.SUBJECTS, mode=mode, subject=subj, topic=topic)


@bp.route('/api/answer', methods=['POST'])
def api_answer():
    """연습 문제 하나 채점·기록 → 정답 번호·해설."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({'error': '요청 형식이 잘못되었습니다.'}), 400
    q = written.get(str(body.get('id', '')))
    if q is None:
        return jsonify({'error': '없는 문제입니다.'}), 404
    picked = body.get('picked')
    if type(picked) is not int or not 0 <= picked <= 3:
        return jsonify({'error': '보기 번호가 잘못되었습니다.'}), 400
    ok = picked == q['answer']
    conn = db.get()
    conn.execute('INSERT INTO written_attempts(qid, subject, ok, picked, user) VALUES(?, ?, ?, ?, ?)',
                 (q['id'], q['subject'], int(ok), picked, db.user_id()))
    conn.commit()
    return jsonify({'ok': ok, 'answer': q['answer'], 'explain': q['explain']})


@bp.route('/mock/<level>')
def mock(level):
    if level not in written.LEVELS:
        abort(404)
    asked = [x for x in request.args.get('q', '').split(',') if x]
    if asked and written.valid_paper(level, asked):    # 풀던 문제지 이어 풀기(브라우저가 기억한 문항)
        qids = asked
    else:
        recent = [r['qid'] for r in db.get().execute(
            'SELECT qid FROM written_attempts WHERE user=? ORDER BY id DESC LIMIT 400', (db.user_id(),))]
        qids = written.build_mock(level, avoid=recent)
    qs = [written.get(i) for i in qids]
    groups = []
    for subj in written.LEVELS[level]['subjects']:
        groups.append({'name': written.SUBJECTS[subj]['name'], 'qs': [q for q in qs if q['subject'] == subj]})
    return render_template('written_mock.html', level=level, L=written.LEVELS, groups=groups, qids=','.join(qids))


@bp.route('/mock/<level>/submit', methods=['POST'])
def mock_submit(level):
    if level not in written.LEVELS:
        abort(404)
    qids = []
    for x in request.form.get('qids', '').split(','):
        q = written.get(x)
        if q and level in q['levels'] and x not in qids:    # 겹친 번호·다른 급 문제는 빼고
            qids.append(x)
    qids = qids[:written.PER_SUBJECT * len(written.LEVELS[level]['subjects'])]
    if not qids:
        return redirect(url_for('.index', level=level))
    seconds = request.form.get('seconds', type=int)
    if seconds is not None:
        seconds = max(0, min(seconds, written.LEVELS[level]['minutes'] * 60 * 3))
    answers = {}
    for qid in qids:
        v = request.form.get('a_' + qid, type=int)
        if v is not None and 0 <= v <= 3:
            answers[qid] = v
    res = written.grade(qids, answers)
    conn = db.get()
    u = db.user_id()
    for row in res['rows']:                              # 안 푼 문제도 오답으로 남겨 '틀린 문제'에서 다시 보게
        conn.execute('INSERT INTO written_attempts(qid, subject, ok, picked, user) VALUES(?, ?, ?, ?, ?)',
                     (row['id'], row['subject'], int(row['ok']), row['picked'], u))
    detail = {'subjects': res['subjects'], 'average': res['average'], 'rows': res['rows']}
    cur = conn.execute('INSERT INTO written_results(level, average, passed, seconds, detail, user) VALUES(?, ?, ?, ?, ?, ?)',
                       (level, res['average'], int(res['passed']), seconds,
                        json.dumps(detail, ensure_ascii=False), u))
    conn.commit()
    return redirect(url_for('.result', rid=cur.lastrowid, done=1))


@bp.route('/result/<int:rid>')
def result(rid):
    row = db.get().execute('SELECT * FROM written_results WHERE id=? AND user=?', (rid, db.user_id())).fetchone()
    if not row:
        abort(404)
    try:
        detail = json.loads(row['detail'] or '{}')
        rows = detail['rows']
        subjects = detail['subjects']
    except (ValueError, KeyError, TypeError):
        abort(404)
    items = []
    for r in rows if isinstance(rows, list) else []:
        q = written.get(r.get('id', '')) if isinstance(r, dict) else None
        if q:
            items.append({'q': q, 'picked': r.get('picked'), 'ok': r.get('ok')})
    return render_template('written_result.html', row=row, subjects=subjects, items=items, L=written.LEVELS,
                           S=written.SUBJECTS, cut=(written.SUBJECT_CUT, written.AVERAGE_CUT))
