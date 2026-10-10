"""내 PC 의 공식 예제(../EX/data/official)와 교재 실습 자료(../EX/data/library)를 data/private.js 로 묶는다.

    python tools/pack_private.py

실기 실습 화면이 열릴 때 이 파일이 있으면 자료를 브라우저(IndexedDB)에 자동으로 넣어 주므로,
zip·폴더를 다시 고르지 않아도 바로 풀 수 있다. 파일이 없으면 아무 일도 일어나지 않는다.
공식 예제는 대한상공회의소, 교재는 출판사 저작물이므로 data/private.js 는 저장소에 올리지 않는다(.gitignore).
"""
import base64
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
SRC = HERE.parent / 'EX' / 'data'
OUT = HERE / 'data' / 'private.js'


def b64(path):
    return {'name': path.name, 'b64': base64.b64encode(path.read_bytes()).decode('ascii')}


def official():
    items = []
    for man in sorted((SRC / 'official').glob('*/manifest.json')):
        m = json.loads(man.read_text(encoding='utf-8'))
        d = man.parent
        f = m.get('files', {})
        if not f.get('answer'):
            continue            # 정답 파일이 없는 세트(2015 연습 예제)는 채점할 수 없어 뺀다
        get = lambda k: b64(d / f[k]) if f.get(k) and (d / f[k]).exists() else None  # noqa: E731
        items.append({'id': m['id'], 'kind': 'official', 'title': m['title'], 'group': '대한상공회의소 공식 예제',
                      'level': m.get('level'), 'category': '공식 예제', 'practice': get('source'), 'answer': get('answer'),
                      'pdf': get('pdf'), 'extras': [b64(d / x) for x in f.get('extra', []) if (d / x).exists()]})
    return items


def library():
    idx = SRC / 'library' / 'index.json'
    if not idx.exists():
        return []
    items = []
    for n, m in enumerate(json.loads(idx.read_text(encoding='utf-8'))):
        d = SRC / 'library' / m['id']
        if not (d / m['practice']).exists() or not (d / m['answer']).exists():
            continue
        items.append({'id': m['id'], 'kind': 'lib', 'title': m['title'], 'group': m.get('group') or '가져온 교재',
                      'level': m.get('level'), 'category': m.get('category'), 'order': n,
                      'practice': b64(d / m['practice']), 'answer': b64(d / m['answer']),
                      'extras': [b64(d / x) for x in m.get('extras', []) if (d / x).exists()]})
    return items


if __name__ == '__main__':
    items = official() + library()
    text = json.dumps({'items': items}, ensure_ascii=False, separators=(',', ':'))
    OUT.write_text('window.EXPRIVATE=' + text + ';\n', encoding='utf-8', newline='\n')
    print('공식 예제 %d세트 + 교재 %d쌍 → %s (%.1fMB)' % (
        sum(1 for i in items if i['kind'] == 'official'), sum(1 for i in items if i['kind'] == 'lib'), OUT, OUT.stat().st_size / 1e6))
