"""문제 은행·함수 사전 읽기와 수식 문제 채점."""
import datetime as dt
import json
import threading
from pathlib import Path

from . import formula as fx

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / 'content'
SHEET = 'Sheet1'
FIXED_TODAY = dt.date(2026, 10, 1)   # TODAY() 가 들어간 문제도 답이 바뀌지 않게

TRACKS = {'work': '실무', 'c2': '컴활 2급', 'c1': '컴활 1급'}

# (키, 이름, 종류, 설명)
CATEGORIES = [
    ('basic', '기본 집계', 'formula', 'SUM·AVERAGE·COUNT·MAX·MIN, 상대·절대 참조'),
    ('cond', '조건 집계', 'formula', 'SUMIF(S)·COUNTIF(S)·AVERAGEIF(S)·MAXIFS'),
    ('logic', '논리', 'formula', 'IF·IFS·AND·OR·IFERROR·SWITCH'),
    ('lookup', '찾기·참조', 'formula', 'VLOOKUP·HLOOKUP·INDEX/MATCH·XLOOKUP·CHOOSE'),
    ('text', '텍스트', 'formula', 'LEFT·MID·RIGHT·LEN·FIND·SUBSTITUTE·TEXT·&'),
    ('date', '날짜·시간', 'formula', 'DATE·YEAR·WEEKDAY·DATEDIF·EOMONTH·NETWORKDAYS'),
    ('math', '수학·통계·순위', 'formula', 'ROUND 계열·INT·MOD·RANK·LARGE·SUMPRODUCT'),
    ('db', '데이터베이스 함수', 'formula', 'DSUM·DAVERAGE·DCOUNT·DMAX·DGET (컴활)'),
    ('array', '배열 수식', 'formula', '{=SUM((조건)*(범위))} 형태 — 컴활 1급'),
    ('dynamic', '동적 배열', 'formula', 'FILTER·UNIQUE·SORT·SEQUENCE (Microsoft 365)'),
    ('pivot', '피벗 테이블', 'feature', '행·열·값 필드, 그룹, 필터, 계산 필드'),
    ('format', '서식·조건부 서식', 'feature', '표시 형식, 조건부 서식, 사용자 지정 형식'),
    ('data', '데이터 도구', 'feature', '정렬·필터·고급 필터·유효성 검사·중복 제거·부분합·통합'),
    ('analysis', '분석 도구', 'feature', '목표값 찾기·시나리오·데이터 표 (컴활)'),
    ('chart', '차트', 'feature', '차트 종류 선택, 요소, 보조 축, 스파크라인'),
    ('macro', '매크로·VBA', 'feature', '매크로 기록, 단추 연결, 간단한 VBA (컴활 1급)'),
    ('shortcut', '단축키·작업 효율', 'feature', '자주 쓰는 단축키와 실무 요령'),
]
CAT_NAMES = {k: n for k, n, _, _ in CATEGORIES}

_lock = threading.Lock()
_cache = {'stamp': None, 'data': None}


def _stamp():
    files = sorted(CONTENT.rglob('*.json'))
    return tuple((str(p), p.stat().st_mtime_ns) for p in files)


def _load():
    datasets = json.loads((CONTENT / 'datasets.json').read_text(encoding='utf-8'))
    problems, order = {}, []
    for p in sorted((CONTENT / 'problems').glob('*.json')):
        doc = json.loads(p.read_text(encoding='utf-8'))
        for item in doc.get('problems', []):
            item.setdefault('category', doc.get('category'))
            item['_file'] = p.name
            if item['id'] in problems:
                raise ValueError(f"문제 번호 중복: {item['id']} ({p.name})")
            problems[item['id']] = item
            order.append(item['id'])
    fpath = CONTENT / 'functions.json'
    functions = json.loads(fpath.read_text(encoding='utf-8'))['functions'] if fpath.exists() else []
    cat_rank = {k: i for i, (k, *_rest) in enumerate(CATEGORIES)}
    pos = {pid: n for n, pid in enumerate(order)}
    order.sort(key=lambda i: (cat_rank.get(problems[i]['category'], 99), problems[i].get('level', 1), pos[i]))
    return {'datasets': datasets, 'problems': problems, 'order': order, 'functions': functions}


def bank():
    """파일이 바뀌면 다시 읽는다(서버를 다시 켜지 않아도 문제 추가가 반영됨)."""
    with _lock:
        st = _stamp()
        if _cache['stamp'] != st:
            _cache['data'] = _load()
            _cache['stamp'] = st
        return _cache['data']


def problems(category=None, track=None):
    b = bank()
    out = [b['problems'][i] for i in b['order']]
    if category:
        out = [p for p in out if p['category'] == category]
    if track:
        out = [p for p in out if track in p.get('tracks', [])]
    return out


def get(pid):
    return bank()['problems'].get(pid)


def function_list():
    """자동 완성용 [이름, 형식, 한 줄 설명] — 함수 사전 + 별칭 + 사전에 없는 지원 함수."""
    out, seen = [], set()
    for f in bank()['functions']:
        desc = f.get('desc', '').split('.')[0][:60]
        for n in [f['name'], *f.get('aliases', [])]:
            key = n.upper()
            if key not in seen:
                seen.add(key)
                syntax = f.get('syntax', key + '()')
                if key != f['name'].upper():
                    syntax = key + syntax[len(f['name']):] if syntax.upper().startswith(f['name'].upper()) else syntax
                out.append([key, syntax, desc])
    for n in fx.FUNCS:
        if n not in seen:
            out.append([n, n + '()', ''])
    return sorted(out)


def function_doc(name):
    key = name.upper()
    for f in bank()['functions']:
        if f['name'].upper() == key or key in [a.upper() for a in f.get('aliases', [])]:
            return f
    return None


# ---------------------------------------------------------- 시트 만들기 -----
def _cell_value(v):
    if isinstance(v, str) and v.startswith('@') and len(v) >= 11:
        d = fx.parse_date_text(v[1:])
        if d is not None:
            return d
    if isinstance(v, str) and v.startswith('='):
        return fx.Formula(v)
    return v


def sheet_spec(problem):
    """문제의 시트 구성 → (셀 사전, 표시 형식 사전{열 문자 또는 주소: 형식})."""
    spec = problem.get('sheet') or {}
    cells, formats = {}, {}
    base = spec.get('base')
    if base:
        ds = bank()['datasets'][base]
        for i, row in enumerate(ds['rows']):
            for j, v in enumerate(row):
                if v is not None:
                    cells[(i + 1, j + 1)] = v
        formats.update(ds.get('formats', {}))
    for i, row in enumerate(spec.get('rows', [])):
        start_r, start_c = fx.parse_addr(spec.get('at', 'A1'))
        for j, v in enumerate(row):
            if v is not None:
                cells[(start_r + i, start_c + j)] = v
    for a, v in (spec.get('cells') or {}).items():
        cells[fx.parse_addr(a)] = v
    formats.update(spec.get('formats', {}))
    return cells, formats


def build_book(problem):
    cells, _ = sheet_spec(problem)
    sheet = fx.Sheet(SHEET, {k: _cell_value(v) for k, v in cells.items()})
    return fx.Book([sheet], today=FIXED_TODAY)


def fill_cells(problem):
    rng = problem.get('fill') or problem['target']
    r1, c1, r2, c2 = fx.parse_range(rng)
    return [(r, c) for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)]


def _run(problem, ast):
    """ast 를 채우기 범위 각 셀에 복사해 계산. → [(r, c, 값, 그 셀의 수식)]"""
    book = build_book(problem)
    sheet = book.sheets[SHEET]
    cells = fill_cells(problem)
    r0, c0 = cells[0]
    out = []
    for r, c in cells:
        node = fx.shift(ast, r - r0, c - c0)
        v = fx.evaluate(node, book, SHEET, r, c)
        sheet.set(r, c, v)
        out.append((r, c, v, '=' + fx.unparse(node)))
    return out, book


def expected(problem):
    ast = fx.parse(problem['answer'])
    res, _ = _run(problem, ast)
    return res


def _fmt_of(formats, r, c):
    return formats.get(fx.addr(r, c)) or formats.get(fx.col_name(c))


def _cell_out(r, c, v, f, formats):
    return {'addr': fx.addr(r, c), 'value': display_cell(v, _fmt_of(formats, r, c)), 'formula': f}


def _spill(r, c, v, formats):
    """동적 배열 결과를 아래·오른쪽 셀로 펼친다."""
    if not isinstance(v, fx.Arr):
        return []
    out = []
    for i, row in enumerate(v.rows):
        for j, x in enumerate(row):
            out.append({'addr': fx.addr(r + i, c + j), 'value': display_cell(x, _fmt_of(formats, r + i, c + j)),
                        'spill': True})
    return out


def display_cell(v, fmt=None):
    if isinstance(v, fx.Arr):
        return display_cell(v.rows[0][0], fmt) if v.h and v.w else ''
    if fmt and fx.is_num(v) and not isinstance(v, bool):
        try:
            return fx.format_value(v, fmt)
        except (fx.XLErr, ValueError, OverflowError):
            pass
    return fx.display(v)


def check(problem, text, reveal=True):
    """수식 문제 채점. reveal=False 면 계산 결과만 보여 주고 기록·정답 공개를 하지 않는다."""
    try:
        ast = fx.parse(text)
    except fx.FormulaError as e:
        return {'ok': False, 'error': f'수식을 읽을 수 없습니다: {e}'}
    used = fx.functions_used(ast)
    unknown = sorted(n for n in used if n not in fx.FUNCS)
    res, _ = _run(problem, ast)
    formats = sheet_spec(problem)[1]
    cells = []
    for r, c, v, f in res:
        cells.append(_cell_out(r, c, v, f, formats))
        cells.extend(_spill(r, c, v, formats))
    out = {'cells': cells, 'unknown': unknown}
    if not reveal:
        return out
    exp = expected(problem)
    per = [fx.same_value(v, e[2]) for (_, _, v, _), e in zip(res, exp)]
    notes = []
    if unknown:
        notes.append('이 연습장이 모르는 함수: ' + ', '.join(unknown) + ' — 철자를 확인하세요.')
    need = [n.upper() for n in problem.get('require', [])]
    missing = [n for n in need if n not in used and not (n == 'RANK.EQ' and 'RANK' in used)]
    if missing:
        notes.append('이 문제는 ' + ', '.join(missing) + ' 함수를 써서 풀어야 합니다.')
    if not fx.has_reference(ast):
        notes.append('값을 직접 입력하지 말고 셀을 참조하는 수식으로 만드세요.')
    ok = all(per) and not missing and fx.has_reference(ast)
    wrong = [fx.addr(r, c) for (r, c, _, _), good in zip(res, per) if not good]
    if wrong and len(res) > 1 and per[0]:
        notes.append(f'첫 셀은 맞지만 {", ".join(wrong[:4])} 에서 결과가 다릅니다 — 채우기로 복사할 때 '
                     '고정해야 할 범위에 $ 를 붙였는지 확인하세요.')
    out.update(ok=ok, per=per, notes=notes,
               expected=[{'addr': fx.addr(r, c), 'value': display_cell(v, _fmt_of(formats, r, c))} for r, c, v, _ in exp])
    return out


def check_choice(problem, picked):
    try:
        k = int(picked)
    except (TypeError, ValueError):
        return {'ok': False, 'error': '보기를 고르세요.'}
    return {'ok': k == problem['answer'], 'answer': problem['answer']}


def grid(problem, max_rows=40, max_cols=16):
    """화면에 그릴 시트: 머리글·행 번호·표시 값·대상 셀 표시."""
    cells, formats = sheet_spec(problem)
    book = build_book(problem)
    sheet = book.sheets[SHEET]
    fill = set(fill_cells(problem)) if problem.get('type') == 'formula' else set()
    rows_n = max([r for r, _ in cells] + [r for r, _ in fill] + [1])
    cols_n = max([c for _, c in cells] + [c for _, c in fill] + [1])
    if problem.get('spill_rows'):
        rows_n = max(rows_n, fx.parse_addr(problem['target'])[0] + int(problem['spill_rows']) - 1)
    rows_n, cols_n = min(rows_n + 1, max_rows), min(cols_n + 1, max_cols)
    out = []
    for r in range(1, rows_n + 1):
        row = []
        for c in range(1, cols_n + 1):
            v = sheet.get(r, c)
            a = fx.addr(r, c)
            fmt = formats.get(a) or formats.get(fx.col_name(c))
            text = ''
            if v is not None:
                if fmt and fx.is_num(v):
                    text = fx.format_value(v, fmt)
                else:
                    text = fx.display(v)
            row.append({'addr': a, 'text': text, 'num': fx.is_num(v), 'target': (r, c) in fill,
                        'head': r == 1 and v is not None and not fx.is_num(v)})
        out.append(row)
    return {'cols': [fx.col_name(c) for c in range(1, cols_n + 1)], 'rows': out}


def validate_problem(p):
    """문제 검사 — 도구·테스트에서 사용. 문제가 없으면 빈 목록."""
    errs = []
    pid = p.get('id', '?')
    for key in ('id', 'type', 'title', 'prompt', 'tracks', 'level', 'category'):
        if key not in p:
            errs.append(f'{pid}: {key} 없음')
    if p.get('category') not in CAT_NAMES:
        errs.append(f'{pid}: 알 수 없는 category {p.get("category")}')
    if not set(p.get('tracks', [])) <= set(TRACKS) or not p.get('tracks'):
        errs.append(f'{pid}: tracks 는 work/c2/c1 중에서')
    if p.get('type') == 'formula':
        try:
            exp = expected(p)
        except Exception as e:  # noqa: BLE001 — 어떤 문제든 보고
            return errs + [f'{pid}: 정답 수식 계산 실패 {e!r}']
        for r, c, v, _ in exp:
            if isinstance(v, fx.XLErr) and not p.get('allow_error'):
                errs.append(f'{pid}: 정답이 {fx.addr(r, c)} 에서 오류 {v.code}')
        for alt in p.get('alts', []):
            res = check(p, alt)
            if not res.get('ok'):
                errs.append(f'{pid}: 다른 정답 {alt} 가 맞지 않음 {res.get("error") or res.get("notes")}')
        main = check(p, p['answer'])
        if not main.get('ok'):
            errs.append(f'{pid}: 정답이 스스로 채점을 통과하지 못함 {main.get("notes")}')
        for bad in p.get('wrong', []):
            if check(p, bad).get('ok'):
                errs.append(f'{pid}: 오답 예시 {bad} 가 정답으로 채점됨')
    elif p.get('type') == 'choice':
        opts = p.get('options', [])
        if len(opts) < 2 or not isinstance(p.get('answer'), int) or not 0 <= p['answer'] < len(opts):
            errs.append(f'{pid}: 보기·정답 번호 확인')
    else:
        errs.append(f'{pid}: type 은 formula 또는 choice')
    if not p.get('explain'):
        errs.append(f'{pid}: explain(해설) 없음')
    return errs
