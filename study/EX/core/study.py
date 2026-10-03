"""풀이 기록과 대시보드 통계."""
import datetime as dt

from . import content, db


def record(problem, ok, answer):
    conn = db.get()
    conn.execute('INSERT INTO attempts(pid, category, ok, answer) VALUES(?, ?, ?, ?)',
                 (problem['id'], problem['category'], 1 if ok else 0, (answer or '')[:500]))
    conn.commit()


def status_map():
    """문제별 상태: solved(한 번이라도 맞힘), last_ok(마지막 풀이), tries, wrong."""
    rows = db.get().execute(
        'SELECT pid, COUNT(*) tries, SUM(ok) oks, SUM(1-ok) wrongs, MAX(id) last_id FROM attempts GROUP BY pid').fetchall()
    last = {r['pid']: r['ok'] for r in db.get().execute(
        'SELECT a.pid, a.ok FROM attempts a JOIN (SELECT pid, MAX(id) mid FROM attempts GROUP BY pid) m ON a.id = m.mid')}
    return {r['pid']: {'tries': r['tries'], 'solved': r['oks'] > 0, 'wrong': r['wrongs'],
                       'last_ok': bool(last.get(r['pid']))} for r in rows}


def stars():
    return {r['pid'] for r in db.get().execute('SELECT pid FROM stars')}


def toggle_star(pid):
    conn = db.get()
    if conn.execute('SELECT 1 FROM stars WHERE pid=?', (pid,)).fetchone():
        conn.execute('DELETE FROM stars WHERE pid=?', (pid,))
        on = False
    else:
        conn.execute('INSERT INTO stars(pid) VALUES(?)', (pid,))
        on = True
    conn.commit()
    return on


def _days(today, n):
    return [(today - dt.timedelta(days=n - 1 - i)) for i in range(n)]


def streak(today=None):
    today = today or dt.date.today()
    days = {r['d'] for r in db.get().execute("SELECT DISTINCT substr(created_at, 1, 10) d FROM attempts")}
    n, d = 0, today
    if d.isoformat() not in days:
        d -= dt.timedelta(days=1)
    while d.isoformat() in days:
        n += 1
        d -= dt.timedelta(days=1)
    return n


def dashboard(track=None, today=None):
    today = today or dt.date.today()
    probs = content.problems(track=track)
    st = status_map()
    conn = db.get()
    ids = {p['id'] for p in probs}

    solved = sum(1 for p in probs if st.get(p['id'], {}).get('solved'))
    tried = sum(1 for p in probs if p['id'] in st)
    rows = conn.execute('SELECT pid, ok, created_at FROM attempts ORDER BY id').fetchall()
    rows = [r for r in rows if r['pid'] in ids]
    first_try = {}
    for r in rows:
        first_try.setdefault(r['pid'], r['ok'])
    acc = round(100 * sum(first_try.values()) / len(first_try)) if first_try else None

    # 분류별 진도
    cats = []
    for key, name, kind, desc in content.CATEGORIES:
        items = [p for p in probs if p['category'] == key]
        if not items:
            continue
        s = sum(1 for p in items if st.get(p['id'], {}).get('solved'))
        ft = [first_try[p['id']] for p in items if p['id'] in first_try]
        cats.append({'key': key, 'name': name, 'kind': kind, 'desc': desc, 'total': len(items), 'solved': s,
                     'pct': round(100 * s / len(items)), 'tried': len(ft),
                     'acc': round(100 * sum(ft) / len(ft)) if ft else None})

    # 최근 14일
    days = _days(today, 14)
    per_day = {d.isoformat(): {'ok': 0, 'bad': 0} for d in days}
    for r in rows:
        d = r['created_at'][:10]
        if d in per_day:
            per_day[d]['ok' if r['ok'] else 'bad'] += 1
    daily = [{'date': d.isoformat(), 'label': f'{d.month}/{d.day}', **per_day[d.isoformat()]} for d in days]

    # 트랙별
    tracks = []
    for tk, tname in content.TRACKS.items():
        items = content.problems(track=tk)
        s = sum(1 for p in items if st.get(p['id'], {}).get('solved'))
        tracks.append({'key': tk, 'name': tname, 'total': len(items), 'solved': s,
                       'pct': round(100 * s / len(items)) if items else 0})

    weak = sorted([c for c in cats if c['acc'] is not None and c['tried'] >= 2], key=lambda c: (c['acc'], -c['tried']))[:3]
    wrong_now = [p for p in probs if p['id'] in st and not st[p['id']]['last_ok']]
    nxt = next((p for p in probs if p['id'] not in st), None)
    today_n = per_day[today.isoformat()]
    recent = conn.execute('SELECT pid, ok, created_at FROM attempts ORDER BY id DESC LIMIT 8').fetchall()
    recent = [{'pid': r['pid'], 'ok': r['ok'], 'at': r['created_at'][5:16], 'p': content.get(r['pid'])}
              for r in recent if content.get(r['pid'])]
    return {'total': len(probs), 'solved': solved, 'tried': tried, 'acc': acc, 'cats': cats, 'daily': daily,
            'tracks': tracks, 'weak': weak, 'wrong_now': wrong_now[:6], 'wrong_count': len(wrong_now),
            'next': nxt, 'today': today_n['ok'] + today_n['bad'], 'streak': streak(today), 'recent': recent}
