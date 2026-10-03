"""내 교재 실습 자료 가져오기: 폴더에서 '실습/정답'(또는 '실습파일/완성파일') 짝을 찾아 data/library 에 복사한다.

교재 파일은 출판사 저작물이므로 이 PC 의 data/ 에만 두고 저장소에는 올리지 않는다.
채점은 core/compare.py (실습 파일과 정답 파일의 차이를 항목으로 나눠 비교).
"""
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import threading
from pathlib import Path

_INDEX_LOCK = threading.RLock()
DISK_BUDGET = 300 * 1024 * 1024        # 공개 서버에서 방문자들이 올린 실습 파일 전체 한도

from . import compare

EXCEL = ('.xlsx', '.xlsm', '.xltm', '.xltx')
EXTRA = ('.txt', '.csv', '.accdb', '.xml', '.prn')
PRACTICE_DIRS = ('실습', '실습파일', '문제', '문제파일')
ANSWER_DIRS = ('정답', '정답파일', '완성', '완성파일', '해답')


def _key(name):
    """'제01회 기출예제(정답).xlsm' 과 '제1회기출예제.xlsm' 을 같은 열쇠로."""
    stem = Path(name).stem
    stem = re.sub(r'\(?(정답|완성|해답)\)?', '', stem)
    stem = re.sub(r'[\s_\-.()]+', '', stem)
    stem = re.sub(r'\d+', lambda m: str(int(m.group())), stem)
    return stem.lower()


def _level(text):
    if '1급' in text:
        return 'c1'
    if '2급' in text:
        return 'c2'
    return None


CATEGORIES = [('기본작업', ('기본작업', '데이터입력', '데이터편집', '서식', '사용자지정', '필터', '텍스트', '붙여넣기', '외부 데이터')),
              ('계산작업', ('계산작업', '함수', '수식')),
              ('분석작업', ('분석작업', '정렬', '부분합', '피벗', '목표값', '시나리오', '통합', '데이터표', '데이터 표')),
              ('기타작업', ('기타작업', '매크로', '차트')),
              ('모의고사', ('기출', '모의', '실전'))]


def category(path_text):
    t = path_text.replace(' ', '')
    for name, words in reversed(CATEGORIES):
        if any(w.replace(' ', '') in t for w in words):
            return name
    return '기타'


def scan(root):
    """폴더 → [{rel, title, group, level, category, practice, answer, extras}] (짝이 있는 것만)."""
    root = Path(root)
    pairs = []
    for pdir in sorted(p for p in root.rglob('*') if p.is_dir() and p.name in PRACTICE_DIRS):
        adir = next((pdir.parent / n for n in ANSWER_DIRS if (pdir.parent / n).is_dir()), None)
        if adir is None:
            continue
        answers = {}
        for f in adir.iterdir():
            if f.suffix.lower() in EXCEL and not f.name.startswith('~'):
                answers.setdefault(_key(f.name), f)
        extras = [f for f in pdir.iterdir() if f.suffix.lower() in EXTRA]
        group = ' / '.join(pdir.parent.relative_to(root).parts)
        for f in sorted(pdir.iterdir()):
            if f.suffix.lower() not in EXCEL or f.name.startswith('~'):
                continue
            a = answers.get(_key(f.name))
            if a is None:
                continue
            rel = str(f.relative_to(root))
            pairs.append({'rel': rel, 'title': f.stem, 'group': group, 'level': _level(rel),
                          'category': category(rel), 'practice': f, 'answer': a, 'extras': extras})
    return pairs


def _id(rel):
    return hashlib.sha1(rel.encode('utf-8')).hexdigest()[:12]


def import_folder(root, dest):
    """짝을 찾아 dest/<id>/ 에 복사하고 index.json 을 다시 쓴다. → 가져온 개수"""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    found = scan(root)
    index = load_index(dest)
    have = {x['id']: x for x in index}
    for p in found:
        iid = _id(p['rel'])
        d = dest / iid
        if d.exists():
            shutil.rmtree(d)
        d.mkdir()
        shutil.copy2(p['practice'], d / p['practice'].name)
        shutil.copy2(p['answer'], d / ('정답_' + p['answer'].name))
        for e in p['extras']:
            shutil.copy2(e, d / e.name)
        have[iid] = {'id': iid, 'rel': p['rel'], 'title': p['title'], 'group': p['group'], 'level': p['level'],
                     'category': p['category'], 'practice': p['practice'].name, 'answer': '정답_' + p['answer'].name,
                     'extras': [e.name for e in p['extras']], 'source_root': str(root),
                     'imported': dt.datetime.now().isoformat(timespec='seconds')}
    items = sorted(have.values(), key=lambda x: (x['group'], _natural(x['title'])))
    with _INDEX_LOCK:
        _write_index(dest, items)
    return len(found)


def _natural(s):
    return [int(t) if t.isdigit() else t for t in re.split(r'(\d+)', s)]


def load_index(dest):
    p = Path(dest) / 'index.json'
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def _write_index(dest, items):
    p = Path(dest) / 'index.json'
    tmp = p.with_suffix(f'.{os.getpid()}.{threading.get_ident()}.tmp')
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, p)


def _dir_size(path):
    return sum(f.stat().st_size for f in Path(path).rglob('*') if f.is_file())


def get(dest, iid):
    return next((x for x in load_index(dest) if x['id'] == iid), None)


def file_path(dest, iid, name):
    it = get(dest, iid)
    if not it or name not in [it['practice'], it['answer'], *it['extras']]:
        return None
    return Path(dest) / iid / name


def grade(dest, item, user_bytes):
    d = Path(dest) / item['id']
    return compare.grade((d / item['practice']).read_bytes(), (d / item['answer']).read_bytes(), user_bytes,
                         None)


def remove(dest, iid):
    with _INDEX_LOCK:
        items = [x for x in load_index(dest) if x['id'] != iid]
        shutil.rmtree(Path(dest) / iid, ignore_errors=True)
        _write_index(dest, items)


SAFE = re.compile(r'[^\w가-힣 .()\[\]_-]+')


def _safe_name(name, default):
    base = Path(name or '').name
    base = SAFE.sub('_', base).strip(' .') or default
    return base[:120]


def add_pair(dest, title, group, practice, answer, extras=(), owner=None, limit=None):
    """실습 파일·정답 파일을 직접 등록. practice/answer/extras = (파일 이름, 내용) — 내용 검사 후 저장. → id"""
    from . import xlsx
    for name, data in (practice, answer):
        if not name.lower().endswith(EXCEL):
            raise xlsx.BadFile(f'{name}: 엑셀 파일(.xlsx·.xlsm)만 등록할 수 있습니다.')
        xlsx.load(data, data_only=True)                 # 압축 폭탄·손상 파일 검사
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    if owner is not None and _dir_size(dest) > DISK_BUDGET:
        raise xlsx.BadFile('서버 저장 공간이 부족합니다. 잠시 뒤 다시 시도해 주세요.')
    _INDEX_LOCK.acquire()
    try:
        return _add_locked(dest, title, group, practice, answer, extras, owner, limit)
    finally:
        _INDEX_LOCK.release()


def _add_locked(dest, title, group, practice, answer, extras, owner, limit):
    items = load_index(dest)
    if owner is not None and limit:
        mine = [x for x in items if x.get('owner') == owner]
        for old in mine[:max(0, len(mine) - limit + 1)]:           # 오래된 것부터 정리
            shutil.rmtree(dest / old['id'], ignore_errors=True)
            items = [x for x in items if x['id'] != old['id']]
    iid = hashlib.sha1(f'{owner}|{title}|{dt.datetime.now().isoformat()}'.encode()).hexdigest()[:12]
    d = dest / iid
    d.mkdir()
    p_name = _safe_name(practice[0], '실습.xlsx')
    a_name = '정답_' + _safe_name(answer[0], '정답.xlsx')
    (d / p_name).write_bytes(practice[1])
    (d / a_name).write_bytes(answer[1])
    ex_names = []
    for name, data in extras:
        n = _safe_name(name, 'file')
        if Path(n).suffix.lower() in EXTRA and n not in (p_name, a_name):
            (d / n).write_bytes(data)
            ex_names.append(n)
    title = (title or Path(p_name).stem).strip()[:100]
    items.append({'id': iid, 'rel': f'직접 등록/{title}', 'title': title, 'group': (group or '직접 등록').strip()[:100],
                  'level': _level(title + ' ' + (group or '')), 'category': category(title + ' ' + (group or '')),
                  'practice': p_name, 'answer': a_name, 'extras': ex_names, 'owner': owner,
                  'imported': dt.datetime.now().isoformat(timespec='seconds')})
    _write_index(dest, items)
    return iid


def visible(items, owner, public):
    """공개 서버에서는 자기가 올린 것만, PC 에서는 모두."""
    if not public:
        return items
    return [x for x in items if x.get('owner') == owner and owner]
