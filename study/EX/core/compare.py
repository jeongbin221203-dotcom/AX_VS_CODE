"""두 파일 비교 채점: '문제(실습)' 파일과 '정답(완성)' 파일의 차이 = 해야 할 일.
그 차이를 시트별 항목으로 나누고, 수험자 파일이 정답과 같아졌는지 본다(공식 예제·교재 실습 파일 공용).
"""
import datetime as dt
import re

from . import formula as fx
from . import pivots, vba, xlsx
from .exam import _cell_mask, _color, _fmt_sample, _norm_text, describe_charts

# 실제 시험 시트 이름일 때의 배점(공식 문제지 기준). 그 밖의 파일은 시트마다 같은 배점으로 100점.
SHEET_POINTS = {
    'c2': {'기본작업-1': 5, '기본작업-2': 10, '기본작업-3': 5, '계산작업': 40, '분석작업-1': 10, '분석작업-2': 10,
           '매크로작업': 10, '차트작업': 10, '기타작업-1': 10, '기타작업-2': 10},
    'c1': {'기본작업-1': 10, '기본작업-2': 5, '계산작업': 30, '분석작업-1': 10, '분석작업-2': 10, '기타작업-1': 10,
           '기타작업-2': 10, '기타작업-3': 15},
}


def section_of(sheet):
    for s in ('기본작업', '계산작업', '분석작업'):
        if sheet.startswith(s):
            return s
    return '기타작업'


def _key(name):
    return name.replace('-', '').replace(' ', '')


def exam_level(sheetnames):
    """시트 이름이 실제 시험 구성과 같으면 'c2' / 'c1', 아니면 None."""
    names = {_key(n) for n in sheetnames}
    if not {'계산작업', '분석작업1'} <= names:
        return None
    if '기본작업3' in names:            # 2급: 기본작업 시트 3개
        return 'c2'
    if '기타작업3' in names or '기타작업1' in names:
        return 'c1'
    return None


def _groups(cells):
    """좌표 집합 → 상하좌우로 이어진 덩어리들(각각 정렬된 좌표 목록)."""
    left = set(cells)
    out = []
    while left:
        start = left.pop()
        stack, comp = [start], [start]
        while stack:
            r, c = stack.pop()
            for nb in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
                if nb in left:
                    left.remove(nb)
                    stack.append(nb)
                    comp.append(nb)
        out.append(sorted(comp))
    out.sort()
    return out


def _span(comp):
    rs = [r for r, _ in comp]
    cs = [c for _, c in comp]
    a, b = fx.addr(min(rs), min(cs)), fx.addr(max(rs), max(cs))
    return a if a == b else f'{a}:{b}'


def _raw(ws, r, c):
    return ws.cell(r, c).value if ws is not None else None


def _style_key(cell):
    f, a, b = cell.font, cell.alignment, cell.border
    fill = _color(cell.fill.fgColor) if cell.fill is not None and cell.fill.patternType == 'solid' else None
    return {'글꼴': f.name, '크기': float(f.sz or 11), '굵게': bool(f.b), '기울임꼴': bool(f.i), '밑줄': f.u or None,
            '글꼴 색': _color(f.color), '채우기': fill, '가로 맞춤': a.horizontal or 'general',
            '세로 맞춤': a.vertical or 'bottom', '줄 바꿈': bool(a.wrap_text),
            '표시 형식': tuple(_fmt_sample(cell.number_format)),
            '테두리': tuple(bool(getattr(b, s).style) for s in ('left', 'right', 'top', 'bottom'))}


def _show_style(k, v):
    if k == '표시 형식':
        return '' if not v else f'(예: {v[0]})'
    if k == '테두리':
        return '있음' if any(v) else '없음'
    if isinstance(v, bool):
        return '예' if v else '아니요'
    return '' if v is None else str(v)


def _same_val(got, exp):
    if isinstance(exp, str) or isinstance(got, str):
        return _norm_text(got) == _norm_text(exp)
    if exp is None:
        return got in (None, '')
    if got is None:
        return False
    if isinstance(exp, dt.datetime) or isinstance(got, dt.datetime) or isinstance(exp, dt.date):
        return fx.same_value(xlsx.plain(got), xlsx.plain(exp), 1e-6)
    return fx.same_value(got, exp, 1e-6)


class Wb:
    def __init__(self, data):
        self.data = data
        self.f = xlsx.load(data, data_only=False)
        self.v = xlsx.load(data, data_only=True)
        self.book = xlsx.to_book(self.f, self.v)
        self._vba = None

    def ws(self, name):
        return self.f[name] if name in self.f.sheetnames else None

    def wv(self, name):
        return self.v[name] if name in self.v.sheetnames else None

    def value(self, sheet, r, c):
        try:
            return self.book.sheet(sheet).get(r, c)
        except fx.XLErr:
            return None

    @property
    def raw_charts(self):
        if not hasattr(self, '_raw_charts'):
            self._raw_charts = xlsx.raw_charts(self.data)
        return self._raw_charts

    @property
    def procs(self):
        if self._vba is None:
            self._vba = vba.procedures(vba.sources(self.data))
        return self._vba


def _cells_items(sheet, src, ans, user):
    """값·수식이 바뀐 칸 → 덩어리마다 한 항목."""
    ws_s, ws_a = src.ws(sheet), ans.ws(sheet)
    ws_u = user.ws(sheet)
    changed = set()
    for row in ws_a.iter_rows():
        for cell in row:
            if cell.value != _raw(ws_s, cell.row, cell.column):
                changed.add((cell.row, cell.column))
    if ws_s is not None:
        for row in ws_s.iter_rows():
            for cell in row:
                if cell.value is not None and ws_a.cell(cell.row, cell.column).value is None:
                    changed.add((cell.row, cell.column))
    items = []
    wv_a = ans.wv(sheet)
    for comp in _groups(changed):
        bad = []
        formulas = sum(1 for r, c in comp if xlsx.formula_text(ws_a.cell(r, c).value) is not None
                       or type(ws_a.cell(r, c).value).__name__ == 'DataTableFormula')
        for r, c in comp:
            a_raw = ws_a.cell(r, c).value
            exp = wv_a.cell(r, c).value
            a_formula = xlsx.formula_text(a_raw) is not None
            dt_table = type(a_raw).__name__ == 'DataTableFormula'
            if ws_u is None:
                bad.append('시트 없음')
                break
            u_raw = ws_u.cell(r, c).value
            got = user.value(ws_u.title, r, c)
            if got is None and u_raw is not None and xlsx.formula_text(u_raw) is None:
                got = xlsx.plain(u_raw)
            if dt_table and type(u_raw).__name__ != 'DataTableFormula':
                bad.append(f'{fx.addr(r, c)}: 데이터 표가 아닙니다')
            elif a_formula and xlsx.formula_text(u_raw) is None and got not in (None, ''):
                bad.append(f'{fx.addr(r, c)}: 수식이 아니라 값입니다')
            elif not _same_val(got, xlsx.plain(exp) if exp is not None else None):
                bad.append(f'{fx.addr(r, c)}: {fx.display(got) if got not in (None, "") else "(빈칸)"} → '
                           f'{fx.display(xlsx.plain(exp)) if exp not in (None, "") else "(빈칸)"}')
            if len(bad) >= 3:
                break
        label = ('수식 결과 ' if formulas * 2 >= len(comp) else '셀 내용 ') + _span(comp)
        r0, c0 = comp[0]
        a0 = ws_a.cell(r0, c0).value
        hint = (f'{fx.addr(r0, c0)} 정답 수식: {xlsx.formula_text(a0)}' if xlsx.formula_text(a0) else
                f"{fx.addr(r0, c0)} 정답 값: {fx.display(xlsx.plain(wv_a.cell(r0, c0).value)) if wv_a.cell(r0, c0).value is not None else '(빈칸)'}")
        items.append({'label': label, 'ok': not bad, 'msgs': bad, 'hint': hint})
    return items


def _style_items(sheet, src, ans, user):
    ws_s, ws_a, ws_u = src.ws(sheet), ans.ws(sheet), user.ws(sheet)
    if ws_s is None:
        return []
    changes = {}
    for row in ws_a.iter_rows():
        for cell in row:
            ka = _style_key(cell)
            ks = _style_key(ws_s.cell(cell.row, cell.column))
            diff = tuple(sorted(k for k in ka if ka[k] != ks[k]))
            if diff:
                changes[(cell.row, cell.column)] = diff
    items = []
    by_attr = {}
    for pos, diff in changes.items():
        for k in diff:
            by_attr.setdefault(k, set()).add(pos)
    for attr in sorted(by_attr, key=lambda k: min(by_attr[k])):
        for comp in _groups(by_attr[attr]):
            bad = []
            for r, c in comp:
                if ws_u is None:
                    bad.append('시트 없음')
                    break
                if _style_key(ws_u.cell(r, c))[attr] != _style_key(ws_a.cell(r, c))[attr]:
                    bad.append(fx.addr(r, c))
            want = _show_style(attr, _style_key(ws_a.cell(*comp[0]))[attr])
            items.append({'label': f'{attr} {_span(comp)}', 'ok': not bad, 'hint': f'{attr}: {want}'.rstrip(': '),
                          'msgs': [f"{', '.join(bad[:4])}{' 등' if len(bad) > 4 else ''} 이(가) 다릅니다"] if bad else []})
    # 병합
    m_src = {str(m) for m in ws_s.merged_cells.ranges}
    m_usr = {str(m) for m in ws_u.merged_cells.ranges} if ws_u is not None else set()
    for m in sorted({str(m) for m in ws_a.merged_cells.ranges} - m_src):
        items.append({'label': f'셀 병합 {m}', 'ok': m in m_usr, 'msgs': [] if m in m_usr else [f'{m} 병합 필요'],
                      'hint': f'{m} 을 병합'})
    # 행 높이
    for r, d in ws_a.row_dimensions.items():
        hs = ws_s.row_dimensions[r].height if r in ws_s.row_dimensions else None
        if d.height and d.customHeight and (hs is None or abs(d.height - hs) > 0.5):   # 직접 지정한 높이만
            hu = ws_u.row_dimensions[r].height if ws_u is not None and r in ws_u.row_dimensions else None
            ok = hu is not None and abs(hu - d.height) <= 0.5
            items.append({'label': f'{r}행 높이 {d.height:g}', 'ok': ok, 'msgs': [] if ok else [f'현재 {hu or "기본"}'],
                          'hint': f'{r}행 높이를 {d.height:g} 으로'})
    return items


def _rules(ws):
    out = []
    for cf in ws.conditional_formatting:
        for rule in cf.rules:
            out.append((str(cf.sqref).replace('$', '').upper(), rule))
    return out


def _rule_key(sq, rule):
    return sq, rule.type, tuple(rule.formula or ())


def _cf_items(sheet, src, ans, user):
    ws_s, ws_a, ws_u = src.ws(sheet), ans.ws(sheet), user.ws(sheet)
    old = {_rule_key(*x) for x in _rules(ws_s)} if ws_s is not None else set()
    items = []
    for sq, rule in _rules(ws_a):
        if _rule_key(sq, rule) in old:
            continue
        label = f'조건부 서식 {sq}'
        hint = f'규칙: ={rule.formula[0]}' if rule.formula else f'규칙 종류: {rule.type}'
        cands = [r for s, r in (_rules(ws_u) if ws_u is not None else []) if s == sq]
        if not cands:
            items.append({'label': label, 'ok': False, 'msgs': [f'{sq} 범위의 규칙이 없습니다'], 'hint': hint})
            continue
        ok, msg = False, '규칙 내용이 다릅니다'
        for r in cands:
            if r.type != rule.type:
                msg = '규칙 종류가 다릅니다'
                continue
            if rule.type == 'expression' and rule.formula:
                try:
                    want = _cell_mask(_Ctx(ans), sheet, sq.split()[0], '=' + rule.formula[0], ans.book)
                    got = _cell_mask(_Ctx(user), sheet, sq.split()[0], '=' + (r.formula or ['FALSE'])[0], user.book)
                except fx.FormulaError:
                    continue
                if want != got:
                    msg = f'규칙 수식 ={(r.formula or [""])[0]} 의 결과가 다릅니다'
                    continue
                da, du = rule.dxf, r.dxf
                fa = (_color(da.font.color) if da and da.font else None, bool(da and da.font and da.font.b),
                      bool(da and da.font and da.font.i))
                fu = (_color(du.font.color) if du and du.font else None, bool(du and du.font and du.font.b),
                      bool(du and du.font and du.font.i))
                fill_a = (_color(da.fill.bgColor) or _color(da.fill.fgColor)) if da and da.fill else None
                fill_u = (_color(du.fill.bgColor) or _color(du.fill.fgColor)) if du and du.fill else None
                if fa != fu or fill_a != fill_u:
                    msg = '서식(글꼴 색·스타일·채우기)이 다릅니다'
                    continue
            ok = True
            break
        items.append({'label': label, 'ok': ok, 'msgs': [] if ok else [msg], 'hint': hint})
    return items


class _Ctx:
    def __init__(self, wb):
        self.orig = wb.book


def _dv_items(sheet, src, ans, user):
    ws_s, ws_a, ws_u = src.ws(sheet), ans.ws(sheet), user.ws(sheet)
    key = lambda d: (str(d.sqref).upper(), d.type, (d.formula1 or '').replace('$', ''), d.operator)  # noqa: E731
    old = {key(d) for d in ws_s.data_validations.dataValidation} if ws_s is not None else set()
    have = {key(d): d for d in ws_u.data_validations.dataValidation} if ws_u is not None else {}
    items = []
    for d in ws_a.data_validations.dataValidation:
        k = key(d)
        if k in old:
            continue
        u = have.get(k)
        bad = []
        if u is None:
            bad.append('유효성 검사 범위·조건이 다릅니다')
        else:
            for attr, name in (('errorTitle', '오류 제목'), ('error', '오류 메시지'), ('promptTitle', '설명 제목'),
                               ('prompt', '설명 메시지'), ('errorStyle', '스타일')):
                if _norm_text(getattr(d, attr)) != _norm_text(getattr(u, attr)):
                    bad.append(name)
        items.append({'label': f'데이터 유효성 {k[0]}', 'ok': not bad, 'msgs': bad,
                      'hint': f"제한 대상 {d.type}, 조건 {d.operator or 'between'} {d.formula1 or ''} {d.formula2 or ''}".strip()})
    return items


def _chart_items(sheet, src, ans, user):
    try:
        a = _charts_of(ans, sheet)
    except KeyError:
        return []
    if not a:
        return []
    s = _charts_of(src, sheet) if src.ws(sheet) is not None else []
    u = _charts_of(user, sheet) if user.ws(sheet) is not None else []
    items = []
    for i, ca in enumerate(a):
        cs = s[i] if i < len(s) else None
        cu = u[i] if i < len(u) else None

        def add(label, getter):
            want = getter(ca)
            if cs is not None and getter(cs) == want:
                return
            got = getter(cu) if cu is not None else None
            items.append({'label': f'차트: {label}', 'ok': got == want, 'hint': f'정답: {want}',
                          'msgs': [] if got == want else [f'{got} → {want}' if got is not None else '차트가 없습니다']})
        add('데이터 계열', lambda c: [x['name'] for x in c['series']])
        add('차트 종류', lambda c: sorted({(x['name'], x['kind']) for x in c['series']}))
        add('보조 축', lambda c: sorted(x['name'] for x in c['series'] if x['secondary']))
        add('데이터 레이블', lambda c: sorted(x['name'] for x in c['series'] if x['labels']))
        add('추세선', lambda c: sorted(x['name'] for x in c['series'] if x['trend']))
        add('차트 제목', lambda c: _norm_text(c['title']))
        add('세로 축 제목', lambda c: _norm_text(c['y_title']))
        add('가로 축 제목', lambda c: _norm_text(c['x_title']))
        add('범례', lambda c: c['legend'])
        add('누적 여부', lambda c: c['grouping'])
    return items


def _charts_of(wb, sheet):
    """openpyxl 이 읽은 차트가 파일 속 차트보다 적으면(그룹 안 차트 등) XML 에서 직접 읽은 것으로."""
    ws = wb.ws(sheet)
    if ws is None:
        raise KeyError(sheet)
    raw = wb.raw_charts.get(sheet, [])
    charts = raw if len(raw) > len(ws._charts) else None
    return describe_charts(_Ctx2(wb), sheet, charts)


class _Ctx2:
    def __init__(self, wb):
        self.wb = wb
        self.book = wb.book

    def ws(self, name):
        ws = self.wb.ws(name)
        if ws is None:
            raise KeyError(name)
        return ws


def _pivot_items(sheet, src, ans, user):
    ws_a = ans.ws(sheet)
    if not getattr(ws_a, '_pivots', None):
        return []
    want = [p for p in pivots.describe(ans.f) if p['sheet'] == sheet]
    got = pivots.describe(user.f)
    items = []
    for p in want:
        r1, c1, _, _ = fx.parse_range(p['ref'])
        spec = {'rows': p['rows'], 'cols': p['cols'], 'filters': p['filters'], 'values': p['values'],
                'sheet': sheet, 'layout': p['layout']}
        best = None
        for g in got:
            ok, why = pivots.match(spec, g)
            if ok and fx.parse_range(g['ref'])[:2] == (r1, c1):
                best = (True, [])
                break
            if best is None or len(why) < len(best[1]):
                best = (False, why or [f"위치 {g['ref']} → {p['ref']}"])
        best = best or (False, ['피벗 테이블이 없습니다'])
        hint = (f"행: {', '.join(p['rows']) or '-'} · 열: {', '.join(p['cols']) or '-'} · 필터: {', '.join(p['filters']) or '-'} · 값: "
                + ', '.join(f"{f} {pivots.FUNC_NAMES.get(fn, fn)}" for f, fn in p['values']) + f" · 위치 {sheet}!{p['ref'].split(':')[0]}")
        items.append({'label': f"피벗 테이블 {p['ref']}", 'ok': best[0], 'msgs': best[1][:3], 'hint': hint})
    return items


def _misc_items(sheet, src, ans, user):
    ws_s, ws_a, ws_u = src.ws(sheet), ans.ws(sheet), user.ws(sheet)
    items = []
    if ws_s is None:
        return items

    def page(ws):
        if ws is None:
            return {}
        return {'용지 방향': ws.page_setup.orientation, '가로 가운데': bool(ws.print_options.horizontalCentered),
                '세로 가운데': bool(ws.print_options.verticalCentered),
                '인쇄 영역': (ws.print_area or '').split('!')[-1].replace('$', ''),
                '반복할 행': (ws.print_title_rows or '').replace('$', ''),
                '머리글': _norm_text(ws.oddHeader.center.text) + '|' + _norm_text(ws.oddHeader.right.text),
                '바닥글': _norm_text(ws.oddFooter.center.text) + '|' + _norm_text(ws.oddFooter.right.text),
                '시트 보호': bool(ws.protection.sheet),
                '보기': ws.sheet_view.view or 'normal'}
    ps, pa, pu = page(ws_s), page(ws_a), page(ws_u)
    for k in pa:
        if pa[k] != ps.get(k):
            items.append({'label': k, 'ok': pu.get(k) == pa[k], 'hint': f'정답: {pa[k]}',
                          'msgs': [] if pu.get(k) == pa[k] else [f"{pu.get(k)} → {pa[k]}"]})
    # 자동 필터·현재 위치 고급 필터: 숨겨진 행
    hid = lambda ws: {r for r, d in ws.row_dimensions.items() if d.hidden} if ws is not None else set()  # noqa: E731
    if hid(ws_a) != hid(ws_s) or (ws_a.auto_filter.ref and ws_a.auto_filter.ref != ws_s.auto_filter.ref):
        want, got = hid(ws_a), hid(ws_u)
        ok = want == got and (not ws_a.auto_filter.ref or bool(ws_u is not None and ws_u.auto_filter.ref))
        msg = []
        if not ok:
            msg = [f'숨겨진 행 {len(got)}개 → {len(want)}개' if want != got else '자동 필터가 설정되어 있지 않습니다']
        items.append({'label': '필터 결과(보이는 행)', 'ok': ok, 'msgs': msg,
                      'hint': f"자동 필터 범위 {ws_a.auto_filter.ref or '-'}, 숨겨진 행 {len(want)}개"})
    # 잠금·숨김
    if pa['시트 보호'] and not ps['시트 보호']:
        bad, changed = [], 0
        for row in ws_a.iter_rows():
            for cell in row:
                p = cell.protection
                o = ws_s.cell(cell.row, cell.column).protection
                if (p.locked, p.hidden) == (o.locked, o.hidden):
                    continue                     # 정답에서 바꾼 칸만 본다
                changed += 1
                q = ws_u.cell(cell.row, cell.column).protection if ws_u is not None else None
                if q is None or (p.locked, p.hidden) != (q.locked, q.hidden):
                    bad.append(cell.coordinate)
        if changed:
            items.append({'label': '셀 잠금·숨김', 'ok': not bad, 'msgs': [f"{', '.join(bad[:4])} 설정 다름"] if bad else []})
    # 메모
    for row in ws_a.iter_rows():
        for cell in row:
            if cell.comment and (ws_s.cell(cell.row, cell.column).comment is None):
                cu = ws_u.cell(cell.row, cell.column).comment if ws_u is not None else None
                want = _norm_text(cell.comment.text.split(':\n', 1)[-1])
                ok = cu is not None and want in _norm_text(cu.text)
                items.append({'label': f'메모 {cell.coordinate}', 'ok': ok, 'hint': f"메모 내용: {want}",
                              'msgs': [] if ok else [f"'{want}' 메모 필요"]})
    # 시나리오
    sc_a = {s.name for s in (ws_a.scenarios.scenario if ws_a.scenarios else [])}
    sc_s = {s.name for s in (ws_s.scenarios.scenario if ws_s.scenarios else [])}
    if sc_a - sc_s:
        sc_u = {s.name for s in (ws_u.scenarios.scenario if ws_u is not None and ws_u.scenarios else [])}
        miss = sorted((sc_a - sc_s) - sc_u)
        items.append({'label': '시나리오 ' + ', '.join(sorted(sc_a - sc_s)), 'ok': not miss,
                      'hint': '; '.join(f"{s.name}: " + ', '.join(f'{ic.r}={ic.val}' for ic in s.inputCells)
                                        for s in ws_a.scenarios.scenario if s.name in sc_a - sc_s),
                      'msgs': [f"없음: {', '.join(miss)}"] if miss else []})
    return items


def _book_items(src, ans, user):
    """통합 문서 전체: 이름 정의, 새 시트(시나리오 요약 등), 매크로·VBA."""
    items = []
    names_a = dict(xlsx._defined_names(ans.f))
    names_s = dict(xlsx._defined_names(src.f))
    names_u = {k.lower(): v for k, v in xlsx._defined_names(user.f)}
    for n, ref in names_a.items():
        if names_s.get(n) == ref:
            continue
        got = names_u.get(n.lower())
        ok = got is not None and got.replace('$', '').replace("'", '').upper() == ref.replace('$', '').replace("'", '').upper()
        items.append(('기본작업', {'label': f'이름 정의 {n}', 'ok': ok, 'hint': f'{n} = {ref}',
                                   'msgs': [] if ok else [f'{got or "없음"} → {ref}']}))
    for s in ans.f.sheetnames:
        if s not in src.f.sheetnames:
            ok = s in user.f.sheetnames
            if ok:
                idx_a, idx_u = ans.f.sheetnames.index(s), user.f.sheetnames.index(s)
                nxt = ans.f.sheetnames[idx_a + 1] if idx_a + 1 < len(ans.f.sheetnames) else None
                if nxt and nxt in user.f.sheetnames and user.f.sheetnames.index(nxt) != idx_u + 1:
                    ok = False
            items.append(('분석작업', {'label': f"'{s}' 시트(위치 포함)", 'ok': ok, 'msgs': [] if ok else ['시트가 없거나 위치가 다릅니다']}))
    # VBA: 정답에서 새로 생겼거나 내용이 바뀐 프로시저
    pa, ps, pu = ans.procs, src.procs, user.procs
    for name, body in pa.items():
        if name in ps and _similar(ps[name], body) >= 0.6:
            continue                             # 문제 파일에 이미 있던(거의 같은) 코드
        if len(_code_lines(body)) <= 1:
            continue
        sec = '계산작업' if body.lstrip().lower().startswith(('public function', 'function')) else '기타작업'
        u = pu.get(name)
        if u is None:
            items.append((sec, {'label': f'VBA {name}', 'ok': False, 'msgs': ['프로시저가 없습니다(.xlsm 으로 저장)']}))
            continue
        score = _similar(u, body)
        ok = score >= 0.6
        items.append((sec, {'label': f'VBA {name}', 'ok': ok, 'code': '\n'.join(_code_lines(body)),
                            'msgs': [] if ok else [f'정답 코드와 {round(score * 100)}% 일치(같은 문장 기준)']}))
    b_a = set(vba.buttons(ans.data)) - set(vba.buttons(src.data))
    b_u = {(_norm_text(t), m.lower()) for t, m in vba.buttons(user.data)}
    for text, macro in sorted(b_a):
        ok = (_norm_text(text), macro.lower()) in b_u
        items.append(('기타작업', {'label': f"단추 '{text}' → {macro}", 'ok': ok,
                                   'msgs': [] if ok else ['단추가 없거나 매크로가 연결되지 않았습니다']}))
    return items


def _similar(got, want):
    """정답 코드의 문장 중 몇 %가 들어 있는지(공백·대소문자·주석 무시)."""
    w = set(_code_lines(want))
    return len(w & set(_code_lines(got))) / max(1, len(w))


def _code_lines(body):
    lines = []
    for line in body.splitlines():
        line = re.sub(r"'.*$", '', line).strip()
        if not line or line.lower().startswith('attribute '):
            continue
        lines.append(re.sub(r'\s+', ' ', line).lower())
    return lines


def grade(src_bytes, ans_bytes, user_bytes, level=None):
    """문제 파일·정답 파일·수험자 파일 → 시트별 항목 채점. level 이 없으면 시트 이름으로 시험 구성인지 판단."""
    src = Wb(src_bytes)
    ans = Wb(ans_bytes)
    user = Wb(user_bytes)
    level = level or exam_level(ans.f.sheetnames)
    points = SHEET_POINTS[level] if level else None
    sheets, book_items = [], _book_items(src, ans, user)
    for sheet in ans.f.sheetnames:
        if sheet not in src.f.sheetnames:
            continue
        items = []
        for fn in (_cells_items, _style_items, _cf_items, _dv_items, _pivot_items, _chart_items, _misc_items):
            items += fn(sheet, src, ans, user)
        pts = {_key(k): v for k, v in points.items()}.get(_key(sheet), 0) if points else 0
        sheets.append({'name': sheet, 'section': section_of(sheet), 'points': pts, 'items': items})
    # 통합 문서 단위 항목은 해당 영역의 첫 시트(매크로는 매크로 시트, VBA 프로시저는 기타작업-3 등)에 붙인다
    for sec, it in book_items:
        target = None
        if it['label'].startswith('VBA') and sec == '기타작업':
            target = next((s for s in reversed(sheets) if s['section'] == '기타작업'), None)
            if '단추' in it['label'] or any(k in it['label'] for k in ('서식', '그래프', '총점', '채우기', '평균')):
                target = next((s for s in sheets if s['name'] in ('매크로작업', '기타작업-2')), target)
        if it['label'].startswith('단추'):
            target = next((s for s in sheets if s['name'] in ('매크로작업', '기타작업-2')), None)
        if target is None:
            target = next((s for s in sheets if s['section'] == sec), sheets[0])
        target['items'].append(it)
    if not points:      # 시험 구성이 아닌 실습 파일: 할 일이 있는 시트끼리 100점을 나눈다
        active = [s for s in sheets if s['items']]
        for s in sheets:
            s['points'] = 100 / len(active) if s in active else 0
    score = 0.0
    for s in sheets:
        n = len(s['items'])
        got = s['points'] * sum(1 for i in s['items'] if i['ok']) / n if n else 0
        score += got
        s['got'] = round(got, 1)
        s['points'] = round(s['points'], 1)
    total = sum(s['points'] for s in sheets)
    sections = []
    for sec in ('기본작업', '계산작업', '분석작업', '기타작업'):
        ss = [s for s in sheets if s['section'] == sec]
        if ss:
            sections.append({'name': sec, 'points': sum(s['points'] for s in ss), 'got': round(sum(s['got'] for s in ss), 1)})
    if not level:                                   # 시험 구성이 아니면 영역 대신 시트별로
        sections = [{'name': s['name'], 'points': s['points'], 'got': s['got']} for s in sheets if s['items']]
        total = 100
    return {'score': round(score, 1), 'total': round(total, 1), 'passed': score >= 70 * total / 100,
            'sheets': sheets, 'sections': sections, 'has_vba': bool(user.procs), 'level': level}
