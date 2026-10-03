"""대시보드(첫 화면)·학습 범위 선택."""
import json

from flask import Blueprint, redirect, render_template, request, url_for

from core import build, content, db, exam, study

bp = Blueprint('main', __name__)


@bp.route('/')
def home():
    from app import current_track
    track = current_track()
    dash = study.dashboard(track or None)
    conn = db.get()
    best = {r['task']: r for r in conn.execute(
        'SELECT task, MAX(score) score, total, COUNT(*) n FROM build_results GROUP BY task')}
    missions = [{'m': m, 'best': best.get(k)} for k, m in build.MISSIONS.items()]
    uploads = conn.execute('SELECT * FROM uploads ORDER BY created_at DESC LIMIT 4').fetchall()
    ebest = {r['exam']: r for r in conn.execute(
        'SELECT exam, MAX(score) score, total, COUNT(*) n, MAX(passed) passed FROM exam_results GROUP BY exam')}
    exams = [{'e': e, 'best': ebest.get(e['id'])} for e in exam.exams()]
    chart = {'labels': [d['label'] for d in dash['daily']],
             'series': [{'name': '정답', 'values': [d['ok'] for d in dash['daily']]},
                        {'name': '오답', 'values': [d['bad'] for d in dash['daily']]}]}
    return render_template('dashboard.html', d=dash, missions=missions, uploads=uploads, exams=exams,
                           chart_json=json.dumps(chart, ensure_ascii=False))


@bp.route('/track', methods=['POST'])
def set_track():
    t = request.form.get('track', '')
    db.set_setting('track', t if t in content.TRACKS else '')
    nxt = request.form.get('next') or url_for('main.home')
    if not nxt.startswith('/') or nxt.startswith('//'):
        nxt = url_for('main.home')
    return redirect(nxt)
