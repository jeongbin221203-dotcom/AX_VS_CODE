"""대시보드 만들기 실습."""
import io
import json

from flask import Blueprint, abort, redirect, render_template, request, send_file, url_for

from core import build, db, xlsx

bp = Blueprint('build', __name__, url_prefix='/build')
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


@bp.route('/')
def index():
    conn = db.get()
    best = {r['task']: r for r in conn.execute(
        'SELECT task, MAX(score) score, total, COUNT(*) n, MAX(created_at) last FROM build_results WHERE user=? '
        'GROUP BY task', (db.user_id(),))}
    return render_template('build.html', missions=build.MISSIONS.values(), best=best)


def _mission(key):
    m = build.mission(key)
    if not m:
        abort(404)
    return m


@bp.route('/<key>')
def task(key):
    m = _mission(key)
    history = db.get().execute('SELECT id, score, total, file_name, created_at FROM build_results WHERE task=? AND user=? '
                               'ORDER BY id DESC LIMIT 10', (key, db.user_id())).fetchall()
    return render_template('build_task.html', m=m, history=history, error=request.args.get('error'))


@bp.route('/<key>/file')
def download(key):
    m = _mission(key)
    return send_file(io.BytesIO(build.workbook(m)), download_name=f"실습_{m['title']}.xlsx", as_attachment=True,
                     mimetype=XLSX)


@bp.route('/<key>/answer')
def answer(key):
    m = _mission(key)
    return send_file(io.BytesIO(build.workbook(m, answers=True)), download_name=f"완성예시_{m['title']}.xlsx",
                     as_attachment=True, mimetype=XLSX)


@bp.route('/<key>/submit', methods=['POST'])
def submit(key):
    m = _mission(key)
    f = request.files.get('file')
    if not f or not f.filename:
        return redirect(url_for('.task', key=key, error='파일을 고르세요.'))
    if not f.filename.lower().endswith(('.xlsx', '.xlsm')):
        return redirect(url_for('.task', key=key, error='.xlsx 파일로 저장해서 올려 주세요.'))
    try:
        res = build.grade(m, f.read())
    except xlsx.BadFile as e:
        return redirect(url_for('.task', key=key, error=str(e)))
    conn = db.get()
    cur = conn.execute('INSERT INTO build_results(task, score, total, detail, file_name, user) VALUES(?, ?, ?, ?, ?, ?)',
                       (key, res['score'], res['total'], json.dumps(res, ensure_ascii=False), f.filename[:200],
                        db.user_id()))
    conn.commit()
    return redirect(url_for('.result', rid=cur.lastrowid))


@bp.route('/result/<int:rid>')
def result(rid):
    row = db.get().execute('SELECT * FROM build_results WHERE id=? AND user=?', (rid, db.user_id())).fetchone()
    if not row:
        abort(404)
    m = _mission(row['task'])
    return render_template('build_result.html', m=m, row=row, res=json.loads(row['detail']))
