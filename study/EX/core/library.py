"""내 교재 실습 자료 가져오기: 폴더에서 '실습/정답'(또는 '실습파일/완성파일') 짝을 찾아 data/library 에 복사한다.

교재 파일은 출판사 저작물이므로 이 PC 의 data/ 에만 두고 저장소에는 올리지 않는다.
채점은 core/compare.py (실습 파일과 정답 파일의 차이를 항목으로 나눠 비교).
"""
import datetime as dt
import hashlib
import json
import re
import shutil
from pathlib import Path

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
    (dest / 'index.json').write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding='utf-8')
    return len(found)


def _natural(s):
    return [int(t) if t.isdigit() else t for t in re.split(r'(\d+)', s)]


def load_index(dest):
    p = Path(dest) / 'index.json'
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else []


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
    items = [x for x in load_index(dest) if x['id'] != iid]
    shutil.rmtree(Path(dest) / iid, ignore_errors=True)
    (Path(dest) / 'index.json').write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding='utf-8')
