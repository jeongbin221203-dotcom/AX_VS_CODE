"""컴활 실기 실습: 공식 예제(대한상공회의소)·내 교재 실습 파일 — 정답 파일과 비교해 채점."""
import json
import os
import secrets
from pathlib import Path

from flask import Blueprint, abort, current_app, redirect, render_template, request, send_file, session, url_for

from core import compare, db, describe, library, official, xlsx

bp = Blueprint('practice', __name__, url_prefix='/practice')


def _official_dir():
    return Path(current_app.config['DATA_DIR']) / 'official'


def _library_dir():
    return Path(current_app.config['DATA_DIR']) / 'library'


def _best(prefix):
    rows = db.get().execute("SELECT exam, MAX(score) score, COUNT(*) n FROM exam_results WHERE exam LIKE ? AND user=? "
                            "GROUP BY exam", (prefix + '%', db.user_id())).fetchall()
    return {r['exam'][len(prefix):]: r for r in rows}


def _owner():
    """공개 서버의 보관함 주인 = 사용자 id (개인 링크로 연결한 기기는 모두 'owner')."""
    return db.user_id()


def _is_owner():
    return not current_app.config.get('PUBLIC') or bool(session.get('owner'))


def _items():
    public = current_app.config.get('PUBLIC')
    items = library.load_index(_library_dir())
    if public and session.get('owner'):          # 개인 링크로 연결한 기기: 내 자료(PC 에서 가져온 것) + 직접 올린 것
        return [x for x in items if x.get('owner') in (None, 'owner')]
    return library.visible(items, _owner() if public else None, public)


@bp.route('/')
def index():
    sets = official.sets(_official_dir())
    items = _items()
    groups = {}
    for it in items:
        groups.setdefault(it['group'], []).append(it)
    default_dir = str(Path(os.path.expanduser('~')) / 'Downloads' / 'MYBOX')
    return render_template('practice.html', sets=sets, groups=groups, n_items=len(items),
                           best_off=_best('official:'), best_lib=_best('lib:'), default_dir=default_dir,
                           msg=request.args.get('msg'), error=request.args.get('error'))


def _local_only():
    if current_app.config.get('PUBLIC'):
        abort(403, '공개 서버에서는 저작물 파일을 받거나 가져올 수 없습니다. PC 에서 실행해 주세요.')


@bp.route('/official/fetch', methods=['POST'])
def official_fetch():
    _local_only()
    try:
        got = official.download(_official_dir())
    except OSError as e:
        return redirect(url_for('.index', error=f'공식 사이트에서 받지 못했습니다: {e}'))
    return redirect(url_for('.index', msg=f'공식 예제 {len(got)}세트를 받았습니다.'))


@bp.route('/library/import', methods=['POST'])
def library_import():
    _local_only()
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
        if not _is_owner():
            abort(404)
        s = official.get(_official_dir(), iid)
        if not s or not s['ready'] or (need_answer and not s['gradable']):
            abort(404)
        f = s['meta']['files']
        return ({'id': iid, 'title': s['title'], 'level': s['level'], 'group': '대한상공회의소 공식 예제',
                 'pdf': f.get('pdf'), 'extras': f.get('extra', []), 'source_page': s['meta']['source_page']},
                _official_dir() / iid, f['source'], f.get('answer'), f'official:{iid}')
    if kind == 'lib':
        it = next((x for x in _items() if x['id'] == iid), None)
        if not it:
            abort(404)
        return (dict(it), _library_dir() / iid, it['practice'], it['answer'], f'lib:{iid}')
    abort(404)


CACHE_VER = 7          # 채점·지문 규칙을 바꾸면 올린다(예전에 저장한 할 일·지문을 다시 만든다)


def _tasks(folder, practice, answer):
    """실습 파일을 정답과 비교한 결과 = 해야 할 일 목록(처음 한 번 계산해 저장)."""
    cache = folder / f'tasks.v{CACHE_VER}.json'
    if cache.exists() and cache.stat().st_mtime >= (folder / answer).stat().st_mtime:
        return json.loads(cache.read_text(encoding='utf-8'))
    try:
        res = compare.grade((folder / practice).read_bytes(), (folder / answer).read_bytes(),
                            (folder / practice).read_bytes())
    except xlsx.BadFile:
        return []                                  # 채점할 수 없는 짝: 화면은 열고 제출 때 이유를 보여 준다
    sheets = [{'name': s['name'], 'points': s['points'], 'items': [
        {'label': i['label'], 'hint': i.get('hint'), 'code': i.get('code')} for i in s['items']]}
        for s in res['sheets'] if s['items']]
    cache.write_text(json.dumps(sheets, ensure_ascii=False), encoding='utf-8')
    return sheets


def _problem(folder, practice, answer):
    """정답 파일에서 만든 문제 지문(처음 한 번 만들어 저장)."""
    cache = folder / f'problem.v{CACHE_VER}.json'
    if cache.exists() and cache.stat().st_mtime >= (folder / answer).stat().st_mtime:
        return json.loads(cache.read_text(encoding='utf-8'))
    try:
        data = describe.describe((folder / practice).read_bytes(), (folder / answer).read_bytes())
    except Exception:  # noqa: BLE001 — 지문 만들기에 실패해도 채점 화면은 연다
        data = []
    for sh in data:
        for t in sh['tasks']:
            t.pop('_key', None)
    cache.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    return data


@bp.route('/<kind>/<iid>')
def item(kind, iid):
    info, folder, practice, answer, key = _resolve(kind, iid)
    history = db.get().execute('SELECT id, score, total, passed, file_name, created_at FROM exam_results WHERE exam=? '
                               'AND user=? ORDER BY id DESC LIMIT 10', (key, db.user_id())).fetchall()
    return render_template('practice_item.html', kind=kind, info=info, practice=practice, answer=answer,
                           tasks=_tasks(folder, practice, answer), history=history,
                           problem=_problem(folder, practice, answer) if kind == 'lib' else [],
                           error=request.args.get('error'))


@bp.route('/<kind>/<iid>/file/<path:name>')
def file(kind, iid, name):
    info, folder, practice, answer, _ = _resolve(kind, iid, need_answer=False)
    allowed = {practice, answer, info.get('pdf'), *info.get('extras', [])} - {None}
    if name not in allowed:
        abort(404)
    inline = name.lower().endswith('.pdf') and request.args.get('view') == '1'
    return send_file(folder / name, as_attachment=not inline, download_name=name)


def _seconds():
    v = request.form.get('seconds', type=int)
    return None if v is None else max(0, min(v, 6 * 3600))


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
    cur = conn.execute('INSERT INTO exam_results(exam, score, total, passed, seconds, detail, file_name, user) '
                       'VALUES(?, ?, ?, ?, ?, ?, ?, ?)',
                       (key, res['score'], res['total'], int(res['passed']), _seconds(),
                        json.dumps(res, ensure_ascii=False), f.filename[:200], db.user_id()))
    conn.commit()
    return redirect(url_for('.result', rid=cur.lastrowid, done=1))


@bp.route('/result/<int:rid>')
def result(rid):
    row = db.get().execute('SELECT * FROM exam_results WHERE id=? AND user=?', (rid, db.user_id())).fetchone()
    if not row or ':' not in row['exam']:
        abort(404)
    kind, iid = row['exam'].split(':', 1)
    info, *_ = _resolve(kind, iid)
    try:
        res = json.loads(row['detail'])
        assert isinstance(res, dict) and {'score', 'total', 'sheets', 'sections'} <= set(res)
    except (ValueError, TypeError, AssertionError):
        abort(404)
    return render_template('practice_result.html', kind=kind, info=info, row=row, res=res)


@bp.route('/library/add', methods=['POST'])
def library_add():
    p, a = request.files.get('practice'), request.files.get('answer')
    if not p or not p.filename or not a or not a.filename:
        return redirect(url_for('.index', error='실습 파일과 정답 파일을 모두 고르세요.'))
    extras = [(f.filename, f.read()) for f in request.files.getlist('extras') if f and f.filename]
    public = current_app.config.get('PUBLIC')
    p_bytes, a_bytes = p.read(), a.read()
    try:
        if all(f.filename.lower().endswith(('.xlsx', '.xlsm')) for f in (p, a)):
            compare.grade(p_bytes, a_bytes, p_bytes)     # 채점할 수 있는 짝인지(같은 시트·차이) 먼저 확인
        iid = library.add_pair(_library_dir(), request.form.get('title'), request.form.get('group'),
                               (p.filename, p_bytes), (a.filename, a_bytes), extras,
                               owner=_owner() if public else None, limit=30 if public else None)
    except xlsx.BadFile as e:
        return redirect(url_for('.index', error=str(e)))
    return redirect(url_for('.item', kind='lib', iid=iid))


@bp.route('/lib/<iid>/delete', methods=['POST'])
def library_delete(iid):
    if not any(x['id'] == iid for x in _items()):
        abort(404)
    library.remove(_library_dir(), iid)
    return redirect(url_for('.index', msg='지웠습니다.'))
