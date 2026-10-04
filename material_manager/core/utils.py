"""공통 유틸 (문자열 정규화, 엑셀 변환)."""

import calendar
from datetime import date, datetime
from io import BytesIO
from typing import Mapping

import pandas as pd

_NULL_TOKENS = ("", "nan", "none", "nat", "<na>")


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def month_end(ym: str) -> str:
    """'2026-08' → '2026-08-31'. 빈 값이면 ''(마감 없음)."""
    if not ym:
        return ""
    y, m = int(ym[:4]), int(ym[5:7])
    return date(y, m, calendar.monthrange(y, m)[1]).isoformat()


def prev_month(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def code_series(s: pd.Series) -> pd.Series:
    """엑셀이 숫자로 읽은 코드(123.0)를 '123'으로 되돌린 뒤 문자열로 정규화한다."""
    fixed = s.map(lambda v: str(int(v)) if isinstance(v, float) and v.is_integer() else v)
    return clean_str_series(fixed)


def clean_str_series(s: pd.Series, default: str = "") -> pd.Series:
    """pandas 2.x / 3.x 공통 문자열 정규화.

    pandas 3.0부터 astype(str)이 결측을 문자열로 바꾸지 않고 NA로 남기므로,
    결측을 먼저 빈 문자열로 치환한 뒤 정규화해야 한다.
    'nan', 'None' 같은 문자열화된 결측도 default로 치환한다.
    """
    out = s.where(s.notna(), "").astype(str).str.strip()
    return out.mask(out.str.lower().isin(_NULL_TOKENS), default)


# 엑셀이 수식으로 해석하는 첫 글자. 사용자가 입력한 '=HYPERLINK(...)' 같은 값이 내려받은 파일에서
# 실행되지 않도록 앞에 작은따옴표를 붙여 글자로 만든다 (CSV/Excel 수식 주입 방어).
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def fmt_qty(value, sep: bool = True, places: int = 4) -> str:
    """수량 표시: 1,234.5 · 25,000 · 0.125 (지수 표기 1e+04 없이, 소수 places 자리까지, 끝의 0 은 뺀다).
    sep=False 는 입력 칸 값 (쉼표 없이, 소수 6자리까지)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "" if value is None else str(value)
    if v != v or v in (float("inf"), float("-inf")):
        return ""
    s = f"{v:,.{places}f}" if sep else f"{v:.6f}"
    s = s.rstrip("0").rstrip(".") if "." in s else s
    return "0" if s in ("-0", "") else s


def neutralize_formula(value):
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return value


EXCEL_MAX_ROWS = 1_048_575                   # 시트 한 장에 넣을 수 있는 데이터 행 (머리글 제외)


def to_csv_zip_bytes(sheets: Mapping[str, pd.DataFrame]) -> bytes:
    """여러 표 → 표마다 CSV(UTF-8 BOM, 엑셀에서 바로 열림) 하나씩 담은 zip. 엑셀보다 몇 배 빠르고 행 수 제한이 없다.
    엑셀로 열 때 수식이 실행되지 않게 글자 칸의 수식 시작 문자는 무력화한다."""
    import zipfile
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, df in sheets.items():
            safe = df.copy()
            for col in safe.columns:
                if safe[col].dtype == object or pd.api.types.is_string_dtype(safe[col]):
                    safe[col] = safe[col].map(neutralize_formula)
            zf.writestr(f"{name}.csv", safe.to_csv(index=False).encode("utf-8-sig"))
    return buf.getvalue()


def to_excel_bytes(sheets: Mapping[str, pd.DataFrame]) -> bytes:
    """여러 표 → .xlsx. 서식 없이 값만 쓰는 빠른 방식(openpyxl write-only — pandas.to_excel 보다 몇 배 빠름).
    글자 칸의 수식 시작 문자는 무력화하고, 한 시트에 다 안 들어가면 '이름 (2)' 시트로 나눈다."""
    from openpyxl import Workbook
    wb = Workbook(write_only=True)
    for sheet_name, df in sheets.items():
        safe = df.copy()
        for col in safe.columns:
            if safe[col].dtype == object or pd.api.types.is_string_dtype(safe[col]):
                safe[col] = safe[col].map(neutralize_formula)
        header = [str(c) for c in safe.columns]
        rows = safe.astype(object).where(safe.notna(), None).values.tolist()
        parts = [rows[i:i + EXCEL_MAX_ROWS] for i in range(0, len(rows), EXCEL_MAX_ROWS)] or [[]]
        for n, part in enumerate(parts, 1):
            name = str(sheet_name)[:31] if n == 1 else f"{str(sheet_name)[:25]} ({n})"
            ws = wb.create_sheet(name)
            ws.append(header)
            for r in part:
                ws.append(r)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def xlsx_problem(data: bytes, max_total: int, max_ratio: int) -> str:
    """엑셀(zip) 압축 폭탄 검사. 문제가 있으면 사유, 없으면 ''."""
    import zipfile
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            infos = zf.infolist()
    except zipfile.BadZipFile:
        return "엑셀 파일이 아니거나 손상되었습니다."
    total = sum(i.file_size for i in infos)
    packed = max(sum(i.compress_size for i in infos), 1)
    if total > max_total or total / packed > max_ratio:
        return "압축을 풀면 비정상적으로 커지는 파일입니다. 엑셀에서 다시 저장해 올려 주세요."
    return ""
