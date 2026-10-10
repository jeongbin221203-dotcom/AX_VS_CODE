"""EX(Flask 앱)의 문제·함수·단축키 자료를 서버 없이 열리는 정적 HTML 용 data/*.js 로 만든다.

    python build.py            # data/written.js · functions.js · problems.js · shortcuts.js 다시 생성

자료 원본은 ../EX/content 이다. 원본을 고친 뒤 이 스크립트를 다시 돌리면 된다.
(자바스크립트 파일로 만드는 이유: file:// 로 그냥 열어도 fetch 제한 없이 읽히게 하려고)
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / 'EX'
sys.path.insert(0, str(SRC))

from core import content, written  # noqa: E402


def js(name, payload):
    text = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    text = text.replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    out = HERE / 'data' / f'{name}.js'
    out.parent.mkdir(exist_ok=True)
    out.write_text(f'window.EXDATA = window.EXDATA || {{}};\nEXDATA.{name} = {text};\n', encoding='utf-8')
    return out.stat().st_size


def build():
    sizes = {}
    qs = [{k: q[k] for k in ('id', 'subject', 'levels', 'topic', 'q', 'options', 'answer', 'explain')}
          for q in written.questions()]
    sizes['written'] = js('written', {
        'subjects': {k: {'name': v['name'], 'topics': v['topics']} for k, v in written.SUBJECTS.items()},
        'levels': written.LEVELS, 'perSubject': written.PER_SUBJECT,
        'subjectCut': written.SUBJECT_CUT, 'averageCut': written.AVERAGE_CUT, 'questions': qs})

    funcs = content.bank()['functions']
    sizes['functions'] = js('functions', {
        'categories': [[k, n, kind, d] for k, n, kind, d in content.CATEGORIES],
        'tracks': content.TRACKS, 'items': funcs})

    datasets = json.loads((SRC / 'content' / 'datasets.json').read_text(encoding='utf-8'))
    probs = []
    for p in content.problems():
        probs.append({k: v for k, v in p.items() if not k.startswith('_')})
    sizes['problems'] = js('problems', {'datasets': datasets, 'items': probs})

    sc = json.loads((SRC / 'content' / 'shortcuts.json').read_text(encoding='utf-8'))
    sizes['shortcuts'] = js('shortcuts', sc)
    return {'written': len(qs), 'functions': len(funcs), 'problems': len(probs),
            'shortcuts': sum(len(g['items']) for g in sc['groups']), 'bytes': sizes}


if __name__ == '__main__':
    info = build()
    print('생성 완료:', json.dumps(info, ensure_ascii=False))
