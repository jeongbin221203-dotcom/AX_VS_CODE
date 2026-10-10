"""EX(Flask 앱)의 문제·함수·단축키 자료를 서버 없이 열리는 정적 HTML 용 data/*.js 로 만든다.

    python build.py            # data/written.js · functions.js · problems.js · shortcuts.js 다시 생성

자료 원본은 ../EX/content 이다. 원본을 고친 뒤 이 스크립트를 다시 돌리면 된다.
(자바스크립트 파일로 만드는 이유: file:// 로 그냥 열어도 fetch 제한 없이 읽히게 하려고)
"""
import json
import sys
import zipfile
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


CORE_FILES = ['__init__', 'formula', 'xlsx', 'pivots', 'vba', 'exam', 'build', 'analyze', 'content', 'compare', 'describe', 'library', 'official']
WHEELS = ['openpyxl', 'et_xmlfile', 'olefile']


def bundle():
    """브라우저 파이썬(Pyodide)에 풀어 쓸 묶음: core/*.py + 모의고사·문제 자료 + openpyxl 등 순수 파이썬 휠."""
    out = HERE / 'py' / 'bundle.zip'
    names = {}
    for n in CORE_FILES:
        names[f'app/core/{n}.py'] = SRC / 'core' / f'{n}.py'
    names['app/core/bridge.py'] = HERE / 'py' / 'bridge.py'
    cdir = SRC / 'content'
    for rel in ['datasets.json', 'functions.json']:
        names[f'app/content/{rel}'] = cdir / rel
    for sub in ('problems', 'exams', 'exams/files'):
        for p in sorted((cdir / sub).glob('*')):
            if p.is_file():
                names[f'app/content/{sub}/{p.name}'] = p
    for whl in sorted((HERE / 'py' / 'wheels').glob('*.whl')):
        with zipfile.ZipFile(whl) as z:
            for info in z.infolist():
                if info.is_dir() or '.dist-info/' in info.filename:
                    continue
                names[f'lib/{info.filename}'] = (whl, info.filename)
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for arc in sorted(names):
            src = names[arc]
            data = zipfile.ZipFile(src[0]).read(src[1]) if isinstance(src, tuple) else src.read_bytes()
            zi = zipfile.ZipInfo(arc, (2026, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, data, compresslevel=9)
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
    sizes['bundle'] = bundle()
    return {'written': len(qs), 'functions': len(funcs), 'problems': len(probs),
            'shortcuts': sum(len(g['items']) for g in sc['groups']), 'bytes': sizes}


if __name__ == '__main__':
    info = build()
    print('생성 완료:', json.dumps(info, ensure_ascii=False))
