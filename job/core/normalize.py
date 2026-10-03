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
    text = re.sub(r"^(대한민국|한국|South\s*Korea|Korea)\s*", "", text, flags=re.I)   # '대한민국 서울특별시 …'
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


def region_from_title(title) -> tuple[str | None, str | None]:
    """근무지가 없는 공고의 제목 괄호에서: '[서울 역삼역] …', '(대전근무)', '[부산근무]' → ('서울', None)."""
    t = clean(title)
    for seg in re.findall(r"[\[(【<]([^\])】>]{1,30})[\])】>]", t):
        seg = re.sub(r"(근무지?|지역)\s*[:：]?", " ", seg).strip()
        sido, sigungu = parse_region(seg)
        if sido and sido not in ("재택",):
            return sido, sigungu
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


_EMP_TYPES = [("무기계약직", "무기계약직"), ("정규직", "정규직"), ("계약직", "계약직"), ("기간제", "기간제"),
              ("전환형 인턴", "전환형 인턴"), ("인턴", "인턴"), ("파견", "파견직"), ("프리랜서", "프리랜서"),
              ("위촉", "위촉직"), ("개인사업자", "개인사업자"), ("도급", "도급"), ("용역", "용역"), ("아르바이트", "아르바이트"),
              ("파트타임", "파트타임"), ("교육생", "교육생"), ("연수생", "연수생"), ("병역특례", "병역특례"), ("전임", "전임")]


def parse_employment(text) -> str | None:
    """'정규직 수습기간 3개월 자격요건 자격요건', '정규직, 계약직, 계약직' → '정규직 (수습 3개월)', '정규직, 계약직'.
    사이트 화면의 이름표(자격요건·직급/직책·1건)가 붙어 들어온 것을 떼고 고용형태와 수습·근무기간만 남긴다."""
    t = clean(text)
    if not t:
        return None
    rest = t
    found: list[tuple[int, str]] = []
    rest = re.sub(r"정규직\s*(?=전환)", lambda m: " " * len(m.group(0)), rest)   # '정규직 전환 가능' 은 고용형태가 아님
    for word, name in _EMP_TYPES:
        for m in re.finditer(re.escape(word), rest):
            found.append((m.start(), name))
        rest = rest.replace(word, " " * len(word))              # '무기계약직' 안의 '계약직' 을 다시 세지 않게
    types = list(dict.fromkeys(n for _, n in sorted(found)))
    if "전환형 인턴" in types and "인턴" in types:
        types.remove("인턴")
    notes = []
    m = re.search(r"수습(?:기간)?\s*[:：]?\s*(\d+\s*개월)", t) or re.search(r"(\d+\s*개월)\s*수습", t)
    if m:
        notes.append("수습 " + m.group(1).replace(" ", ""))
    m = re.search(r"(?:근무|계약)기간\s*[:：]?\s*((?:\d+\s*년)?\s*(?:\d+\s*개월)?)", t)
    if m and m.group(1).strip():
        notes.append("근무기간 " + m.group(1).replace(" ", ""))
    if re.search(r"정규직\s*전환", t):
        notes.append("정규직 전환 가능")
    if not types:
        return t[:100]                                           # 모르는 표기는 그대로
    return ", ".join(types) + (f" ({' · '.join(notes)})" if notes else "")


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
    if y >= 2100:
        return None                                   # '9999-01-01' 은 상시 채용 표시
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
