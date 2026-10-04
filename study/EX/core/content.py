"""문제 은행·함수 사전 읽기와 수식 문제 채점."""
import datetime as dt
import json
import re
import threading
import time
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
    names = {k: (v if '!' in v or not fx.re.fullmatch(r'=?\$?[A-Za-z]{1,3}\$?\d+(:\$?[A-Za-z]{1,3}\$?\d+)?', v)
                 else '=' + SHEET + '!' + v.lstrip('='))
             for k, v in ((problem.get('sheet') or {}).get('names') or {}).items()}
    return fx.Book([sheet], today=FIXED_TODAY, names=names)


def fill_cells(problem):
    rng = problem.get('fill') or problem['target']
    r1, c1, r2, c2 = fx.parse_range(rng)
    return [(r, c) for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)]


TIME_BUDGET = 6.0          # 한 번 채점에 쓰는 계산 시간(초) — 넘으면 멈추고 안내


class TooSlow(Exception):
    pass


def _run(problem, ast, budget=None):
    """ast 를 채우기 범위 각 셀에 복사해 계산. → [(r, c, 값, 그 셀의 수식)]"""
    book = build_book(problem)
    sheet = book.sheets[SHEET]
    cells = fill_cells(problem)
    r0, c0 = cells[0]
    out = []
    start = time.monotonic()
    if len(cells) > 1:                       # 범위 전체에 한 번에 넣는 배열 수식(FREQUENCY 등): 결과 배열이 채우기 범위 모양이면 그대로 펼침
        first = fx.evaluate(ast, book, SHEET, r0, c0)
        rows = sorted({r for r, _ in cells})
        cols = sorted({c for _, c in cells})
        if isinstance(first, fx.Arr) and len(rows) * len(cols) == len(cells) and \
                first.h >= len(rows) and first.w >= len(cols) and (first.h > 1 or first.w > 1):
            text = '=' + fx.unparse(ast)
            for r, c in cells:
                v = first.rows[r - rows[0]][c - cols[0]]
                sheet.set(r, c, v)
                out.append((r, c, v, text))
            return out, book
    for r, c in cells:
        if budget and time.monotonic() - start > budget:
            raise TooSlow()
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


SPILL_MAX = 400            # 화면에 펼쳐 보일 배열 결과 칸 수


def _spill(r, c, v, formats):
    """동적 배열 결과를 아래·오른쪽 셀로 펼친다(너무 크면 앞부분만)."""
    if not isinstance(v, fx.Arr):
        return []
    out = []
    for i, row in enumerate(v.rows[:40]):
        for j, x in enumerate(row[:SPILL_MAX // 40]):
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
        msg = f'수식을 읽을 수 없습니다: {e}'
        if re.search(r'[ㄱ-ㅎㅏ-ㅣ가-힣]', re.sub(r'"[^"]*"', '', text)):
            msg += ' — 따옴표 밖에 한글이 있습니다. 한/영 키로 영문 입력인지 확인하세요.'
        return {'ok': False, 'error': msg}
    used = fx.functions_used(ast)
    unknown = sorted(n for n in used if n not in fx.FUNCS)
    circ = _self_reference(problem, ast)
    if circ:
        return {'ok': False, 'error': f'순환 참조: {circ} 셀의 수식이 자기 자신({circ})이 들어 있는 범위를 참조합니다 — '
                                      '엑셀에서는 0 과 순환 참조 경고가 나옵니다. 범위에서 결과 칸을 빼세요.'}
    if unknown and reveal:                              # 철자가 틀린 함수(=su 등)는 오답으로 기록하지 않음
        return {'ok': False, 'error': '이 연습장이 모르는 함수: ' + ', '.join(unknown) +
                ' — 철자를 확인하세요(함수 이름을 입력하는 중이면 목록에서 Tab·Enter 로 고르세요).'}
    try:
        res, _ = _run(problem, ast, budget=TIME_BUDGET)
    except TooSlow:
        return {'ok': False, 'error': '계산이 너무 오래 걸립니다 — 전체 열·아주 큰 범위 대신 표 범위(예: G2:G16)를 쓰세요.'}
    bad_args = next((v for _, _, v, _ in res if isinstance(v, fx.ArgError)), None)
    if bad_args is not None and reveal:
        return {'ok': False, 'error': f'{bad_args.func} 함수의 인수 개수(또는 형식)가 맞지 않습니다 — '
                                      f'함수 사전에서 {bad_args.func} 의 형식을 확인하세요.'}
    formats = sheet_spec(problem)[1]
    cells = []
    for r, c, v, f in res:
        cells.append(_cell_out(r, c, v, f, formats))
        cells.extend(_spill(r, c, v, formats))
    out = {'cells': cells, 'unknown': unknown}
    if not reveal:
        return out
    if res and all(isinstance(v, fx.XLErr) and fx.display(v) == '#NAME?' for _, _, v, _ in res):
        return {'ok': False, 'cells': cells, 'error': '#NAME? — 이 연습장이 모르는 이름이 들어 있습니다. 함수 이름 철자와 '
                '따옴표를 확인하세요(함수를 고르는 중이면 목록에서 Tab·Enter).'}
    exp = expected(problem)
    per = [fx.same_value(v, e[2]) for (_, _, v, _), e in zip(res, exp)]
    notes = []
    if unknown:
        notes.append('이 연습장이 모르는 함수: ' + ', '.join(unknown) + ' — 철자를 확인하세요.')
    need = [n.upper() for n in problem.get('require', [])]
    missing = [n for n in need if n not in used and not (n == 'RANK.EQ' and 'RANK' in used)]
    if missing:
        notes.append('이 문제는 ' + ', '.join(missing) + ' 함수를 써서 풀어야 합니다.')
    groups = problem.get('require_any') or []          # 예: [["MINIFS"], ["MIN", "IF"]] — 어느 한 묶음을 모두 쓰면 됨
    if groups and not any(all(n.upper() in used for n in g) for g in groups):
        missing.append('|'.join('+'.join(g) for g in groups))
        notes.append('이 문제는 ' + ' 또는 '.join('+'.join(g) for g in groups) + ' 로 풀어야 합니다.')
    banned = sorted(n for n in (x.upper() for x in problem.get('forbid', [])) if n in used)
    if banned:
        missing.append(','.join(banned))
        notes.append(', '.join(banned) + ' 함수는 쓰지 않고 풀어야 합니다(문제의 지시 방법대로).')
    if not fx.has_reference(ast):
        notes.append('값을 직접 입력하지 말고 셀을 참조하는 수식으로 만드세요.')
    if _wants_cse(problem) and not text.strip().startswith('{') and _needs_array(ast):
        missing.append('cse')
        notes.append('배열 수식으로 입력해야 합니다 — 엑셀에서는 Ctrl+Shift+Enter 로 확정해 {=…} 처럼 중괄호가 붙게 하세요'
                     '(여기서는 {=…} 로 입력). 중괄호 없이 Enter 만 누르면 Excel 2019 이하에서는 결과가 달라집니다.')
    exact = _exact_lookup_missing(problem.get('answer', ''), text)
    if exact:
        missing.append('exact')
        notes.append(f'{exact} 의 마지막 인수(찾는 방법)에 0 또는 FALSE 를 넣어 정확히 일치하는 값을 찾으세요 — '
                     '생략하면 비슷하게 일치(정렬된 표 전용)라 자료에 따라 엉뚱한 값이 나옵니다.')
    for (r, c, v, _), e, good in zip(res, exp, per):
        if good:
            continue
        if fx.display(v) == fx.display(e[2]) and isinstance(v, str) != isinstance(e[2], str):
            notes.append(f'{fx.addr(r, c)} 처럼 값은 같아 보여도 ' + ('문자' if isinstance(v, str) else '숫자') +
                         ' 입니다 — 기대한 것은 ' + ('문자' if isinstance(e[2], str) else '숫자') +
                         ' (예: LEFT·MID·RIGHT 결과는 문자, 숫자로 바꾸려면 VALUE 나 *1).')
            break
        if isinstance(v, str) and isinstance(e[2], str) and v.lower() == e[2].lower():
            notes.append(f'{fx.addr(r, c)}: 대소문자가 다릅니다("{v}" → "{e[2]}") — 따옴표 안 글자를 문제와 똑같이 쓰세요.')
            break
    ok = all(per) and not missing and fx.has_reference(ast)
    wrong = [fx.addr(r, c) for (r, c, _, _), good in zip(res, per) if not good]
    if wrong and len(res) > 1 and per[0] and fx.unparse(ast).count('$') < str(problem.get('answer', '')).count('$'):
        notes.append(f'첫 셀은 맞지만 {", ".join(wrong[:4])} 에서 결과가 다릅니다 — 채우기로 복사할 때 '
                     '고정해야 할 범위에 $ 를 붙였는지 확인하세요.')
    out.update(ok=ok, per=per, notes=notes,
               expected=[{'addr': fx.addr(r, c), 'value': display_cell(v, _fmt_of(formats, r, c))} for r, c, v, _ in exp])
    return out


def _wants_cse(problem):
    """'배열 수식으로' 구하라는 문제(정답이 {=…})."""
    return str(problem.get('answer', '')).startswith('{') and '배열 수식' in problem.get('prompt', '')


ARRAY_NATIVE = {'SUMPRODUCT', 'MMULT', 'SUMIF', 'SUMIFS', 'COUNTIF', 'COUNTIFS', 'AVERAGEIF', 'AVERAGEIFS', 'MAXIFS',
                'MINIFS', 'LOOKUP', 'AGGREGATE', 'XLOOKUP', 'XMATCH', 'FILTER', 'SORT', 'SORTBY', 'UNIQUE'}


def _needs_array(node, inside_native=False):
    """범위끼리 계산(B2:B16=I2, E2:E13*G2:G13)이 SUMPRODUCT 같은 함수 밖에 있으면 배열 수식이 필요."""
    if not isinstance(node, tuple):
        return False
    kind = node[0]
    if kind == 'call':
        native = inside_native or node[1] in ARRAY_NATIVE
        return any(_needs_array(a, native) for a in node[2])
    if kind in ('bin', 'neg', 'pct') and not inside_native:
        def has_range(n):
            if not isinstance(n, tuple):
                return False
            if n[0] == 'range':
                return True
            if n[0] == 'call':
                return False
            return any(has_range(x) for x in n[1:] if isinstance(x, tuple))
        if has_range(node):
            return True
    return any(_needs_array(x, inside_native) for x in node[1:] if isinstance(x, tuple))


def _self_reference(problem, ast):
    """채우기 범위의 칸이 자기 자신을 포함한 범위를 참조하면 그 칸 주소(엑셀 순환 참조)."""
    cells = fill_cells(problem)
    r0, c0 = cells[0]
    for r, c in cells[:400]:
        node = fx.shift(ast, r - r0, c - c0)
        if _refers_to(node, r, c):
            return fx.addr(r, c)
    return None


def _refers_to(node, r, c):
    if not isinstance(node, tuple):
        return False
    kind = node[0]
    if kind == 'ref':
        return node[1] in (None, SHEET) and node[2] == r and node[3] == c
    if kind == 'range':
        _, sh, r1, c1, _, _, r2, c2, _, _ = node
        if sh not in (None, SHEET):
            return False
        rows_ok = r1 is None or min(r1, r2) <= r <= max(r1, r2)
        return rows_ok and min(c1, c2) <= c <= max(c1, c2)
    if kind == 'call':
        if node[1] in ('ROW', 'ROWS', 'COLUMN', 'COLUMNS'):     # 위치만 쓰므로 순환 참조가 아님(=ROWS($H$2:H2))
            return False
        return any(_refers_to(a, r, c) for a in node[2])
    if kind == 'arr':
        return False
    return any(_refers_to(x, r, c) for x in node[1:] if isinstance(x, tuple))


def _lookup_calls(text):
    """수식 글자에서 VLOOKUP·HLOOKUP 호출마다 (함수 이름, 인수 글자 목록)."""
    out = []
    for m in re.finditer(r'\b([VH]LOOKUP)\s*\(', text, re.I):
        depth, args, cur, in_str = 0, [], '', False
        for ch in text[m.end():]:
            if ch == '"':
                in_str = not in_str
            if not in_str:
                if ch in '({':
                    depth += 1
                elif ch in ')}':
                    if depth == 0:
                        break
                    depth -= 1
                elif ch == ',' and depth == 0:
                    args.append(cur.strip())
                    cur = ''
                    continue
            cur += ch
        args.append(cur.strip())
        out.append((m.group(1).upper(), args))
    return out


def _exact_lookup_missing(answer, text):
    """정답이 정확히 일치(0·FALSE)로 찾는데 수험자 수식은 그 인수를 빠뜨렸거나 TRUE 면 그 함수 이름."""
    def exact(args):
        return len(args) >= 4 and args[3].upper() in ('0', 'FALSE', '')
    want = [n for n, a in _lookup_calls(answer) if exact(a)]
    if not want:
        return None
    for n, a in _lookup_calls(text):
        if not exact(a):
            return n
    return None


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
    head_cols = 0                                   # 1행에서 A 열부터 이어진 글자 = 표 머리글(그 오른쪽 입력 칸은 아님)
    while head_cols < cols_n and isinstance(sheet.get(1, head_cols + 1), str):
        head_cols += 1
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
                        'head': r == 1 and c <= head_cols})
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
