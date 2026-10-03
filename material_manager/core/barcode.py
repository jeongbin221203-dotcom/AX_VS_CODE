"""Code 128 바코드 (라벨 인쇄용). 외부 라이브러리 없이 SVG로 그린다.

- 숫자만이고 4자리 이상이면 C 세트(두 자리씩 → 짧은 바코드), 아니면 B 세트(영문·숫자·기호).
  숫자가 홀수 자리면 앞부분은 C, 마지막 한 자리는 B로 바꿔 넣는다.
- 한글처럼 ASCII가 아닌 글자는 Code 128로 만들 수 없다 → None.
- 일반 1D 스캐너·카메라(BarcodeDetector·ZXing)가 모두 읽는다.
"""
from __future__ import annotations

from html import escape

# 값 0~106의 막대·공백 너비 (모듈 수). 106 = 끝(STOP, 7칸).
PATTERNS = (
    "212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 221312 231212 112232 122132 122231 113222 "
    "123122 123221 223211 221132 221231 213212 223112 312131 311222 321122 321221 312212 322112 322211 212123 212321 "
    "232121 111323 131123 131321 112313 132113 132311 211313 231113 231311 112133 112331 132131 113123 113321 133121 "
    "313121 211331 231131 213113 213311 213131 311123 311321 331121 312113 312311 332111 314111 221411 431111 111224 "
    "111422 121124 121421 141122 141221 112214 112412 122114 122411 142112 142211 241211 221114 413111 241112 134111 "
    "111242 121142 121241 114212 124112 124211 411212 421112 421211 212141 214121 412121 111143 111341 131141 114113 "
    "114311 411113 411311 113141 114131 311141 411131 211412 211214 211232 2331112"
).split()
START_B, START_C, CODE_B, CODE_C, STOP = 104, 105, 100, 99, 106
assert len(PATTERNS) == 107 and all(sum(map(int, p)) == 11 for p in PATTERNS[:106])


def encode(text: str) -> list[int] | None:
    """글자 → Code 128 값 목록 (시작·검사·끝 포함). 만들 수 없으면 None."""
    if not text or any(not 32 <= ord(c) <= 126 for c in text):
        return None
    vals: list[int]
    if text.isdigit() and len(text) >= 4:
        even = text if len(text) % 2 == 0 else text[:-1]
        vals = [START_C] + [int(even[i:i + 2]) for i in range(0, len(even), 2)]
        if len(text) % 2:
            vals += [CODE_B, ord(text[-1]) - 32]
    else:
        vals = [START_B] + [ord(c) - 32 for c in text]
    check = (vals[0] + sum(i * v for i, v in enumerate(vals[1:], 1))) % 103
    return vals + [check, STOP]


def modules(text: str) -> list[int] | None:
    """막대·공백 너비 목록 (막대부터 번갈아)."""
    vals = encode(text)
    if vals is None:
        return None
    return [int(w) for v in vals for w in PATTERNS[v]]


def svg(text: str, height: int = 40, quiet: int = 10) -> str | None:
    """막대만 그린 SVG (가로는 늘어나는 viewBox — 라벨 칸 너비에 맞춰 그린다)."""
    widths = modules(text)
    if widths is None:
        return None
    total = sum(widths) + quiet * 2
    x, rects = quiet, []
    for i, w in enumerate(widths):
        if i % 2 == 0:
            rects.append(f'<rect x="{x}" y="0" width="{w}" height="{height}"/>')
        x += w
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total} {height}" preserveAspectRatio="none" '
            f'shape-rendering="crispEdges" role="img" aria-label="바코드 {escape(text)}"><rect width="{total}" height="{height}" '
            f'fill="#fff"/><g fill="#000">{"".join(rects)}</g></svg>')
