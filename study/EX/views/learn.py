"""문제 목록·풀이·채점·오답 노트."""
import json

from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for

from core import content, study
from core import formula as fx

bp = Blueprint('learn', __name__)


def _track():
    from app import current_track
    return current_track() or None


@bp.route('/learn')
def index():
    track = _track()
    st = study.status_map()
    cats = []
    for key, name, kind, desc in content.CATEGORIES:
        items = content.problems(key, track)
        if not items:
            continue
        solved = sum(1 for p in items if st.get(p['id'], {}).get('solved'))
        cats.append({'key': key, 'name': name, 'kind': kind, 'desc': desc, 'total': len(items), 'solved': solved,
                     'pct': round(100 * solved / len(items)),
                     'levels': [sum(1 for p in items if p.get('level') == lv) for lv in (1, 2, 3)]})
    return render_template('learn.html', cats=cats)


@bp.route('/learn/<cat>')
def category(cat):
    if cat not in content.CAT_NAMES:
        abort(404)
    items = content.problems(cat, _track())
    level = request.args.get('level', type=int)
    if level:
        items = [p for p in items if p.get('level') == level]
    return render_template('learn_cat.html', cat=cat, items=items, st=study.status_map(), stars=study.stars(),
                           level=level)


def _neighbors(p):
    ids = [x['id'] for x in content.problems(p['category'], _track())]
    if p['id'] not in ids:
        return None, None
    i = ids.index(p['id'])
    return (ids[i - 1] if i else None), (ids[i + 1] if i + 1 < len(ids) else None)


@bp.route('/p/<pid>')
def problem(pid):
    p = content.get(pid)
    if not p:
        abort(404)
    prev_id, next_id = _neighbors(p)
    is_formula = p['type'] == 'formula'
    grid = content.grid(p) if (p.get('sheet') or is_formula) else None
    data = {'pid': pid, 'type': p['type'], 'target': p.get('target'),
            'fill': [fx.addr(r, c) for r, c in content.fill_cells(p)] if is_formula else [],
            'funcs': content.function_list() if is_formula else []}
    docs = [d for d in (content.function_doc(n) for n in p.get('functions', [])) if d]
    return render_template('problem.html', p=p, grid=grid, st=study.status_map().get(pid), prev_id=prev_id,
                           next_id=next_id, starred=pid in study.stars(), docs=docs,
                           data_json=json.dumps(data, ensure_ascii=False).replace('</', r'<\/'))


@bp.route('/api/check', methods=['POST'])
def check():
    body = request.get_json(silent=True) or {}
    p = content.get(str(body.get('pid', '')))
    if not p:
        abort(404)
    mode = body.get('mode', 'check')
    if p['type'] == 'choice':
        if mode == 'try':
            return jsonify({'error': '보기 문제는 바로 채점합니다.'})
        res = content.check_choice(p, body.get('answer'))
        answer_text = str(body.get('answer'))
    else:
        answer_text = str(body.get('answer') or '')[:1000]
        res = content.check(p, answer_text, reveal=mode != 'try')
    if mode == 'try' or res.get('error'):
        return jsonify(res)
    study.record(p, res['ok'], answer_text)
    res['explain'] = p.get('explain', '')
    if p['type'] == 'formula':
        res['answer'] = p['answer']
        res['alts'] = p.get('alts', [])
    return jsonify(res)


@bp.route('/api/star', methods=['POST'])
def star():
    body = request.get_json(silent=True) or {}
    pid = str(body.get('pid', ''))
    if not content.get(pid):
        abort(404)
    return jsonify({'on': study.toggle_star(pid)})


@bp.route('/next')
def next_problem():
    """안 푼 문제 → 없으면 마지막에 틀린 문제 → 없으면 목록."""
    st = study.status_map()
    cat = request.args.get('cat')
    items = content.problems(cat if cat in content.CAT_NAMES else None, _track())
    for p in items:
        if p['id'] not in st:
            return redirect(url_for('.problem', pid=p['id']))
    for p in items:
        if not st[p['id']]['last_ok']:
            return redirect(url_for('.problem', pid=p['id']))
    return redirect(url_for('.index'))


@bp.route('/review')
def review():
    st = study.status_map()
    stars = study.stars()
    items = content.problems(track=_track())
    wrong = [p for p in items if p['id'] in st and not st[p['id']]['last_ok']]
    often = sorted([p for p in items if st.get(p['id'], {}).get('wrong', 0) >= 2], key=lambda p: -st[p['id']]['wrong'])
    starred = [p for p in items if p['id'] in stars]
    return render_template('review.html', wrong=wrong, often=often, starred=starred, st=st)
