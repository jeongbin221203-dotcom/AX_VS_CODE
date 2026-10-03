"""학습 기록 내보내기·복원 — 무료 서버가 다시 시작돼 기록이 지워져도 브라우저에 남긴 사본으로 되살린다."""
import json
import sqlite3

from . import db

# 열마다 허용하는 형식 — 맞지 않는 행은 건너뛴다
TYPES = {'pid': str, 'category': str, 'answer': str, 'task': str, 'exam': str, 'file_name': str, 'created_at': str,
         'detail': str, 'ok': (int, bool), 'score': (int, float), 'total': (int, float), 'passed': (int, bool),
         'seconds': (int, float, type(None))}
REQUIRED = {'pid', 'category', 'ok', 'task', 'exam', 'score', 'total', 'passed'}
DETAIL_KEYS = {'build_results': ('items', 'score', 'total'), 'exam_results': ('score', 'total')}

VERSION = 1
LIMITS = {'attempts': 20000, 'stars': 2000, 'build_results': 300, 'exam_results': 300}
COLS = {'attempts': ('pid', 'category', 'ok', 'answer', 'created_at'),
        'stars': ('pid', 'created_at'),
        'build_results': ('task', 'score', 'total', 'detail', 'file_name', 'created_at'),
        'exam_results': ('exam', 'score', 'total', 'passed', 'seconds', 'detail', 'file_name', 'created_at')}


def export():
    conn = db.get()
    u = db.user_id()
    out = {'version': VERSION}
    for table, cols in COLS.items():
        rows = conn.execute(f"SELECT {', '.join(cols)} FROM {table} WHERE user=? ORDER BY rowid DESC LIMIT ?",
                            (u, LIMITS[table])).fetchall()
        out[table] = [dict(r) for r in reversed(rows)]
    out['track'] = db.setting('track', '')
    return out


def counts(data=None):
    data = data if data is not None else export()
    return {t: len(data.get(t) or []) for t in COLS}


def quick_counts():
    conn = db.get()
    u = db.user_id()
    return {t: conn.execute(f'SELECT COUNT(*) FROM {t} WHERE user=?', (u,)).fetchone()[0] for t in COLS}


def is_empty():
    conn = db.get()
    u = db.user_id()
    return not any(conn.execute(f'SELECT 1 FROM {t} WHERE user=? LIMIT 1', (u,)).fetchone() for t in COLS)


def restore(data, replace=True):
    """내보낸 기록을 현재 사용자 기록으로 되살린다. → 테이블별 개수"""
    if not isinstance(data, dict) or data.get('version') != VERSION:
        raise ValueError('엑셀 연습장 기록 파일이 아닙니다.')
    conn = db.get()
    u = db.user_id()
    done = {}
    if replace:
        for t in COLS:
            conn.execute(f'DELETE FROM {t} WHERE user=?', (u,))
    for t, cols in COLS.items():
        rows = data.get(t) or []
        if not isinstance(rows, list):
            raise ValueError('기록 파일 형식이 잘못되었습니다.')
        n = 0
        for row in rows[-LIMITS[t]:]:
            if not isinstance(row, dict):
                continue
            vals = []
            for c in cols:
                v = row.get(c)
                if c == 'created_at' and not v:
                    v = __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                if c == 'detail' and v is not None and not isinstance(v, str):
                    v = json.dumps(v, ensure_ascii=False)
                if isinstance(v, str):
                    v = v[:200000]
                vals.append(v)
            row_ok = True
            for c, v in zip(cols, vals):
                if v is None and c in REQUIRED:
                    row_ok = False
                elif v is not None and not isinstance(v, TYPES.get(c, object)):
                    row_ok = False
            if row_ok and t in DETAIL_KEYS:
                try:
                    d = json.loads(vals[cols.index('detail')] or '')
                    row_ok = isinstance(d, dict) and all(k in d for k in DETAIL_KEYS[t])
                except (ValueError, TypeError):
                    row_ok = False
            if not row_ok:
                continue
            placeholders = ', '.join('?' for _ in cols)
            try:
                if t == 'stars':
                    conn.execute(f"INSERT OR IGNORE INTO stars({', '.join(cols)}, user) VALUES({placeholders}, ?)",
                                 (*vals, u))
                else:
                    conn.execute(f"INSERT INTO {t}({', '.join(cols)}, user) VALUES({placeholders}, ?)", (*vals, u))
            except sqlite3.Error:
                continue
            n += 1
        done[t] = n
    conn.commit()
    if data.get('track') in ('', 'work', 'c2', 'c1'):
        db.set_setting('track', data['track'])
    return done
