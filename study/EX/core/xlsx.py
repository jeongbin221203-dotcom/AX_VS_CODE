"""올린 엑셀 파일을 안전하게 읽기(압축 폭탄·크기 제한)와 계산기 통합문서로 바꾸기."""
import datetime as dt
import io
import zipfile

import openpyxl
from openpyxl.worksheet.formula import ArrayFormula

from . import formula as fx

MAX_UNZIPPED = 150 * 1024 * 1024
MAX_RATIO = 200


class BadFile(ValueError):
    pass


def check_zip(data):
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise BadFile('엑셀(.xlsx) 파일이 아닙니다. .xls 는 Excel 에서 .xlsx 로 다시 저장해 주세요.')
    total = sum(i.file_size for i in zf.infolist())
    packed = sum(i.compress_size for i in zf.infolist()) or 1
    if total > MAX_UNZIPPED or total / packed > MAX_RATIO and total > 10 * 1024 * 1024:
        raise BadFile('압축을 풀면 너무 큰 파일입니다.')


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
                f = formula_text(v)
                if f:
                    cached = wv.cell(cell.row, cell.column).value if wv is not None else None
                    if cached is not None:
                        cells[(cell.row, cell.column)] = plain(cached)
                    else:
                        try:
                            cells[(cell.row, cell.column)] = fx.Formula(f)
                        except fx.FormulaError:
                            cells[(cell.row, cell.column)] = fx.NAME
                else:
                    cells[(cell.row, cell.column)] = plain(v)
        book.add(fx.Sheet(ws.title, cells))
    return book
