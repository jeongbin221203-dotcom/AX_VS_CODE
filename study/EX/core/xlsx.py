"""올린 엑셀 파일을 안전하게 읽기(압축 폭탄·크기 제한)와 계산기 통합문서로 바꾸기."""
import datetime as dt
import io
import re
import zipfile

import openpyxl
from openpyxl.worksheet.formula import ArrayFormula

from . import formula as fx

MAX_UNZIPPED = 40 * 1024 * 1024        # 압축을 푼 전체 크기
MAX_SHEET_XML = 20 * 1024 * 1024       # 시트 하나의 XML
MAX_RATIO = 100                        # 압축률(정상 엑셀은 보통 10배 안팎)
MAX_CELLS = 400_000                    # 시트 하나의 칸 수(dimension 기준)


class BadFile(ValueError):
    pass


def check_zip(data):
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise BadFile('엑셀(.xlsx) 파일이 아닙니다. .xls 는 Excel 에서 .xlsx 로 다시 저장해 주세요.')
    total = sum(i.file_size for i in zf.infolist())
    packed = sum(i.compress_size for i in zf.infolist()) or 1
    if total > MAX_UNZIPPED or (total > 2 * 1024 * 1024 and total / packed > MAX_RATIO):
        raise BadFile('압축을 풀면 너무 큰 파일입니다(연습용 파일은 보통 몇 MB 이하입니다).')
    for info in zf.infolist():
        if not re.match(r'xl/worksheets/sheet\d+\.xml$', info.filename):
            continue
        if info.file_size > MAX_SHEET_XML:
            raise BadFile('시트가 너무 큽니다.')
        head = zf.open(info).read(4096).decode('utf-8', 'replace')
        m = re.search(r'<dimension ref="([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?"', head)
        if m and m.group(3):
            rows = int(m.group(4)) - int(m.group(2)) + 1
            cols = fx.col_num(m.group(3)) - fx.col_num(m.group(1)) + 1
            if rows * cols > MAX_CELLS:
                raise BadFile(f'시트가 너무 큽니다({rows:,}행 × {cols}열).')


def load(data, data_only):
    check_zip(data)
    try:
        return openpyxl.load_workbook(io.BytesIO(data), data_only=data_only)
    except Exception as e:  # noqa: BLE001 — 손상된 파일은 어떤 예외든 같은 안내
        raise BadFile(f'파일을 열 수 없습니다: {type(e).__name__}')


def formula_text(v):
    if isinstance(v, ArrayFormula):
        return v.text if v.text.startswith('=') else '=' + v.text
    if isinstance(v, str) and v.startswith('=') and len(v) > 1:
        return v
    return None


def plain(v):
    """openpyxl 셀 값 → 계산기 값."""
    if isinstance(v, (dt.datetime, dt.date)):
        return fx.date_serial(v) if isinstance(v, dt.datetime) and (v.hour or v.minute or v.second) \
            else fx.date_serial(v.date() if isinstance(v, dt.datetime) else v)
    if isinstance(v, dt.time):
        return (v.hour * 3600 + v.minute * 60 + v.second) / 86400
    if isinstance(v, str) and v in fx.ERRORS:
        return fx.ERRORS[v]
    if isinstance(v, float) and v == int(v) and abs(v) < 1e15:
        return int(v)
    return v


def to_book(wb_f, wb_v, today=None):
    """수식 통합문서 + 값 통합문서 → 계산기 Book. 저장된 값이 있으면 그것을, 없으면 수식을 계산한다."""
    book = fx.Book(today=today)
    for ws in wb_f.worksheets:
        wv = wb_v[ws.title] if wb_v is not None else None
        cells = {}
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if v is None:
                    continue
                if type(v).__name__ == 'DataTableFormula':      # 데이터 표 칸: 엑셀이 저장한 값
                    cached = wv.cell(cell.row, cell.column).value if wv is not None else None
                    if cached is not None:
                        cells[(cell.row, cell.column)] = plain(cached)
                    continue
                f = formula_text(v)
                if f:
                    cached = wv.cell(cell.row, cell.column).value if wv is not None else None
                    cached = plain(cached) if cached is not None else None
                    try:
                        cells[(cell.row, cell.column)] = fx.Formula(f, cached)
                    except fx.FormulaError:
                        cells[(cell.row, cell.column)] = cached if cached is not None else fx.NAME
                else:
                    cells[(cell.row, cell.column)] = plain(v)
        book.add(fx.Sheet(ws.title, cells))
    for name, dn in _defined_names(wb_f):
        try:
            book.define(name, dn)
        except fx.FormulaError:
            pass
    return book


def _defined_names(wb):
    """통합문서·시트 범위 이름 정의 → [(이름, '=참조')]. 엑셀 내부 이름(_xlnm.)은 뺀다."""
    out = []
    items = list(wb.defined_names.items()) if hasattr(wb.defined_names, 'items') else [(d.name, d) for d in wb.defined_names.definedName]
    for ws in wb.worksheets:
        items += list(getattr(ws, 'defined_names', {}).items())
    for name, d in items:
        text = getattr(d, 'attr_text', None) or getattr(d, 'value', None)
        if not text or name.startswith('_xlnm') or '#REF!' in text:
            continue
        out.append((name, '=' + text))
    return out


def _rels(z, part):
    """part 의 관계 파일 → {rId: 대상 경로(zip 안)}"""
    folder, name = part.rsplit('/', 1)
    path = f'{folder}/_rels/{name}.rels'
    if path not in z.namelist():
        return {}
    text = z.read(path).decode('utf-8', 'replace')
    out = {}
    for m in re.finditer(r'<Relationship\b([^>]*)/?>', text):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        target = attrs.get('Target', '')
        if target.startswith('/'):
            full = target.lstrip('/')
        else:
            parts = folder.split('/')
            for seg in target.split('/'):
                if seg == '..':
                    parts.pop()
                elif seg != '.':
                    parts.append(seg)
            full = '/'.join(parts)
        out[attrs.get('Id')] = (full, attrs.get('Type', ''))
    return out


def raw_charts(data):
    """openpyxl 이 놓치는 차트(그룹 안 차트 등)까지: {시트 이름: [openpyxl 차트 객체]} — 파일의 차트 XML 을 직접 읽는다."""
    from openpyxl.chart.chartspace import ChartSpace
    from openpyxl.chart.reader import read_chart
    from openpyxl.xml.functions import fromstring
    out = {}
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        wbx = z.read('xl/workbook.xml').decode('utf-8', 'replace')
    except (zipfile.BadZipFile, KeyError):
        return out
    rels = _rels(z, 'xl/workbook.xml')
    for m in re.finditer(r'<sheet\b([^>]*)/>', wbx):
        attrs = dict(re.findall(r'([\w:]+)="([^"]*)"', m.group(1)))
        name = attrs.get('name', '').replace('&amp;', '&')
        sheet_part = rels.get(attrs.get('r:id'), (None,))[0]
        if not sheet_part or sheet_part not in z.namelist():
            continue
        charts = []
        for target, kind in _rels(z, sheet_part).values():
            if not kind.endswith('/drawing') or target not in z.namelist():
                continue
            for ctarget, ckind in _rels(z, target).values():
                if ckind.endswith('/chart') and ctarget in z.namelist():
                    try:
                        charts.append(read_chart(ChartSpace.from_tree(fromstring(z.read(ctarget)))))
                    except Exception:  # noqa: BLE001 — 읽을 수 없는 차트는 건너뜀
                        continue
        out[name] = charts
    return out
