"""실습 파일 → 정답 파일의 차이를 보고 실제 시험 말투의 문제 지문을 만든다(교재 지문이 없는 실습 파일용).

만든 지문은 사용자 PC 의 data/ 에만 둔다(원본 교재 파일에서 나온 내용).
"""
import re

from . import formula as fx
from . import pivots, xlsx
from .compare import Wb, _charts_of, _groups, _span, _style_key
from .exam import _color, _norm_text

COLOR_NAMES = {'FF0000': '빨강', 'C00000': '진한 빨강', 'FFC000': '주황', 'FFFF00': '노랑', '92D050': '연한 녹색',
               '00B050': '녹색', '00B0F0': '연한 파랑', '0070C0': '파랑', '002060': '진한 파랑', '7030A0': '자주',
               '0000FF': '파랑', '000000': '검정', 'FFFFFF': '흰색', 'FF00FF': '자홍', '00FFFF': '청록',
               '808080': '회색', 'D9D9D9': '흰색, 배경 1, 15% 더 어둡게'}
FUNC_KO = {'sum': '합계', 'count': '개수', 'average': '평균', 'max': '최대', 'min': '최소', 'product': '곱'}
SUBTOTAL_KO = {1: '평균', 2: '숫자 개수', 3: '개수', 4: '최대', 5: '최소', 6: '곱', 9: '합계', 10: '분산', 7: '표준 편차'}


# ------------------------------------------------------------ 한국어 ---------
def _batchim(word):
    word = str(word).strip().strip("'\"]")
    if not word:
        return False
    ch = word[-1]
    if '가' <= ch <= '힣':
        return (ord(ch) - 0xAC00) % 28 != 0
    if ch.isdigit():
        return ch in '0136789'
    return ch.lower() in 'lmnr'


def _rieul(word):
    ch = str(word).strip().strip("'\"]")[-1:]
    return '가' <= ch <= '힣' and (ord(ch) - 0xAC00) % 28 == 8


def j(word, pair):
    """조사: j('지점', '이/가') → '지점이'"""
    a, b = pair.split('/')
    if pair == '으로/로':
        return f"{word}{'로' if not _batchim(word) or _rieul(word) else '으로'}"
    return f'{word}{a if _batchim(word) else b}'


def q(v):
    return f"'{v}'"


def color_name(rgb):
    if not rgb:
        return '테마 색'
    return COLOR_NAMES.get(rgb.upper(), '#' + rgb.upper())


# ------------------------------------------------------------ 표 찾기 --------
def _text(ws, r, c):
    if r < 1 or c < 1:
        return None
    v = ws.cell(r, c).value
    return v.strip() if isinstance(v, str) and v.strip() and not v.startswith('=') else None


def header_row(ws, r, c1, c2):
    """r 행이 속한 표의 머리글 행: 위로 올라가며 글자가 절반 이상이고 바로 아래 행에 숫자·날짜가 있는 행."""
    width = c2 - c1 + 1
    for rr in range(r, max(0, r - 60), -1):
        texts = sum(1 for c in range(c1, c2 + 1) if _text(ws, rr, c))
        below = [ws.cell(rr + 1, c).value for c in range(c1, c2 + 1)]
        if texts * 2 >= width and texts >= 2 and any(not isinstance(v, str) and v is not None for v in below):
            return rr
    return None


def header_of(ws, r, c, max_up=25):
    """(r, c) 칸이 속한 열의 머리글: 위로 올라가며 처음 만나는 글자(숫자·수식이 아닌) 칸."""
    for rr in range(r - 1, max(0, r - max_up), -1):
        t = _text(ws, rr, c)
        if t and not t.startswith('='):
            return t.replace('\n', ' ')
    return None


def table_title(ws, r, c):
    """가까운 '[표n]' 제목."""
    for rr in range(r - 1, max(0, r - 40), -1):
        for cc in range(c, max(0, c - 13), -1):        # 같은 행이면 가장 가까운 왼쪽 제목
            t = _text(ws, rr, cc)
            if t and re.match(r'^\[표\s*\d+\]', t):
                return re.match(r'^\[표\s*\d+\]', t).group(0).replace(' ', '')
    return None


def label_left(ws, r, c):
    for cc in range(c - 1, max(0, c - 6), -1):
        t = _text(ws, r, cc)
        if t:
            return t
    return None


# ------------------------------------------------------------ 수식을 말로 ----
OPS = {'>=': '이상', '<=': '이하', '>': '초과', '<': '미만'}


class Speaker:
    """조건 수식을 '…이 … 이상' 같은 말로. 못 바꾸는 부분은 수식 그대로."""

    def __init__(self, ws, header_row=None):
        self.ws = ws
        self.header_row = header_row

    def name(self, node):
        if node[0] == 'ref':
            r, c = node[2], node[3]
            h = (_text(self.ws, self.header_row, c) if self.header_row else None) or header_of(self.ws, r, c)
            return q(h) if h else fx.unparse(node)
        if node[0] == 'range':
            c = node[3]
            h = _text(self.ws, self.header_row, c) if self.header_row else header_of(self.ws, node[2] or 2, c)
            return q(h) if h else fx.unparse(node)
        if node[0] == 'str':
            return q(node[1])
        if node[0] in ('num', 'bool'):
            return fx.unparse(node)
        if node[0] == 'call':
            f, a = node[1], node[2]
            simple = {'AVERAGE': '평균', 'MAX': '최대값', 'MIN': '최소값', 'SUM': '합계', 'MEDIAN': '중간값'}
            if f in simple and len(a) == 1:
                return f'{self.name(a[0])}의 {simple[f]}'
            if f in ('LARGE', 'SMALL') and len(a) == 2:
                return f"{self.name(a[0])}의 {fx.unparse(a[1])}번째로 {'큰' if f == 'LARGE' else '작은'} 값"
            if f in ('YEAR', 'MONTH', 'DAY') and len(a) == 1:
                return f"{self.name(a[0])}의 {'연도' if f == 'YEAR' else ('월' if f == 'MONTH' else '일')}"
            if f in ('LEFT', 'RIGHT') and len(a) in (1, 2):
                n = fx.unparse(a[1]) if len(a) == 2 else '1'
                return f"{self.name(a[0])}의 {'앞' if f == 'LEFT' else '뒤'} {n}글자"
            if f == 'MID' and len(a) == 3:
                return f'{self.name(a[0])}의 {fx.unparse(a[1])}번째부터 {fx.unparse(a[2])}글자'
            if f == 'COUNTIF' and len(a) == 2:
                return f'{self.name(a[0])}에서 {self.name(a[1])}의 개수'
        return fx.unparse(node)

    def cond(self, node):
        if node[0] == 'call' and node[1] in ('AND', 'OR'):
            parts = [self.cond(a) for a in node[2]]
            return (' 이고 ' if node[1] == 'AND' else ' 이거나 ').join(parts)
        if node[0] == 'call' and node[1] == 'NOT' and len(node[2]) == 1:
            return f'{self.cond(node[2][0])} 이(가) 아님'
        if node[0] == 'bin' and node[1] in ('=', '<>', '>=', '<=', '>', '<'):
            a, b = node[2], node[3]
            if a[0] == 'call' and a[1] == 'MOD' and b == ('num', 0):
                inner = a[2]
                if inner and inner[0][0] == 'call' and inner[0][1] == 'ROW' and inner[1] == ('num', 2):
                    return '행 번호가 짝수'
            if a[0] == 'call' and a[1] == 'MOD' and b == ('num', 1):
                inner = a[2]
                if inner and inner[0][0] == 'call' and inner[0][1] == 'ROW' and inner[1] == ('num', 2):
                    return '행 번호가 홀수'
            left, right = self.name(a), self.name(b)
            if node[1] == '=':
                return f'{j(left, "이/가")} {right}'
            if node[1] == '<>':
                return f'{j(left, "이/가")} {right} 이(가) 아님'
            return f'{j(left, "이/가")} {right} {OPS[node[1]]}'
        return '=' + fx.unparse(node)


def crit_words(text):
    """고급 필터·자동 필터 조건 값 '>=80', '김*', '서울' → 말."""
    s = str(text)
    m = re.match(r'^(<=|>=|<>|=|<|>)?(.*)$', s)
    op, rest = m.group(1) or '', m.group(2)
    if op in OPS:
        return f'{rest} {OPS[op]}'
    if op == '<>':
        return f'{q(rest)} 이(가) 아님'
    if rest.endswith('*') and not rest.startswith('*'):
        return f"{j(q(rest[:-1]), '으로/로')} 시작"
    if rest.startswith('*') and rest.endswith('*'):
        return f'{q(rest[1:-1])} 포함'
    if rest.startswith('*'):
        return f"{j(q(rest[1:]), '으로/로')} 끝남"
    return q(rest)


# ------------------------------------------------------------ 지문 ----------
def _funcs(texts):
    used = set()
    for t in texts:
        try:
            used |= fx.functions_used(fx.parse(t))
        except fx.FormulaError:
            continue
    return sorted(used)


def _cells(ws_s, ws_a, wv_a):
    changed = set()
    for row in ws_a.iter_rows():
        for cell in row:
            if cell.value != (ws_s.cell(cell.row, cell.column).value if ws_s is not None else None):
                changed.add((cell.row, cell.column))
    return _groups(changed)


def _is_empty_in(ws, comp):
    return ws is None or all(ws.cell(r, c).value in (None, '') for r, c in comp)


def _subtotal_task(sheet, ws_a, comp):
    texts = [xlsx.formula_text(ws_a.cell(r, c).value) or '' for r, c in comp]
    subs = [t for t in texts if 'SUBTOTAL' in t.upper()]
    if not subs:
        return None
    funcs = {}
    for t in subs:
        m = re.search(r'SUBTOTAL\(\s*(\d+)\s*,\s*\$?([A-Z]+)', t.upper())
        if m:
            funcs.setdefault(int(m.group(1)) % 100, set()).add(m.group(2))
    label_cells = [(r, c) for r, c in comp if isinstance(ws_a.cell(r, c).value, str)
                   and re.search(r'(요약|평균|최대|최소|개수|합계)\s*$', ws_a.cell(r, c).value)]
    gcol = label_cells[0][1] if label_cells else None
    top = min(r for r, _ in comp)
    cmin = min(c for _, c in comp)
    cmax = max(c for _, c in comp)
    hr = header_row(ws_a, top - 1, max(1, cmin - 3), cmax)
    name = lambda c: _text(ws_a, hr, c) if hr else None  # noqa: E731
    group = name(gcol) if gcol else None
    parts = []
    for fno, cols in sorted(funcs.items(), key=lambda kv: -len(kv[1])):
        fields = ', '.join(q(name(fx.col_num(cl)) or cl) for cl in sorted(cols))
        parts.append(f"{fields}의 {SUBTOTAL_KO.get(fno, fno)}")
    head = f"{j(q(group), '을/를')} 기준으로 " if group else ''
    joined = ' '.join(j(p_, '과/와') for p_ in parts[:-1]) + (' ' if len(parts) > 1 else '') + j(parts[-1], '을/를')
    return {'text': f"{q(sheet)} 시트에 대하여 {head}{joined} 계산하는 부분합을 작성하시오.",
            'items': ([f"정렬은 {j(q(group), '을/를')} 기준으로 오름차순(또는 정답과 같은 순서)으로 정렬하시오."] if group else [])
            + (['함수가 두 가지 이상이면 두 번째부터는 "새로운 값으로 대치"를 해제하시오.'] if len(parts) > 1 else []),
            'refs': _span(comp)}


def _sort_task(sheet, ws_s, ws_a, wv_a, comp):
    """원본 행을 재배열한 결과면 정렬 기준을 추정한다."""
    r1 = min(r for r, _ in comp)
    r2 = max(r for r, _ in comp)
    c1 = min(c for _, c in comp)
    c2 = max(c for _, c in comp)
    if r2 - r1 < 2 or ws_s is None:
        return None
    src = [tuple(ws_s.cell(r, c).value for c in range(c1, c2 + 1)) for r in range(r1, r2 + 1)]
    ans = [tuple(ws_a.cell(r, c).value for c in range(c1, c2 + 1)) for r in range(r1, r2 + 1)]
    if sorted(map(str, src)) != sorted(map(str, ans)) or src == ans:
        return None
    keys = []
    rows = ans
    for k in range(c2 - c1 + 1):
        col = [row[k] for row in rows]
        try:
            asc = all(not (a is not None and b is not None and a > b) for a, b in zip(col, col[1:]))
            desc = all(not (a is not None and b is not None and a < b) for a, b in zip(col, col[1:]))
        except TypeError:
            continue
        if asc or desc:
            keys.append((header_of(ws_a, r1, c1 + k) or fx.col_name(c1 + k), '오름차순' if asc else '내림차순'))
            break
    if not keys:
        first = header_of(ws_a, r1, c1)
        return {'text': f"{q(sheet)} 시트의 [{_span(comp)}] 영역을 정답과 같은 순서로 정렬하시오(사용자 지정 목록 또는 여러 기준 — 힌트 참조).",
                'refs': _span(comp), 'items': [f'첫 열 {q(first)} 의 순서를 확인하세요.'] if first else []}
    name, how = keys[0]
    return {'text': f"{q(sheet)} 시트의 데이터를 {j(q(name), '을/를')} 기준으로 {how} 정렬하시오.",
            'items': ['같은 값이 있으면 둘째 기준이 있을 수 있습니다(힌트·정답과 비교).'], 'refs': _span(comp)}


def _criteria_text(ws, r, c, h, w):
    heads = [_text(ws, r, c + k) or '' for k in range(w)]
    rows = []
    for i in range(1, h):
        parts = []
        for k in range(w):
            v = ws.cell(r + i, c + k).value
            if v in (None, ''):
                continue
            f = xlsx.formula_text(v)
            if f:
                try:
                    parts.append(Speaker(ws).cond(fx.parse(f)))
                except fx.FormulaError:
                    parts.append(f)
            else:
                parts.append(f"{j(q(heads[k]), '이/가')} {crit_words(v)}")
        if parts:
            rows.append(' 이고 '.join(parts))
    return ' 이거나 '.join(rows)


def _block_tables(ws, comp):
    r1, c1 = min(r for r, _ in comp), min(c for _, c in comp)
    r2, c2 = max(r for r, _ in comp), max(c for _, c in comp)
    return r1, c1, r2, c2


def _filter_tasks(sheet, ws_s, ws_a, comps):
    """고급 필터: 머리글이 원본 표 머리글과 같은 '조건' 덩어리 + '결과' 덩어리."""
    # 원본 표 머리글 모음
    src_heads = set()
    for row in (ws_s.iter_rows(max_row=min(ws_s.max_row, 40)) if ws_s is not None else []):
        for cell in row:
            if isinstance(cell.value, str):
                src_heads.add(cell.value.strip())
    crit, out = None, None
    for comp in comps:
        r1, c1, r2, c2 = _block_tables(ws_a, comp)
        heads = [_text(ws_a, r1, c) for c in range(c1, c2 + 1)]
        if not heads or not all(heads):
            continue
        known = sum(1 for h in heads if h in src_heads)
        if known == len(heads) and r2 - r1 <= 4 and crit is None and any(
                ws_a.cell(r, c).value not in (None, '') for r in range(r1 + 1, r2 + 1) for c in range(c1, c2 + 1)):
            crit = (r1, c1, r2 - r1 + 1, c2 - c1 + 1)
        elif known >= max(1, len(heads) - 0) and r2 - r1 >= 1:
            out = (r1, c1, heads)
    if crit is None and out is None:
        return None
    # 계산 조건(필드 이름이 아닌 머리글)도 조건으로 본다
    if crit is None:
        return None
    r, c, h, w = crit
    cond = _criteria_text(ws_a, r, c, h, w)
    text = f"{q(sheet)} 시트에서 {cond}인 데이터를 고급 필터로 추출하시오."
    items = [f"조건은 [{fx.addr(r, c)}:{fx.addr(r + h - 1, c + w - 1)}] 영역 내에 입력하시오."]
    if out:
        items.append(f"결과는 [{fx.addr(out[0], out[1])}] 셀부터 표시하시오.")
        items.append('추출할 필드: ' + ', '.join(q(x) for x in out[2]))
    return {'text': text, 'items': items, 'refs': ''}


def _autofilter_task(sheet, ws_s, ws_a):
    af = ws_a.auto_filter
    if not af.ref or (ws_s is not None and ws_s.auto_filter.ref == af.ref and not af.filterColumn):
        return None
    r1, c1, _, _ = fx.parse_range(af.ref)
    conds = []
    for fc in af.filterColumn or []:
        name = q(_text(ws_a, r1, c1 + fc.colId) or fx.col_name(c1 + fc.colId))
        if fc.customFilters is not None:
            parts = [crit_words(f"{({'greaterThanOrEqual': '>=', 'lessThanOrEqual': '<=', 'greaterThan': '>', 'lessThan': '<', 'notEqual': '<>', 'equal': ''}).get(cf.operator or 'equal', '')}{cf.val}")
                     for cf in fc.customFilters.customFilter]
            conds.append(f"{j(name, '이/가')} " + (' 이고 ' if fc.customFilters._and else ' 이거나 ').join(parts))
        elif fc.filters is not None:
            vals = [f if isinstance(f, str) else f.val for f in (fc.filters.filter or [])]
            vals += [f'{d.year}-{d.month or ""}' for d in (fc.filters.dateGroupItem or [])]
            conds.append(f"{j(name, '이/가')} {', '.join(q(v) for v in vals)}" + (' 중 하나' if len(vals) > 1 else ''))
        elif fc.top10 is not None:
            t = fc.top10
            conds.append(f"{name} {'하위' if t.top is False else '상위'} {int(t.val)}{'%' if t.percent else '개'}")
    if not conds:
        return None
    return {'text': f"{q(sheet)} 시트의 [{af.ref}] 영역에서 자동 필터를 이용하여 {' 이고 '.join(conds)}인 데이터만 표시하시오.",
            'refs': af.ref}


def _goalseek_task(sheet, ws_s, ws_a, wv_a, comp):
    if len(comp) != 1 or ws_s is None:
        return None
    r, c = comp[0]
    a, s_ = ws_a.cell(r, c).value, ws_s.cell(r, c).value
    if xlsx.formula_text(a) or not isinstance(a, (int, float)) or not isinstance(s_, (int, float)):
        return None
    me = fx.addr(r, c)
    for row in ws_a.iter_rows():
        for cell in row:
            f = xlsx.formula_text(cell.value)
            if f and re.search(r'(?<![A-Z$])\$?' + re.escape(fx.col_name(c)) + r'\$?' + str(r) + r'(?!\d)', f.upper()):
                target = wv_a.cell(cell.row, cell.column).value
                if isinstance(target, (int, float)):
                    def both(rr, cc):
                        row_lab, col_lab = label_left(ws_a, rr, cc), header_of(ws_a, rr, cc)
                        if row_lab and col_lab and row_lab != col_lab:
                            return f"{row_lab}의 {col_lab}"
                        return row_lab or col_lab
                    lab, who = both(cell.row, cell.column), both(r, c)
                    return {'text': (f"{q(sheet)} 시트에서 목표값 찾기를 이용하여 {q(lab) if lab else ''}[{cell.coordinate}]가 "
                                     f"{fx.display(round(target, 6))}이(가) 되려면 {q(who) if who else ''}[{me}]이(가) 얼마가 되어야 하는지 계산하시오."),
                            'refs': me}
    return None


def _formula_task(sheet, ws_a, comp):
    r0, c0 = comp[0]
    texts = [xlsx.formula_text(ws_a.cell(r, c).value) for r, c in comp]
    texts = [t for t in texts if t]
    funcs = [f for f in _funcs(texts) if f in fx.FUNCS or re.match(r'^FN', f, re.I)]
    title = table_title(ws_a, r0, c0)
    span = _span(comp)
    head = header_of(ws_a, r0, c0)
    if len(comp) == 1:
        lab = label_left(ws_a, r0, c0) or head
        what = f"{q(lab)}에 해당하는 값을 " if lab else ''
        text = f"{title + '에서 ' if title else ''}{what}계산하여 [{span}] 셀에 표시하시오."
    else:
        what = f"{head}[{span}]" if head else f"[{span}] 영역"
        obj = f"{head}[{span}]{'을' if _batchim(head) else '를'}" if head else f"[{span}] 영역에 알맞은 값을"
        text = f"{j(title, '을/를') + ' 이용하여 ' if title else ''}{obj} 표시하시오."
    items = []
    if funcs:
        items.append(f"▶ {', '.join(funcs)} 함수 사용")
    if any(t.startswith('{') for t in texts) or any(type(ws_a.cell(r, c).value).__name__ == 'ArrayFormula' for r, c in comp):
        items.insert(0, '배열 수식으로 작성하시오.')
    return {'text': text, 'items': items, 'refs': span, 'kind': 'formula'}


def _input_task(sheet, wv_a, comp):
    r1, c1, r2, c2 = _block_tables(wv_a, comp)
    table = [[wv_a.cell(r, c).value for c in range(c1, c2 + 1)] for r in range(r1, r2 + 1)]
    def show(v):
        if v is None:
            return ''
        if hasattr(v, 'year'):
            return v.strftime('%Y-%m-%d')
        return v if isinstance(v, str) else fx.display(xlsx.plain(v))
    table = [[show(v) for v in row] for row in table]
    return {'text': f"{q(sheet)} 시트의 [{_span(comp)}] 영역에 다음의 자료를 주어진 대로 입력하시오.", 'table': table,
            'start': fx.addr(r1, c1), 'refs': _span(comp)}


STYLE_PHRASE = {
    '글꼴': lambda v: f"글꼴 {q(v)}", '크기': lambda v: f"크기 {q(int(v) if v == int(v) else v)}",
    '굵게': lambda v: "글꼴 스타일 '굵게'" if v else "'굵게' 해제", '기울임꼴': lambda v: "글꼴 스타일 '기울임꼴'" if v else '',
    '밑줄': lambda v: {'single': "밑줄 '실선'", 'double': "밑줄 '이중 실선'"}.get(v, "밑줄 해제"),
    '글꼴 색': lambda v: f"글꼴 색 {q(color_name(v))}", '채우기': lambda v: f"채우기 색 {q(color_name(v))}" if v else '채우기 없음',
    '가로 맞춤': lambda v: {'center': "가로 '가운데 맞춤'", 'centerContinuous': "'선택 영역의 가운데로'", 'left': "가로 '왼쪽'",
                         'right': "가로 '오른쪽'", 'distributed': "가로 '균등 분할'"}.get(v, f'가로 {v}'),
    '세로 맞춤': lambda v: {'center': "세로 '가운데'", 'top': "세로 '위쪽'", 'bottom': "세로 '아래쪽'"}.get(v, f'세로 {v}'),
    '줄 바꿈': lambda v: "'자동 줄 바꿈'" if v else '', '테두리': lambda v: "'모든 테두리'(⊞)" if all(v) else "테두리",
}


def _style_tasks(sheet, ws_s, ws_a, wv_a):
    if ws_s is None:
        return []
    by_span = {}
    order = []
    changes = {}
    for row in ws_a.iter_rows():
        for cell in row:
            ka, ks = _style_key(cell), _style_key(ws_s.cell(cell.row, cell.column))
            for k in ka:
                if ka[k] != ks[k]:
                    changes.setdefault(k, set()).add((cell.row, cell.column))
    for k, cells in changes.items():
        for comp in _groups(cells):
            span = _span(comp)
            r, c = comp[0]
            v = _style_key(ws_a.cell(r, c))[k]
            if k == '표시 형식':
                cell = ws_a.cell(r, c)
                raw = wv_a.cell(r, c).value
                if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                    sample = raw
                elif hasattr(raw, 'year'):
                    sample = xlsx.plain(raw)
                elif re.search(r'[yd]', re.sub(r'"[^"]*"', '', cell.number_format.lower())):
                    sample = 45430
                else:
                    sample = 1234.5
                try:
                    shown = fx.format_value(xlsx.plain(sample), cell.number_format)
                except Exception:  # noqa: BLE001
                    shown = cell.number_format
                shown_in = (fx.format_value(sample, 'yyyy-mm-dd') if re.search(r'[yd]', re.sub(r'"[^"]*"', '', cell.number_format.lower()))
                            else fx.display(xlsx.plain(sample)))
                phrase = f"표시 형식 [표시 예: {shown_in} → {shown.strip()}]"
            else:
                phrase = STYLE_PHRASE.get(k, lambda x: f'{k} {x}')(v)
            if not phrase:
                continue
            if span not in by_span:
                by_span[span] = []
                order.append(span)
            by_span[span].append(phrase)
    merged = {str(m) for m in ws_a.merged_cells.ranges} - {str(m) for m in ws_s.merged_cells.ranges}
    for m in sorted(merged):
        if m not in by_span:
            by_span[m] = []
            order.append(m)
        center = any('가운데' in p for p in by_span[m])
        by_span[m] = [("'병합하고 가운데 맞춤'" if center else "'셀 병합'")] + [p for p in by_span[m] if "가로 '가운데" not in p]
    tasks = []
    for span in order:
        phrases = [p for p in by_span[span] if p]
        if not phrases:
            continue
        body = ', '.join(phrases[:-1] + [j(phrases[-1], '으로/로')])
        tasks.append({'text': f"[{span}] 영역은 {body} 지정하시오.", 'refs': span, 'kind': 'style'})
    rows = []
    for r, d in sorted(ws_a.row_dimensions.items()):
        hs = ws_s.row_dimensions[r].height if r in ws_s.row_dimensions else None
        if d.height and d.customHeight and (hs is None or abs(d.height - hs) > 0.5):
            if rows and rows[-1][1] == r - 1 and rows[-1][2] == d.height:
                rows[-1][1] = r
            else:
                rows.append([r, r, d.height])
    for a, b, h in rows:
        span = f'{a}행' if a == b else f'{a}~{b}행'
        tasks.append({'text': f"{span}의 높이를 {h:g}로 지정하시오.", 'refs': '', 'kind': 'style'})
    return tasks


def _cf_tasks(sheet, ws_s, ws_a):
    old = set()
    if ws_s is not None:
        for cf in ws_s.conditional_formatting:
            for rule in cf.rules:
                old.add((str(cf.sqref), tuple(rule.formula or ())))
    out = []
    for cf in ws_a.conditional_formatting:
        sq = str(cf.sqref)
        for rule in cf.rules:
            if (sq, tuple(rule.formula or ())) in old:
                continue
            first = sq.split()[0]
            r1, c1, r2, c2 = fx.parse_range(first)
            fmt = []
            d = rule.dxf
            if d is not None and d.font is not None:
                if d.font.b:
                    fmt.append("글꼴 스타일 '굵게'")
                if d.font.i:
                    fmt.append("'기울임꼴'")
                if _color(d.font.color):
                    fmt.append(f"글꼴 색 {q(color_name(_color(d.font.color)))}")
            if d is not None and d.fill is not None:
                fc = _color(d.fill.bgColor) or _color(d.fill.fgColor)
                if fc:
                    fmt.append(f"채우기 색 {q(color_name(fc))}")
            fmt_text = ', '.join(fmt) or '정답과 같은 서식'
            if rule.type == 'expression' and rule.formula:
                try:
                    cond = Speaker(ws_a, header_row=r1 - 1).cond(fx.parse('=' + rule.formula[0]))
                except fx.FormulaError:
                    cond = '=' + rule.formula[0]
                whole = c2 > c1
                text = (f"{q(sheet)} 시트의 [{sq}] 영역에 대하여 {cond}인 {'행 전체' if whole else '셀'}에 {fmt_text}을 적용하는 "
                        f"조건부 서식을 작성하시오.")
                items = ["규칙 유형은 '수식을 사용하여 서식을 지정할 셀 결정'을 사용하시오."]
                funcs = _funcs(['=' + rule.formula[0]])
                if funcs:
                    items.append(f"▶ {', '.join(funcs)} 함수 사용")
            elif rule.type == 'containsText':
                text, items = f"[{sq}] 영역에서 {q(rule.text)}{'을' if _batchim(rule.text) else '를'} 포함하는 셀에 {fmt_text}을 적용하시오.", []
            elif rule.type == 'dataBar':
                text, items = f"[{sq}] 영역에 '데이터 막대' 조건부 서식을 적용하시오.", []
            elif rule.type == 'iconSet':
                text, items = f"[{sq}] 영역에 '아이콘 집합' 조건부 서식을 적용하시오.", []
            elif rule.type == 'colorScale':
                text, items = f"[{sq}] 영역에 '색조' 조건부 서식을 적용하시오.", []
            elif rule.type == 'cellIs':
                opmap = {'greaterThan': '보다 큼', 'lessThan': '보다 작음', 'between': '사이', 'equal': '같음',
                         'greaterThanOrEqual': '크거나 같음', 'lessThanOrEqual': '작거나 같음'}
                text = (f"[{sq}] 영역에서 셀 값이 {', '.join(rule.formula or [])} {opmap.get(rule.operator, rule.operator)}인 "
                        f"셀에 {fmt_text}을 적용하시오.")
                items = []
            elif rule.type in ('top10',):
                text = f"[{sq}] 영역에서 {'하위' if rule.bottom else '상위'} {rule.rank}{'%' if rule.percent else ''} 항목에 {fmt_text}을 적용하시오."
                items = []
            else:
                text, items = f"[{sq}] 영역에 조건부 서식({rule.type})을 적용하시오.", []
            if re.search(r'\$?[A-Z]+\$?10[0-9]{5}', ' '.join(rule.formula or [])):
                continue                     # 행 번호가 엑셀 끝(1048576 근처)으로 깨진 규칙
            key = (sq.replace('$', ''), tuple(rule.formula or ()), rule.type)
            prev = next((o for o in out if o.get('_key') == key), None)
            if prev is not None:
                if '정답과 같은 서식' in prev['text'] and '정답과 같은 서식' not in text:
                    prev['text'] = text
                continue
            out.append({'text': text, 'items': items, 'refs': sq, '_key': key})
    return out


def _dv_tasks(sheet, ws_s, ws_a):
    key = lambda d: (str(d.sqref), d.type, d.formula1)  # noqa: E731
    old = {key(d) for d in ws_s.data_validations.dataValidation} if ws_s is not None else set()
    out = []
    names = {'whole': '정수', 'decimal': '소수점', 'list': '목록', 'date': '날짜', 'time': '시간', 'textLength': '텍스트 길이',
             'custom': '사용자 지정'}
    ops = {'between': '해당 범위', 'notBetween': '제외 범위', 'equal': '=', 'notEqual': '<>', 'greaterThan': '>',
           'lessThan': '<', 'greaterThanOrEqual': '>=', 'lessThanOrEqual': '<='}
    for d in ws_a.data_validations.dataValidation:
        if key(d) in old:
            continue
        cond = (f"원본 {d.formula1}" if d.type == 'list' else
                f"{ops.get(d.operator or 'between')} {d.formula1 or ''}{(' ~ ' + d.formula2) if d.formula2 else ''}")
        items = []
        if d.promptTitle or d.prompt:
            items.append(f"설명 메시지: 제목 {q(d.promptTitle or '')}, 내용 {q(d.prompt or '')}")
        if d.errorTitle or d.error:
            items.append(f"오류 메시지: 스타일 {q({'stop': '중지', 'warning': '경고', 'information': '정보'}.get(d.errorStyle or 'stop'))}, "
                         f"제목 {q(d.errorTitle or '')}, 내용 {q(d.error or '')}")
        out.append({'text': f"[{d.sqref}] 영역에 제한 대상 {q(names.get(d.type, d.type))}, {cond}인 데이터 유효성 검사를 설정하시오.",
                    'items': items, 'refs': str(d.sqref)})
    return out


def _pivot_tasks(sheet, ans):
    out = []
    for p in pivots.describe(ans.f):
        if p['sheet'] != sheet:
            continue
        src = p['source'] or (None, None, None)
        where = f"{q(src[0])} 시트의 [{src[1]}] 영역" if src[0] and src[1] else (f"{q(src[2])}" if src[2] else '원본 데이터')
        vals = ', '.join(f"{q(f)}의 {FUNC_KO.get(fn, fn)}" for f, fn in p['values'])
        items = [f"피벗 테이블 보고서는 {q(sheet)} 시트의 [{p['ref'].split(':')[0]}] 셀에서 시작하시오."]
        place = []
        if p['filters']:
            place.append(f"필터에 {', '.join(q(x) for x in p['filters'])}")
        if p['rows']:
            place.append(f"행에 {', '.join(q(x) for x in p['rows'])}")
        if p['cols']:
            place.append(f"열에 {', '.join(q(x) for x in p['cols'])}")
        place.append(f"값에 {vals}")
        items.append(', '.join(place) + '를 배치하시오.')
        if p['grouped']:
            items.append(f"{', '.join(q(g) for g in p['grouped'])} 필드는 그룹을 지정하시오(단위는 정답 참조).")
        if p['layout'] != 'compact':
            items.append(f"보고서 레이아웃은 {j(q({'outline': '개요 형식으로 표시', 'tabular': '테이블 형식으로 표시'}[p['layout']]), '으로/로')} 지정하시오.")
        if not p['grand_rows'] or not p['grand_cols']:
            items.append("총합계는 " + ('행·열 모두' if not p['grand_rows'] and not p['grand_cols'] else ('행' if not p['grand_rows'] else '열'))
                         + ' 총합계를 표시하지 않도록 지정하시오.')
        out.append({'text': f"{where}을 이용하여 피벗 테이블 보고서를 작성하시오.", 'items': items, 'refs': p['ref']})
    return out


def _chart_tasks(sheet, src, ans):
    try:
        a = _charts_of(ans, sheet)
    except KeyError:
        return []
    if not a:
        return []
    s = _charts_of(src, sheet) if src.ws(sheet) is not None else []
    out = []
    for i, ca in enumerate(a):
        cs = s[i] if i < len(s) else None
        items = []
        if cs is None:
            items.append('차트를 새로 작성하시오(정답 그림 참고).')
        names_a = [x['name'] for x in ca['series']]
        if cs is not None and names_a != [x['name'] for x in cs['series']]:
            add = [n for n in names_a if n not in [x['name'] for x in cs['series']]]
            rem = [x['name'] for x in cs['series'] if x['name'] not in names_a]
            if add:
                items.append(f"{', '.join(q(n) for n in add)} 계열을 추가하시오.")
            if rem:
                items.append(f"{', '.join(q(n) for n in rem)} 계열을 삭제하시오.")
        kinds = {x['name']: x['kind'] for x in ca['series']}
        kinds_s = {x['name']: x['kind'] for x in cs['series']} if cs else {}
        if cs and cs['series']:                         # 새로 넣은 계열은 원래 차트의 주 종류와 비교
            main = max(set(kinds_s.values()), key=list(kinds_s.values()).count)
            kinds_s = {n: kinds_s.get(n, main) for n in kinds}
        kname = {'col': '세로 막대형', 'bar': '가로 막대형', 'line': '꺾은선형', 'pie': '원형', 'area': '영역형',
                 'scatter': '분산형', 'doughnut': '도넛형', 'radar': '방사형'}
        for n, k in kinds.items():
            if kinds_s.get(n, k if cs else None) != k:
                items.append(f"{q(n)} 계열의 차트 종류를 {j(q(kname.get(k, k)), '으로/로')} 변경하시오.")
        for n in [x['name'] for x in ca['series'] if x['secondary']]:
            if not (cs and any(x['name'] == n and x['secondary'] for x in cs['series'])):
                items.append(f"{q(n)} 계열은 보조 축으로 지정하시오.")
        for n in [x['name'] for x in ca['series'] if x['labels']]:
            if not (cs and any(x['name'] == n and x['labels'] for x in cs['series'])):
                items.append(f"{q(n)} 계열에 데이터 레이블을 표시하시오.")
        for n in [x['name'] for x in ca['series'] if x['trend']]:
            if not (cs and any(x['name'] == n and x['trend'] for x in cs['series'])):
                items.append(f"{q(n)} 계열에 추세선을 추가하시오.")
        if ca['title'] and (not cs or _norm_text(cs['title']) != _norm_text(ca['title'])):
            items.append(f"차트 제목은 {j(q(ca['title']), '으로/로')} 입력하시오.")
        if ca['y_title'] and (not cs or cs['y_title'] != ca['y_title']):
            items.append(f"세로 (값) 축 제목은 {j(q(ca['y_title']), '으로/로')} 입력하시오.")
        if ca['x_title'] and (not cs or cs['x_title'] != ca['x_title']):
            items.append(f"가로 (항목) 축 제목은 {j(q(ca['x_title']), '으로/로')} 입력하시오.")
        if cs is None or cs['legend'] != ca['legend']:
            pos = {'b': '아래쪽', 't': '위쪽', 'r': '오른쪽', 'l': '왼쪽', None: '없음'}
            items.append(f"범례는 {q(pos.get(ca['legend'], ca['legend']))}에 표시하시오." if ca['legend'] else '범례를 표시하지 마시오.')
        if ca['grouping'] in ('stacked', 'percentStacked') and (not cs or cs['grouping'] != ca['grouping']):
            items.append(f"차트 종류를 {q('누적' if ca['grouping'] == 'stacked' else '100% 기준 누적')} 형식으로 변경하시오.")
        if items:
            out.append({'text': f"{q(sheet)} 시트의 차트를 다음 지시사항에 따라 수정하시오.", 'items': items, 'refs': ''})
    return out


def _misc_tasks(sheet, ws_s, ws_a):
    out = []
    if ws_s is None:
        return out
    ps, pa = ws_s.page_setup, ws_a.page_setup
    items = []
    if (pa.orientation or 'portrait') != (ps.orientation or 'portrait'):
        items.append(f"용지 방향을 {q('가로' if pa.orientation == 'landscape' else '세로')}로 지정하시오.")
    if ws_a.print_options.horizontalCentered and not ws_s.print_options.horizontalCentered:
        items.append("페이지 가운데 맞춤을 '가로'로 지정하시오.")
    if ws_a.print_options.verticalCentered and not ws_s.print_options.verticalCentered:
        items.append("페이지 가운데 맞춤을 '세로'로 지정하시오.")
    if ws_a.print_area and ws_a.print_area != ws_s.print_area:
        items.append(f"인쇄 영역을 [{ws_a.print_area.split('!')[-1].replace('$', '')}]로 지정하시오.")
    if ws_a.print_title_rows and ws_a.print_title_rows != ws_s.print_title_rows:
        items.append(f"{ws_a.print_title_rows.replace('$', '')}행이 매 페이지마다 반복 인쇄되도록 지정하시오.")
    for part, label in ((ws_a.oddHeader, '머리글'), (ws_a.oddFooter, '바닥글')):
        for pos, kname in (('left', '왼쪽'), ('center', '가운데'), ('right', '오른쪽')):
            t = getattr(part, pos).text
            old = getattr(getattr(ws_s, 'oddHeader' if label == '머리글' else 'oddFooter'), pos).text
            if t and t != old:
                items.append(f"{label} {kname} 구역에 {q(t)}(이)가 표시되도록 지정하시오.")
    if ws_a.sheet_view.view and ws_a.sheet_view.view != ws_s.sheet_view.view:
        items.append(f"시트 보기를 {q({'pageBreakPreview': '페이지 나누기 미리 보기', 'pageLayout': '페이지 레이아웃'}.get(ws_a.sheet_view.view, ws_a.sheet_view.view))}로 지정하시오.")
    if items:
        out.append({'text': f"{q(sheet)} 시트에 대하여 다음과 같이 페이지 설정·보기를 지정하시오.", 'items': items, 'refs': ''})
    if ws_a.protection.sheet and not ws_s.protection.sheet:
        unlocked = [c.coordinate for row in ws_a.iter_rows() for c in row if not c.protection.locked]
        hidden = [c.coordinate for row in ws_a.iter_rows() for c in row if c.protection.hidden]
        items = []
        if unlocked:
            items.append(f"[{_span([fx.parse_addr(a) for a in unlocked])}] 영역은 셀 잠금을 해제하시오.")
        if hidden:
            items.append(f"[{_span([fx.parse_addr(a) for a in hidden])}] 영역은 수식을 숨기시오.")
        out.append({'text': f"{q(sheet)} 시트를 보호하시오(암호는 지정하지 않음).", 'items': items, 'refs': ''})
    for row in ws_a.iter_rows():
        for cell in row:
            if cell.comment and ws_s.cell(cell.row, cell.column).comment is None:
                body = _norm_text(cell.comment.text.split(':\n', 1)[-1])
                out.append({'text': f"[{cell.coordinate}] 셀에 {q(body)}라는 메모를 삽입하시오.", 'refs': cell.coordinate})
    sc_a = ws_a.scenarios.scenario if ws_a.scenarios else []
    sc_s = {s.name for s in (ws_s.scenarios.scenario if ws_s.scenarios else [])}
    new = [s for s in sc_a if s.name not in sc_s]
    if new:
        items = [f"시나리오 {q(s.name)}: " + ', '.join(f"[{ic.r}]={ic.val}" for ic in s.inputCells) for s in new]
        out.append({'text': f"{q(sheet)} 시트에서 시나리오 관리자를 이용하여 다음 시나리오를 추가하고 '시나리오 요약' 보고서를 작성하시오.",
                    'items': items, 'refs': ''})
    return out


def _datatable_tasks(sheet, ws_a):
    out = []
    for row in ws_a.iter_rows():
        for cell in row:
            v = cell.value
            if type(v).__name__ == 'DataTableFormula':
                two = str(v.dt2D) in ('1', 'True')
                ins = (f"행 입력 셀 [{v.r1}], 열 입력 셀 [{v.r2}]" if two else
                       (f"{'행' if str(v.dtr) in ('1', 'True') else '열'} 입력 셀 [{v.r1}]"))
                out.append({'text': f"{q(sheet)} 시트에서 데이터 표 기능을 이용하여 [{v.ref}] 영역의 값을 계산하시오.",
                            'items': [ins.replace('$', '') + '를 이용하시오.'], 'refs': v.ref})
    return out


def _inside(ref, cells):
    if not ref or not cells or not re.match(r'^[A-Z]+\d+(:[A-Z]+\d+)?$', ref):
        return False
    r1, c1, r2, c2 = fx.parse_range(ref)
    return all((r, c) in cells for r in range(r1, r2 + 1) for c in range(c1, c2 + 1))


def describe(src_bytes, ans_bytes):
    """→ [{sheet, tasks: [{text, items, table, refs}]}]"""
    src, ans = Wb(src_bytes), Wb(ans_bytes)
    out = []
    names_a = dict(xlsx._defined_names(ans.f))
    names_s = dict(xlsx._defined_names(src.f))
    for sheet in ans.f.sheetnames:
        if sheet not in src.f.sheetnames:
            continue
        ws_s, ws_a, wv_a = src.ws(sheet), ans.ws(sheet), ans.wv(sheet)
        tasks = []
        skip = set()
        for p in pivots.describe(ans.f):
            if p['sheet'] == sheet:
                r1, c1, r2, c2 = fx.parse_range(p['ref'])
                r1 -= len(p['filters']) + 1 if p['filters'] else 0
                skip |= {(r, c) for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)}
        comps = [cp for cp in _cells(ws_s, ws_a, wv_a) if not (set(cp) & skip)]
        flt = _filter_tasks(sheet, ws_s, ws_a, comps)
        if flt:
            tasks.append(flt)
        dt_cells = {(c.row, c.column) for row in ws_a.iter_rows() for c in row
                    if type(c.value).__name__ == 'DataTableFormula'}
        subtotal_done = False
        for comp in comps:
            if any(p in dt_cells for p in comp):
                continue
            if not subtotal_done:
                st = _subtotal_task(sheet, ws_a, comp)
                if st:
                    tasks.append(st)
                    subtotal_done = True
                    continue
            if flt or subtotal_done:
                if not any('SUBTOTAL' not in (xlsx.formula_text(ws_a.cell(r, c).value) or 'SUBTOTAL') for r, c in comp):
                    continue
                if subtotal_done:
                    continue
            if any('SUBTOTAL' in (xlsx.formula_text(ws_a.cell(r, c).value) or '').upper() for r, c in comp):
                continue
            nform = sum(1 for r, c in comp if xlsx.formula_text(ws_a.cell(r, c).value))
            if nform * 2 >= len(comp):
                tasks.append(_formula_task(sheet, ws_a, comp))
                continue
            srt = _sort_task(sheet, ws_s, ws_a, wv_a, comp)
            if srt:
                tasks.append(srt)
            elif _is_empty_in(ws_s, comp):
                tasks.append(_input_task(sheet, wv_a, comp))
            elif (gs := _goalseek_task(sheet, ws_s, ws_a, wv_a, comp)):
                tasks.append(gs)
            elif len(comp) <= 3 and all(isinstance(wv_a.cell(r, c).value, str) for r, c in comp):
                for r, c in comp:
                    tasks.append({'text': f"[{fx.addr(r, c)}] 셀의 내용을 {q(wv_a.cell(r, c).value)}로 수정하시오.",
                                  'refs': fx.addr(r, c)})
            else:
                tasks.append({'text': f"[{_span(comp)}] 영역의 값을 정답 결과와 같게 만드시오(목표값 찾기·통합·텍스트 나누기 등 — 힌트 참조).",
                              'refs': _span(comp)})
        tasks += _datatable_tasks(sheet, ws_a)
        af = _autofilter_task(sheet, ws_s, ws_a)
        if af:
            tasks.append(af)
        if not subtotal_done:                       # 부분합이 만든 굵게·윤곽 서식은 빼고
            tasks += [t for t in _style_tasks(sheet, ws_s, ws_a, wv_a) if not _inside(t['refs'], skip)]
        tasks += _cf_tasks(sheet, ws_s, ws_a)
        tasks += _dv_tasks(sheet, ws_s, ws_a)
        tasks += _pivot_tasks(sheet, ans)
        tasks += _chart_tasks(sheet, src, ans)
        tasks += _misc_tasks(sheet, ws_s, ws_a)
        out.append({'sheet': sheet, 'tasks': tasks})
    # 통합 문서: 이름 정의, 매크로·VBA
    book = []
    for n, ref in names_a.items():
        if names_s.get(n) != ref:
            book.append({'text': f"[{ref.lstrip('=').split('!')[-1].replace('$', '')}] 영역의 이름을 {q(n)}로 정의하시오.", 'refs': ''})
    from . import vba
    procs_a, procs_s = ans.procs, src.procs
    btn_a = set(vba.buttons(ans_bytes)) - set(vba.buttons(src_bytes))
    for name, body in procs_a.items():
        if name in procs_s:
            continue
        is_func = body.lstrip().lower().startswith(('public function', 'function'))
        btn = [t for t, m in btn_a if m.lower() == name]
        if is_func:
            sig = re.search(r'function\s+([^\s(]+)\s*\(([^)]*)\)', body, re.I)
            text = f"사용자 정의 함수 {q(sig.group(1) if sig else name)}를 작성하시오" + (f"(인수: {sig.group(2)})" if sig and sig.group(2) else '') + '.'
        elif name.endswith(('_click', '_initialize', '_change', '_activate')):
            text = f"{q(name)} 이벤트 프로시저를 작성하시오."
        else:
            text = f"{q(name)} 매크로를 작성하여 실행하시오."
            if btn:
                text += f" {q(btn[0])} 단추(또는 도형)에 {q(name)} 매크로를 지정하시오."
        book.append({'text': text, 'refs': '', 'code': True})
    if book:
        out.append({'sheet': '통합 문서(이름·매크로·VBA)', 'tasks': book})
    return [s for s in out if s['tasks']]
