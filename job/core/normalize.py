"""공고마다 다른 표기(지역·경력·학력·연봉)를 공통 형태로 맞춘다."""
from __future__ import annotations

import html
import re
from datetime import date, datetime

# 짧은 이름 → 긴 이름들 (행정구역 개편 이름 포함)
SIDO = {
    "서울": ("서울특별시", "서울시"),
    "부산": ("부산광역시", "부산시"),
    "대구": ("대구광역시", "대구시"),
    "인천": ("인천광역시", "인천시"),
    "광주": ("광주광역시", "광주시"),
    "대전": ("대전광역시", "대전시"),
    "울산": ("울산광역시", "울산시"),
    "세종": ("세종특별자치시", "세종시"),
    "경기": ("경기도",),
    "강원": ("강원도", "강원특별자치도"),
    "충북": ("충청북도",),
    "충남": ("충청남도",),
    "전북": ("전라북도", "전북특별자치도"),
    "전남": ("전라남도",),
    "경북": ("경상북도",),
    "경남": ("경상남도",),
    "제주": ("제주특별자치도", "제주도"),
}
SIDO_ORDER = list(SIDO) + ["재택", "해외", "전국"]
_ALIASES = {long: short for short, longs in SIDO.items() for long in longs}
_ALIASES.update({"재택근무": "재택", "원격근무": "재택", "리모트": "재택", "원격": "재택",
                 "전국": "전국", "해외": "해외"})
# 경기도 광주시는 '광주'가 아니라 '경기'로 봐야 하므로 긴 이름을 먼저 찾는다
_SIDO_KEYS = sorted(list(_ALIASES) + list(SIDO), key=len, reverse=True)

EDUCATION_LEVELS = ["무관", "고졸", "초대졸", "대졸", "석사", "박사"]
CAREER_TYPES = ["신입", "경력", "신입·경력", "무관"]


def clean(text) -> str:
    if text is None:
        return ""
    text = html.unescape(str(text))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_region(text) -> tuple[str | None, str | None]:
    """'서울 > 강남구', '경기도 성남시 분당구', '서울특별시 강남구 테헤란로' → ('서울', '강남구')."""
    text = clean(text)
    if not text:
        return None, None
    first = re.split(r"[,/|·]| 외 ", text)[0].replace(">", " ")
    first = re.sub(r"\s+", " ", first).strip()
    for key in _SIDO_KEYS:
        if first.startswith(key) or (key in ("재택", "재택근무", "원격근무", "리모트") and key in first):
            short = _ALIASES.get(key, key)
            rest = first[len(key):].strip() if first.startswith(key) else ""
            sigungu = None
            m = re.match(r"(\S+?(?:시|군|구))(?:\s+(\S+?구))?(?:\s|$)", rest)
            if m:
                sigungu = m.group(1) + (" " + m.group(2) if m.group(2) else "")
            return short, sigungu
    if "재택" in text or "원격" in text:
        return "재택", None
    return None, None


def parse_career(text, lo=None, hi=None) -> tuple[str, int | None, int | None]:
    """'신입', '경력 3년↑', '경력(2~5년)', '신입/경력', '경력무관', '관계없음' → (구분, 최소, 최대)."""
    t = clean(text).replace(" ", "")
    rng = re.search(r"(\d+)\s*년?\s*[~\-]\s*(\d+)\s*년", t)
    if rng:
        nums = [int(rng.group(1)), int(rng.group(2))]
    else:
        nums = [int(n) for n in re.findall(r"(\d+)\s*년", t)] or [int(n) for n in re.findall(r"\d+", t)]
    lo = _int(lo) if lo not in (None, "", 0, "0") else None
    hi = _int(hi) if hi not in (None, "", 0, "0") else None
    if lo is None and nums:
        lo = nums[0]
    if hi is None and len(nums) > 1:
        hi = nums[1]
    if not t:
        kind = "무관"
    elif "무관" in t or "관계없음" in t or "경력무관" in t:
        kind = "무관"
    elif "신입" in t and "경력" in t:
        kind = "신입·경력"
    elif "신입" in t:
        kind = "신입"
        lo = hi = None
    elif "경력" in t or lo:
        kind = "경력"
    else:
        kind = "무관"
    return kind, lo, hi


def parse_education(text) -> str:
    t = clean(text).replace(" ", "")
    if not t or "무관" in t or "관계없음" in t:
        return "무관"
    t = _before_range(t)                  # '대졸(2~3년)~대졸(4년)', '고졸~대졸' → 앞의 최소 조건만
    # 초대졸을 대졸보다 먼저 본다: '대졸(2~3년)' 에도 '대졸' 이 들어 있다
    for level, words in (("박사", ("박사",)), ("석사", ("석사", "대학원")),
                         ("초대졸", ("2,3년", "2~3년", "전문대", "초대졸", "2년", "3년")),
                         ("대졸", ("4년", "대학교", "대졸", "학사")),
                         ("고졸", ("고졸", "고등학교"))):
        if any(w in t for w in words):
            return level
    return "무관"


def _before_range(t: str) -> str:
    """괄호 밖의 첫 '~' 앞부분. 괄호 안의 '2~3년' 은 범위 구분으로 보지 않는다."""
    depth = 0
    for i, ch in enumerate(t):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif ch == "~" and depth == 0 and i > 0:
            return t[:i]
    return t


def to_date(value) -> str | None:
    """unix 초, 'YYYY-MM-DD', 'YYYYMMDD', 'YY-MM-DD', '2026.10.31' 등을 'YYYY-MM-DD' 로. 상시·채용시는 None."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit() and len(value) >= 10):
        try:
            return datetime.fromtimestamp(int(value)).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    t = clean(value)
    if any(w in t for w in ("상시", "채용시", "채용 시", "수시")):
        return None
    m = re.search(r"(\d{4})[-./년 ]\s*(\d{1,2})[-./월 ]\s*(\d{1,2})", t) or re.search(r"(\d{4})(\d{2})(\d{2})", t)
    if m:
        y, mo, d = (int(x) for x in m.groups())
    else:
        m = re.search(r"\b(\d{2})[-./](\d{1,2})[-./](\d{1,2})\b", t)
        if not m:
            return None
        y, mo, d = 2000 + int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def split_keywords(text) -> list[str]:
    return [k.strip() for k in re.split(r"[,\n/|·]", clean(text)) if k.strip()]


def _int(v) -> int | None:
    try:
        return int(float(str(v).replace(",", "")))
    except (TypeError, ValueError):
        return None
