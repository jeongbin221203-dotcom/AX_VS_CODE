"""엑셀 단축키 모음 (content/shortcuts.json)."""
import json
import threading
from pathlib import Path

from flask import Blueprint, render_template

bp = Blueprint('shortcuts', __name__)

FILE = Path(__file__).resolve().parent.parent / 'content' / 'shortcuts.json'
_lock = threading.Lock()
_cache = {'stamp': None, 'groups': []}


def groups():
    """파일이 바뀌면 다시 읽는다."""
    stamp = FILE.stat().st_mtime_ns
    with _lock:
        if _cache['stamp'] != stamp:
            _cache['groups'] = json.loads(FILE.read_text(encoding='utf-8'))['groups']
            _cache['stamp'] = stamp
        return _cache['groups']


def validate(gs):
    """형식 검사 → 문제 목록(빈 목록이면 이상 없음)."""
    bad = []
    keys = set()
    for g in gs:
        if g['key'] in keys:
            bad.append(f"그룹 이름 중복: {g['key']}")
        keys.add(g['key'])
        seen = set()
        for it in g['items']:
            if not it.get('k') or not all(isinstance(x, str) and x for x in it['k']):
                bad.append(f"{g['key']}: 키가 비었습니다 {it}")
            if not it.get('d'):
                bad.append(f"{g['key']}: 설명이 없습니다 {it.get('k')}")
            combo = (tuple(it['k']), bool(it.get('seq')))
            if combo in seen:
                bad.append(f"{g['key']}: 같은 키가 두 번 나옵니다 {it['k']}")
            seen.add(combo)
            for alt in it.get('also', []):
                if not alt or not all(isinstance(x, str) and x for x in alt):
                    bad.append(f"{g['key']}: also 가 잘못됐습니다 {it['k']}")
    return bad


@bp.route('/shortcuts')
def index():
    gs = groups()
    total = sum(len(g['items']) for g in gs)
    hot = sum(1 for g in gs for it in g['items'] if it.get('hot'))
    return render_template('shortcuts.html', groups=gs, total=total, hot=hot)
