"""엑셀 파일 속 피벗 테이블 읽기·채점 (필드 배치·값 요약 방식·위치·총합계)."""
import math

from . import formula as fx

FUNC_NAMES = {'sum': '합계', 'count': '개수', 'average': '평균', 'max': '최대', 'min': '최소', 'product': '곱',
              'countNums': '숫자 개수', 'stdDev': '표준 편차', 'var': '분산'}
ALIASES = {'avg': 'average', 'mean': 'average', '합계': 'sum', '개수': 'count', '평균': 'average', '최대': 'max',
           '최소': 'min'}


def _names(pt):
    return [f.name for f in pt.cache.cacheFields]


def _field_names(fields, names):
    out = []
    for f in fields or []:
        x = getattr(f, 'x', None)
        if x is None:
            x = getattr(f, 'fld', None)
        if x is not None and 0 <= x < len(names):      # x=-2 는 '값' 가상 필드
            out.append(names[x])
    return out


def _fmt_code(wb, num_id):
    """표시 형식 번호 → 형식 글자(기본 형식 또는 이 통합문서의 사용자 지정 형식)."""
    from openpyxl.styles.numbers import BUILTIN_FORMATS
    if num_id is None:
        return None
    if num_id in BUILTIN_FORMATS:
        return BUILTIN_FORMATS[num_id]
    custom = list(getattr(wb, '_number_formats', []) or [])
    i = num_id - 164
    return custom[i] if 0 <= i < len(custom) else None


def _cell_formats(ws, ref):
    """피벗 범위 안 숫자 칸들의 표시 형식(값 필드 대신 셀 서식으로 지정한 경우)."""
    try:
        r1, c1, r2, c2 = fx.parse_range(ref)
    except ValueError:
        return []
    out = set()
    for row in ws.iter_rows(min_row=r1, max_row=min(r2, r1 + 200), min_col=c1, max_col=c2):
        for cell in row:
            if isinstance(cell.value, (int, float)) and not isinstance(cell.value, bool):
                out.add(cell.number_format)
    return sorted(out)


def describe(wb):
    """통합문서의 모든 피벗 → [{sheet, ref, rows, cols, filters, values[(필드, 함수)], source, grouped}]"""
    out = []
    for ws in wb.worksheets:
        for pt in getattr(ws, '_pivots', []):
            names = _names(pt)
            value_formats = [c for c in (_fmt_code(wb, getattr(d, 'numFmtId', None)) for d in pt.dataFields) if c]
            src = pt.cache.cacheSource.worksheetSource if pt.cache.cacheSource else None
            grouped = [cf.name for cf in pt.cache.cacheFields if getattr(cf, 'fieldGroup', None) is not None]
            out.append({
                'sheet': ws.title, 'ref': pt.location.ref, 'name': pt.name,
                'rows': _field_names(pt.rowFields, names), 'cols': _field_names(pt.colFields, names),
                'filters': _field_names(pt.pageFields, names),
                'values': [(names[d.fld], d.subtotal or 'sum') for d in pt.dataFields if 0 <= d.fld < len(names)],
                'source': (getattr(src, 'sheet', None), getattr(src, 'ref', None), getattr(src, 'name', None)) if src else None,
                'grouped': grouped,
                'grand_rows': pt.rowGrandTotals is not False, 'grand_cols': pt.colGrandTotals is not False,
                'layout': 'compact' if pt.compact is not False and pt.outline is not False else
                          ('outline' if pt.outline else 'tabular'),
                'value_formats': value_formats, 'cell_formats': _cell_formats(ws, pt.location.ref),
            })
    return out


def _norm(name):
    return str(name).replace(' ', '').lower()


def _func(f):
    f = str(f)
    return ALIASES.get(f, f)


def match(spec, pv):
    """spec: {rows, cols, filters, values:[[필드, 함수]], sheet, at} → (맞음 여부, 틀린 이유 목록)"""
    why = []
    for key, label in (('rows', '행'), ('cols', '열'), ('filters', '필터')):
        if key in spec:
            want = [_norm(x) for x in spec[key]]
            got = [_norm(x) for x in pv[key]]
            if want != got:
                why.append(f"{label} 영역: {', '.join(spec[key]) or '(없음)'} 이어야 하는데 {', '.join(pv[key]) or '(없음)'}")
    if 'values' in spec:
        want = sorted((_norm(f), _func(fn)) for f, fn in spec['values'])
        got = sorted((_norm(f), _func(fn)) for f, fn in pv['values'])
        if want != got:
            fmt = lambda xs: ', '.join(f'{f} {FUNC_NAMES.get(_func(fn), fn)}' for f, fn in xs) or '(없음)'  # noqa: E731
            why.append(f"값 영역: {fmt(spec['values'])} 이어야 하는데 {fmt(pv['values'])}")
    if spec.get('sheet') and _norm(spec['sheet']) != _norm(pv['sheet']):
        why.append(f"위치: '{spec['sheet']}' 시트여야 하는데 '{pv['sheet']}'")
    if spec.get('at'):
        r, c = fx.parse_addr(spec['at'])
        r1, c1, _, _ = fx.parse_range(pv['ref'])
        # 보고서 필터가 있으면 표 위쪽 (필터 수 + 1)행에서 시작한다
        starts = {(r1, c1), (r1 - len(pv['filters']) - 1, c1)} if pv['filters'] else {(r1, c1)}
        if (r, c) not in starts:
            why.append(f"위치: {spec['at']} 에서 시작해야 하는데 {fx.addr(*min(starts))}")
    if spec.get('group') and not any(_norm(g) in [_norm(x) for x in pv['grouped']] for g in spec['group']):
        why.append(f"{', '.join(spec['group'])} 필드를 그룹으로 묶지 않았습니다")
    if spec.get('no_grand_rows') and pv['grand_rows']:
        why.append('행 총합계를 해제해야 합니다')
    if spec.get('no_grand_cols') and pv['grand_cols']:
        why.append('열 총합계를 해제해야 합니다')
    if spec.get('layout') and spec['layout'] != pv['layout']:
        names = {'compact': '압축', 'outline': '개요', 'tabular': '테이블'}
        why.append(f"보고서 레이아웃: {names[spec['layout']]} 형식이어야 합니다")
    if spec.get('value_numfmt'):
        from .exam import _same_fmt
        have = (pv.get('value_formats') or []) + (pv.get('cell_formats') or [])
        if not any(_same_fmt(f, spec['value_numfmt'], [1234567, 1234.5]) for f in have):
            why.append(f"값 영역 표시 형식: {spec['value_numfmt']}(예: 1,234,567) 이어야 합니다 — [값 필드 설정] > [표시 형식]")
    return not why, why


def grand_total(ws_values, pv):
    """피벗 표 오른쪽 아래 칸(총합계) 값 — 엑셀이 저장한 값."""
    _, _, r2, c2 = fx.parse_range(pv['ref'])
    return ws_values.cell(r2, c2).value


def expected_total(book, pv, field, func):
    """원본 범위에서 값 필드를 직접 집계한 기대 총합계."""
    src = pv['source']
    if not src or not src[1]:
        return None
    sheet = book.sheet(src[0])
    r1, c1, r2, c2 = fx.parse_range(src[1])
    heads = [_norm(sheet.get(r1, c)) for c in range(c1, c2 + 1)]
    if _norm(field) not in heads:
        return None
    col = c1 + heads.index(_norm(field))
    vals = [sheet.get(r, col) for r in range(r1 + 1, r2 + 1)]
    nums = [v for v in vals if fx.is_num(v) and not isinstance(v, bool)]
    f = _func(func)
    if f == 'sum':
        return fx.fix(math.fsum(nums))
    if f == 'count':
        return sum(1 for v in vals if v is not None)
    if f == 'average':
        return fx.fix(math.fsum(nums) / len(nums)) if nums else None
    if f == 'max':
        return max(nums) if nums else None
    if f == 'min':
        return min(nums) if nums else None
    return None


def check(spec, wb_f, wb_v, book):
    """채점 항목 하나. → {'ok', 'msgs'}"""
    pvs = describe(wb_f)
    if not pvs:
        return {'ok': False, 'msgs': ['피벗 테이블이 없습니다']}
    best = None
    for pv in pvs:
        ok, why = match(spec, pv)
        if best is None or len(why) < len(best[1]):
            best = (pv, why)
        if ok:
            msgs = [f"{pv['sheet']}!{pv['ref']} 행: {', '.join(pv['rows']) or '-'} · 열: {', '.join(pv['cols']) or '-'} · 값: "
                    + ', '.join(f'{f} {FUNC_NAMES.get(_func(fn), fn)}' for f, fn in pv['values'])]
            if spec.get('check_total', True) and len(pv['values']) == 1 and pv['grand_rows'] and pv['grand_cols']:
                got = grand_total(wb_v[pv['sheet']], pv)
                exp = expected_total(book, pv, *pv['values'][0])
                if got is not None and exp is not None and not fx.same_value(fx.fix(got) if fx.is_num(got) else got, exp, 1e-6):
                    return {'ok': False, 'msgs': msgs + [f'총합계 {fx.display(got)} — 원본 기준 {fx.display(exp)} 이어야 합니다(원본 범위 확인)']}
            return {'ok': True, 'msgs': msgs}
    return {'ok': False, 'msgs': best[1][:3]}
