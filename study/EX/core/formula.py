"""작은 엑셀 수식 계산기 — 학습 문제 채점과 실습 파일 확인에 쓴다.

지원: 사칙·^·&·비교·%, 셀/범위/열 전체/다른 시트 참조, 배열 상수 {1,2;3,4},
배열 수식(범위끼리 연산 → 요소별), 함수 약 130개(FUNCS 참고).
값: 숫자(int/float), 문자열, bool, None(빈 셀), Arr(2차원), XLErr(오류 값).
"""
import calendar
import datetime as dt
import math
import re
import unicodedata
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal

EPOCH = dt.date(1899, 12, 30)


# ---------------------------------------------------------------- 값 ---------
class XLErr(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code

    def __repr__(self):
        return self.code

    def __eq__(self, other):
        return isinstance(other, XLErr) and other.code == self.code

    def __hash__(self):
        return hash(self.code)


NA, VALUE, REF, DIV0, NUM, NAME = (XLErr(c) for c in ('#N/A', '#VALUE!', '#REF!', '#DIV/0!', '#NUM!', '#NAME?'))
ERRORS = {e.code: e for e in (NA, VALUE, REF, DIV0, NUM, NAME, XLErr('#NULL!'), XLErr('#SPILL!'), XLErr('#CALC!'))}


class _Empty:
    """=VLOOKUP(a,b,2,) 처럼 쉼표 뒤가 빈 인수. '생략'과 달리 0/FALSE/"" 로 쓰인다."""
    __slots__ = ()

    def __repr__(self):
        return 'EMPTY'


EMPTY = _Empty()


class ArgError(XLErr):
    """함수 인수 개수·형식이 맞지 않음(엑셀은 입력을 거부, 여기서는 #VALUE! 와 안내)."""

    def __init__(self, func):
        super().__init__('#VALUE!')
        self.func = func


class FormulaError(ValueError):
    """수식을 읽을 수 없을 때(문법 오류·지원하지 않는 기능)."""


class Arr:
    """2차원 값 묶음. origin=(시트, 행, 열) 이면 범위 참조에서 온 것."""
    __slots__ = ('rows', 'origin')

    def __init__(self, rows, origin=None):
        self.rows = rows
        self.origin = origin

    @property
    def h(self):
        return len(self.rows)

    @property
    def w(self):
        return len(self.rows[0]) if self.rows else 0

    def flat(self):
        return [v for row in self.rows for v in row]

    def vector(self):
        if self.h != 1 and self.w != 1:
            raise VALUE
        return self.flat()

    def map(self, fn):
        return Arr([[fn(v) for v in row] for row in self.rows])

    def __repr__(self):
        return f'Arr({self.rows})'


def is_num(v):
    return type(v) in (int, float)


def date_serial(d):
    if isinstance(d, dt.datetime):
        delta = d - dt.datetime(1899, 12, 30)
        return delta.days + delta.seconds / 86400
    return (d - EPOCH).days


def serial_date(n):
    try:
        return EPOCH + dt.timedelta(days=int(math.floor(n)))
    except (OverflowError, ValueError):
        raise XLErr('#VALUE!')


NUM_RE = re.compile(r'[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?')
TIME_RE = re.compile(r'(?:(오전|오후)\s*)?(\d{1,2}):(\d{2})(?::(\d{2}(?:\.\d+)?))?\s*(AM|PM|A|P|오전|오후)?', re.I)


def _time_part(t):
    m = TIME_RE.fullmatch(t.strip())
    if not m:
        return None
    h, mi = int(m.group(2)), int(m.group(3))
    sec = float(m.group(4) or 0)
    ampm = (m.group(1) or m.group(5) or '').upper()
    if ampm in ('PM', 'P', '오후') and h < 12:
        h += 12
    if ampm in ('AM', 'A', '오전') and h == 12:
        h = 0
    if mi > 59 or sec >= 60:
        return None
    return (h * 3600 + mi * 60 + sec) / 86400


def parse_date_text(s):
    """'2026-10-03', '2026년 10월 3일', '10:30', '2026-10-03 10:30' → 일련번호(아니면 None)."""
    s = s.strip()
    m = re.fullmatch(r'(\d{4})[-./년 ]\s*(\d{1,2})[-./월 ]\s*(\d{1,2})일?(?:\s+(.+))?', s)
    if m:
        try:
            base = date_serial(dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            return None
        if m.group(4):
            t = _time_part(m.group(4))
            return None if t is None else base + t
        return base
    t = _time_part(s)
    return t


# ---------------------------------------------------------- 시트·통합문서 ----
class Sheet:
    """셀 값 저장소. 값이 Formula 이면 처음 읽을 때 계산해 기억한다."""

    def __init__(self, name, cells=None):
        self.name = name
        self.cells = dict(cells or {})
        self.book = None
        self._busy = set()

    @property
    def max_row(self):
        return max((r for r, _ in self.cells), default=0)

    @property
    def max_col(self):
        return max((c for _, c in self.cells), default=0)

    def raw(self, r, c):
        return self.cells.get((r, c))

    def get(self, r, c):
        v = self.cells.get((r, c))
        if isinstance(v, Formula) and v.cached is not None:
            return v.cached
        if isinstance(v, Formula):
            key = (r, c)
            if key in self._busy:
                return XLErr('#CIRC!')
            self._busy.add(key)
            try:
                res = v.evaluate(self.book, self.name, r, c)
            finally:
                self._busy.discard(key)
            if isinstance(res, Arr):  # 넘치는 결과는 왼쪽 위 값만 셀에 둔다
                res = res.rows[0][0] if res.h and res.w else None
            v.cached = res          # 수식은 남겨 둔다(D함수 계산 조건 등이 수식을 다시 읽음)
            return res
        return v

    def set(self, r, c, v):
        self.cells[(r, c)] = v


class Book:
    def __init__(self, sheets=(), today=None, names=None):
        self.sheets = {}
        self.today = today or dt.date.today()
        self.names = {}             # 이름 정의: 대문자 이름 → AST (예: 단가표 → 'Sheet1'!$K$2:$L$6)
        for s in sheets:
            self.add(s)
        for k, v in (names or {}).items():
            self.define(k, v)

    def define(self, name, text):
        """이름 정의 추가. text 는 '=$K$2:$L$6' · 'Sheet1!$A$1' · '=0.1' 같은 수식."""
        self.names[name.upper()] = parse(text if text.startswith('=') else '=' + text)

    def add(self, sheet):
        sheet.book = self
        self.sheets[sheet.name] = sheet
        return sheet

    def sheet(self, name):
        for k, s in self.sheets.items():
            if k.lower() == name.lower():
                return s
        raise REF


class Formula:
    """수식 셀. cached 가 있으면(엑셀이 저장한 결과) 다시 계산하지 않고 그 값을 쓴다."""

    def __init__(self, text, cached=None):
        self.text = text
        self.ast = parse(text)
        self.cached = cached

    def evaluate(self, book, sheet, r, c):
        return evaluate(self.ast, book, sheet, r, c)


# ------------------------------------------------------------- 주소 ----------
def col_num(letters):
    n = 0
    for ch in letters.upper():
        n = n * 26 + ord(ch) - 64
    return n


def col_name(n):
    s = ''
    while n:
        n, rem = divmod(n - 1, 26)
        s = chr(65 + rem) + s
    return s


def addr(r, c):
    return f'{col_name(c)}{r}'


def parse_addr(a):
    m = re.fullmatch(r'\$?([A-Za-z]{1,3})\$?(\d+)', a.strip())
    if not m:
        raise ValueError(f'잘못된 셀 주소: {a}')
    return int(m.group(2)), col_num(m.group(1))


def parse_range(a):
    """'B2:D9' 또는 'B2' → (r1, c1, r2, c2)."""
    parts = a.split(':')
    r1, c1 = parse_addr(parts[0])
    r2, c2 = parse_addr(parts[-1])
    return min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2)


# ------------------------------------------------------------- 토큰 ----------
_SHEET = r"(?:(?P<sheet>'(?:[^']|'')+'|[A-Za-z_가-힣][\w가-힣.]*)!)?"
_CELL = r'\$?[A-Za-z]{1,3}\$?\d+'
TOKEN_RE = re.compile(r'''
    (?P<ws>\s+)
  | (?P<str>"(?:[^"]|"")*")
  | (?P<err>\#(?:N/A|VALUE!|REF!|DIV/0!|NUM!|NAME\?|NULL!))
  | (?P<func>(?:_xlfn\.|_xlws\.)*[A-Za-z_가-힣][A-Za-z0-9._가-힣]*)\(
  | (?P<ref>''' + _SHEET + r'''(?P<a>''' + _CELL + r''')(?::(?P<b>''' + _CELL + r'''))?)(?![A-Za-z0-9_(])
  | (?P<cols>''' + _SHEET.replace('sheet', 'csheet') + r'''(?P<ca>\$?[A-Za-z]{1,3}):(?P<cb>\$?[A-Za-z]{1,3}))(?![A-Za-z0-9_(])
  | (?P<num>(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<bool>TRUE|FALSE)(?![A-Za-z0-9_(])
  | (?P<name>[A-Za-z_가-힣][\w가-힣.]*)
  | (?P<op><>|<=|>=|[-+*/^&=<>%(),;{}:])
''', re.X | re.I)

NORMALIZE = str.maketrans({'＝': '=', '，': ',', '（': '(', '）': ')', '“': '"', '”': '"', '＄': '$', '：': ':',
                           '＋': '+', '－': '-', '＊': '*', '／': '/', '＜': '<', '＞': '>', '＆': '&', '；': ';',
                           '｛': '{', '｝': '}', ' ': ' '})


def clean_input(text):
    """사용자 입력 정리: 전각 기호·전각 영문, 배열 수식 중괄호 {=...}, 앞의 = / +."""
    t = (text or '').translate(NORMALIZE).strip()
    t = ''.join(x if x.startswith('"') else unicodedata.normalize('NFKC', x) for x in re.split(r'("[^"]*")', t))
    if t.startswith('{=') and t.endswith('}'):
        t = t[1:-1].strip()
    if t.startswith('='):
        t = t[1:]
    elif t.startswith('+'):
        t = t[1:]
    return t.strip()


def _ref_part(text):
    m = re.fullmatch(r'(\$?)([A-Za-z]{1,3})(\$?)(\d+)', text)
    return int(m.group(4)), col_num(m.group(2)), bool(m.group(3)), bool(m.group(1))


def _sheet_name(s):
    if not s:
        return None
    if s.startswith("'"):
        return s[1:-1].replace("''", "'")
    return s


def tokenize(text):
    out, pos = [], 0
    while pos < len(text):
        m = TOKEN_RE.match(text, pos)
        if not m:
            raise FormulaError(f'읽을 수 없는 문자: {text[pos:pos + 10]!r}')
        pos = m.end()
        kind = m.lastgroup
        if kind == 'ws':
            continue
        if m.group('str') is not None:
            out.append(('str', m.group('str')[1:-1].replace('""', '"')))
        elif m.group('err') is not None:
            out.append(('err', m.group('err').upper()))
        elif m.group('func') is not None:
            name = re.sub(r'^(?:_xlfn\.|_xlws\.)+', '', m.group('func')).upper()
            out.append(('func', name))
        elif m.group('ref') is not None:
            sheet = _sheet_name(m.group('sheet'))
            a = _ref_part(m.group('a'))
            b = _ref_part(m.group('b')) if m.group('b') else None
            if b:
                out.append(('range', sheet, a, b))
            else:
                out.append(('ref', sheet, a))
        elif m.group('cols') is not None:
            sheet = _sheet_name(m.group('csheet'))
            ca, cb = m.group('ca'), m.group('cb')
            a = (None, col_num(ca.lstrip('$')), True, ca.startswith('$'))
            b = (None, col_num(cb.lstrip('$')), True, cb.startswith('$'))
            out.append(('range', sheet, a, b))
        elif m.group('num') is not None:
            s = m.group('num')
            out.append(('num', float(s) if re.search(r'[.eE]', s) else int(s)))
        elif m.group('bool') is not None:
            out.append(('bool', m.group('bool').upper() == 'TRUE'))
        elif m.group('name') is not None:
            out.append(('name', m.group('name')))
        else:
            out.append(('op', m.group('op')))
    out.append(('end', None))
    return out


# ------------------------------------------------------------- 파서 ----------
BIN_PREC = {'=': 1, '<>': 1, '<': 1, '>': 1, '<=': 1, '>=': 1, '&': 2, '+': 3, '-': 3, '*': 4, '/': 4, '^': 5}


class Parser:
    def __init__(self, tokens):
        self.t = tokens
        self.i = 0

    def peek(self):
        return self.t[self.i]

    def take(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def expect(self, op):
        tok = self.take()
        if tok != ('op', op):
            raise FormulaError(f"'{op}' 가 필요합니다")

    def expr(self, min_prec=1):
        left = self.unary()
        while True:
            tok = self.peek()
            if tok[0] != 'op' or tok[1] not in BIN_PREC or BIN_PREC[tok[1]] < min_prec:
                return left
            op = tok[1]
            self.take()
            prec = BIN_PREC[op]
            right = self.expr(prec + 1)  # 엑셀은 ^ 도 왼쪽부터 계산
            left = ('bin', op, left, right)

    def unary(self):
        tok = self.peek()
        if tok in (('op', '-'), ('op', '+')):
            self.take()
            inner = self.unary()
            return ('neg', inner) if tok[1] == '-' else inner
        return self.postfix()

    def postfix(self):
        node = self.primary()
        while self.peek() == ('op', '%'):
            self.take()
            node = ('pct', node)
        return node

    def primary(self):
        tok = self.take()
        kind = tok[0]
        if kind in ('num', 'str', 'bool'):
            return (kind, tok[1])
        if kind == 'err':
            return ('err', tok[1])
        if kind == 'ref':
            node = ('ref', tok[1], *tok[2])
            if self.peek() == ('op', ':') and self.t[self.i + 1][0] == 'func':
                raise FormulaError(f"범위 끝에 함수를 쓰는 형태(A1:{self.t[self.i + 1][1]}(…))는 이 연습장에서 아직 계산하지 못합니다 — OFFSET 등 다른 방법으로 써 보세요")
            if self.peek() == ('op', ':') and self.t[self.i + 1][0] == 'ref':
                self.take()
                b = self.take()
                return ('range', tok[1], *tok[2], *b[2])
            return node
        if kind == 'range':
            return ('range', tok[1], *tok[2], *tok[3])
        if kind == 'func':
            args = []
            if self.peek() == ('op', ')'):
                self.take()
                return ('call', tok[1], args)
            while True:
                if self.peek() in (('op', ','), ('op', ')')):
                    args.append(('empty',))
                else:
                    args.append(self.expr())
                sep = self.take()
                if sep == ('op', ')'):
                    return ('call', tok[1], args)
                if sep[0] == 'end':                    # 수식 끝에서 괄호가 안 닫힘 → 엑셀처럼 닫아 준다
                    self.i -= 1
                    return ('call', tok[1], args)
                if sep != ('op', ','):
                    raise FormulaError(f"{tok[1]} 함수의 인수 사이에는 쉼표(,)가 필요합니다 — '{sep[1]}' 근처를 확인하세요")
        if tok == ('op', '('):
            node = self.expr()
            if self.peek()[0] != 'end':                # 끝에서 안 닫힌 괄호는 닫아 준다
                self.expect(')')
            return node
        if tok == ('op', '{'):
            rows, row = [], []
            while True:
                neg = False
                if self.peek() == ('op', '-'):
                    self.take()
                    neg = True
                v = self.take()
                if v[0] not in ('num', 'str', 'bool', 'err'):
                    raise FormulaError('배열 상수에는 숫자·문자만 쓸 수 있습니다')
                val = ERRORS[v[1]] if v[0] == 'err' else v[1]
                row.append(-val if neg else val)
                sep = self.take()
                if sep == ('op', ','):
                    continue
                rows.append(row)
                row = []
                if sep == ('op', ';'):
                    continue
                if sep == ('op', '}'):
                    break
                raise FormulaError('배열 상수 형식이 잘못되었습니다')
            if len({len(r) for r in rows}) != 1:
                raise FormulaError('배열 상수의 행 길이가 다릅니다')
            return ('arr', rows)
        if kind == 'name':
            return ('name', tok[1])
        if kind == 'end':
            raise FormulaError('수식이 끝나지 않았습니다')
        raise FormulaError(f"예상하지 못한 '{tok[1]}'")


def parse(text):
    t = clean_input(text)
    if not t:
        raise FormulaError('수식이 비어 있습니다')
    if len(t) > 8000:
        raise FormulaError('수식이 너무 깁니다')
    p = Parser(tokenize(t))
    try:
        node = p.expr()
    except RecursionError:
        raise FormulaError('괄호가 너무 깊게 겹쳐 있습니다')
    if p.peek()[0] != 'end':
        tok = p.peek()
        raise FormulaError(f"'{tok[1]}' 근처에서 수식이 잘못되었습니다")
    return node


def functions_used(node, acc=None):
    acc = set() if acc is None else acc
    if isinstance(node, tuple):
        if node[0] == 'call':
            acc.add(node[1])
            for a in node[2]:
                functions_used(a, acc)
        else:
            for x in node[1:]:
                if isinstance(x, tuple):
                    functions_used(x, acc)
    return acc


def has_reference(node):
    if not isinstance(node, tuple):
        return False
    if node[0] in ('ref', 'range', 'name'):
        return True
    if node[0] == 'call':
        if node[1] in ('ROW', 'COLUMN') and not [a for a in node[2] if a != ('empty',)]:
            return True                        # =ROW()-1 은 자기 위치를 쓰는 수식
        return any(has_reference(a) for a in node[2])
    return any(has_reference(x) for x in node[1:] if isinstance(x, tuple))


def shift(node, dr, dc):
    """채우기 핸들로 dr행·dc열 복사했을 때의 수식(상대 참조만 움직인다)."""
    if not isinstance(node, tuple):
        return node
    kind = node[0]
    if kind == 'ref':
        _, sh, r, c, ra, ca = node
        r2 = r if ra else r + dr
        c2 = c if ca else c + dc
        if r2 < 1 or c2 < 1:
            return ('err', '#REF!')
        return ('ref', sh, r2, c2, ra, ca)
    if kind == 'range':
        _, sh, r1, c1, ra1, ca1, r2, c2, ra2, ca2 = node
        nr1 = r1 if (ra1 or r1 is None) else r1 + dr
        nc1 = c1 if ca1 else c1 + dc
        nr2 = r2 if (ra2 or r2 is None) else r2 + dr
        nc2 = c2 if ca2 else c2 + dc
        if min(x for x in (nr1, nc1, nr2, nc2) if x is not None) < 1:
            return ('err', '#REF!')
        return ('range', sh, nr1, nc1, ra1, ca1, nr2, nc2, ra2, ca2)
    if kind == 'call':
        return ('call', node[1], [shift(a, dr, dc) for a in node[2]])
    if kind == 'arr':
        return node
    return tuple(shift(x, dr, dc) if isinstance(x, tuple) else x for x in node)


# ------------------------------------------------------------- 평가 ----------
class Ctx:
    __slots__ = ('book', 'sheet', 'r', 'c')

    def __init__(self, book, sheet, r, c):
        self.book, self.sheet, self.r, self.c = book, sheet, r, c

    def sheet_obj(self, name):
        return self.book.sheet(name or self.sheet)


def evaluate(node, book, sheet, r=1, c=1):
    """수식 AST 계산. 오류는 XLErr 값으로 돌려준다. 빈 셀만 가리키면 0."""
    ctx = Ctx(book, sheet, r, c)
    try:
        v = ev(node, ctx)
    except XLErr as e:
        return e
    except RecursionError:
        return XLErr('#CALC!')
    if v is None:
        return 0
    if isinstance(v, Arr) and v.h == 1 and v.w == 1:
        v = v.rows[0][0] if v.rows[0][0] is not None else 0
    if isinstance(v, Arr):
        return Arr([[0 if x is None else _final(x) for x in row] for row in v.rows])
    return _final(v)


def _final(x):
    """셀에 들어갈 최종 값: 숫자는 엑셀처럼 15자리, 글자는 32,767자까지."""
    if isinstance(x, str) and len(x) > 32767:
        return VALUE
    if is_num(x) and not isinstance(x, bool):
        try:
            return fix(x)
        except XLErr as e:
            return e
    return x


def evaluate_text(text, book, sheet, r=1, c=1):
    return evaluate(parse(text), book, sheet, r, c)


def range_arr(node, ctx):
    _, sh, r1, c1, _, _, r2, c2, _, _ = node
    s = ctx.sheet_obj(sh)
    if r1 is None:
        r1, r2 = 1, max(s.max_row, 1)
    r1, r2 = min(r1, r2), max(r1, r2)
    c1, c2 = min(c1, c2), max(c1, c2)
    if (r2 - r1 + 1) * (c2 - c1 + 1) > 2_000_000:
        raise XLErr('#CALC!')
    return Arr([[s.get(r, c) for c in range(c1, c2 + 1)] for r in range(r1, r2 + 1)], origin=(s.name, r1, c1))


def ev(node, ctx):
    kind = node[0]
    if kind in ('num', 'str', 'bool'):
        return node[1]
    if kind == 'err':
        return ERRORS.get(node[1], XLErr(node[1]))
    if kind == 'empty':
        return None
    if kind == 'ref':
        s = ctx.sheet_obj(node[1])
        return s.get(node[2], node[3])
    if kind == 'range':
        return range_arr(node, ctx)
    if kind == 'arr':
        return Arr([list(r) for r in node[1]])
    if kind == 'name':
        ast = ctx.book.names.get(node[1].upper())
        if ast is None:
            return NAME
        return ev(ast, ctx)
    if kind == 'neg':
        return elementwise(lambda x: -to_num(x), ev(node[1], ctx))
    if kind == 'pct':
        return elementwise(lambda x: to_num(x) / 100, ev(node[1], ctx))
    if kind == 'bin':
        try:
            a = ev(node[2], ctx)
        except XLErr as e:                      # 왼쪽 오류가 먼저(=NA()+1/0 → #N/A)
            a = e
        try:
            b = ev(node[3], ctx)
        except XLErr as e:
            b = e
        return binop(node[1], a, b, ctx)
    if kind == 'call':
        name = node[1]
        spec = FUNCS.get(name)
        if spec is None:
            return NAME
        fn, lazy = spec
        try:
            if lazy:
                return fn(node[2], ctx)
            return fn(*eval_args(node[2], ctx))
        except (XLErr, FormulaError, RecursionError):
            raise
        except (TypeError, IndexError):
            raise ArgError(name)
        except ZeroDivisionError:
            raise DIV0
        except (ValueError, OverflowError, ArithmeticError, MemoryError):
            raise NUM
    raise FormulaError(f'알 수 없는 노드 {kind}')


def eval_args(nodes, ctx):
    out = []
    for a in nodes:
        if a[0] == 'ref':
            s = ctx.sheet_obj(a[1])
            out.append(Arr([[s.get(a[2], a[3])]], origin=(s.name, a[2], a[3])))
        elif a[0] == 'empty':
            out.append(EMPTY)
        else:
            out.append(ev(a, ctx))
    return out


def elementwise(fn, v):
    if isinstance(v, Arr):
        return Arr([[_safe(fn, x) for x in row] for row in v.rows])
    return fn(v)


def _safe(fn, *xs):
    try:
        return fn(*xs)
    except XLErr as e:
        return e


def broadcast(a, b):
    """두 값을 같은 크기 배열 행으로 맞춘다. 크기가 다르면 남는 칸은 #N/A."""
    ah, aw = (a.h, a.w) if isinstance(a, Arr) else (1, 1)
    bh, bw = (b.h, b.w) if isinstance(b, Arr) else (1, 1)
    h = max(ah, bh) if 1 in (ah, bh) else max(ah, bh)
    w = max(aw, bw) if 1 in (aw, bw) else max(aw, bw)

    def pick(x, xh, xw, i, j):
        if not isinstance(x, Arr):
            return x
        ii = 0 if xh == 1 else i
        jj = 0 if xw == 1 else j
        if ii >= xh or jj >= xw:
            return NA
        return x.rows[ii][jj]

    return h, w, (lambda i, j: pick(a, ah, aw, i, j)), (lambda i, j: pick(b, bh, bw, i, j))


def binop(op, a, b, ctx):
    if isinstance(a, Arr) or isinstance(b, Arr):
        h, w, pa, pb = broadcast(a, b)
        return Arr([[_safe(scalar_bin, op, pa(i, j), pb(i, j)) for j in range(w)] for i in range(h)])
    return scalar_bin(op, a, b)


def scalar_bin(op, a, b):
    if isinstance(a, XLErr):
        raise a
    if isinstance(b, XLErr):
        raise b
    if op == '&':
        return to_str(a) + to_str(b)
    if op in ('=', '<>', '<', '>', '<=', '>='):
        c = compare(a, b)
        return {'=': c == 0, '<>': c != 0, '<': c < 0, '>': c > 0, '<=': c <= 0, '>=': c >= 0}[op]
    x, y = to_num(a), to_num(b)
    if op == '+':
        return _exact(x + y)
    if op == '-':
        return _exact(x - y)
    if op == '*':
        return _exact(x * y)
    if op == '/':
        if y == 0:
            raise DIV0
        return _exact(x / y)
    if op == '^':
        try:
            if x < 0 and y != int(y) and abs(1 / y - round(1 / y)) < 1e-9 and round(1 / y) % 2:
                r = -((-float(x)) ** y)          # (-8)^(1/3) = -2 (홀수 제곱근)
            else:
                r = float(x) ** y
        except (OverflowError, ZeroDivisionError):
            raise NUM
        if isinstance(r, complex):
            raise NUM
        return _exact(r)
    raise FormulaError(op)


def _exact(x):
    """계산 중간 값: 15자리로 자르지 않고 그대로(무한·NaN 만 #NUM!, 큰 정수는 실수로)."""
    if type(x) is int:
        return x if abs(x) < 10 ** 15 else fix(x)
    if math.isnan(x) or math.isinf(x):
        raise NUM
    return int(x) if x == int(x) and abs(x) < 1e15 else x


def fix(x):
    """부동소수 잔여 오차를 엑셀처럼 15자리에서 정리. 큰 정수는 실수로(엑셀은 15자리 정밀도)."""
    if type(x) is int and abs(x) >= 10 ** 15:
        try:
            x = float(x)
        except OverflowError:
            raise NUM
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            raise NUM
        if x == int(x) and abs(x) < 1e15:
            return int(x)
        return float(f'{x:.15g}')
    return x


def compare(a, b):
    """엑셀 비교: 빈 셀은 상대 형식의 0/""/FALSE, 숫자 < 문자 < 논리, 문자는 대소문자 무시."""
    if a is None:
        a = '' if isinstance(b, str) else (False if isinstance(b, bool) else 0)
    if b is None:
        b = '' if isinstance(a, str) else (False if isinstance(a, bool) else 0)
    rank = lambda v: 2 if isinstance(v, bool) else (1 if isinstance(v, str) else 0)
    ra, rb = rank(a), rank(b)
    if ra != rb:
        return -1 if ra < rb else 1
    if ra == 1:
        a, b = a.lower(), b.lower()
    elif ra == 0:                                # 숫자 비교는 15자리로(엑셀: =0.1+0.2=0.3 은 TRUE)
        a, b = fix(a), fix(b)
    return (a > b) - (a < b)


# ------------------------------------------------------------ 변환 -----------
def scalar(v, ctx=None):
    if v is EMPTY:
        return None
    if isinstance(v, Arr):
        if v.h == 1 and v.w == 1:
            return v.rows[0][0]
        if ctx is not None and v.origin and v.origin[0] == ctx.sheet:  # 암시적 교차
            _, r0, c0 = v.origin
            if v.w == 1 and r0 <= ctx.r < r0 + v.h:
                return v.rows[ctx.r - r0][0]
            if v.h == 1 and c0 <= ctx.c < c0 + v.w:
                return v.rows[0][ctx.c - c0]
        raise VALUE
    return v


def to_num(v):
    v = scalar(v)
    if isinstance(v, XLErr):
        raise v
    if v is None:
        return 0
    if isinstance(v, bool):
        return int(v)
    if is_num(v):
        return v
    if isinstance(v, str):
        s = v.strip().replace(',', '')
        if not s:
            raise VALUE
        pct = s.endswith('%')
        if pct:
            s = s[:-1]
        if not NUM_RE.fullmatch(s):
            d = parse_date_text(v)
            if d is None:
                raise VALUE
            return d
        n = float(s)
        n = n / 100 if pct else n
        return int(n) if n == int(n) and abs(n) < 1e15 else n
    raise VALUE


def to_int(v):
    return int(math.floor(to_num(v)))


def fmt_general(n):
    if isinstance(n, bool):
        return 'TRUE' if n else 'FALSE'
    if isinstance(n, int):
        return str(n) if abs(n) < 10 ** 15 else fmt_general(float(n))
    if n == int(n) and abs(n) < 1e15:
        return str(int(n))
    s = f'{n:.15g}'
    if 'e' in s:
        mant, exp = s.split('e')
        s = f"{mant}E{'+' if int(exp) >= 0 else '-'}{abs(int(exp)):02d}"
    return s


def to_str(v):
    v = scalar(v)
    if isinstance(v, XLErr):
        raise v
    if v is None:
        return ''
    if isinstance(v, bool):
        return 'TRUE' if v else 'FALSE'
    if is_num(v):
        return fmt_general(v)
    return str(v)


def to_bool(v):
    v = scalar(v)
    if isinstance(v, XLErr):
        raise v
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if is_num(v):
        return v != 0
    if isinstance(v, str):
        if v.upper() == 'TRUE':
            return True
        if v.upper() == 'FALSE':
            return False
    raise VALUE


def as_arr(v):
    if isinstance(v, Arr):
        return v
    return Arr([[v]])


def nums_from(args, count_text=True):
    """SUM·AVERAGE 류 숫자 모음: 범위 안 문자·논리·빈칸은 건너뛰고, 직접 쓴 값은 변환."""
    out = []
    for a in args:
        if isinstance(a, Arr):
            for v in a.flat():
                if isinstance(v, XLErr):
                    raise v
                if is_num(v):
                    out.append(v)
        elif a is None:
            continue
        else:
            if isinstance(a, XLErr):
                raise a
            if isinstance(a, str) and not count_text:
                continue
            out.append(to_num(a))
    return out


# ------------------------------------------------------------ 조건 -----------
def wildcard_re(pattern):
    out, i = '', 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == '~' and i + 1 < len(pattern):
            out += re.escape(pattern[i + 1])
            i += 2
            continue
        out += '.*' if ch == '*' else ('.' if ch == '?' else re.escape(ch))
        i += 1
    return re.compile(out, re.I | re.S)


def make_crit(c, prefix=False):
    """SUMIF·COUNTIF 조건 → 함수. prefix=True 는 D함수·고급 필터(문자는 '…로 시작')."""
    if isinstance(c, Arr):
        c = scalar(c)
    if isinstance(c, XLErr):
        raise c
    if c is None:
        c = 0 if not prefix else ''
    if isinstance(c, bool):
        return lambda v: isinstance(v, bool) and v == c
    if is_num(c):
        return lambda v: (is_num(v) and v == c) or (isinstance(v, str) and _num_or_none(v) == c)
    s = str(c)
    m = re.match(r'^(<=|>=|<>|=|<|>)?(.*)$', s, re.S)
    op, rest = m.group(1) or '', m.group(2)
    num = _num_or_none(rest)
    if num is None and rest:
        num = parse_date_text(rest)
    if op in ('<', '>', '<=', '>='):
        if num is not None:
            return lambda v: is_num(v) and not isinstance(v, bool) and _cmp_op(op, v, num)
        return lambda v: isinstance(v, str) and _cmp_op(op, v.lower(), rest.lower())
    if op == '<>':
        if rest == '':
            return lambda v: v is not None and v != ''
        if num is not None:
            return lambda v: not (is_num(v) and v == num)
        rx = wildcard_re(rest)
        return lambda v: not (isinstance(v, str) and rx.fullmatch(v))
    # '=' 또는 연산자 없음
    if rest == '':
        if op == '=' or not prefix:
            return lambda v: v is None or v == ''
        return lambda v: True
    if num is not None:
        return lambda v: (is_num(v) and not isinstance(v, bool) and v == num) or (isinstance(v, str) and _num_or_none(v) == num)
    if rest.upper() in ('TRUE', 'FALSE'):
        b = rest.upper() == 'TRUE'
        return lambda v: isinstance(v, bool) and v == b
    rx = wildcard_re(rest + ('*' if prefix and op == '' else ''))
    return lambda v: isinstance(v, str) and bool(rx.fullmatch(v))


def _num_or_none(s):
    t = str(s).strip().replace(',', '')
    pct = t.endswith('%')
    if pct:
        t = t[:-1]
    if not NUM_RE.fullmatch(t):
        return None
    n = float(t) / (100 if pct else 1)
    return int(n) if n == int(n) and abs(n) < 1e15 else n


def _cmp_op(op, a, b):
    return {'<': a < b, '>': a > b, '<=': a <= b, '>=': a >= b}[op]


def _same_shape(*arrs):
    if len({(a.h, a.w) for a in arrs}) != 1:
        raise VALUE


def _ifs_mask(pairs):
    arrs = [as_arr(pairs[i]) for i in range(0, len(pairs), 2)]
    if not arrs:
        raise VALUE
    _same_shape(*arrs)
    crits = [make_crit(pairs[i + 1]) for i in range(0, len(pairs), 2)]
    flats = [a.flat() for a in arrs]
    return [all(cr(f[k]) for cr, f in zip(crits, flats)) for k in range(len(flats[0]))], arrs[0]


# ------------------------------------------------------------ 함수 -----------
FUNCS = {}


def fn(*names, lazy=False):
    def deco(f):
        for n in names:
            FUNCS[n] = (f, lazy)
        return f
    return deco


# 집계
@fn('SUM')
def f_sum(*args):
    return fix(math.fsum(nums_from(args)))


@fn('AVERAGE')
def f_average(*args):
    xs = nums_from(args)
    if not xs:
        raise DIV0
    return fix(math.fsum(xs) / len(xs))


@fn('AVERAGEA')
def f_averagea(*args):
    xs = []
    for a in args:
        for v in (a.flat() if isinstance(a, Arr) else [a]):
            if isinstance(v, XLErr):
                raise v
            if v is None:
                continue
            xs.append(v if is_num(v) else (int(v) if isinstance(v, bool) else 0))
    if not xs:
        raise DIV0
    return fix(math.fsum(xs) / len(xs))


@fn('COUNT')
def f_count(*args):
    n = 0
    for a in args:
        if isinstance(a, Arr):
            n += sum(1 for v in a.flat() if is_num(v))
        elif a is not None and not isinstance(a, XLErr):
            try:
                to_num(a)
                n += 1
            except XLErr:
                pass
    return n


@fn('COUNTA')
def f_counta(*args):
    n = 0
    for a in args:
        if isinstance(a, Arr):
            n += sum(1 for v in a.flat() if v is not None)
        elif a is not None:
            n += 1
    return n


@fn('COUNTBLANK')
def f_countblank(rng):
    return sum(1 for v in as_arr(rng).flat() if v is None or v == '')


@fn('MAX')
def f_max(*args):
    xs = nums_from(args)
    return max(xs) if xs else 0


@fn('MIN')
def f_min(*args):
    xs = nums_from(args)
    return min(xs) if xs else 0


@fn('PRODUCT')
def f_product(*args):
    p = 1.0
    for x in nums_from(args):
        p *= x
    return fix(p)


@fn('MEDIAN')
def f_median(*args):
    xs = sorted(nums_from(args))
    if not xs:
        raise NUM
    n = len(xs)
    return fix(xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2)


@fn('MODE', 'MODE.SNGL')
def f_mode(*args):
    xs = nums_from(args)
    best, cnt = None, 1
    for x in xs:
        k = xs.count(x)
        if k > cnt:
            best, cnt = x, k
    if best is None:
        raise NA
    return best


def _var(xs, pop):
    n = len(xs)
    if n - (0 if pop else 1) <= 0:
        raise DIV0
    m = math.fsum(xs) / n
    return math.fsum((x - m) ** 2 for x in xs) / (n if pop else n - 1)


@fn('VAR', 'VAR.S')
def f_var(*args):
    return fix(_var(nums_from(args), False))


@fn('VAR.P', 'VARP')
def f_varp(*args):
    return fix(_var(nums_from(args), True))


@fn('STDEV', 'STDEV.S')
def f_stdev(*args):
    return fix(math.sqrt(_var(nums_from(args), False)))


@fn('STDEV.P', 'STDEVP')
def f_stdevp(*args):
    return fix(math.sqrt(_var(nums_from(args), True)))


@fn('LARGE')
def f_large(arr, k):
    xs = sorted(nums_from([as_arr(arr)]), reverse=True)
    return _kth(xs, k)


@fn('SMALL')
def f_small(arr, k):
    xs = sorted(nums_from([as_arr(arr)]))
    return _kth(xs, k)


def _kth(xs, k):
    if isinstance(k, Arr):
        return k.map(lambda kk: _kth(xs, kk))
    i = math.ceil(to_num(k))
    if i < 1 or i > len(xs):
        raise NUM
    return xs[i - 1]


@fn('RANK', 'RANK.EQ')
def f_rank(n, ref, order=None):
    if isinstance(n, Arr):
        return n.map(lambda x: f_rank(x, ref, order))
    x = to_num(n)
    xs = nums_from([as_arr(ref)])
    if x not in xs:
        raise NA
    asc = order is not None and to_num(order) != 0
    return 1 + sum(1 for v in xs if (v < x if asc else v > x))


@fn('RANK.AVG')
def f_rank_avg(n, ref, order=None):
    if isinstance(n, Arr) and not (n.h == 1 and n.w == 1):
        return n.map(lambda v: f_rank_avg(v, ref, order))
    n = scalar(n)
    x = to_num(n)
    xs = nums_from([as_arr(ref)])
    if x not in xs:
        raise NA
    first = f_rank(n, ref, order)
    return fix(first + (xs.count(x) - 1) / 2)


@fn('PERCENTILE', 'PERCENTILE.INC')
def f_percentile(arr, k):
    xs = sorted(nums_from([as_arr(arr)]))
    p = to_num(k)
    if not xs or p < 0 or p > 1:
        raise NUM
    pos = p * (len(xs) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(xs) - 1)
    return fix(xs[lo] + (xs[hi] - xs[lo]) * (pos - lo))


@fn('QUARTILE', 'QUARTILE.INC')
def f_quartile(arr, q):
    qi = to_int(q)
    if qi < 0 or qi > 4:
        raise NUM
    return f_percentile(arr, qi / 4)


@fn('SUMPRODUCT')
def f_sumproduct(*arrs):
    arrs = [as_arr(a) for a in arrs]
    _same_shape(*arrs)
    total = 0
    flats = [a.flat() for a in arrs]
    for k in range(len(flats[0])):
        p = 1
        for f in flats:
            v = f[k]
            if isinstance(v, XLErr):
                raise v
            p *= v if is_num(v) else 0
        total += p
    return fix(total)


# 조건 집계
def _resize(rng, other, ctx):
    """SUMIF(범위, 조건, 합계 범위): 합계 범위는 왼쪽 위 칸에서 조건 범위 크기만큼."""
    if other is None or other is EMPTY:
        return rng
    other = as_arr(other)
    if other.origin and (other.h, other.w) != (rng.h, rng.w):
        sh, r0, c0 = other.origin
        return area(sh, r0, c0, r0 + rng.h - 1, c0 + rng.w - 1, ctx)
    return other


@fn('SUMIF', lazy=True)
def f_sumif_lazy(args, ctx):
    if len(args) not in (2, 3):
        raise VALUE
    vals = eval_args(args, ctx)
    rng = as_arr(vals[0])
    return f_sumif(rng, vals[1], _resize(rng, vals[2] if len(vals) > 2 else None, ctx))


def f_sumif(rng, crit, sum_rng=None):
    rng = as_arr(rng)
    if isinstance(crit, Arr) and crit.origin is None and (crit.h > 1 or crit.w > 1):
        return crit.map(lambda c: f_sumif(rng, c, sum_rng))
    sr = as_arr(sum_rng) if sum_rng is not None else rng
    cr = make_crit(crit)
    total = 0
    for i, row in enumerate(rng.rows):
        for j, v in enumerate(row):
            if cr(v):
                s = sr.rows[i][j] if i < sr.h and j < sr.w else None
                if isinstance(s, XLErr):
                    raise s
                if is_num(s):
                    total += s
    return fix(total)


@fn('SUMIFS')
def f_sumifs(sum_rng, *pairs):
    if len(pairs) % 2:
        raise VALUE
    mask, _ = _ifs_mask(pairs)
    vals = as_arr(sum_rng).flat()
    if len(vals) != len(mask):
        raise VALUE
    return fix(math.fsum(v for v, ok in zip(vals, mask) if ok and is_num(v)))


@fn('COUNTIF')
def f_countif(rng, crit):
    if isinstance(crit, Arr) and (crit.h > 1 or crit.w > 1):    # 배열 수식: 조건이 범위면 칸마다(1/COUNTIF(A:A,A:A))
        return crit.map(lambda c: f_countif(rng, c))
    cr = make_crit(crit)
    return sum(1 for v in as_arr(rng).flat() if cr(v))


@fn('COUNTIFS')
def f_countifs(*pairs):
    if len(pairs) % 2:
        raise VALUE
    mask, _ = _ifs_mask(pairs)
    return sum(mask)


@fn('AVERAGEIF', lazy=True)
def f_averageif_lazy(args, ctx):
    if len(args) not in (2, 3):
        raise VALUE
    vals = eval_args(args, ctx)
    rng = as_arr(vals[0])
    return f_averageif(rng, vals[1], _resize(rng, vals[2] if len(vals) > 2 else None, ctx))


def f_averageif(rng, crit, avg_rng=None):
    rng = as_arr(rng)
    if isinstance(crit, Arr) and crit.origin is None and (crit.h > 1 or crit.w > 1):
        return crit.map(lambda c: f_averageif(rng, c, avg_rng))
    ar = as_arr(avg_rng) if avg_rng is not None else rng
    cr = make_crit(crit)
    xs = []
    for i, row in enumerate(rng.rows):
        for j, v in enumerate(row):
            if cr(v):
                s = ar.rows[i][j] if i < ar.h and j < ar.w else None
                if is_num(s):
                    xs.append(s)
    if not xs:
        raise DIV0
    return fix(math.fsum(xs) / len(xs))


@fn('AVERAGEIFS')
def f_averageifs(avg_rng, *pairs):
    mask, _ = _ifs_mask(pairs)
    xs = [v for v, ok in zip(as_arr(avg_rng).flat(), mask) if ok and is_num(v)]
    if not xs:
        raise DIV0
    return fix(math.fsum(xs) / len(xs))


@fn('MAXIFS')
def f_maxifs(rng, *pairs):
    mask, _ = _ifs_mask(pairs)
    xs = [v for v, ok in zip(as_arr(rng).flat(), mask) if ok and is_num(v)]
    return max(xs) if xs else 0


@fn('MINIFS')
def f_minifs(rng, *pairs):
    mask, _ = _ifs_mask(pairs)
    xs = [v for v, ok in zip(as_arr(rng).flat(), mask) if ok and is_num(v)]
    return min(xs) if xs else 0


# 논리
@fn('IF', lazy=True)
def f_if(args, ctx):
    if not 1 < len(args) <= 3:
        raise VALUE
    cond = ev(args[0], ctx)
    if isinstance(cond, Arr) and not (cond.h == 1 and cond.w == 1):
        a = ev(args[1], ctx)
        b = ev(args[2], ctx) if len(args) > 2 else False
        h, w, pc, _ = broadcast(cond, a)
        h2, w2, _, _ = broadcast(cond, b)
        H, W = max(h, h2), max(w, w2)
        _, _, pcond, pa = broadcast(Arr([[None] * W for _ in range(H)]), cond)
        out = []
        for i in range(H):
            row = []
            for j in range(W):
                c = pa(i, j)
                try:
                    pick = to_bool(c)
                except XLErr as e:
                    row.append(e)
                    continue
                src = a if pick else b
                row.append(_pick(src, i, j))
            out.append(row)
        return Arr(out)
    if to_bool(scalar(cond, ctx)):
        return ev(args[1], ctx)
    return ev(args[2], ctx) if len(args) > 2 else False


def _pick(x, i, j):
    if not isinstance(x, Arr):
        return x
    ii = 0 if x.h == 1 else i
    jj = 0 if x.w == 1 else j
    if ii >= x.h or jj >= x.w:
        return NA
    return x.rows[ii][jj]


@fn('IFS', lazy=True)
def f_ifs(args, ctx):
    if len(args) % 2:
        raise VALUE
    for i in range(0, len(args), 2):
        if to_bool(scalar(ev(args[i], ctx), ctx)):
            return ev(args[i + 1], ctx)
    raise NA


@fn('SWITCH', lazy=True)
def f_switch(args, ctx):
    v = scalar(ev(args[0], ctx), ctx)
    rest = args[1:]
    for i in range(0, len(rest) - 1, 2):
        if compare(v, scalar(ev(rest[i], ctx), ctx)) == 0:
            return ev(rest[i + 1], ctx)
    if len(rest) % 2:
        return ev(rest[-1], ctx)
    raise NA


def _bools(args):
    out = []
    for a in args:
        if isinstance(a, Arr):
            for v in a.flat():
                if isinstance(v, XLErr):
                    raise v
                if isinstance(v, bool) or is_num(v):
                    out.append(bool(v))
        elif a is not None:
            out.append(to_bool(a))
    if not out:
        raise VALUE
    return out


@fn('AND')
def f_and(*args):
    return all(_bools(args))


@fn('OR')
def f_or(*args):
    return any(_bools(args))


@fn('XOR')
def f_xor(*args):
    return sum(_bools(args)) % 2 == 1


@fn('NOT')
def f_not(v):
    return elementwise(lambda x: not to_bool(x), v)


@fn('TRUE')
def f_true():
    return True


@fn('FALSE')
def f_false():
    return False


@fn('IFERROR', lazy=True)
def f_iferror(args, ctx):
    try:
        v = ev(args[0], ctx)
    except XLErr:
        return ev(args[1], ctx)
    if isinstance(v, Arr):
        fb = None
        out = []
        for row in v.rows:
            new = []
            for x in row:
                if isinstance(x, XLErr):
                    if fb is None:
                        fb = scalar(ev(args[1], ctx))
                    new.append(fb)
                else:
                    new.append(x)
            out.append(new)
        return Arr(out, v.origin)
    if isinstance(v, XLErr):
        return ev(args[1], ctx)
    return v


@fn('IFNA', lazy=True)
def f_ifna(args, ctx):
    try:
        v = ev(args[0], ctx)
    except XLErr as e:
        if e.code == '#N/A':
            return ev(args[1], ctx)
        raise
    if isinstance(v, XLErr) and v.code == '#N/A':
        return ev(args[1], ctx)
    return v


@fn('CHOOSE', lazy=True)
def f_choose(args, ctx):
    idx = ev(args[0], ctx)
    if isinstance(idx, Arr) and not (idx.h == 1 and idx.w == 1) and idx.origin is None:
        picks = [[to_int(x) for x in row] for row in idx.rows]       # CHOOSE({1,2},B:B,A:A) — 왼쪽 찾기에 쓰는 형태
        if any(i < 1 or i >= len(args) for row in picks for i in row):
            raise VALUE
        vals = {i: ev(args[i], ctx) for row in picks for i in row}
        if idx.h == 1:
            cols = [vals[i] for i in picks[0]]
            hh = max(v.h if isinstance(v, Arr) else 1 for v in cols)
            return Arr([[(c.rows[r][0] if c.h > r else NA) if isinstance(c, Arr) else c for c in cols] for r in range(hh)])
        rows = [vals[row[0]] for row in picks]
        ww = max(v.w if isinstance(v, Arr) else 1 for v in rows)
        return Arr([[(v.rows[0][k] if v.w > k else NA) if isinstance(v, Arr) else v for k in range(ww)] for v in rows])
    i = to_int(scalar(idx, ctx))
    if i < 1 or i >= len(args):
        raise VALUE
    return ev(args[i], ctx)


# 정보
def _err_test(test):
    def f(args, ctx):
        try:
            v = ev(args[0], ctx)
        except XLErr as e:
            return test(e)
        if isinstance(v, Arr) and not (v.h == 1 and v.w == 1):
            return v.map(test)
        return test(scalar(v))
    return f


FUNCS['ISERROR'] = (_err_test(lambda v: isinstance(v, XLErr)), True)
FUNCS['ISERR'] = (_err_test(lambda v: isinstance(v, XLErr) and v.code != '#N/A'), True)
FUNCS['ISNA'] = (_err_test(lambda v: isinstance(v, XLErr) and v.code == '#N/A'), True)


def _type_test(name, test, per_item=True):
    """ISNUMBER·ISTEXT 등: 인수가 오류여도 오류를 넘기지 않고 FALSE (=ISNUMBER(FIND("x",A1)) 이 FALSE)."""
    def safe(x):
        return False if isinstance(x, XLErr) else test(x)

    def f(args, ctx):
        if len(args) != 1:
            raise ArgError(name)
        try:
            v = ev(args[0], ctx)
        except XLErr:
            return False
        if isinstance(v, Arr) and not (v.h == 1 and v.w == 1):
            return v.map(safe) if per_item else safe(scalar(v))
        return safe(scalar(v))
    return f


FUNCS['ISBLANK'] = (_type_test('ISBLANK', lambda x: x is None), True)
FUNCS['ISNUMBER'] = (_type_test('ISNUMBER', is_num), True)
FUNCS['ISTEXT'] = (_type_test('ISTEXT', lambda x: isinstance(x, str)), True)
FUNCS['ISLOGICAL'] = (_type_test('ISLOGICAL', lambda x: isinstance(x, bool), per_item=False), True)


@fn('ISEVEN')
def f_iseven(v):
    return int(math.trunc(to_num(v))) % 2 == 0


@fn('ISODD')
def f_isodd(v):
    return int(math.trunc(to_num(v))) % 2 == 1


@fn('NA')
def f_na():
    return NA


@fn('N')
def f_n(v):
    if isinstance(v, Arr) and (v.h > 1 or v.w > 1):
        return v.map(lambda x: f_n(x))
    v = scalar(v)
    if isinstance(v, XLErr):
        raise v
    return int(v) if isinstance(v, bool) else (v if is_num(v) else 0)


# 수학
def _round(x, digits, mode):
    x = fix(to_num(x))                 # 엑셀처럼 15자리 값을 기준으로(3104999.9999999995 → 3105000)
    d = int(to_num(digits)) if digits is not None else 0     # 2.5 → 2, -2.5 → -2 (0 쪽으로)
    q = Decimal(1).scaleb(-d)
    r = Decimal(repr(float(x)) if isinstance(x, float) else x).quantize(q, rounding=mode) if d >= 0 else \
        (Decimal(repr(float(x)) if isinstance(x, float) else x) / Decimal(10) ** (-d)).quantize(Decimal(1), rounding=mode) * Decimal(10) ** (-d)
    return fix(float(r))


@fn('ROUND')
def f_round(x, digits=None):
    return elementwise(lambda v: _round(v, digits, ROUND_HALF_UP), x)


@fn('ROUNDUP')
def f_roundup(x, digits=None):
    return elementwise(lambda v: _round(v, digits, ROUND_UP), x)


@fn('ROUNDDOWN')
def f_rounddown(x, digits=None):
    return elementwise(lambda v: _round(v, digits, ROUND_DOWN), x)


@fn('TRUNC')
def f_trunc(x, digits=None):
    return elementwise(lambda v: _round(v, digits, ROUND_DOWN), x)


@fn('INT')
def f_int(x):
    return elementwise(lambda v: int(math.floor(to_num(v))), x)


@fn('ABS')
def f_abs(x):
    return elementwise(lambda v: abs(to_num(v)), x)


@fn('MOD')
def f_mod(a, b):
    def m(x, y):
        x, y = to_num(x), to_num(y)
        if y == 0:
            raise DIV0
        return fix(x - y * math.floor(x / y))
    if isinstance(a, Arr) or isinstance(b, Arr):
        h, w, pa, pb = broadcast(a, b)
        return Arr([[_safe(m, pa(i, j), pb(i, j)) for j in range(w)] for i in range(h)])
    return m(a, b)


@fn('QUOTIENT')
def f_quotient(a, b):
    y = to_num(b)
    if y == 0:
        raise DIV0
    return int(to_num(a) / y)


@fn('POWER')
def f_power(a, b):
    return scalar_bin('^', scalar(a), scalar(b))


@fn('SQRT')
def f_sqrt(x):
    v = to_num(x)
    if v < 0:
        raise NUM
    return fix(math.sqrt(v))


@fn('PI')
def f_pi():
    return math.pi


@fn('SIGN')
def f_sign(x):
    if isinstance(x, Arr) and (x.h > 1 or x.w > 1):
        return x.map(lambda v: f_sign(v))
    v = to_num(scalar(x))
    return (v > 0) - (v < 0)


@fn('CEILING')
def f_ceiling(x, sig=None):
    v = to_num(x)
    s = to_num(sig) if sig is not None else 1
    if s == 0:
        return 0
    if v > 0 and s < 0:
        raise NUM
    return fix(math.ceil(v / s) * s)


@fn('FLOOR')
def f_floor(x, sig=None):
    v = to_num(x)
    s = to_num(sig) if sig is not None else 1
    if s == 0:
        raise DIV0
    if v > 0 and s < 0:
        raise NUM
    return fix(math.floor(v / s) * s)


@fn('CEILING.MATH')
def f_ceiling_math(x, sig=None, mode=None):
    v = to_num(x)
    s = abs(to_num(sig)) if sig is not None else 1
    if s == 0:
        return 0
    if v < 0 and mode is not None and to_num(mode) != 0:
        return fix(-math.ceil(-v / s) * s)
    return fix(math.ceil(v / s) * s)


@fn('FLOOR.MATH')
def f_floor_math(x, sig=None, mode=None):
    v = to_num(x)
    s = abs(to_num(sig)) if sig is not None else 1
    if s == 0:
        return 0
    if v < 0 and mode is not None and to_num(mode) != 0:
        return fix(-math.floor(-v / s) * s)
    return fix(math.floor(v / s) * s)


@fn('SUMSQ')
def f_sumsq(*args):
    return fix(math.fsum(x * x for x in nums_from(args)))


@fn('SEQUENCE')
def f_sequence(rows, cols=None, start=None, step=None):
    r = to_int(rows)
    c = to_int(cols) if cols is not None else 1
    s = to_num(start) if start is not None else 1
    st = to_num(step) if step is not None else 1
    if r < 1 or c < 1 or r * c > 100000:
        raise VALUE
    return Arr([[fix(s + (i * c + j) * st) for j in range(c)] for i in range(r)])


# 텍스트
def _txt(fn_):
    def wrapper(*args):
        if args and isinstance(args[0], Arr) and not (args[0].h == 1 and args[0].w == 1):
            return args[0].map(lambda v: fn_(v, *args[1:]))
        return fn_(*args)
    return wrapper


def _left(t, n=None):
    k = 1 if n is None else to_int(n)
    if k < 0:
        raise VALUE
    return to_str(t)[:k]


def _right(t, n=None):
    k = 1 if n is None else to_int(n)
    if k < 0:
        raise VALUE
    s = to_str(t)
    return s[len(s) - k:] if k else ''


def _mid(t, start, n):
    s, st, k = to_str(t), to_int(start), to_int(n)
    if st < 1 or k < 0:
        raise VALUE
    return s[st - 1:st - 1 + k]


FUNCS['LEFT'] = (_txt(_left), False)
FUNCS['RIGHT'] = (_txt(_right), False)
FUNCS['MID'] = (_txt(_mid), False)
FUNCS['LEN'] = (_txt(lambda t: len(to_str(t))), False)
FUNCS['TRIM'] = (_txt(lambda t: re.sub(' +', ' ', to_str(t).strip(' '))), False)
FUNCS['UPPER'] = (_txt(lambda t: to_str(t).upper()), False)
FUNCS['LOWER'] = (_txt(lambda t: to_str(t).lower()), False)
FUNCS['PROPER'] = (_txt(lambda t: re.sub(r'[A-Za-z]+', lambda m: m.group(0).capitalize(), to_str(t).lower())), False)


def _value(t):
    if isinstance(scalar(t), bool):
        raise VALUE
    return to_num(t)


FUNCS['VALUE'] = (_txt(_value), False)


@fn('CONCATENATE')
def f_concatenate(*args):
    if any(isinstance(a, Arr) and not (a.h == 1 and a.w == 1) for a in args):
        h = max(a.h if isinstance(a, Arr) else 1 for a in args)
        w = max(a.w if isinstance(a, Arr) else 1 for a in args)

        def at(a, i, j):
            if not isinstance(a, Arr):
                return a
            return a.rows[0 if a.h == 1 else i][0 if a.w == 1 else j] if (a.h == 1 or i < a.h) and (a.w == 1 or j < a.w) else NA
        return Arr([[_safe(lambda i=i, j=j: ''.join(to_str(at(a, i, j)) for a in args)) for j in range(w)] for i in range(h)])
    return _limit(''.join(to_str(a) for a in args))


def _limit(text):
    if len(text) > 32767:                        # 엑셀 셀 글자 수 한도
        raise VALUE
    return text


@fn('CONCAT')
def f_concat(*args):
    out = []
    for a in args:
        out.extend(to_str(v) for v in (a.flat() if isinstance(a, Arr) else [a]))
    return ''.join(out)


@fn('TEXTJOIN')
def f_textjoin(delim, ignore_empty, *args):
    d = to_str(delim)
    skip = to_bool(ignore_empty)
    parts = []
    for a in args:
        for v in (a.flat() if isinstance(a, Arr) else [a]):
            s = to_str(v)
            if skip and s == '':
                continue
            parts.append(s)
    return d.join(parts)


@fn('REPT')
def f_rept(t, n):
    k = to_int(n)
    text = to_str(t)
    if k < 0 or len(text) * k > 32767:
        raise VALUE
    return text * k


@fn('EXACT')
def f_exact(a, b):
    return to_str(a) == to_str(b)


def _find(needle, hay, start, case):
    n, h = to_str(needle), to_str(hay)
    st = 1 if start is None else to_int(start)
    if st < 1 or st > len(h) + 1:
        raise VALUE
    if case:
        i = h.find(n, st - 1)
        if i < 0:
            raise VALUE
        return i + 1
    rx = wildcard_re(n)
    m = re.compile(rx.pattern, re.I | re.S).search(h, st - 1)
    if not m:
        raise VALUE
    return m.start() + 1


FUNCS['FIND'] = (_txt(lambda n, h, s=None: _find(n, h, s, True)), False)
FUNCS['SEARCH'] = (_txt(lambda n, h, s=None: _find(n, h, s, False)), False)


@fn('SUBSTITUTE')
def f_substitute(t, old, new, inst=None):
    s, o, nw = to_str(t), to_str(old), to_str(new)
    if not o:
        return s
    if inst is None:
        return s.replace(o, nw)
    k = to_int(inst)
    if k < 1:
        raise VALUE
    idx = -1
    for _ in range(k):
        idx = s.find(o, idx + 1)
        if idx < 0:
            return s
    return s[:idx] + nw + s[idx + len(o):]


@fn('REPLACE')
def f_replace(t, start, n, new):
    s, st, k = to_str(t), to_int(start), to_int(n)
    if st < 1 or k < 0:
        raise VALUE
    return s[:st - 1] + to_str(new) + s[st - 1 + k:]


@fn('CHAR')
def f_char(n):
    return chr(to_int(n))


@fn('CODE')
def f_code(t):
    s = to_str(t)
    if not s:
        raise VALUE
    return ord(s[0])


@fn('TEXT')
def f_text(v, fmt):
    if isinstance(v, Arr) and not (v.h == 1 and v.w == 1):
        return v.map(lambda x: f_text(x, fmt))
    x = scalar(v)
    if isinstance(x, bool):                      # TEXT(TRUE,"0") = TRUE
        return 'TRUE' if x else 'FALSE'
    return format_value(x, to_str(fmt))


@fn('FIXED')
def f_fixed(v, digits=None, no_commas=None):
    d = int(to_num(digits)) if digits is not None else 2
    fmt = ('0' if no_commas is not None and to_bool(no_commas) else '#,##0') + ('.' + '0' * d if d > 0 else '')
    x = to_num(v)
    if d < 0:
        x = _round(x, d, ROUND_HALF_UP)
    return format_value(x, fmt)


@fn('NUMBERVALUE')
def f_numbervalue(t, dec=None, grp=None):
    text = to_str(t)
    d = to_str(dec) if dec is not None and dec is not EMPTY else '.'
    g = to_str(grp) if grp is not None and grp is not EMPTY else ','
    if g:
        text = text.replace(g[0], '')
    if d and d[0] != '.':
        text = text.replace(d[0], '.')
    return to_num(text.replace(' ', ''))


KO_DAYS = ['월', '화', '수', '목', '금', '토', '일']
EN_DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']


def _clean_codes(fmt):
    """따옴표 밖의 _x(공백 자리) · *x(채우기) · [색]/[조건] 제거, [$₩-412] → ₩."""
    out = []
    for part in re.split(r'("[^"]*")', fmt):
        if part.startswith('"'):
            out.append(part)
            continue
        part = re.sub(r'\[\$([^\]-]*)(?:-[0-9A-Fa-f]+)?\]', lambda m: '"' + m.group(1) + '"' if m.group(1) else '', part)
        part = re.sub(r'\[(?![hHmMsS]+\])[^\]]*\]', '', part)     # [h]·[mm]·[ss] 경과 시간은 남김
        part = re.sub(r'_.', '', part)
        part = re.sub(r'\*.', '', part)
        out.append(part)
    return ''.join(out)


def format_value(v, fmt):
    """TEXT 함수 서식: 날짜(yyyy mm dd aaa…)와 숫자(0 # , . %) 기본 형식."""
    if isinstance(v, XLErr):
        raise v
    if fmt in ('@', '') or fmt.strip().lower() in ('general', 'g/표준'):
        return to_str(v)
    # 엑셀이 저장할 때 붙이는 \ 이스케이프(\ 공백, \. \( 등)는 따옴표 글자와 같다
    fmt = re.sub(r'\\(.)', lambda m: '"' + m.group(1) + '"', fmt)
    fmt = _clean_codes(fmt)
    if isinstance(v, str):
        try:
            v = to_num(v)
        except XLErr:
            return v
    section = fmt.split(';')
    if len(section) > 1 and is_num(v):
        fmt = section[0] if v > 0 or len(section) == 1 else (section[1] if v < 0 else (section[2] if len(section) > 2 else section[0]))
        if v < 0 and len(section) > 1:
            v = -v
    x = to_num(v)
    if re.search(r'[yYdDaA]|[hH]|(?<![#0])[mM]', re.sub(r'"[^"]*"', '', fmt)):
        return _format_date(x, fmt)
    return _format_number(x, fmt)


class _Day0:
    """시각만 있는 값(0.x)의 날짜 부분 = 엑셀의 1900-01-00(월 1, 일 0)."""
    year, month, day = 1900, 1, 0

    @staticmethod
    def weekday():
        return 5                               # 1900-01-00 은 엑셀에서 토요일


def _format_date(x, fmt):
    d = serial_date(x) if math.floor(x) != 0 else _Day0
    frac = x - math.floor(x)
    secs = int(round(frac * 86400))
    total_secs = int(round(x * 86400))
    hh, mm, ss = secs // 3600, secs // 60 % 60, secs % 60
    tokens = re.findall(r'"[^"]*"|\[h+\]|\[m+\]|\[s+\]|yyyy|yy|mmmm|mmm|mm|m|dddd|ddd|dd|d|aaaa|aaa|hh|h|ss|s|AM/PM|A/P|.',
                        fmt, re.I)
    ampm = any(t.upper() in ('AM/PM', 'A/P') for t in tokens)
    # m/mm 뒤(':' 건너뛰고)에 초가 오면 분
    minute_at = set()
    for i, t in enumerate(tokens):
        if t.lower() in ('m', 'mm'):
            k = i + 1
            while k < len(tokens) and tokens[k] in (':', ' ', '.'):
                k += 1
            if k < len(tokens) and tokens[k].lower() in ('s', 'ss'):
                minute_at.add(i)
    out, prev_h = [], False
    for i, t in enumerate(tokens):
        lo = t.lower()
        if lo.startswith('[h'):
            out.append(str(total_secs // 3600).rjust(len(lo) - 2, '0'))
            prev_h = True
            continue
        if lo.startswith('[m'):
            out.append(str(total_secs // 60).rjust(len(lo) - 2, '0'))
            continue
        if lo.startswith('[s'):
            out.append(str(total_secs).rjust(len(lo) - 2, '0'))
            continue
        if lo in ('am/pm', 'a/p'):
            pm = hh >= 12
            out.append(('PM' if pm else 'AM') if lo == 'am/pm' else ('P' if pm else 'A'))
            continue
        if lo in ('hh', 'h') and ampm:
            h12 = hh % 12 or 12
            out.append(f'{h12:02d}' if lo == 'hh' else str(h12))
            prev_h = True
            continue
        if lo in ('mm', 'm') and i in minute_at:
            out.append(f'{mm:02d}' if lo == 'mm' else str(mm))
            continue
        if t.startswith('"'):
            out.append(t[1:-1])
        elif lo == 'yyyy':
            out.append(f'{d.year:04d}')
        elif lo == 'yy':
            out.append(f'{d.year % 100:02d}')
        elif lo == 'mmmm':
            out.append(calendar.month_name[d.month])
        elif lo == 'mmm':
            out.append(calendar.month_abbr[d.month])
        elif lo in ('mm', 'm'):
            val = mm if prev_h else d.month
            out.append(f'{val:02d}' if lo == 'mm' else str(val))
        elif lo == 'dddd':
            out.append(EN_DAYS[d.weekday()])
        elif lo == 'ddd':
            out.append(EN_DAYS[d.weekday()][:3])
        elif lo == 'dd':
            out.append(f'{d.day:02d}')
        elif lo == 'd':
            out.append(str(d.day))
        elif lo == 'aaaa':
            out.append(KO_DAYS[d.weekday()] + '요일')
        elif lo == 'aaa':
            out.append(KO_DAYS[d.weekday()])
        elif lo in ('hh', 'h'):
            out.append(f'{hh:02d}' if lo == 'hh' else str(hh))
            prev_h = True
            continue
        elif lo in ('ss', 's'):
            out.append(f'{ss:02d}' if lo == 'ss' else str(ss))
        else:
            out.append(t)
        if lo in ('yyyy', 'yy', 'mmmm', 'mmm', 'mm', 'm', 'dddd', 'ddd', 'dd', 'd', 'aaaa', 'aaa', 'ss', 's'):
            prev_h = False                     # 시 뒤의 m 은 사이에 글자("시간 ")가 있어도 분 — 날짜·초 코드가 오면 끝
    return ''.join(out)


def _format_number(x, fmt):
    parts = re.split(r'("[^"]*")', fmt)
    plain = ''.join(p for p in parts if not p.startswith('"'))
    me = re.search(r'([#0]*)(?:\.([#0]*))?[eE]([+-])(0+)', plain)
    if me:                                            # 지수 표기 0.00E+00
        nd = len(me.group(2) or '')
        mant, exp = f'{x:.{nd}E}'.split('E')
        e = int(exp)
        shown = f"{mant}E{'+' if e >= 0 else '-'}{abs(e):0{len(me.group(4))}d}"
        return plain.replace(me.group(0), shown, 1)
    m = re.search(r'[#0,]*[#0](?:\.[#0]*)?,*', plain)
    if not m:
        return ''.join(p[1:-1] if p.startswith('"') else p for p in parts)
    spec = m.group(0)
    scale = len(spec) - len(spec.rstrip(','))       # 끝의 쉼표 하나마다 1000 으로 나눔
    core_spec = spec.rstrip(',')
    pct = '%' in plain
    val = (x * 100 if pct else x) / (1000 ** scale)
    if abs(val) >= 1e15:  # 엑셀도 15자리 넘으면 지수 표기
        return f'{val:.5E}'.replace('E+', 'E+')
    dec = core_spec.split('.')[1] if '.' in core_spec else ''
    nd = len(dec)
    rounded = Decimal(repr(float(val))).quantize(Decimal(1).scaleb(-nd), rounding=ROUND_HALF_UP)
    neg = rounded < 0
    rounded = abs(rounded)
    int_part, _, frac = f'{rounded:f}'.partition('.')
    min_int = core_spec.split('.')[0].replace(',', '').count('0')
    int_part = int_part.lstrip('0') or ''
    int_part = int_part.rjust(min_int, '0') if min_int else int_part
    if ',' in core_spec.split('.')[0] and int_part:
        int_part = f'{int(int_part):,}'.rjust(len(int_part), '0') if int_part.isdigit() else int_part
    if nd:
        frac = frac.ljust(nd, '0')[:nd]
        trailing = len(dec) - len(dec.rstrip('#'))
        if trailing:
            keep = nd - trailing
            frac = frac[:keep] + frac[keep:].rstrip('0')
        num = int_part + '.' + frac                  # 엑셀은 소수 자리가 비어도 점은 찍는다(#.## → 1.)
    else:
        num = int_part
    if not num:
        num = '0' if '0' in core_spec else ''
    num = ('-' if neg else '') + num
    out, done = [], False
    for p in parts:
        if p.startswith('"'):
            out.append(p[1:-1])
        elif not done and spec in p:
            out.append(p.replace(spec, num, 1))
            done = True
        else:
            out.append(p)
    return ''.join(out)


# 날짜
def _date_of(v):
    v = scalar(v)
    if isinstance(v, str):
        return serial_date(to_num(v))
    n = to_num(v)
    if n < 0:
        raise NUM
    return serial_date(n)


@fn('DATE')
def f_date(y, m, d):
    yy, mm, dd = to_int(y), to_int(m), to_int(d)
    if yy < 1900:
        yy += 1900
    yy += (mm - 1) // 12
    mm = (mm - 1) % 12 + 1
    if not 1900 <= yy <= 9999:
        raise NUM
    n = date_serial(dt.date(yy, mm, 1)) + dd - 1
    if n < 0 or n > 2958465:
        raise NUM
    return n


FUNCS['YEAR'] = (_txt(lambda v: _date_of(v).year), False)
FUNCS['MONTH'] = (_txt(lambda v: _date_of(v).month), False)
FUNCS['DAY'] = (_txt(lambda v: _date_of(v).day), False)


@fn('WEEKDAY')
def f_weekday(v, kind=None):
    def w(x):
        d = _date_of(x)
        k = to_int(kind) if kind is not None else 1
        wd = d.weekday()  # 월=0
        if k == 1 or k == 17:
            return (wd + 1) % 7 + 1
        if k == 2:
            return wd + 1
        if k == 3:
            return wd
        if 11 <= k <= 16:                 # 11=월요일 시작 … 16=토요일 시작
            return (wd - (k - 11)) % 7 + 1
        raise NUM
    return elementwise(w, v) if isinstance(v, Arr) and not (v.h == 1 and v.w == 1) else w(v)


@fn('TODAY', lazy=True)
def f_today(args, ctx):
    return date_serial(ctx.book.today)


@fn('NOW', lazy=True)
def f_now(args, ctx):
    return date_serial(ctx.book.today)


def _add_months(d, k):
    y, m = divmod(d.month - 1 + k, 12)
    year, month = d.year + y, m + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return dt.date(year, month, day)


@fn('EDATE')
def f_edate(v, months):
    return date_serial(_add_months(_date_of(v), to_int(months)))


@fn('EOMONTH')
def f_eomonth(v, months):
    d = _add_months(_date_of(v).replace(day=1), to_int(months))
    return date_serial(d.replace(day=calendar.monthrange(d.year, d.month)[1]))


@fn('DAYS')
def f_days(end, start):
    return (_date_of(end) - _date_of(start)).days


@fn('DATEDIF')
def f_datedif(start, end, unit):
    a, b, u = _date_of(start), _date_of(end), to_str(unit).upper()
    if a > b:
        raise NUM
    months = (b.year - a.year) * 12 + b.month - a.month - (1 if b.day < a.day else 0)
    if u == 'Y':
        return months // 12
    if u == 'M':
        return months
    if u == 'D':
        return (b - a).days
    if u == 'YM':
        return months % 12
    if u == 'MD':
        if b.day >= a.day:
            return b.day - a.day
        prev = _add_months(b.replace(day=1), -1)
        return (b - _add_months(prev, 0).replace(day=min(a.day, calendar.monthrange(prev.year, prev.month)[1]))).days
    if u == 'YD':
        try:
            anchor = a.replace(year=b.year)
        except ValueError:
            anchor = dt.date(b.year, 3, 1)
        if anchor > b:
            anchor = anchor.replace(year=b.year - 1)
        return (b - anchor).days
    raise NUM


def _holidays(h):
    if h is None:
        return set()
    return {_date_of(v) for v in as_arr(h).flat() if v is not None}


@fn('NETWORKDAYS')
def f_networkdays(start, end, hol=None):
    a, b = _date_of(start), _date_of(end)
    sign = 1
    if a > b:
        a, b, sign = b, a, -1
    hs = _holidays(hol)
    n = sum(1 for k in range((b - a).days + 1)
            if (a + dt.timedelta(k)).weekday() < 5 and (a + dt.timedelta(k)) not in hs)
    return sign * n


@fn('WORKDAY')
def f_workday(start, days, hol=None):
    d, k = _date_of(start), to_int(days)
    hs = _holidays(hol)
    step = 1 if k >= 0 else -1
    while k:
        d += dt.timedelta(step)
        if d.weekday() < 5 and d not in hs:
            k -= step
    return date_serial(d)


@fn('TIME')
def f_time(h, m, s):
    return fix(((to_num(h) * 3600 + to_num(m) * 60 + to_num(s)) % 86400) / 86400)


def _secs(v):
    n = to_num(v)
    return int(round((n - math.floor(n)) * 86400)) % 86400


FUNCS['HOUR'] = (_txt(lambda v: _secs(v) // 3600), False)
FUNCS['MINUTE'] = (_txt(lambda v: _secs(v) // 60 % 60), False)
FUNCS['SECOND'] = (_txt(lambda v: _secs(v) % 60), False)


@fn('DATEVALUE')
def f_datevalue(t):
    d = parse_date_text(to_str(t))
    if d is None:
        raise VALUE
    return d


# 찾기·참조
def _lookup_eq(a, b):
    if isinstance(a, str) and isinstance(b, str):
        return bool(wildcard_re(a).fullmatch(b)) if any(ch in a for ch in '*?~') else a.lower() == b.lower()
    if is_num(a) and is_num(b) and not isinstance(a, bool) and not isinstance(b, bool):
        return a == b
    return type(a) is type(b) and a == b


def _same_type(a, b):
    return (isinstance(a, str) and isinstance(b, str)) or (is_num(a) and is_num(b)) or \
        (isinstance(a, bool) and isinstance(b, bool))


def _approx_index(vals, v, descending=False):
    best = None
    for i, x in enumerate(vals):
        if x is None or not _same_type(x, v):
            continue
        c = compare(x, v)
        if c == 0:
            best = i
            if not descending:
                continue
            return i
        if (c < 0) != descending:
            best = i
        else:
            break
    if best is None:
        raise NA
    return best


@fn('VLOOKUP')
def f_vlookup(v, table, col, approx=None):
    if isinstance(v, Arr) and not (v.h == 1 and v.w == 1):
        return v.map(lambda x: f_vlookup(x, table, col, approx))
    v = scalar(v)
    if isinstance(v, XLErr):
        raise v
    t = as_arr(table)
    k = to_int(col)
    if k < 1:
        raise VALUE
    if k > t.w:
        raise REF
    first = [row[0] for row in t.rows]
    exact = approx is not None and not to_bool(approx)
    if exact:
        for i, x in enumerate(first):
            if _lookup_eq(v, x):
                return t.rows[i][k - 1]
        raise NA
    return t.rows[_approx_index(first, v)][k - 1]


@fn('HLOOKUP')
def f_hlookup(v, table, row, approx=None):
    v = scalar(v)
    t = as_arr(table)
    k = to_int(row)
    if k < 1:
        raise VALUE
    if k > t.h:
        raise REF
    first = t.rows[0]
    exact = approx is not None and not to_bool(approx)
    if exact:
        for j, x in enumerate(first):
            if _lookup_eq(v, x):
                return t.rows[k - 1][j]
        raise NA
    return t.rows[k - 1][_approx_index(first, v)]


@fn('LOOKUP')
def f_lookup(v, lookup, result=None):
    v = scalar(v)
    la = as_arr(lookup)
    vals = la.vector() if (la.h == 1 or la.w == 1) else [r[0] for r in la.rows]
    i = _approx_index(vals, v)
    if result is None:
        if la.h == 1 or la.w == 1:
            return vals[i]
        return la.rows[i][-1]
    res = as_arr(result).vector()
    if i >= len(res):
        raise NA
    return res[i]


@fn('MATCH')
def f_match(v, arr, kind=None):
    if isinstance(v, Arr) and not (v.h == 1 and v.w == 1):
        return v.map(lambda x: f_match(x, arr, kind))
    v = scalar(v)
    if isinstance(v, XLErr):
        raise v
    try:
        vals = as_arr(arr).vector()
    except XLErr:
        raise NA
    k = 1 if kind is None else to_int(kind)
    if k == 0:
        for i, x in enumerate(vals):
            if _lookup_eq(v, x):
                return i + 1
        raise NA
    return _approx_index(vals, v, descending=k < 0) + 1


@fn('XMATCH')
def f_xmatch(v, arr, mode=None, search=None):
    return _xfind(scalar(v), as_arr(arr).vector(), mode, search) + 1


def _xfind(v, vals, mode, search):
    m = 0 if mode is None else to_int(mode)
    s = 1 if search is None else to_int(search)
    order = list(range(len(vals)))
    if s < 0:
        order.reverse()
    if m in (0, 2):
        for i in order:
            x = vals[i]
            if m == 2 and isinstance(v, str) and isinstance(x, str):
                if wildcard_re(v).fullmatch(x):
                    return i
            elif m == 0 and (_same_type(v, x) and compare(v, x) == 0):
                return i
        raise NA
    best = None
    for i in order:
        x = vals[i]
        if x is None or not _same_type(x, v):
            continue
        c = compare(x, v)
        if c == 0:
            return i
        if m == -1 and c < 0 and (best is None or compare(x, vals[best]) > 0):
            best = i
        if m == 1 and c > 0 and (best is None or compare(x, vals[best]) < 0):
            best = i
    if best is None:
        raise NA
    return best


@fn('XLOOKUP', lazy=True)
def f_xlookup(args, ctx):
    if len(args) < 3:
        raise VALUE
    v = ev(args[0], ctx)
    look = as_arr(ev(args[1], ctx))
    ret = as_arr(ev(args[2], ctx))
    nf = args[3] if len(args) > 3 and args[3] != ('empty',) else None
    mode = scalar(ev(args[4], ctx)) if len(args) > 4 else None
    search = scalar(ev(args[5], ctx)) if len(args) > 5 else None

    def one(val):
        try:
            i = _xfind(scalar(val), look.vector(), mode, search)
        except XLErr as e:
            if e.code == '#N/A' and nf is not None:
                return ev(nf, ctx)
            raise
        if look.w == 1:  # 세로 찾기 → 반환 범위의 i번째 행
            row = ret.rows[i]
            return row[0] if ret.w == 1 else Arr([row])
        col = [r[i] for r in ret.rows]
        return col[0] if ret.h == 1 else Arr([[x] for x in col])

    if isinstance(v, Arr) and not (v.h == 1 and v.w == 1):
        return v.map(lambda x: scalar(one(x)))
    return one(v)


@fn('INDEX')
def f_index(arr, r, c=None):
    a = as_arr(arr)
    ri = to_int(r) if r is not None else 0
    ci = to_int(c) if c is not None else 0
    if c is None and (a.h == 1 or a.w == 1) and ri:
        vals = a.flat()
        if ri < 1 or ri > len(vals):
            raise REF
        return vals[ri - 1]
    if ri < 0 or ci < 0 or ri > a.h or ci > a.w:
        raise REF
    if ri and ci:
        return a.rows[ri - 1][ci - 1]
    if ri:
        return Arr([a.rows[ri - 1]])
    if ci:
        return Arr([[row[ci - 1]] for row in a.rows])
    return a


def _ref_node_info(node, ctx):
    if node[0] == 'ref':
        return node[2], node[3], 1, 1
    a = ref_value(node, ctx)
    return a.origin[1], a.origin[2], a.h, a.w


def ref_value(node, ctx):
    """참조로 쓰이는 인수 → origin 이 있는 Arr (셀·범위·이름·OFFSET·INDIRECT)."""
    if node[0] == 'ref':
        s = ctx.sheet_obj(node[1])
        return Arr([[s.get(node[2], node[3])]], origin=(s.name, node[2], node[3]))
    v = ev(node, ctx)
    if isinstance(v, Arr) and v.origin is not None:
        return v
    raise VALUE


def area(sheet_name, r1, c1, r2, c2, ctx):
    if min(r1, c1) < 1 or r2 < r1 or c2 < c1 or r2 > 1_048_576 or c2 > 16_384:
        raise REF
    s = ctx.sheet_obj(sheet_name)
    if (r2 - r1 + 1) * (c2 - c1 + 1) > 2_000_000:
        raise XLErr('#CALC!')
    return Arr([[s.get(r, c) for c in range(c1, c2 + 1)] for r in range(r1, r2 + 1)], origin=(s.name, r1, c1))


@fn('OFFSET', lazy=True)
def f_offset(args, ctx):
    if not 3 <= len(args) <= 5:
        raise VALUE
    base = ref_value(args[0], ctx)
    sh, r0, c0 = base.origin
    dr = to_int(scalar(ev(args[1], ctx), ctx)) if args[1] != ('empty',) else 0
    dc = to_int(scalar(ev(args[2], ctx), ctx)) if args[2] != ('empty',) else 0
    h = to_int(scalar(ev(args[3], ctx), ctx)) if len(args) > 3 and args[3] != ('empty',) else base.h
    w = to_int(scalar(ev(args[4], ctx), ctx)) if len(args) > 4 and args[4] != ('empty',) else base.w
    if h == 0 or w == 0:
        raise REF
    top, left = r0 + dr, c0 + dc
    r1, r2 = (top, top + h - 1) if h > 0 else (top + h + 1, top)      # 음수 높이·너비: 기준에서 위·왼쪽으로
    c1, c2 = (left, left + w - 1) if w > 0 else (left + w + 1, left)
    if r1 < 1 or c1 < 1:
        raise REF
    return area(sh, r1, c1, r2, c2, ctx)


@fn('INDIRECT', lazy=True)
def f_indirect(args, ctx):
    text = to_str(scalar(ev(args[0], ctx), ctx)).strip()
    a1 = True if len(args) < 2 or args[1] == ('empty',) else to_bool(scalar(ev(args[1], ctx), ctx))
    if text.upper() in ctx.book.names:
        return ref_value(('name', text), ctx)
    sheet = None
    m = re.match(r"^(?:'((?:[^']|'')+)'|([^!]+))!(.+)$", text)
    if m:
        sheet = (m.group(1) or '').replace("''", "'") or m.group(2)
        text = m.group(3)
    try:
        if a1 and re.fullmatch(r'\$?[A-Za-z]{1,3}:\$?[A-Za-z]{1,3}', text):
            a, b = text.replace('$', '').split(':')
            s_ = ctx.sheet_obj(sheet or ctx.sheet)
            r1, r2, c1, c2 = 1, max(s_.max_row, 1), col_num(a), col_num(b)
        elif a1 and re.fullmatch(r'\$?\d+:\$?\d+', text):
            a, b = text.replace('$', '').split(':')
            s_ = ctx.sheet_obj(sheet or ctx.sheet)
            r1, r2, c1, c2 = int(a), int(b), 1, max(s_.max_col, 1)
        elif a1:
            parts = text.replace('$', '').split(':')
            r1, c1 = parse_addr(parts[0])
            r2, c2 = parse_addr(parts[-1])
        else:
            cells = [re.fullmatch(r'R(\d+)C(\d+)', p.strip(), re.I) for p in text.split(':')]
            if not all(cells):
                raise ValueError(text)
            r1, c1 = int(cells[0].group(1)), int(cells[0].group(2))
            r2, c2 = int(cells[-1].group(1)), int(cells[-1].group(2))
    except ValueError:
        raise REF
    return area(sheet or ctx.sheet, min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2), ctx)


@fn('ROW', lazy=True)
def f_row(args, ctx):
    if not args or args[0] == ('empty',):
        return ctx.r
    r, _, h, _ = _ref_node_info(args[0], ctx)
    return r if h == 1 else Arr([[r + i] for i in range(h)])


@fn('COLUMN', lazy=True)
def f_column(args, ctx):
    if not args or args[0] == ('empty',):
        return ctx.c
    _, c, _, w = _ref_node_info(args[0], ctx)
    return c if w == 1 else Arr([[c + j for j in range(w)]])


@fn('ROWS')
def f_rows(a):
    return as_arr(a).h


@fn('COLUMNS')
def f_columns(a):
    return as_arr(a).w


@fn('TRANSPOSE')
def f_transpose(a):
    a = as_arr(a)
    return Arr([list(col) for col in zip(*a.rows)])


# 동적 배열
@fn('FILTER')
def f_filter(arr, include, if_empty=None):
    a, inc = as_arr(arr), as_arr(include)
    if inc.w == 1 and inc.h == a.h:
        rows = [row for row, k in zip(a.rows, inc.flat()) if to_bool(k)]
        out = Arr(rows) if rows else None
    elif inc.h == 1 and inc.w == a.w:
        keep = [j for j, k in enumerate(inc.flat()) if to_bool(k)]
        out = Arr([[row[j] for j in keep] for row in a.rows]) if keep else None
    else:
        raise VALUE
    if out is None:
        if if_empty is None:
            raise XLErr('#CALC!')
        return if_empty
    return out


@fn('UNIQUE')
def f_unique(arr, by_col=None, exactly_once=None):
    a = as_arr(arr)
    once = exactly_once is not None and to_bool(exactly_once)
    keys = [tuple((x.lower() if isinstance(x, str) else x) for x in row) for row in a.rows]
    out, seen = [], set()
    for k, row in zip(keys, a.rows):
        if once and keys.count(k) > 1:
            continue
        if k in seen:
            continue
        seen.add(k)
        out.append(row)
    return Arr(out)


def _sort_key(v):
    rank = 2 if isinstance(v, bool) else (1 if isinstance(v, str) else (3 if v is None else 0))
    return (rank, v.lower() if isinstance(v, str) else (0 if v is None else v))


@fn('SORT')
def f_sort(arr, idx=None, order=None, by_col=None):
    a = as_arr(arr)
    k = to_int(idx) - 1 if idx is not None else 0
    desc = order is not None and to_num(order) < 0
    if k < 0 or k >= a.w:
        raise VALUE
    return Arr(sorted(a.rows, key=lambda row: _sort_key(row[k]), reverse=desc))


@fn('SORTBY')
def f_sortby(arr, by, order=None):
    a, b = as_arr(arr), as_arr(by).vector()
    desc = order is not None and to_num(order) < 0
    pairs = sorted(zip(b, a.rows), key=lambda p: _sort_key(p[0]), reverse=desc)
    return Arr([row for _, row in pairs])


# 데이터베이스 함수
def _db_rows(args, ctx):
    """D함수 공통: (데이터베이스, 필드, 조건 범위) → 조건에 맞는 행의 필드 값(필드가 없으면 행 전체).

    조건 범위의 머리글이 필드 이름이면 일반 조건(문자는 '…로 시작'), 머리글이 비었거나 필드 이름이 아닌 칸에
    수식이 있으면 '계산 조건' — 수식이 데이터베이스 첫 레코드를 가리킨다고 보고 레코드마다 행을 옮겨 계산한다.
    """
    if len(args) != 3:
        raise VALUE
    db = as_arr(ev(args[0], ctx))
    field = None if args[1] == ('empty',) else scalar(ev(args[1], ctx), ctx)
    cr = ref_value(args[2], ctx) if args[2][0] != 'arr' else as_arr(ev(args[2], ctx))
    heads = [to_str(h).strip().lower() for h in db.rows[0]]
    if field is None:
        col = None
    elif is_num(field):
        col = to_int(field) - 1
        if col < 0 or col >= db.w:
            raise VALUE
    else:
        name = to_str(field).strip().lower()
        if name not in heads:
            raise VALUE
        col = heads.index(name)
    cheads = [to_str(h).strip().lower() for h in cr.rows[0]]
    csheet = ctx.sheet_obj(cr.origin[0]) if cr.origin else None
    data_top = db.origin[1] + 1 if db.origin else None
    conds = []
    for i, row in enumerate(cr.rows[1:], 1):
        tests = []
        for j, (h, v) in enumerate(zip(cheads, row)):
            raw = csheet.raw(cr.origin[1] + i, cr.origin[2] + j) if csheet else None
            if h in heads and not (h == '' and isinstance(raw, Formula)):
                if v is None or v == '':
                    continue
                tests.append(('field', heads.index(h), make_crit(v, prefix=True)))
            elif isinstance(raw, Formula):
                if data_top is None:
                    raise VALUE
                tests.append(('calc', raw, (cr.origin[0], cr.origin[1] + i, cr.origin[2] + j)))
            elif v is None or v == '':
                continue
            else:
                raise VALUE
        conds.append(tests)
    if not conds:
        conds = [[]]

    def passes(k, row, test):
        if test[0] == 'field':
            return test[2](row[test[1]])
        raw, (sh, r, c) = test[1], test[2]
        node = shift(raw.ast, k, 0)
        v = evaluate(node, ctx.book, sh, r, c)
        try:
            return to_bool(v)
        except XLErr:
            return False

    out = []
    for k, row in enumerate(db.rows[1:]):
        if any(all(passes(k, row, t) for t in tests) for tests in conds):
            out.append(row[col] if col is not None else row)
    return out


def _dfn(name, reduce):
    FUNCS[name] = (lambda args, ctx: reduce(_db_rows(args, ctx), args), True)


def _dnums(rows):
    return [v for v in rows if is_num(v)]


def _davg(rows, _a):
    xs = _dnums(rows)
    if not xs:
        raise DIV0
    return fix(math.fsum(xs) / len(xs))


def _dcount(rows, args):
    if args[1] == ('empty',):
        return sum(1 for r in rows if any(is_num(v) for v in r))
    return len(_dnums(rows))


def _dcounta(rows, args):
    if args[1] == ('empty',):
        return len(rows)
    return sum(1 for v in rows if v is not None and v != '')


def _dget(rows, _a):
    if not rows:
        raise VALUE
    if len(rows) > 1:
        raise NUM
    return rows[0]


_dfn('DSUM', lambda rows, a: fix(math.fsum(_dnums(rows))))
_dfn('DAVERAGE', _davg)
_dfn('DCOUNT', _dcount)
_dfn('DCOUNTA', _dcounta)
_dfn('DMAX', lambda rows, a: max(_dnums(rows)) if _dnums(rows) else 0)
_dfn('DMIN', lambda rows, a: min(_dnums(rows)) if _dnums(rows) else 0)
_dfn('DPRODUCT', lambda rows, a: fix(math.prod(_dnums(rows))) if _dnums(rows) else 0)
_dfn('DSTDEV', lambda rows, a: fix(math.sqrt(_var(_dnums(rows), False))))
_dfn('DVAR', lambda rows, a: fix(_var(_dnums(rows), False)))
_dfn('DGET', _dget)


SUBTOTAL_FUNCS = {1: 'AVERAGE', 2: 'COUNT', 3: 'COUNTA', 4: 'MAX', 5: 'MIN', 6: 'PRODUCT', 7: 'STDEV',
                  8: 'STDEV.P', 9: 'SUM', 10: 'VAR', 11: 'VAR.P'}


@fn('SUBTOTAL', lazy=True)
def f_subtotal(args, ctx):
    if len(args) < 2:
        raise VALUE
    k = to_int(scalar(ev(args[0], ctx), ctx))
    name = SUBTOTAL_FUNCS.get(k % 100)
    if not name:
        raise VALUE
    vals = []
    for node in args[1:]:
        a = ref_value(node, ctx)
        sh = ctx.sheet_obj(a.origin[0])
        r0, c0 = a.origin[1], a.origin[2]
        rows = []
        for i, row in enumerate(a.rows):
            new = []
            for j, v in enumerate(row):
                raw = sh.raw(r0 + i, c0 + j)
                if isinstance(raw, Formula) and re.search(r'\b(SUBTOTAL|AGGREGATE)\s*\(', raw.text, re.I):
                    new.append(None)
                else:
                    new.append(v)
            rows.append(new)
        vals.append(Arr(rows))
    return FUNCS[name][0](*vals)


def supported_functions():
    return sorted(FUNCS)


# ---------------------------------------------------------- 결과 비교 -------
def same_value(a, b, tol=1e-9):
    if isinstance(a, Arr) or isinstance(b, Arr):
        if not (isinstance(a, Arr) and isinstance(b, Arr)) or (a.h, a.w) != (b.h, b.w):
            return False
        return all(same_value(x, y, tol) for x, y in zip(a.flat(), b.flat()))
    if a is None:
        a = 0
    if b is None:
        b = 0
    if isinstance(a, XLErr) or isinstance(b, XLErr):
        return a == b
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if is_num(a) and is_num(b):
        return abs(a - b) <= tol * max(1, abs(a), abs(b))
    return a == b


def display(v):
    """화면 표시용 문자열."""
    if isinstance(v, XLErr):
        return v.code
    if isinstance(v, Arr):
        return '{' + ';'.join(','.join(display(x) for x in row) for row in v.rows) + '}'
    if v is None:
        return ''
    if isinstance(v, bool):
        return 'TRUE' if v else 'FALSE'
    if is_num(v):  # 엑셀 '일반' 서식처럼 쉼표 없이
        return str(v) if isinstance(v, int) else f'{v:.10g}'
    return str(v)


# ------------------------------------------------------------ 다시 쓰기 ------
def _ref_text(r, c, ra, ca):
    return ('$' if ca else '') + col_name(c) + ('' if r is None else ('$' if ra else '') + str(r))


def _sheet_prefix(sh):
    if not sh:
        return ''
    return (sh if re.fullmatch(r'[A-Za-z_가-힣][\w가-힣.]*', sh) else "'" + sh.replace("'", "''") + "'") + '!'


def _lit(v):
    if isinstance(v, str):
        return '"' + v.replace('"', '""') + '"'
    if isinstance(v, bool):
        return 'TRUE' if v else 'FALSE'
    if isinstance(v, XLErr):
        return v.code
    return fmt_general(v)


def unparse(node, parent_prec=0):
    """AST → 수식 문자열(앞의 = 없음). 채우기 결과 수식을 보여 줄 때 쓴다."""
    kind = node[0]
    if kind == 'num':
        return fmt_general(node[1])
    if kind == 'str':
        return _lit(node[1])
    if kind == 'bool':
        return 'TRUE' if node[1] else 'FALSE'
    if kind == 'err':
        return node[1]
    if kind == 'empty':
        return ''
    if kind == 'name':
        return node[1]
    if kind == 'ref':
        return _sheet_prefix(node[1]) + _ref_text(*node[2:6])
    if kind == 'range':
        return _sheet_prefix(node[1]) + _ref_text(*node[2:6]) + ':' + _ref_text(*node[6:10])
    if kind == 'arr':
        return '{' + ';'.join(','.join(_lit(v) for v in row) for row in node[1]) + '}'
    if kind == 'neg':
        return '-' + unparse(node[1], 6)
    if kind == 'pct':
        return unparse(node[1], 7) + '%'
    if kind == 'call':
        return node[1] + '(' + ','.join(unparse(a) for a in node[2]) + ')'
    if kind == 'bin':
        p = BIN_PREC[node[1]]
        s = unparse(node[2], p) + node[1] + unparse(node[3], p + 1)
        return '(' + s + ')' if p < parent_prec else s
    raise FormulaError(kind)


# ------------------------------------------------------------ 추가 함수(컴활·실무) --
@fn('FREQUENCY')
def f_frequency(data, bins):
    """구간별 개수: bins 의 각 값 이하(앞 구간 초과), 마지막 칸은 가장 큰 구간 초과."""
    xs = nums_from([as_arr(data)])
    bs = sorted(nums_from([as_arr(bins)]))
    out, prev = [], None
    for b in bs:
        out.append([sum(1 for x in xs if (prev is None or x > prev) and x <= b)])
        prev = b
    out.append([sum(1 for x in xs if prev is None or x > prev)])
    return Arr(out)


def _tvm_type(t):
    return 1 if t is not None and to_num(t) != 0 else 0


@fn('PMT')
def f_pmt(rate, nper, pv, fv=None, kind=None):
    r, n, p = to_num(rate), to_num(nper), to_num(pv)
    f = to_num(fv) if fv is not None else 0
    t = _tvm_type(kind)
    if n == 0:
        raise NUM
    if r == 0:
        return fix(-(p + f) / n)
    q = (1 + r) ** n
    return fix(-(r * (p * q + f)) / ((1 + r * t) * (q - 1)))


@fn('FV')
def f_fv(rate, nper, pmt, pv=None, kind=None):
    r, n, m = to_num(rate), to_num(nper), to_num(pmt)
    p = to_num(pv) if pv is not None else 0
    t = _tvm_type(kind)
    if r == 0:
        return fix(-(p + m * n))
    q = (1 + r) ** n
    return fix(-(p * q + m * (1 + r * t) * (q - 1) / r))


@fn('PV')
def f_pv(rate, nper, pmt, fv=None, kind=None):
    r, n, m = to_num(rate), to_num(nper), to_num(pmt)
    f = to_num(fv) if fv is not None else 0
    t = _tvm_type(kind)
    if r == 0:
        return fix(-(f + m * n))
    q = (1 + r) ** n
    return fix(-(f + m * (1 + r * t) * (q - 1) / r) / q)


@fn('NPER')
def f_nper(rate, pmt, pv, fv=None, kind=None):
    r, m, p = to_num(rate), to_num(pmt), to_num(pv)
    f = to_num(fv) if fv is not None else 0
    t = _tvm_type(kind)
    if r == 0:
        if m == 0:
            raise NUM
        return fix(-(p + f) / m)
    a = m * (1 + r * t) / r
    try:
        return fix(math.log((a - f) / (a + p)) / math.log(1 + r))
    except (ValueError, ZeroDivisionError):
        raise NUM


@fn('RATE')
def f_rate(nper, pmt, pv, fv=None, kind=None, guess=None):
    n, m, p = to_num(nper), to_num(pmt), to_num(pv)
    f = to_num(fv) if fv is not None else 0
    t = _tvm_type(kind)
    r = to_num(guess) if guess is not None else 0.1

    def g(x):
        if abs(x) < 1e-12:
            return p + m * n + f
        q = (1 + x) ** n
        return p * q + m * (1 + x * t) * (q - 1) / x + f
    for _ in range(100):                           # 엑셀처럼 반복 계산(뉴턴 방법)
        y = g(r)
        dy = (g(r + 1e-7) - y) / 1e-7
        if dy == 0:
            break
        nr = r - y / dy
        if abs(nr - r) < 1e-10:
            return fix(nr)
        r = nr
    raise NUM


@fn('NPV')
def f_npv(rate, *values):
    r = to_num(rate)
    xs = nums_from(values)
    return fix(math.fsum(v / (1 + r) ** (i + 1) for i, v in enumerate(xs)))


@fn('WEEKNUM')
def f_weeknum(v, kind=None):
    d = _date_of(v)
    k = to_int(kind) if kind is not None else 1
    if k == 21:                                     # ISO 주
        return d.isocalendar()[1]
    start = {1: 6, 2: 0, 11: 0, 12: 1, 13: 2, 14: 3, 15: 4, 16: 5, 17: 6}.get(k)   # 주 시작 요일(월=0)
    if start is None:
        raise NUM
    jan1 = dt.date(d.year, 1, 1)
    offset = (jan1.weekday() - start) % 7
    return ((d - jan1).days + offset) // 7 + 1


@fn('DAYS360')
def f_days360(start, end, method=None):
    a, b = _date_of(start), _date_of(end)
    eu = method is not None and to_bool(method)
    d1, d2 = a.day, b.day
    if eu:
        d1, d2 = min(d1, 30), min(d2, 30)
    else:
        def last_feb(d):
            return d.month == 2 and (d + dt.timedelta(1)).month == 3
        if d1 == 31 or last_feb(a):
            d1 = 30
        if d2 == 31 and d1 >= 30:
            d2 = 30
    return (b.year - a.year) * 360 + (b.month - a.month) * 30 + (d2 - d1)


@fn('YEARFRAC')
def f_yearfrac(start, end, basis=None):
    a, b = sorted([_date_of(start), _date_of(end)])
    k = to_int(basis) if basis is not None else 0
    if k == 0:
        return fix(f_days360(date_serial(a), date_serial(b)) / 360)
    if k == 1:
        days = (b - a).days
        yrs = range(a.year, b.year + 1)
        avg = sum(366 if calendar.isleap(y) else 365 for y in yrs) / len(yrs)
        return fix(days / avg)
    if k == 2:
        return fix((b - a).days / 360)
    if k == 3:
        return fix((b - a).days / 365)
    if k == 4:
        return fix(f_days360(date_serial(a), date_serial(b), True) / 360)
    raise NUM


@fn('MROUND')
def f_mround(x, m):
    v, mm = to_num(x), to_num(m)
    if mm == 0:
        return 0
    if v * mm < 0:
        raise NUM
    q = Decimal(repr(float(v))) / Decimal(repr(float(mm)))
    return fix(float(q.quantize(Decimal(1), rounding=ROUND_HALF_UP) * Decimal(repr(float(mm)))))


@fn('FACT')
def f_fact(x):
    n = to_int(x)
    if n < 0 or n > 170:
        raise NUM
    return math.factorial(n)


@fn('EXP')
def f_exp(x):
    try:
        return fix(math.exp(to_num(x)))
    except OverflowError:
        raise NUM


@fn('LN')
def f_ln(x):
    v = to_num(x)
    if v <= 0:
        raise NUM
    return fix(math.log(v))


@fn('LOG')
def f_log(x, base=None):
    v = to_num(x)
    b = to_num(base) if base is not None else 10
    if v <= 0 or b <= 0 or b == 1:
        raise NUM
    return fix(math.log(v, b))


@fn('LOG10')
def f_log10(x):
    return f_log(x, 10)


@fn('EVEN')
def f_even(x):
    v = to_num(x)
    n = math.ceil(abs(v) / 2) * 2
    return n if v >= 0 else -n


@fn('ODD')
def f_odd(x):
    v = to_num(x)
    n = math.ceil(abs(v))
    if n % 2 == 0:
        n += 1
    return n if v >= 0 else -n


@fn('ISNONTEXT')
def f_isnontext(v):
    return not isinstance(scalar(v), str)


@fn('TYPE')
def f_type(v):
    if isinstance(v, Arr) and not (v.h == 1 and v.w == 1):
        return 64
    v = scalar(v)
    if isinstance(v, XLErr):
        return 16
    if isinstance(v, bool):
        return 4
    if isinstance(v, str):
        return 2
    return 1


@fn('TIMEVALUE')
def f_timevalue(t):
    n = parse_date_text(to_str(t))
    if n is None:
        raise VALUE
    return fix(n - math.floor(n))


@fn('RAND')
def f_rand():
    import random
    return random.random()


@fn('RANDBETWEEN')
def f_randbetween(a, b):
    import random
    lo, hi = math.ceil(to_num(a)), math.floor(to_num(b))
    if lo > hi:
        raise NUM
    return random.randint(lo, hi)


@fn('GEOMEAN')
def f_geomean(*args):
    xs = nums_from(args)
    if not xs or any(x <= 0 for x in xs):
        raise NUM
    return fix(math.exp(math.fsum(math.log(x) for x in xs) / len(xs)))


@fn('HARMEAN')
def f_harmean(*args):
    xs = nums_from(args)
    if not xs or any(x <= 0 for x in xs):
        raise NUM
    return fix(len(xs) / math.fsum(1 / x for x in xs))


@fn('ADDRESS')
def f_address(row, col, abs_num=None, a1=None, sheet=None):
    r, c = to_int(row), to_int(col)
    k = to_int(abs_num) if abs_num is not None else 1
    if r < 1 or c < 1 or k not in (1, 2, 3, 4):
        raise VALUE
    ra, ca = k in (1, 2), k in (1, 3)
    text = ('$' if ca else '') + col_name(c) + ('$' if ra else '') + str(r)
    if sheet is not None:
        text = _sheet_prefix(to_str(sheet)) + text
    return text


@fn('MMULT')
def f_mmult(a, b):
    A, B = as_arr(a), as_arr(b)
    if A.w != B.h:
        raise VALUE
    return Arr([[fix(math.fsum(to_num(A.rows[i][k]) * to_num(B.rows[k][j]) for k in range(A.w)))
                 for j in range(B.w)] for i in range(A.h)])


@fn('PERCENTRANK', 'PERCENTRANK.INC')
def f_percentrank(arr, x, sig=None):
    xs = sorted(nums_from([as_arr(arr)]))
    v = to_num(x)
    digits = to_int(sig) if sig is not None else 3
    if not xs or v < xs[0] or v > xs[-1]:
        raise NA
    n = len(xs)
    below = sum(1 for t in xs if t < v)
    if v in xs:
        r = below / (n - 1) if n > 1 else 1
    else:
        lo = max(t for t in xs if t < v)
        hi = min(t for t in xs if t > v)
        r = (below - 1 + (v - lo) / (hi - lo)) / (n - 1)
    q = Decimal(repr(r)).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_DOWN)
    return fix(float(q))


# ------------------------------------------------- 2026-10-04 점검에서 추가 -----
@fn('CLEAN')
def f_clean(t):
    return elementwise(lambda v: re.sub(r'[\x00-\x1f]', '', to_str(v)), t)


@fn('T')
def f_t(v):
    v = scalar(v)
    return v if isinstance(v, str) else ''


def _nums_a(args):
    """MAXA·MINA: 범위의 논리값 TRUE=1·FALSE=0, 글자=0 도 센다."""
    out = []
    for a in args:
        for v in (a.flat() if isinstance(a, Arr) else [a]):
            if isinstance(v, XLErr):
                raise v
            if isinstance(v, bool):
                out.append(int(v))
            elif is_num(v):
                out.append(v)
            elif isinstance(v, str):
                out.append(0)
    return out


@fn('MAXA')
def f_maxa(*args):
    xs = _nums_a(args)
    return max(xs) if xs else 0


@fn('MINA')
def f_mina(*args):
    xs = _nums_a(args)
    return min(xs) if xs else 0


@fn('GCD')
def f_gcd(*args):
    xs = [int(to_num(v)) for v in nums_from(args)]
    if any(x < 0 for x in xs):
        raise NUM
    out = 0
    for x in xs:
        out = math.gcd(out, x)
    return out


@fn('LCM')
def f_lcm(*args):
    xs = [int(to_num(v)) for v in nums_from(args)]
    if any(x < 0 for x in xs):
        raise NUM
    out = 1
    for x in xs:
        out = out * x // math.gcd(out, x) if x and out else 0
    return out


@fn('COMBIN')
def f_combin(n, k):
    n, k = int(to_num(n)), int(to_num(k))
    if n < 0 or k < 0 or k > n:
        raise NUM
    return fix(math.comb(n, k))


@fn('PERMUT')
def f_permut(n, k):
    n, k = int(to_num(n)), int(to_num(k))
    if n < 0 or k < 0 or k > n:
        raise NUM
    return fix(math.perm(n, k))


FUNCS['RADIANS'] = (_txt(lambda v: fix(math.radians(to_num(v)))), False)
FUNCS['DEGREES'] = (_txt(lambda v: fix(math.degrees(to_num(v)))), False)


@fn('PERCENTILE.EXC')
def f_percentile_exc(arr, k):
    xs = sorted(nums_from([as_arr(arr)]))
    p = to_num(k)
    n = len(xs)
    pos = p * (n + 1)
    if not xs or pos < 1 or pos > n:
        raise NUM
    lo = int(math.floor(pos))
    hi = min(lo + 1, n)
    return fix(xs[lo - 1] + (xs[hi - 1] - xs[lo - 1]) * (pos - lo))


@fn('QUARTILE.EXC')
def f_quartile_exc(arr, q):
    qi = to_int(q)
    if qi < 1 or qi > 3:
        raise NUM
    return f_percentile_exc(arr, qi / 4)


AGG_FUNCS = {1: 'AVERAGE', 2: 'COUNT', 3: 'COUNTA', 4: 'MAX', 5: 'MIN', 6: 'PRODUCT', 7: 'STDEV.S', 8: 'STDEV.P',
             9: 'SUM', 10: 'VAR.S', 11: 'VAR.P', 12: 'MEDIAN', 13: 'MODE.SNGL', 14: 'LARGE', 15: 'SMALL',
             16: 'PERCENTILE.INC', 17: 'QUARTILE.INC', 18: 'PERCENTILE.EXC', 19: 'QUARTILE.EXC'}


@fn('AGGREGATE')
def f_aggregate(func, options, ref, k=None):
    """AGGREGATE(함수 번호, 옵션, 범위[, k]) — 옵션 2·3·6·7 은 오류 값을 건너뜀(숨긴 행은 이 연습장에 없음)."""
    f = to_int(func)
    opt = to_int(options) if options is not None and options is not EMPTY else 0
    name = AGG_FUNCS.get(f)
    if name is None or name not in FUNCS:
        raise VALUE
    vals = as_arr(ref)
    if opt in (2, 3, 6, 7):
        vals = Arr([[None if isinstance(v, XLErr) else v for v in row] for row in vals.rows])
    fn_, _ = FUNCS[name]
    if f >= 14:
        if k is None:
            raise VALUE
        return fn_(vals, k)
    return fn_(vals)
