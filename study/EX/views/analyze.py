"""파일 → 대시보드."""
import json

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
import io

from core import analyze as an
from core import db, xlsx

bp = Blueprint('analyze', __name__, url_prefix='/analyze')
KEEP = 20  # 올린 파일 보관 개수


def _folder():
    return current_app.config['UPLOADS']


def _store(data, name):
    uid, table = an.save_upload(_folder(), data, name)
    conn = db.get()
    conn.execute('INSERT INTO uploads(id, name, sheet, rows, cols, user) VALUES(?, ?, ?, ?, ?, ?)',
                 (uid, name[:200], table['sheet'], len(table['rows']), len(table['columns']), db.user_id()))
    for row in conn.execute('SELECT id FROM uploads WHERE user=? ORDER BY created_at DESC, rowid DESC LIMIT -1 OFFSET ?',
                            (db.user_id(), KEEP)):
        an.delete_upload(_folder(), row['id'])
        conn.execute('DELETE FROM uploads WHERE id=?', (row['id'],))
    conn.commit()
    return uid


@bp.route('/')
def index():
    uploads = db.get().execute('SELECT * FROM uploads WHERE user=? ORDER BY created_at DESC, rowid DESC',
                               (db.user_id(),)).fetchall()
    return render_template('analyze.html', uploads=uploads, error=request.args.get('error'))


@bp.route('/upload', methods=['POST'])
def upload():
    f = request.files.get('file')
    if not f or not f.filename:
        return redirect(url_for('.index', error='파일을 고르세요.'))
    try:
        uid = _store(f.read(), f.filename)
    except xlsx.BadFile as e:
        return redirect(url_for('.index', error=str(e)))
    return redirect(url_for('.view', uid=uid))


@bp.route('/sample', methods=['POST'])
def sample():
    uid = _store(an.sample_file(), '샘플_상반기매출.xlsx')
    return redirect(url_for('.view', uid=uid))


@bp.route('/sample.xlsx')
def sample_download():
    return send_file(io.BytesIO(an.sample_file()), download_name='샘플_상반기매출.xlsx', as_attachment=True,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


def _table(uid):
    if not db.get().execute('SELECT 1 FROM uploads WHERE id=? AND user=?', (uid, db.user_id())).fetchone():
        abort(404)
    doc = an.load_upload(_folder(), uid)
    if not doc:
        abort(404)
    sheet = request.args.get('s')
    table = doc['tables'].get(sheet) if sheet else None
    return doc, (table or an.default_table(doc))


@bp.route('/<uid>')
def view(uid):
    doc, table = _table(uid)
    dash = an.dashboard(table, request.args)
    cols = table['columns']
    charts = {}
    if dash.get('group'):
        charts['group'] = {'labels': [str(k) for k, _, _ in dash['group']],
                           'series': [{'name': an.AGGS[dash['a']], 'values': [v for _, v, _ in dash['group']]}]}
    if dash.get('trend'):
        charts['trend'] = {'labels': [k for k, _, _ in dash['trend']],
                           'series': [{'name': an.AGGS[dash['a']], 'values': [v for _, v, _ in dash['trend']]}]}
    for i, cc in enumerate(dash['cat_counts']):
        charts[f'cat{i}'] = {'labels': [str(k) for k, _, _ in cc['items']],
                             'series': [{'name': '건수', 'values': [v for _, v, _ in cc['items']]}]}
    return render_template('analyze_view.html', uid=uid, doc=doc, table=table, cols=cols, d=dash, aggs=an.AGGS,
                           charts_json=json.dumps(charts, ensure_ascii=False).replace('</', '<\\/'))


@bp.route('/<uid>/export')
def export(uid):
    doc, table = _table(uid)
    dash = an.dashboard(table, request.args)
    name = doc['name'].rsplit('.', 1)[0] + '_요약.xlsx'
    return send_file(io.BytesIO(an.export_xlsx(table, dash)), download_name=name, as_attachment=True,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@bp.route('/<uid>/delete', methods=['POST'])
def delete(uid):
    conn = db.get()
    if not conn.execute('SELECT 1 FROM uploads WHERE id=? AND user=?', (uid, db.user_id())).fetchone():
        abort(404)
    an.delete_upload(_folder(), uid)
    conn.execute('DELETE FROM uploads WHERE id=?', (uid,))
    conn.commit()
    return redirect(url_for('.index'))
