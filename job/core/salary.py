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

MAX_CONVERT_HOURLY = 30_000      # 이보다 높은 시급은 연봉으로 환산하지 않음 (시간제·전문직)
MAX_CONVERTED = 30_000           # 월·시급·일급을 연봉으로 바꾼 값이 이 만원(3억)을 넘으면 버림
_ALLOWANCE = re.compile(r"(식대|수당|교통비|상여|보너스|인센티브|복지|장려금|축하금|지원금|성과급|면접비|숙소비|중식|"
                        r"요금|통신비|휴대폰|건당|건별|편당|회당|가능시|가능 시|인상|상금|포인트|식권)")
PLACEHOLDER_TOP = 10_000         # 범위 위쪽이 딱 1억이고 아래쪽이 5천 이하면 위쪽은 의미 없는 끝값
MIN_WAGE_YEARS = (2_516, 2_588)  # 최저임금 연봉(만원): 2025년 25,155,240원 · 2026년 25,882,560원
MIN_ANNUAL = 300                 # 연봉 환산이 이보다 작으면(만원) 버림 — 주 몇 시간 파트도 연 500만원 안팎
ABSURD_TOP = 10_000              # 범위의 위쪽이 1억 이상이면서 아래쪽의 5배를 넘으면 위쪽은 믿지 않음
MONTHLY_AS_ANNUAL = 1500 * 10**4  # '월급 3,300만원'처럼 월 1,500만원 이상이면 연봉을 월급으로 잘못 적은 것으로 봄
_PERIOD_KEYS = [(re.compile(r"시급|시간당"), "hour"), (re.compile(r"일급|일당"), "day"),
                (re.compile(r"월급|월평균|매월|/월|월(?=\d)|월(?=약\d)|실수령"), "month"),
                (re.compile(r"연봉|평균연봉|연평균|년봉|연(?=\d)|연(?=약\d)"), "year")]

_NOT_MONEY = re.compile(r"(%|퍼센트|프로|개월|개|명|세|살|년|시간|시|분|층|호|회|차|주|일|건|평|km|kg)")
_TOKEN = re.compile(r"(\d+(?:\.\d+)?)(억|천만|천|백만|백|만원|만|원)?")
_UNIT = {"억": 10**8, "천만": 10**7, "백만": 10**6, "만원": 10**4, "만": 10**4, "원": 1}


def parse(text, pay_type: str | None = None) -> tuple[int | None, int | None, bool]:
    """(최소, 최대, 협의 여부). 금액은 연봉 환산 만원. 모르면 None."""
    raw = clean(text)
    hint = clean(pay_type)
    if not raw:
        return None, None, False
    # '회사 내규에 따름 … 서초대로 301, 16~18F' 처럼 협의 문구에 붙은 숫자(주소·층)는 금액이 아니다:
    # 협의 문구가 있고 돈 단위(원·만원·억)가 없으면 협의로 본다
    # (단위 없이 '25,882,560' 처럼 쓴 백만 단위 이상 금액은 돈으로 본다 — 주소·층 숫자는 작다)
    has_money = re.search(r"\d[\d,.]*\s*(억|천만|만\s*원|만|원)", raw) or re.search(r"\d{1,3}(,\d{3}){2,}|\d{7,}", raw)
    if any(w in raw for w in NEGOTIABLE_WORDS) and not has_money:
        return None, None, True
    items, t = _amounts(raw)
    kept, prev_skip = [], None
    for won, pos in items:                              # 식대·수당 등은 연봉이 아님
        if kept and "+" in t[kept[-1][1]:pos]:
            continue                                    # '기본 3,064만원 + 인센티브 평균 250만원': + 뒤는 덧붙는 돈
        if _is_allowance(t, pos) or (prev_skip is not None and pos - prev_skip <= 12
                                     and re.fullmatch(r"[\d.]*(만원|만|원)?~", t[prev_skip:pos])):
            prev_skip = pos                             # '인센티브(평균 15~20만원)' 의 20 도 수당
            continue
        prev_skip = None
        kept.append((won, pos))
    items = kept
    if not items:
        return None, None, any(w in raw for w in NEGOTIABLE_WORDS)

    keys = _period_keys(t)
    values = []
    dropped = False
    for won, pos in items:
        if won >= 10**10:
            won /= 10**4                                # '월급 2,236,300만원' — 원을 만원으로 잘못 적음 (100억 이상은 없음)
        near = _period_at(keys, pos)
        period = near or _period(hint + " " + raw, [w for w, _ in items])
        if not near and period == "hour" and won >= 100_000:
            # '기본급 2,156,880원 … / 특근시급 15,480원': 글 어딘가의 '시급' 때문에 시급으로 짐작했지만 10만원 넘는 시급은 없음
            period = "month" if won < MONTHLY_AS_ANNUAL else "year"
        if period == "month" and won >= MONTHLY_AS_ANNUAL:
            period = "year"
        if period == "hour" and won > MAX_CONVERT_HOURLY:
            continue                                    # 시간제·전문직 높은 시급은 풀타임 연봉으로 바꾸지 않음
        factor = {"hour": HOURS_PER_MONTH * 12, "day": WORKDAYS_PER_MONTH * 12, "month": 12, "year": 1}[period]
        v = round(won * factor / 10**4)
        if period == "year" and any(abs(v - m * 10) <= 15 for m in MIN_WAGE_YEARS):
            v = round(v / 10)                           # '25,882만원' = 최저임금 연봉 25,882,560원을 만원 단위로 잘못 적음
        if v < MIN_ANNUAL:
            continue                                    # 연 300만원 미만은 금액을 잘못 읽은 것 ('NET 280' 을 280원으로 등)
        if period != "year" and v > MAX_CONVERTED:
            dropped = True
            continue                                    # 월·시급을 바꾼 값이 3억을 넘으면 성과급 문구 등 — 버림
        values.append(v)
    if not values:
        return None, None, any(w in raw for w in NEGOTIABLE_WORDS)

    compact = raw.replace(" ", "")
    if dropped and len(values) == 1:
        return values[0], None, False                  # 범위의 위쪽을 버렸으면 '이상'으로
    if len(values) >= 2 and values[0] != values[1]:
        lo, hi = sorted(values[:2])                    # 첫 범위만 (뒤의 '10~20만원 추가' 같은 금액은 무시)
        if hi == PLACEHOLDER_TOP and lo <= 1_000:
            return None, None, False                   # 리멤버 '1000~10000만원': 입력 칸 처음값~끝값 그대로 = 미공개
        if hi >= ABSURD_TOP and hi > lo * 5:
            return lo, None, False                     # '3,000~50,000만원' 처럼 위쪽이 터무니없으면 '이상'으로
        if hi == PLACEHOLDER_TOP and lo <= PLACEHOLDER_TOP // 2:
            return lo, None, False                     # 잡코리아 '25,900,000원~100,000,000원': 1억은 입력 칸의 끝값 — '이상'으로
    elif len(values) >= 2:
        lo = hi = values[0]
        if re.search(r"(이상|↑|(?:원|만|\d)부터|최소)", compact):
            hi = None
    elif re.search(r"(이상|↑|(?:원|만|\d)부터|최소)", compact):
        lo, hi = values[0], None
    elif re.search(r"(이하|까지|최대|↓)", compact) or compact.lstrip("연봉월급시급:").startswith("~"):
        lo, hi = None, values[0]
    else:
        lo = hi = values[0]
    return lo, hi, False


_COMMISSION = re.compile(r"위촉|프리랜서|개인사업자|도급계약|FC(?![A-Za-z])|설계사")


def is_commission(p: dict) -> bool:
    """실적에 따라 받는 직군(위촉직·프리랜서·개인사업자) — 공고의 연봉 범위는 성과급 예시라 비교에 쓰지 않는다."""
    return bool(_COMMISSION.search(str(p.get("employment_type") or "")) or
                re.search(r"위촉|설계사", str(p.get("title") or "")))


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


def _period_keys(t: str) -> list[tuple[int, str]]:
    return sorted((m.end(), period) for rx, period in _PERIOD_KEYS for m in rx.finditer(t))


def _period_at(keys: list[tuple[int, str]], pos: int, window: int = 20) -> str | None:
    """금액 바로 앞(window 글자 안)에서 가장 가까운 '시급·월·연봉' 단어."""
    before = [(k, p) for k, p in keys if k <= pos and pos - k <= window]
    return before[-1][1] if before else None


def _is_allowance(t: str, pos: int) -> bool:
    """금액 바로 앞(8글자)에 식대·수당 같은 말이 있으면 추가 금액. 단 '수당 포함 월평균 300만원' 처럼
    그 뒤에 '포함'이나 '월·연봉' 단어가 다시 나오면 본급이다."""
    m = re.search(r"[\d.]+(만원|만|원)?~$", t[:pos])        # '인센티브(평균 15~20만원)': 범위 앞부분부터 본다
    if m:
        pos = m.start()
    window = t[max(0, pos - 8):pos]
    last = None
    for m in _ALLOWANCE.finditer(window):
        last = m
    if not last:
        return False
    rest = window[last.end():]
    if "포함" in rest or any(rx.search(rest) for rx, _ in _PERIOD_KEYS):
        return False
    return True


def _amount_groups(text: str) -> list[float]:
    return [w for w, _ in _amounts(text)[0]]


def _amounts(text: str) -> tuple[list[tuple[float, int]], str]:
    """붙어 있는 숫자·단위 조각을 하나의 금액(원)으로 묶어 (금액, 위치) 목록과 정리한 글을 돌려준다.
    단위 없는 숫자는 뒤 금액의 단위를 따른다."""
    t = re.sub(r"(\d)\.(\d{3})(?!\d)", lambda m: m.group(1) + m.group(2), text)          # '시급 25.000원' 의 점은 천 단위 구분
    t = t.replace(",", "").replace(" ", "")
    t = re.sub(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}", "", t)          # 날짜 제거
    t = re.sub(r"(주|하루|일)\d+(시간|일)", "", t)                # 근무시간 표기 제거
    groups: list[list[tuple[float, str | None]]] = []
    starts: list[int] = []
    ends: list[int] = []
    prev_end = -1
    for m in _TOKEN.finditer(t):
        if m.group(1) in ("", "."):
            continue
        if not m.group(2) and _NOT_MONEY.match(t, m.end()):
            prev_end = -1
            continue                                    # '100%'·'3개월'·'2명'·'9시' 는 금액이 아님
        tok = (float(m.group(1)), m.group(2))
        if groups and m.start() == prev_end and groups[-1][-1][1] not in (None, "원", "만원", "만"):
            groups[-1].append(tok)
            ends[-1] = m.end()
        else:
            groups.append([tok])
            starts.append(m.start())
            ends.append(m.end())
        prev_end = m.end()

    amounts: list[float | None] = []
    units: list[int | None] = []
    for gi, g in enumerate(groups):
        has_man = any(u in ("만", "만원", "억", "천만", "백만") for _, u in g)
        total, unit_seen = 0.0, None
        for n, u in g:
            if u == "천":
                # '3천 중후반'·'4천 이상' 처럼 천만 단위를 줄여 쓴 것 (뒤에 '원' 이 붙은 '3천원' 은 그대로)
                mul = 10**7 if has_man or len(g) > 1 or (n < 10 and not t[ends[gi]:].startswith("원")) else 10**3
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
            result.append((a, starts[i]))
            continue
        n = groups[i][0][0]
        nxt = next((units[j] for j in range(i + 1, len(units)) if units[j]), None)
        if n < 100 and nxt not in (10**8, 10**7):
            continue                                    # '수습 3개월'의 3, '1~2년'의 1 같은 작은 맨숫자는 금액이 아님
        if n < 100:
            result.append((n * nxt, starts[i]))         # '3~4천만원' 의 3 → 3천만원
            continue
        if nxt in (10**4, 10**8, 10**7, 10**6):
            result.append((n * 10**4 if nxt != 10**8 else n * 10**8, starts[i]))
        elif nxt == 1 or n >= 100000:
            result.append((n, starts[i]))
        elif n >= 100:
            result.append((n * 10**4, starts[i]))                    # '3000' → 3000만원
        # 100 미만의 맨숫자(년수·시간 등)는 금액으로 보지 않는다
    return [(w, pos) for w, pos in result if w and w > 0], t


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
