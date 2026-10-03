"""대시보드(첫 화면)·학습 범위 선택."""
import json

from flask import Blueprint, Response, jsonify, redirect, render_template, request, url_for

from core import backup, build, content, db, exam, study

bp = Blueprint('main', __name__)


@bp.route('/')
def home():
    from app import current_track
    track = current_track()
    dash = study.dashboard(track or None)
    conn = db.get()
    best = {r['task']: r for r in conn.execute(
        'SELECT task, MAX(score) score, total, COUNT(*) n FROM build_results WHERE user=? GROUP BY task',
        (db.user_id(),))}
    missions = [{'m': m, 'best': best.get(k)} for k, m in build.MISSIONS.items()]
    uploads = conn.execute('SELECT * FROM uploads WHERE user=? ORDER BY created_at DESC LIMIT 4', (db.user_id(),)).fetchall()
    ebest = {r['exam']: r for r in conn.execute(
        'SELECT exam, MAX(score) score, total, COUNT(*) n, MAX(passed) passed FROM exam_results WHERE user=? '
        'GROUP BY exam', (db.user_id(),))}
    exams = [{'e': e, 'best': ebest.get(e['id'])} for e in exam.exams()]
    chart = {'labels': [d['label'] for d in dash['daily']],
             'series': [{'name': '정답', 'values': [d['ok'] for d in dash['daily']]},
                        {'name': '오답', 'values': [d['bad'] for d in dash['daily']]}]}
    return render_template('dashboard.html', d=dash, missions=missions, uploads=uploads, exams=exams,
                           msg=request.args.get('msg'),
                           chart_json=json.dumps(chart, ensure_ascii=False))


@bp.route('/api/backup')
def api_backup():
    data = backup.export()
    return jsonify({'counts': backup.counts(data), 'data': data})


@bp.route('/api/restore', methods=['POST'])
def api_restore():
    try:
        done = backup.restore(request.get_json(silent=True) or {})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'restored': done})


@bp.route('/backup.json')
def backup_download():
    body = json.dumps(backup.export(), ensure_ascii=False)
    return Response(body, mimetype='application/json',
                    headers={'Content-Disposition': "attachment; filename*=UTF-8''%EC%97%91%EC%85%80%EC%97%B0%EC%8A%B5%EC%9E%A5_%EA%B8%B0%EB%A1%9D.json"})


@bp.route('/restore', methods=['POST'])
def restore_upload():
    f = request.files.get('file')
    try:
        data = json.loads((f.read() if f else b'').decode('utf-8'))
        done = backup.restore(data)
    except (ValueError, UnicodeDecodeError) as e:
        return redirect(url_for('main.home', msg=f'복원하지 못했습니다: {e}'))
    return redirect(url_for('main.home', msg=f"기록을 복원했습니다(풀이 {done['attempts']}개, 모의고사·실습 {done['exam_results']}개)."))


@bp.route('/track', methods=['POST'])
def set_track():
    t = request.form.get('track', '')
    db.set_setting('track', t if t in content.TRACKS else '')
    nxt = request.form.get('next') or url_for('main.home')
    if not nxt.startswith('/') or nxt.startswith('//'):
        nxt = url_for('main.home')
    return redirect(nxt)
