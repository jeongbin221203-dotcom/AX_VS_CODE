"""연봉 문구를 '연 만원' 범위로 바꾼다.

'2,600~2,800만원', '연봉 4천5백만원 이상', '월 250만원', '시급 10,030원', '1억 2천만원',
'회사내규에 따름', '면접 후 결정' 같은 표기를 모두 받는다.
"""
from __future__ import annotations

import re

from .normalize import clean

HOURS_PER_MONTH = 209          # 주 40시간 + 주휴 기준 월 소정근로시간
WORKDAYS_PER_MONTH = 22
NEGOTIABLE_WORDS = ("내규", "협의", "면접", "추후", "결정", "경력에 따라", "능력에 따라")

_TOKEN = re.compile(r"(\d+(?:\.\d+)?)(억|천만|천|백만|백|만원|만|원)?")
_UNIT = {"억": 10**8, "천만": 10**7, "백만": 10**6, "만원": 10**4, "만": 10**4, "원": 1}


def parse(text, pay_type: str | None = None) -> tuple[int | None, int | None, bool]:
    """(최소, 최대, 협의 여부). 금액은 연봉 환산 만원. 모르면 None."""
    raw = clean(text)
    hint = clean(pay_type)
    if not raw:
        return None, None, False
    groups = _amount_groups(raw)
    if not groups:
        return None, None, any(w in raw for w in NEGOTIABLE_WORDS)

    period = _period(hint + " " + raw, groups)
    factor = {"hour": HOURS_PER_MONTH * 12, "day": WORKDAYS_PER_MONTH * 12, "month": 12, "year": 1}[period]
    values = [round(g * factor / 10**4) for g in groups]

    compact = raw.replace(" ", "")
    if len(values) >= 2:
        lo, hi = min(values[:2]), max(values[:2])
    elif re.search(r"(이상|↑|부터|최소)", compact):
        lo, hi = values[0], None
    elif re.search(r"(이하|까지|최대|↓)", compact) or compact.lstrip("연봉월급시급:").startswith("~"):
        lo, hi = None, values[0]
    else:
        lo = hi = values[0]
    return lo, hi, False


def midpoint(lo: int | None, hi: int | None) -> int | None:
    if lo and hi:
        return round((lo + hi) / 2)
    return lo or hi


def format_manwon(v: int | None) -> str:
    if v is None:
        return "-"
    if v >= 10000:
        eok, rest = divmod(v, 10000)
        return f"{eok}억 {rest:,}만원" if rest else f"{eok}억원"
    return f"{v:,}만원"


def format_range(lo: int | None, hi: int | None, negotiable: bool = False) -> str:
    if lo is None and hi is None:
        return "회사내규·협의" if negotiable else "미공개"
    if lo == hi or hi is None:
        return format_manwon(lo) + ("" if hi is not None else " 이상")
    if lo is None:
        return "~" + format_manwon(hi)
    return f"{format_manwon(lo)} ~ {format_manwon(hi)}"


def _amount_groups(text: str) -> list[float]:
    """붙어 있는 숫자·단위 조각을 하나의 금액(원)으로 묶는다. 단위 없는 숫자는 뒤 금액의 단위를 따른다."""
    t = text.replace(",", "").replace(" ", "")
    t = re.sub(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}", "", t)          # 날짜 제거
    t = re.sub(r"(주|하루|일)\d+(시간|일)", "", t)                # 근무시간 표기 제거
    groups: list[list[tuple[float, str | None]]] = []
    prev_end = -1
    for m in _TOKEN.finditer(t):
        if m.group(1) in ("", "."):
            continue
        tok = (float(m.group(1)), m.group(2))
        if groups and m.start() == prev_end and groups[-1][-1][1] not in (None, "원", "만원", "만"):
            groups[-1].append(tok)
        else:
            groups.append([tok])
        prev_end = m.end()

    amounts: list[float | None] = []
    units: list[int | None] = []
    for g in groups:
        has_man = any(u in ("만", "만원", "억", "천만", "백만") for _, u in g)
        total, unit_seen = 0.0, None
        for n, u in g:
            if u == "천":
                mul = 10**7 if has_man or len(g) > 1 else 10**3
            elif u == "백":
                mul = 10**6 if has_man or len(g) > 1 else 10**2
            else:
                mul = _UNIT.get(u) if u else None
            if mul is None:
                total = None
                break
            total += n * mul
            unit_seen = mul
        amounts.append(total)
        units.append(unit_seen)

    # 단위 없는 숫자: 다음 금액의 마지막 단위, 없으면 크기로 짐작
    result = []
    for i, a in enumerate(amounts):
        if a is not None:
            result.append(a)
            continue
        n = groups[i][0][0]
        nxt = next((units[j] for j in range(i + 1, len(units)) if units[j]), None)
        if nxt in (10**4, 10**8, 10**7, 10**6):
            result.append(n * 10**4 if nxt != 10**8 else n * 10**8)
        elif nxt == 1 or n >= 100000:
            result.append(n)
        elif n >= 100:
            result.append(n * 10**4)                                 # '3000' → 3000만원
        # 100 미만의 맨숫자(년수·시간 등)는 금액으로 보지 않는다
    return [r for r in result if r and r > 0]


def _period(text: str, groups: list[float]) -> str:
    t = text.replace(" ", "")
    if "시급" in t or "시간당" in t:
        return "hour"
    if "일급" in t or "일당" in t:
        return "day"
    if "월급" in t or t.startswith("월") or "월" in t[:4] or "/월" in t:
        return "month"
    if "연봉" in t or "연" in t[:3]:
        return "year"
    top = max(groups)
    if top < 100_000:                                                # 10만원 미만 → 시급으로 본다
        return "hour"
    if top <= 1000 * 10**4:                                          # 1,000만원 이하 → 월급으로 본다
        return "month"
    return "year"
