"""컴활 실기 실습: 공식 예제(대한상공회의소)·내 교재 실습 파일 — 정답 파일과 비교해 채점."""
import json
import os
from pathlib import Path

from flask import Blueprint, abort, current_app, redirect, render_template, request, send_file, url_for

from core import compare, db, library, official, xlsx

bp = Blueprint('practice', __name__, url_prefix='/practice')


def _official_dir():
    return Path(current_app.config['DATA_DIR']) / 'official'


def _library_dir():
    return Path(current_app.config['DATA_DIR']) / 'library'


def _best(prefix):
    rows = db.get().execute("SELECT exam, MAX(score) score, COUNT(*) n FROM exam_results WHERE exam LIKE ? GROUP BY exam",
                            (prefix + '%',)).fetchall()
    return {r['exam'][len(prefix):]: r for r in rows}


@bp.route('/')
def index():
    sets = official.sets(_official_dir())
    items = library.load_index(_library_dir())
    groups = {}
    for it in items:
        groups.setdefault(it['group'], []).append(it)
    default_dir = str(Path(os.path.expanduser('~')) / 'Downloads' / 'MYBOX')
    return render_template('practice.html', sets=sets, groups=groups, n_items=len(items),
                           best_off=_best('official:'), best_lib=_best('lib:'), default_dir=default_dir,
                           msg=request.args.get('msg'), error=request.args.get('error'))


@bp.route('/official/fetch', methods=['POST'])
def official_fetch():
    try:
        got = official.download(_official_dir())
    except OSError as e:
        return redirect(url_for('.index', error=f'공식 사이트에서 받지 못했습니다: {e}'))
    return redirect(url_for('.index', msg=f'공식 예제 {len(got)}세트를 받았습니다.'))


@bp.route('/library/import', methods=['POST'])
def library_import():
    folder = (request.form.get('folder') or '').strip().strip('"')
    if not folder or not Path(folder).is_dir():
        return redirect(url_for('.index', error='폴더를 찾을 수 없습니다: ' + folder))
    n = library.import_folder(folder, _library_dir())
    if not n:
        return redirect(url_for('.index', error="'실습'·'정답'(또는 '실습파일'·'완성파일') 폴더 짝을 찾지 못했습니다."))
    return redirect(url_for('.index', msg=f'실습·정답 파일 {n}쌍을 가져왔습니다.'))


def _resolve(kind, iid, need_answer=True):
    """→ (항목 정보, 폴더, 실습 파일 이름, 정답 파일 이름, 결과 키)"""
    if kind == 'official':
        s = official.get(_official_dir(), iid)
        if not s or not s['ready'] or (need_answer and not s['gradable']):
            abort(404)
        f = s['meta']['files']
        return ({'id': iid, 'title': s['title'], 'level': s['level'], 'group': '대한상공회의소 공식 예제',
                 'pdf': f.get('pdf'), 'extras': f.get('extra', []), 'source_page': s['meta']['source_page']},
                _official_dir() / iid, f['source'], f.get('answer'), f'official:{iid}')
    if kind == 'lib':
        it = library.get(_library_dir(), iid)
        if not it:
            abort(404)
        return (dict(it), _library_dir() / iid, it['practice'], it['answer'], f'lib:{iid}')
    abort(404)


def _tasks(folder, practice, answer):
    """실습 파일을 정답과 비교한 결과 = 해야 할 일 목록(처음 한 번 계산해 저장)."""
    cache = folder / 'tasks.json'
    if cache.exists() and cache.stat().st_mtime >= (folder / answer).stat().st_mtime:
        return json.loads(cache.read_text(encoding='utf-8'))
    res = compare.grade((folder / practice).read_bytes(), (folder / answer).read_bytes(), (folder / practice).read_bytes())
    sheets = [{'name': s['name'], 'points': s['points'], 'items': [
        {'label': i['label'], 'hint': i.get('hint'), 'code': i.get('code')} for i in s['items']]}
        for s in res['sheets'] if s['items']]
    cache.write_text(json.dumps(sheets, ensure_ascii=False), encoding='utf-8')
    return sheets


@bp.route('/<kind>/<iid>')
def item(kind, iid):
    info, folder, practice, answer, key = _resolve(kind, iid)
    history = db.get().execute('SELECT id, score, total, passed, file_name, created_at FROM exam_results WHERE exam=? '
                               'ORDER BY id DESC LIMIT 10', (key,)).fetchall()
    return render_template('practice_item.html', kind=kind, info=info, practice=practice, answer=answer,
                           tasks=_tasks(folder, practice, answer), history=history,
                           error=request.args.get('error'))


@bp.route('/<kind>/<iid>/file/<path:name>')
def file(kind, iid, name):
    info, folder, practice, answer, _ = _resolve(kind, iid, need_answer=False)
    allowed = {practice, answer, info.get('pdf'), *info.get('extras', [])} - {None}
    if name not in allowed:
        abort(404)
    inline = name.lower().endswith('.pdf') and request.args.get('view') == '1'
    return send_file(folder / name, as_attachment=not inline, download_name=name)


@bp.route('/<kind>/<iid>/submit', methods=['POST'])
def submit(kind, iid):
    info, folder, practice, answer, key = _resolve(kind, iid)
    f = request.files.get('file')
    if not f or not f.filename or not f.filename.lower().endswith(('.xlsx', '.xlsm', '.xltm')):
        return redirect(url_for('.item', kind=kind, iid=iid, error='.xlsx 또는 .xlsm 파일을 올려 주세요.'))
    try:
        res = compare.grade((folder / practice).read_bytes(), (folder / answer).read_bytes(), f.read(),
                            info.get('level') if kind == 'official' else None)
    except xlsx.BadFile as e:
        return redirect(url_for('.item', kind=kind, iid=iid, error=str(e)))
    conn = db.get()
    cur = conn.execute('INSERT INTO exam_results(exam, score, total, passed, seconds, detail, file_name) '
                       'VALUES(?, ?, ?, ?, ?, ?, ?)',
                       (key, res['score'], res['total'], int(res['passed']), request.form.get('seconds', type=int),
                        json.dumps(res, ensure_ascii=False), f.filename[:200]))
    conn.commit()
    return redirect(url_for('.result', rid=cur.lastrowid))


@bp.route('/result/<int:rid>')
def result(rid):
    row = db.get().execute('SELECT * FROM exam_results WHERE id=?', (rid,)).fetchone()
    if not row or ':' not in row['exam']:
        abort(404)
    kind, iid = row['exam'].split(':', 1)
    info, *_ = _resolve(kind, iid)
    return render_template('practice_result.html', kind=kind, info=info, row=row, res=json.loads(row['detail']))


@bp.route('/lib/<iid>/delete', methods=['POST'])
def library_delete(iid):
    library.remove(_library_dir(), iid)
    return redirect(url_for('.index', msg='지웠습니다.'))
