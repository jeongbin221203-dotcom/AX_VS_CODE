"""브라우저(Pyodide)에서 Flask 버전의 core/ 를 그대로 쓰기 위한 얇은 연결 계층.

JS 가 dispatch(이름, JSON 인수)를 부른다. 파일 바이트는 가상 파일 /tmp/in.bin(올린 파일) · /tmp/out.bin(만든 파일)로 주고받는다.
화면 만들기는 JS 가 맡고, 여기서는 Flask 뷰가 하던 데이터 준비만 한다.
"""
import io
import json
import re
import shutil
import zipfile
from pathlib import Path

from core import analyze as an
from core import build, compare, content, describe, library, official, xlsx
from core import exam as ex
from core import formula as fx

IN = Path('/tmp/in.bin')
ANS = Path('/tmp/ans.bin')
USER = Path('/tmp/user.bin')
OUT = Path('/tmp/out.bin')
CIRCLED = '①②③④⑤⑥⑦⑧⑨⑩'
_docs = {}          # 분석 중인 파일(요약 표) — 브라우저 IndexedDB 에서 필요할 때 다시 받는다


def _in():
    return IN.read_bytes()


def _out(data):
    OUT.write_bytes(data)
    return len(data)


# ------------------------------------------------------------ 실기 모의고사 -
def _items(raw):
    """지시사항 줄 → [{kind: item|sub|code, num, text}] (views/exam.py 와 같음)."""
    own = any(x.lstrip()[:1] in CIRCLED for x in raw)
    out, k = [], 0
    for x in raw:
        if '\n' in x or x.startswith(('Public Function', 'Function ', 'Sub ')):
            out.append({'kind': 'code', 'num': '', 'text': x})
        elif x.startswith(('  -', '- ', '  ')):
            out.append({'kind': 'sub', 'num': '', 'text': x.strip().lstrip('-').strip()})
        elif x.startswith('▶') or len(raw) == 1:
            out.append({'kind': 'item', 'num': '▶', 'text': x.lstrip('▶ ')})
        elif own:
            out.append({'kind': 'item', 'num': '', 'text': x})
        else:
            out.append({'kind': 'item', 'num': CIRCLED[k], 'text': x})
            k += 1
    return out


def _show(v):
    if v is None:
        return ''
    if isinstance(v, str) and v.startswith('@'):
        return v[1:]
    return str(v)


def exam_list(_a):
    groups = []
    for k, n in ex.LEVELS.items():
        items = [{'id': e['id'], 'title': e['title'], 'minutes': e['minutes'],
                  'sections': {s: p for s, p in ex.section_points(e).items() if p}} for e in ex.exams(k)]
        groups.append({'level': k, 'name': n, 'items': items})
    return {'groups': groups}


def exam_paper(a):
    e = ex.get(a['id'])
    if not e:
        return {'error': '없는 모의고사입니다.'}
    sections = []
    for s in ex.SECTIONS:
        tasks = [t for t in e['tasks'] if t['section'] == s]
        if tasks:
            sections.append({'name': s, 'points': sum(ex.task_points(t) for t in tasks), 'tasks': [
                {'no': t['no'], 'title': t['title'], 'text': t.get('text', ''), 'points': ex.task_points(t),
                 'table': [[_show(v) for v in row] for row in t['table']['rows']] if t.get('table') else None,
                 'items': _items(t.get('items') or [])} for t in tasks]})
    return {'id': e['id'], 'title': e['title'], 'level': e['level'], 'levelName': ex.LEVELS[e['level']],
            'round': e.get('round', ''), 'minutes': e['minutes'], 'pass': e['pass'], 'intro': e.get('intro', ''),
            'forms': [f['name'][3:] for f in (e.get('forms') or [])], 'dataFiles': list((e.get('data_files') or {}).keys()),
            'sections': sections}


def exam_file(a):
    e = ex.get(a['id'])
    data, ext = ex.problem_file(e)
    name = f"{ex.LEVELS[e['level']].replace(' ', '')}_모의{e.get('round', '')}회.{ext}"
    _out(data)
    return {'name': name}


def exam_data(a):
    e = ex.get(a['id'])
    data = ex.data_file(e, a['name'])
    if data is None:
        return {'error': '없는 자료 파일입니다.'}
    _out(data)
    return {'name': a['name']}


def exam_grade(a):
    e = ex.get(a['id'])
    name = a.get('name', '답안.xlsx')
    if not name.lower().endswith(('.xlsx', '.xlsm')):
        return {'error': '.xlsx 또는 .xlsm 파일만 올릴 수 있습니다.'}
    try:
        return {'res': ex.grade(e, _in(), name)}
    except xlsx.BadFile as err:
        return {'error': str(err)}


# ------------------------------------------------------------ 대시보드 실습 -
def build_list(_a):
    return {'missions': [{'key': m['key'], 'title': m['title'], 'level': m['level'], 'tracks': m['tracks'],
                          'desc': m['desc'], 'count': len([c for c in m['checks'] if not c.get('optional')])}
                         for m in build.MISSIONS.values()], 'tracks': content.TRACKS}


def build_task(a):
    m = build.mission(a['key'])
    if not m:
        return {'error': '없는 과제입니다.'}
    return {'key': m['key'], 'title': m['title'], 'desc': m['desc'],
            'checks': [{'label': c['label'], 'at': c.get('at', ''), 'hint': c.get('hint', ''),
                        'optional': bool(c.get('optional'))} for c in m['checks']]}


def build_file(a):
    m = build.mission(a['key'])
    _out(build.workbook(m, answers=bool(a.get('answers'))))
    return {'name': ('완성예시_' if a.get('answers') else '실습_') + m['title'] + '.xlsx'}


def build_grade(a):
    m = build.mission(a['key'])
    if not a.get('name', '').lower().endswith(('.xlsx', '.xlsm')):
        return {'error': '.xlsx 파일로 저장해서 올려 주세요.'}
    try:
        return {'res': build.grade(m, _in())}
    except xlsx.BadFile as err:
        return {'error': str(err)}


# ------------------------------------------------------------ 파일 분석 ----
def an_sample(_a):
    _out(an.sample_file())
    return {'name': '샘플_상반기매출.xlsx'}


def an_read(a):
    """올린 파일 → 시트별 요약 표. 문서(doc)를 돌려주고 JS 가 IndexedDB 에 보관한다."""
    name = a.get('name', '')
    try:
        sheets = an.read_sheets(_in(), name)
        tables, errors = {}, {}
        for sname, rows in sheets:
            try:
                tables[sname] = an.to_table(sname, rows)
            except xlsx.BadFile as e:
                errors[sname] = str(e)
        if not tables:
            raise xlsx.BadFile(next(iter(errors.values()), '읽을 수 있는 표가 없습니다.'))
    except xlsx.BadFile as e:
        return {'error': str(e)}
    doc = {'name': name, 'tables': tables, 'skipped': errors}
    default = an.default_table(doc)
    return {'doc': doc, 'sheet': default['sheet']}


def an_open(a):
    _docs[a['uid']] = json.loads(a['doc'])
    return {'ok': True}


def an_has(a):
    return {'has': a['uid'] in _docs}


def _table(a):
    doc = _docs[a['uid']]
    t = doc['tables'].get(a.get('sheet')) if a.get('sheet') else None
    return doc, (t or an.default_table(doc))


def an_view(a):
    doc, table = _table(a)
    dash = an.dashboard(table, a.get('params') or {})
    return {'name': doc['name'], 'sheets': list(doc['tables'].keys()), 'table': {k: v for k, v in table.items() if k != 'rows'},
            'rowCount': len(table['rows']), 'dash': dash, 'aggs': an.AGGS}


def an_export(a):
    doc, table = _table(a)
    dash = an.dashboard(table, a.get('params') or {})
    _out(an.export_xlsx(table, dash))
    return {'name': doc['name'].rsplit('.', 1)[0] + '_요약.xlsx'}


# ------------------------------------------------------------ 수식 문제 -----
def formula_check(a):
    """문제 풀기의 수식 채점(자체 엑셀 계산기로 값 비교). a = {id, text, reveal}"""
    p = content.get(a['id'])
    if not p:
        return {'error': '없는 문제입니다.'}
    res = content.check(p, str(a.get('text') or '')[:1000], reveal=bool(a.get('reveal', True)))
    return res


def formula_grid(a):
    p = content.get(a['id'])
    return {'grid': content.grid(p) if p else None}


# ------------------------------------------------------------ 실기 실습 ----
def pr_scan(a):
    """폴더에서 고른 파일 경로 목록 → 실습/정답 짝(library.scan 을 빈 파일 뼈대에 그대로 돌린다)."""
    root = Path('/tmp/scan')
    shutil.rmtree(root, ignore_errors=True)
    paths = [str(x).replace(chr(92), '/').strip('/') for x in a.get('paths', [])][:4000]
    try:
        for rel in paths:
            parts = rel.split('/')
            if '..' in parts or not all(parts):
                continue
            f = root.joinpath(*parts)
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b'')
        pairs = library.scan(root)
        out = [{'rel': x['rel'].replace(chr(92), '/'), 'title': x['title'], 'group': x['group'], 'level': x['level'],
                'category': x['category'], 'practice': x['practice'].relative_to(root).as_posix(),
                'answer': x['answer'].relative_to(root).as_posix(),
                'extras': [e.relative_to(root).as_posix() for e in x['extras']]} for x in pairs]
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return {'pairs': out}


def pr_official(a):
    """대한상공회의소 예제 zip(사용자가 공식 사이트에서 받은 것) → 세트별 문제지·소스·정답 파일을 /tmp/off/<세트>/ 에 푼다."""
    try:
        z = zipfile.ZipFile(IN)
    except zipfile.BadZipFile:
        return {'error': 'zip 파일이 아닙니다.'}
    names = official._zip_names(z)
    zipname = a.get('zipname', '').replace(' ', '')
    root = Path('/tmp/off')
    shutil.rmtree(root, ignore_errors=True)
    found = []
    for sid, pkg, level, title, where, roles in official.SETS:
        if not where and not ((pkg == '2015-c1' and '1급엑셀' in zipname) or (pkg == '2015-c2' and '2급엑셀' in zipname)):
            continue
        dest = root / sid
        files = {}
        for name, info in names.items():
            base = name.rsplit('/', 1)[-1]
            if not base or base.startswith('~') or (where and f'/{where}/' not in f'/{name}'):
                continue
            for role, pat in roles.items():
                if re.search(pat, base):
                    if role == 'extra' and re.search(roles.get('source', '^$'), base):
                        continue
                    dest.mkdir(parents=True, exist_ok=True)
                    (dest / base).write_bytes(z.read(info))
                    if role == 'extra':
                        files.setdefault('extra', []).append(base)
                    elif role not in files:
                        files[role] = base
                    break
        if files.get('source'):
            found.append({'id': sid, 'level': level, 'title': title, 'files': files})
    if not found:
        return {'error': '대한상공회의소 예제 꾸러미가 아닙니다 — 공식 사이트의 "컴퓨터활용능력 예제 문제" zip 을 그대로 고르세요.'}
    return {'sets': found}


def pr_tasks(a):
    """실습 파일을 정답과 비교한 결과 = 해야 할 일 목록(+ 교재 짝이면 자동 지문)."""
    p, ans = IN.read_bytes(), ANS.read_bytes()
    try:
        res = compare.grade(p, ans, p)
    except xlsx.BadFile as e:
        return {'tasks': [], 'problem': [], 'warn': str(e)}
    tasks = [{'name': s['name'], 'points': s['points'], 'items': [
        {'label': i['label'], 'hint': i.get('hint'), 'code': i.get('code')} for i in s['items']]}
        for s in res['sheets'] if s['items']]
    problem = []
    if a.get('describe'):
        try:
            problem = describe.describe(p, ans)
        except Exception:  # noqa: BLE001 — 지문 만들기에 실패해도 채점 화면은 연다
            problem = []
        for sh in problem:
            for t in sh['tasks']:
                t.pop('_key', None)
    return {'tasks': tasks, 'problem': problem}


def pr_grade(a):
    name = a.get('name', '')
    if not name.lower().endswith(('.xlsx', '.xlsm', '.xltm')):
        return {'error': '.xlsx 또는 .xlsm 파일을 올려 주세요.'}
    try:
        return {'res': compare.grade(IN.read_bytes(), ANS.read_bytes(), USER.read_bytes(), a.get('level'))}
    except xlsx.BadFile as e:
        return {'error': str(e)}


FUNCS = {k: v for k, v in dict(globals()).items()
         if k.startswith(('exam_', 'build_', 'an_', 'formula_', 'pr_')) and callable(v)}


def dispatch(name, args_json):
    try:
        fn = FUNCS[name]
        return json.dumps(fn(json.loads(args_json or '{}')), ensure_ascii=False, default=str)
    except xlsx.BadFile as e:
        return json.dumps({'error': str(e)}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001 — 화면에 원인을 알려 준다
        return json.dumps({'error': f'처리 중 오류가 났습니다: {type(e).__name__}: {e}'}, ensure_ascii=False)
