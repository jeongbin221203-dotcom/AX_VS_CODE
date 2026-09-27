"""공통 유틸 (문자열 정규화, 엑셀 변환)."""

from datetime import datetime
from io import BytesIO
from typing import Mapping

import pandas as pd

_NULL_TOKENS = ("", "nan", "none", "nat", "<na>")


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def clean_str_series(s: pd.Series, default: str = "") -> pd.Series:
    """pandas 2.x / 3.x 공통 문자열 정규화.

    pandas 3.0부터 astype(str)이 결측을 문자열로 바꾸지 않고 NA로 남기므로,
    결측을 먼저 빈 문자열로 치환한 뒤 정규화해야 한다.
    'nan', 'None' 같은 문자열화된 결측도 default로 치환한다.
    """
    out = s.where(s.notna(), "").astype(str).str.strip()
    return out.mask(out.str.lower().isin(_NULL_TOKENS), default)


def to_excel_bytes(sheets: Mapping[str, pd.DataFrame]) -> bytes:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for sheet_name, df in sheets.items():
            df.to_excel(writer, sheet_name=str(sheet_name)[:31], index=False)
    return buf.getvalue()
