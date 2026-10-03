"""컴활 실기 형식 모의고사: 문제지·문제 파일·답안 제출·채점 결과."""
import io
import json

from flask import Blueprint, abort, redirect, render_template, request, send_file, url_for

from core import db, xlsx
from core import exam as ex

bp = Blueprint('exam', __name__, url_prefix='/exam')
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
CIRCLED = '①②③④⑤⑥⑦⑧⑨⑩'


def _best():
    rows = db.get().execute('SELECT exam, MAX(score) score, total, COUNT(*) n, MAX(passed) passed '
                            'FROM exam_results GROUP BY exam').fetchall()
    return {r['exam']: r for r in rows}


@bp.route('/')
def index():
    best = _best()
    groups = [{'level': k, 'name': n, 'items': ex.exams(k)} for k, n in ex.LEVELS.items()]
    return render_template('exam_list.html', groups=groups, best=best, ex=ex)


def _exam(eid):
    e = ex.get(eid)
    if not e:
        abort(404)
    return e


@bp.route('/<eid>')
def paper(eid):
    e = _exam(eid)
    history = db.get().execute('SELECT id, score, total, passed, seconds, file_name, created_at FROM exam_results '
                               'WHERE exam=? ORDER BY id DESC LIMIT 10', (eid,)).fetchall()
    sections = []
    for s in ex.SECTIONS:
        tasks = [t for t in e['tasks'] if t['section'] == s]
        if tasks:
            sections.append({'name': s, 'points': sum(ex.task_points(t) for t in tasks), 'tasks': tasks})
    tables = {t['no']: [[_show(v) for v in row] for row in t['table']['rows']] for t in e['tasks'] if t.get('table')}
    return render_template('exam_paper.html', e=e, sections=sections, history=history, ex=ex, circled=CIRCLED,
                           tables=tables, error=request.args.get('error'))


def _show(v):
    """문제지 표에 보일 글자 ('@2026-09-01' → 2026-09-01)."""
    if v is None:
        return ''
    if isinstance(v, str) and v.startswith('@'):
        return v[1:]
    return str(v)


def _file_name(e, ext='xlsx'):
    return f"{ex.LEVELS[e['level']].replace(' ', '')}_모의{e.get('round', '')}회.{ext}"


@bp.route('/<eid>/file')
def download(eid):
    e = _exam(eid)
    return send_file(io.BytesIO(ex.problem_workbook(e)), download_name=_file_name(e), as_attachment=True,
                     mimetype=XLSX)


@bp.route('/<eid>/submit', methods=['POST'])
def submit(eid):
    e = _exam(eid)
    f = request.files.get('file')
    if not f or not f.filename:
        return redirect(url_for('.paper', eid=eid, error='답안 파일을 고르세요.'))
    if not f.filename.lower().endswith(('.xlsx', '.xlsm')):
        return redirect(url_for('.paper', eid=eid, error='.xlsx 또는 .xlsm 파일만 올릴 수 있습니다.'))
    try:
        res = ex.grade(e, f.read(), f.filename)
    except xlsx.BadFile as err:
        return redirect(url_for('.paper', eid=eid, error=str(err)))
    seconds = request.form.get('seconds', type=int)
    conn = db.get()
    cur = conn.execute('INSERT INTO exam_results(exam, score, total, passed, seconds, detail, file_name) '
                       'VALUES(?, ?, ?, ?, ?, ?, ?)',
                       (eid, res['score'], res['total'], int(res['passed']), seconds,
                        json.dumps(res, ensure_ascii=False), f.filename[:200]))
    conn.commit()
    return redirect(url_for('.result', rid=cur.lastrowid))


@bp.route('/result/<int:rid>')
def result(rid):
    row = db.get().execute('SELECT * FROM exam_results WHERE id=?', (rid,)).fetchone()
    if not row:
        abort(404)
    e = _exam(row['exam'])
    return render_template('exam_result.html', e=e, row=row, res=json.loads(row['detail']), ex=ex)
