"""거래명세서 파일(PDF·이미지)에서 품목 줄 읽기. 읽은 값은 미리보기에만 채우고, 사람이 원본과 비교해 확인한 뒤 등록한다.

  PDF(글자가 들어 있는 것 — ERP·전자문서에서 내보낸 명세서)
      pdfplumber로 표를 찾아 머리글(품번·품명·수량·단가·공급가액·세액 …)이 맞는 표를 쓴다.
      표가 없으면 글자 줄을 읽는다. 인터넷·OCR 없이 동작한다.
  스캔 이미지(JPG·PNG·WEBP) · 스캔 PDF(글자 없음)
      Tesseract OCR(한국어 kor)이 서버에 있을 때만 읽는다 (설정: MM_TESSERACT_CMD, MM_OCR_LANG).
      없으면 '읽을 수 없음'을 알리고, 엑셀이나 직접 입력으로 등록한다. 파일은 외부로 보내지 않는다.

글자 줄 읽기 규칙: 한 줄 끝의 숫자들을 [수량, 단가, 공급가액, (세액)]으로 보고, 앞쪽은 자재코드(영문·숫자)와 품명으로 본다.
합계·소계·머리글 줄은 건너뛴다. 스캔은 글자를 잘못 읽을 수 있으므로 모든 줄에 '확인 필요' 경고를 붙인다.
"""

from __future__ import annotations

import io
import os
import re
import shutil

import pandas as pd

import config
from core import excel_forms
from core.statements import LINE_COLS, SKIP_RE, Line, lines_from_frame

PDF_MAGIC = b"%PDF"
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")
NUM_RE = re.compile(r"^-?[\d,]+(\.\d+)?$")
CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{1,39}$")
HEADER_WORDS = ("품명", "품목", "수량", "단가", "공급가액", "금액", "세액", "규격", "품번")
READ_WARNING = "파일에서 읽은 값입니다. 원본과 비교해 확인하세요."
OCR_WARNING = "스캔(OCR)으로 읽은 값입니다. 숫자·품명을 원본과 꼭 비교하세요."


def ocr_available() -> bool:
    cmd = getattr(config, "TESSERACT_CMD", "") or shutil.which("tesseract")
    if not cmd or not (os.path.exists(cmd) or shutil.which(cmd)):
        return False
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    return True


def _ocr_text(images) -> str:
    import pytesseract
    if getattr(config, "TESSERACT_CMD", ""):
        pytesseract.pytesseract.tesseract_cmd = config.TESSERACT_CMD
    lang = getattr(config, "OCR_LANG", "kor+eng")
    return "\n".join(pytesseract.image_to_string(img, lang=lang, config="--psm 6") for img in images)


def _lookup() -> dict[str, str]:
    """머리글 별칭(엑셀 양식 '거래명세서 품목' 포함) → 표준 머리글."""
    _title, fields = excel_forms.IMPORT_FORMS["statement_lines"]
    cfg = excel_forms.load("statement_lines")
    out: dict[str, str] = {}
    for field_key, (std, defaults) in fields.items():
        for n in [std, *defaults, *cfg.get("aliases", {}).get(field_key, [])]:
            out.setdefault(excel_forms._norm(n), std)
    return out


def _table_lines(tables: list[list[list]]) -> list[Line]:
    """pdfplumber 표 → 머리글이 가장 많이 맞는 표의 줄."""
    lookup = _lookup()
    best, best_score = None, 0
    for table in tables:
        for hi, row in enumerate(table[:5]):                       # 표 위쪽 몇 줄 안에서 머리글 찾기
            names = [lookup.get(excel_forms._norm(str(c or "")), "") for c in row]
            score = sum(1 for n in names if n)
            if score > best_score and "수량" in names:
                best, best_score = (table, hi, names), score
    if best is None:
        return []
    table, hi, names = best
    rows = [[(c or "").replace("\n", " ").strip() if isinstance(c, str) else c for c in r] for r in table[hi + 1:]]
    df = pd.DataFrame(rows, columns=[n or f"_{i}" for i, n in enumerate(names)])
    lines, _errors = lines_from_frame(df)
    return lines


def _text_lines(text: str) -> list[Line]:
    out: list[Line] = []
    for raw in text.splitlines():
        line = raw.replace("|", " ").strip()
        if not line or SKIP_RE.match(line.split()[0] if line.split() else "") or "합계" in line.replace(" ", ""):
            continue
        tokens = line.split()
        nums = []
        while tokens and NUM_RE.match(tokens[-1]):
            nums.insert(0, float(tokens.pop().replace(",", "")))
        if len(nums) < 2 or sum(1 for w in HEADER_WORDS if w in line) >= 2:
            continue                                            # 숫자가 모자라거나 머리글 줄
        if len(nums) > 4:
            nums = nums[-4:]
        if len(tokens) > 1 and re.fullmatch(r"\d{1,3}", tokens[0]):  # 맨 앞 순번 열 (코드보다 먼저 뗀다)
            tokens.pop(0)
        code = tokens.pop(0).upper() if tokens and CODE_RE.match(tokens[0]) and re.search(r"\d", tokens[0]) else ""
        name = " ".join(tokens)
        qty, price = nums[0], nums[1]
        supply = nums[2] if len(nums) >= 3 else None
        tax = nums[3] if len(nums) >= 4 else None
        out.append(Line(no=len(out) + 1, code=code, name=name, qty=qty, unit_price=price, supply=supply, tax=tax))
    return out


def read(data: bytes, filename: str) -> tuple[list[Line], list[str], str]:
    """(줄, 문제, 방법). 방법: pdf-table | pdf-text | ocr | ''."""
    name = (filename or "").lower()
    images = []
    if data[:4] == PDF_MAGIC or name.endswith(".pdf"):
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                tables, texts = [], []
                for page in pdf.pages[:10]:
                    tables += page.extract_tables() or []
                    texts.append(page.extract_text() or "")
        except Exception:
            return [], ["PDF를 열 수 없습니다(암호가 걸렸거나 손상된 파일)."], ""
        lines = _table_lines(tables)
        if lines:
            return _mark(lines, READ_WARNING), [], "pdf-table"
        text = "\n".join(texts)
        if text.strip():
            lines = _text_lines(text)
            if lines:
                return _mark(lines, READ_WARNING), [], "pdf-text"
            return [], ["PDF에서 품목 줄(품명·수량·단가)을 찾지 못했습니다. 엑셀로 올리거나 직접 입력하세요."], ""
        try:                                                    # 글자가 없는 스캔 PDF → 페이지를 이미지로
            import fitz
            doc = fitz.open(stream=data, filetype="pdf")
            from PIL import Image
            for page in list(doc)[:5]:
                pix = page.get_pixmap(dpi=300)
                images.append(Image.open(io.BytesIO(pix.tobytes("png"))))
        except Exception:
            return [], ["스캔 PDF를 이미지로 바꾸지 못했습니다."], ""
    elif name.endswith(IMAGE_EXT):
        from PIL import Image
        try:
            images.append(Image.open(io.BytesIO(data)))
        except Exception:
            return [], ["이미지를 열 수 없습니다."], ""
    else:
        return [], ["읽을 수 있는 파일이 아닙니다."], ""

    if not ocr_available():
        return [], ["스캔 이미지는 서버에 Tesseract OCR(한국어)이 설치되어 있어야 읽을 수 있습니다. "
                    "지금은 엑셀이나 직접 입력으로 품목을 넣어 주세요. (시스템관리자: README '거래명세서 스캔 읽기')"], ""
    try:
        text = _ocr_text(images)
    except Exception as exc:
        return [], [f"OCR에 실패했습니다: {type(exc).__name__}"], ""
    lines = _text_lines(text)
    if not lines:
        return [], ["스캔에서 품목 줄을 읽지 못했습니다. 더 선명하게 스캔하거나 엑셀·직접 입력을 쓰세요."], ""
    return _mark(lines, OCR_WARNING), [], "ocr"


def _mark(lines: list[Line], warning: str) -> list[Line]:
    for ln in lines:
        ln.source_warning = warning
    return lines


__all__ = ["read", "ocr_available", "LINE_COLS"]
