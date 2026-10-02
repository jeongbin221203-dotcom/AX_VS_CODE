"""공고 링크로 가져오기 — 사람인·잡코리아·고용24·잡플래닛·링커리어·자소설닷컴·원티드·리멤버.

목록을 대량으로 긁지 않고, 사용자가 붙여 넣은 공고 페이지만 한 장씩 읽는다.
페이지에 있는 구조화 데이터(JSON-LD JobPosting, 구글 채용 검색용)를 먼저 쓰고,
없으면 og:title / og:description 으로 채운다. 허용한 사이트 주소만 받는다(다른 주소로 요청하지 않게).
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from urllib.parse import urlparse

from core.postings import build

from . import SourceError, http_get

SITES = {
    "saramin": ("사람인", ("saramin.co.kr",)),
    "jobkorea": ("잡코리아", ("jobkorea.co.kr",)),
    "work24": ("고용24", ("work24.go.kr",)),
    "jobplanet": ("잡플래닛", ("jobplanet.co.kr",)),
    "linkareer": ("링커리어", ("linkareer.com",)),
    "jasoseol": ("자소설닷컴", ("jasoseol.com",)),
    "wanted": ("원티드", ("wanted.co.kr",)),
    "remember": ("리멤버", ("rememberapp.co.kr",)),
}
# 공고 하나를 가리키는 주소 모양(괄호 = 사이트 공고번호). 첫 화면·목록 주소를 넣으면 공고가 아니라고 알려 준다.
# 공고번호를 저장 키로 써서 API·크롤링·링크로 받은 같은 공고가 하나로 합쳐진다(사람인 API id = rec_idx).
POSTING_URL = {
    "saramin": r"rec_idx=(\d+)",
    "jobkorea": r"/Recruit/GI_Read/(\d+)",
    "work24": r"wantedAuthNo=(\w+)",
    "jobplanet": r"(?:posting_ids(?:\[\]|%5B%5D)?=|/job_postings/|/job/)(\d+)",
    "linkareer": r"/activity/(\d+)",
    "jasoseol": r"/recruit/(\d+)",
    "wanted": r"/wd/(\d+)",
    "remember": r"/job/posting/(\d+)",
}
# 상세를 읽을 때 쓰는 주소. 사람인 relay/view 는 빈 껍데기라 요약·본문·기업정보가 다 있는 jobs/view 를 읽는다
DETAIL_URL = {"saramin": "https://www.saramin.co.kr/zf_user/jobs/view?rec_idx={id}"}
MAX_LINKS = 20
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"


def site_of(url: str) -> str | None:
    try:
        u = urlparse(url.strip())
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not u.hostname:
        return None
    host = u.hostname.lower()
    for key, (_, domains) in SITES.items():
        if any(host == d or host.endswith("." + d) for d in domains):
            return key
    return None


def posting_id(url: str, site: str) -> str | None:
    pat = POSTING_URL.get(site)
    m = re.search(pat, url, re.I) if pat else None
    return m.group(1) if m else None


def detail_url(url: str, site: str) -> str:
    pid = posting_id(url, site)
    return DETAIL_URL[site].format(id=pid) if pid and site in DETAIL_URL else url


def is_posting_url(url: str, site: str) -> bool:
    return posting_id(url, site) is not None


def split_links(text: str) -> list[str]:
    return [t for t in re.split(r"\s+", text or "") if t.startswith(("http://", "https://"))]


def fetch(url: str) -> dict:
    site = site_of(url)
    if not site:
        names = ", ".join(n for n, _ in SITES.values())
        raise SourceError(f"지원하는 사이트 주소가 아닙니다 ({names})")
    if not is_posting_url(url, site):
        raise SourceError(f"{SITES[site][0]} 첫 화면·목록 주소입니다. 공고 하나를 열어 그 주소를 붙여 넣으세요")
    # 일부 사이트는 브라우저가 아닌 요청에 다른 화면을 주므로 일반 브라우저 머리글을 쓴다
    res = http_get(detail_url(url.strip(), site), {}, {"Accept": "text/html", "Accept-Language": "ko-KR,ko;q=0.9",
                                                      "User-Agent": BROWSER_UA})
    return parse(res.text, url.strip(), site)


def parse(page: str, url: str, site: str) -> dict:
    if site == "work24":
        w = _work24_fields(page)
        if w:
            sid = posting_id(url, site) or hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
            return build(site, sid, url=url, **w)
    if site == "saramin" and "jv_summary" in page:
        return _saramin(page, url)
    jp = _job_posting(page)
    og = _meta(page)
    title = company = None
    if jp:
        title = jp.get("title")
        org = jp.get("hiringOrganization")
        company = org.get("name") if isinstance(org, dict) else org
    if not title:
        raw = _strip_site(og.get("og:title") or _title_tag(page) or "")
        title = raw.split(" | ")[0].strip()          # '제목 | 공모전 대외활동' 꼴의 분류 꼬리 제거
    if not title:
        raise SourceError("페이지에서 공고 제목을 찾지 못했습니다 (로그인이 필요한 공고일 수 있음)")
    if not company:
        company = _company_from_title(title, site)
    title = _clean_title(_strip_site(title), company)

    desc = og.get("og:description") or og.get("description")
    s = _summary_fields(desc)                         # '경력:신입, 학력:…, 마감일:…' 요약문
    salary_text, career, education = s.get("salary"), s.get("career"), s.get("education")
    emp, deadline, location = s.get("employment"), s.get("deadline"), s.get("location")
    posted = keywords = None
    if jp:
        location = _location(jp.get("jobLocation")) or location
        if jp.get("jobLocationType") == "TELECOMMUTE" and not location:
            location = "재택근무"
        salary_text = _salary(jp.get("baseSalary") or jp.get("estimatedSalary")) or salary_text
        career = _experience(jp.get("experienceRequirements")) or career
        education = _text(jp.get("educationRequirements")) or education
        emp = _employment(jp.get("employmentType")) or emp
        deadline = jp.get("validThrough") or deadline
        posted = jp.get("datePosted")
        keywords = _text(jp.get("skills")) or _text(jp.get("occupationalCategory"))
        desc = "\n\n".join(filter(None, [jp.get("description"), _text(jp.get("qualifications")),
                                          _text(jp.get("preferredQualifications"))])) or desc

    company_info = None
    if site == "jobkorea" and "모집요강" in page:
        # 잡코리아 표가 구조화 데이터보다 정확하다 (예: 데이터는 FULL_TIME 인데 표는 '계약직')
        x = _jobkorea_table(page)
        emp = x.get("고용형태") or emp
        if _has_amount(x.get("급여")):
            salary_text = x["급여"]
        career, education = x.get("경력") or career, x.get("학력") or education
        posted, deadline = x.get("시작일") or posted, x.get("마감일") or deadline
        lines = [f"{k}: {x[k]}" for k in ("모집분야", "모집인원", "고용형태", "급여", "근무시간", "경력", "학력", "우대사항")
                 if x.get(k)]
        if lines:
            desc = "\n".join(["[모집요강]", *lines, "", desc or "", "(상세 본문은 원문에서 확인하세요 — 이미지로 된 경우가 많습니다)"])
        company_info = {k2: x[k] for k, k2 in (("기업구분", "기업형태"), ("산업(업종)", "업종"), ("사원수", "사원수"),
                                               ("설립", "설립일"), ("매출액", "매출액"), ("위치", "기업주소")) if x.get(k)} or None

    sid = posting_id(url, site) or hashlib.sha1(url.split("#")[0].encode("utf-8")).hexdigest()[:16]
    return build(site, sid, title=title, company=company or "(회사명 확인 필요)", url=url, location=location,
                 career=career, education=education, employment_type=emp, salary_text=salary_text,
                 keywords=keywords, description=desc, deadline=deadline, posted_at=posted,
                 job_category=_text(jp.get("industry")) if jp else None, company_info=company_info)


_JK_LABELS = ("모집분야", "모집인원", "고용형태", "급여", "근무시간", "경력", "학력", "우대사항", "시작일", "마감일",
              "사원수", "기업구분", "산업(업종)", "설립", "매출액", "위치")
_JK_STOP = ("지원자격", "로그인", "TOP", "궁금해요", "접수기간 · 방법", "기업 정보", "기업정보 더보기", "지도보기",
            "모집요강", "💌")


def _jobkorea_table(page: str) -> dict:
    """잡코리아 공고 화면의 모집요강·지원자격·접수기간·기업 정보 표 (라벨 → 값)."""
    toks = _tokens(page)
    try:
        start = toks.index("모집요강")
    except ValueError:
        return {}
    out: dict[str, str] = {}
    cur = None
    for t in toks[start:start + 200]:
        if t in _JK_LABELS and t not in out:
            cur = t
            out[cur] = ""
        elif cur and t in _JK_STOP:
            cur = None
        elif cur and not t.startswith(("<", "class=")):
            out[cur] = (out[cur] + " " + t).strip()
        if "위치" in out and out["위치"] and cur is None:
            break
    if out.get("모집인원"):
        out["모집인원"] = out["모집인원"].replace(" 명", "명")
    return {k: v for k, v in out.items() if v}


# ── 페이지 해석 ─────────────────────────────────────────────

_SAR_SUMMARY = ("경력", "학력", "근무형태", "급여", "근무지역", "근무일시", "직급/직책", "필수사항", "우대사항")
_SAR_SUMMARY_STOP = ("지도보기", "최저임금계산에 대한 알림", "조회수", "상세보기")
_SAR_COMPANY = ("대표자명", "기업형태", "업종", "사원수", "설립일", "매출액", "기업주소", "홈페이지")
_SAR_COMPANY_STOP = ("채용정보", "기업정보 전체보기", "관심기업")


def _section(page: str, cls: str, ends: tuple[str, ...]) -> list[str]:
    i = page.find(f'class="{cls}')
    if i < 0:
        return []
    stops = [j for j in (page.find(e, i + 10) for e in ends) if j > 0]
    toks = _tokens(page[i:min(stops) if stops else i + 30000])
    return [t for t in toks if not t.startswith(("class=", "<", "id="))]


def _pairs(toks: list[str], labels: tuple[str, ...], stops: tuple[str, ...]) -> dict:
    out: dict[str, str] = {}
    cur = None
    for t in toks:
        if t in labels:
            cur = t
            out.setdefault(cur, "")
        elif cur and (t in stops or t.startswith("*")):
            cur = None
        elif cur:
            out[cur] = (out[cur] + " " + t).strip()
    return {k: v for k, v in out.items() if v}


def _saramin(page: str, url: str) -> dict:
    """사람인 jobs/view 페이지: 핵심 정보·본문·접수 기간·기업정보."""
    og = _meta(page)
    raw = _strip_site(og.get("og:title") or _title_tag(page) or "")
    company = _company_from_title(raw, "saramin")
    title = _clean_title(raw.split(" | ")[0].strip(), company)
    if not title:
        raise SourceError("페이지에서 공고 제목을 찾지 못했습니다")
    s = _pairs(_section(page, "jv_cont jv_summary", ('class="jv_cont ',)), _SAR_SUMMARY, _SAR_SUMMARY_STOP)
    howto = _section(page, "jv_cont jv_howto", ('class="jv_cont ',))
    when = {t: howto[i + 1] for i, t in enumerate(howto[:-1]) if t in ("시작일", "마감일")}
    comp = _pairs(_section(page, "jv_cont jv_company", ('class="jv_cont ', "기업리뷰")), _SAR_COMPANY, _SAR_COMPANY_STOP)
    body = [t for t in _section(page, "user_content", ('class="jv_cont jv_howto', 'class="jv_cont '))
            if t not in ("Saramin Recruitment Template",)]
    text = "\n".join(body)
    if len(text) < 40:
        text = (text + "\n" if text else "") + "(본문이 이미지로 되어 있습니다 — 원문에서 확인하세요)"
    summary = _summary_fields(og.get("og:description"))          # 핵심 정보가 비면 요약문으로 보충
    salary_text = s.get("급여") or summary.get("salary")
    if not _has_amount(salary_text):
        salary_text = _salary_from_body(body) or salary_text      # '면접 후 결정'이어도 본문에 금액이 있으면
    if not company:
        company = comp.get("대표자명") and None
    return build(
        "saramin", posting_id(url, "saramin") or hashlib.sha1(url.encode("utf-8")).hexdigest()[:16],
        title=title, company=company or "(회사명 확인 필요)", url=url,
        location=s.get("근무지역") or summary.get("location"),
        career=s.get("경력") or summary.get("career"),
        education=s.get("학력") or summary.get("education"),
        employment_type=s.get("근무형태") or summary.get("employment"),
        salary_text=salary_text,
        keywords=", ".join(filter(None, [s.get("직급/직책")])) or None,
        description=text,
        posted_at=when.get("시작일"),
        deadline=when.get("마감일") or summary.get("deadline"),
        company_info={k: v for k, v in comp.items() if k != "대표자명"} or None,
    )


def _has_amount(text: str | None) -> bool:
    from core import salary
    lo, hi, _ = salary.parse(text)
    return bool(lo or hi)


def _salary_from_body(lines: list[str]) -> str | None:
    """본문의 '급여조건 : 연봉2800만원~3000만원' 같은 줄에서 금액을 찾는다."""
    for line in lines:
        if re.search(r"(급여|연봉|월급|시급|임금)", line) and re.search(r"\d", line) and _has_amount(line):
            return re.sub(r"^[ㆍ·\-\s]*(급여조건|급여|임금)\s*[:：]?\s*", "", line)[:100]
    return None


def _tokens(page: str) -> list[str]:
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "\n", body)).replace("\xa0", " ")
    return [re.sub(r"\s+", " ", t).strip() for t in text.split("\n") if t.strip()]


def _work24_fields(page: str) -> dict | None:
    """고용24 채용정보 상세: 구조화 데이터가 없고 '라벨 → 값' 순서로 적혀 있다.
    회사명·제목은 '조회수' 바로 앞 두 칸. 페이지 모양이 다르면 None (일반 해석으로 넘어감)."""
    toks = _tokens(page)
    try:
        k = toks.index("조회수")
    except ValueError:
        return None
    if k < 2:
        return None

    def after(label: str, skip: tuple = ()) -> str | None:
        for i in range(k, len(toks) - 1):
            if toks[i] == label:
                for v in toks[i + 1:i + 4]:
                    if v != label and v not in skip:
                        return v
        return None

    desc, i = [], toks.index("직무내용", k) + 1 if "직무내용" in toks[k:] else None
    if i:
        while i < len(toks) and toks[i] not in ("더보기", "접기") and len(desc) < 60:
            desc.append(toks[i])
            i += 1
    salary = after("임금") or ""
    pay_type = next((w for w in ("월급", "연봉", "시급", "일급") if salary.startswith(w)), None)
    emp = after("고용형태") or ""
    emp = {"기간의 정함이 없는 근로계약": "정규직", "기간의 정함이 있는 근로계약": "계약직"}.get(emp, emp)
    return {
        "company": toks[k - 2], "title": toks[k - 1],
        "career": after("경력"), "education": after("학력"),
        "salary_text": salary or None, "pay_type": pay_type,
        "location": after("지역"), "employment_type": emp or None,
        "deadline": after("접수 마감일"), "keywords": after("직종 키워드"),
        "description": "\n".join(desc) or None,
    }


def _job_posting(page: str) -> dict | None:
    for block in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(html.unescape(block.strip()))
        except json.JSONDecodeError:
            continue
        for item in _walk(data):
            t = item.get("@type")
            if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                return item
    return None


def _walk(data):
    if isinstance(data, dict):
        yield data
        for v in data.get("@graph", []) if isinstance(data.get("@graph"), list) else []:
            yield from _walk(v)
    elif isinstance(data, list):
        for v in data:
            yield from _walk(v)


def _meta(page: str) -> dict:
    out = {}
    for tag in re.findall(r"<meta\b[^>]*>", page, re.I):
        name = re.search(r'(?:property|name)=["\']([^"\']+)["\']', tag, re.I)
        content = re.search(r'content=["\']([^"\']*)["\']', tag, re.I)
        if name and content:
            out.setdefault(name.group(1).lower(), html.unescape(content.group(1)).strip())
    return out


def _title_tag(page: str) -> str | None:
    m = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
    return html.unescape(m.group(1)).strip() if m else None


_SITE_SUFFIX = re.compile(r"\s*[|\-–—:]\s*(사람인|잡코리아|JOBKOREA|링커리어|LINKareer|자소설닷컴|잡플래닛|JOBPLANET|원티드|Wanted|리멤버|고용24)\b.*$", re.I)


def _strip_site(title: str) -> str:
    return _SITE_SUFFIX.sub("", title).strip()


def _company_from_title(title: str, site: str) -> str | None:
    """'[회사명] 공고 제목' 이나 '회사명 채용 - 공고 제목' 꼴에서 회사명을 짐작한다."""
    t = _strip_site(title)
    m = re.match(r"^\s*(?:\[([^\]]+)\]|【([^】]+)】)", t)     # '[현대오토에버(주)]' 처럼 괄호가 들어 있어도
    if m:
        return (m.group(1) or m.group(2)).strip()
    m = re.match(r"^(.+?)\s*(?:채용|공채|채용공고)\b", t)
    return m.group(1).strip() if m else None


def _clean_title(title: str, company: str | None) -> str:
    """앞의 '[회사명]', 뒤의 '(D-12)'·'(~10/12)' 같은 표시를 뗀다."""
    t = title
    if company:
        t = re.sub(r"^\s*[\[【]" + re.escape(company) + r"[\]】]\s*", "", t)
    t = re.sub(r"\s*\((?:D-?\s*\d+|D-?day|~\s*[\d./]+|오늘\s*마감|내일\s*마감|상시)\)\s*$", "", t, flags=re.I)
    return t.strip() or title


_SUMMARY_KEYS = {
    "career": ("경력",), "education": ("학력",), "salary": ("급여", "연봉", "임금"),
    "deadline": ("마감일", "마감", "접수마감"), "location": ("근무지역", "근무지", "지역"),
    "employment": ("고용형태", "근무형태"),
}


def _summary_fields(desc: str | None) -> dict:
    """'현대오토에버(주), …, 경력:신입, 학력:대학교졸업(4년)이상, 면접 후 결정, 마감일:2026-10-12' 같은 요약문을 나눈다."""
    out: dict = {}
    if not desc:
        return out
    for part in re.split(r"\s*,(?!\d{3}\b)\s*", desc):          # '4,200 만원' 의 쉼표는 나누지 않는다
        if ":" in part:
            key, _, val = part.partition(":")
            key, val = key.strip(), val.strip()
            for field, names in _SUMMARY_KEYS.items():
                if key in names and val and field not in out:
                    out[field] = val
        elif "salary" not in out and re.search(r"(만원|내규|면접\s*후\s*결정|협의)", part):
            out["salary"] = part.strip()
    return out


def _text(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, list):
        return ", ".join(filter(None, (_text(x) for x in v))) or None
    if isinstance(v, dict):
        return _text(v.get("name") or v.get("credentialCategory") or v.get("description"))
    return str(v)


def _location(v) -> str | None:
    if isinstance(v, list):
        v = v[0] if v else None
    if not isinstance(v, dict):
        return _text(v)
    addr = v.get("address") or v
    if isinstance(addr, str):
        return addr
    parts = [addr.get("addressRegion"), addr.get("addressLocality"), addr.get("streetAddress")]
    return " ".join(p for p in parts if p) or None


def _salary(v) -> str | None:
    """schema.org MonetaryAmount → '3000만원~3600만원' 같은 문구 (단위 MONTH/HOUR 는 앞에 붙인다)."""
    if not isinstance(v, dict):
        return _text(v)
    val = v.get("value", v)
    unit = ""
    if isinstance(val, dict):
        unit = (val.get("unitText") or "").upper()
        lo, hi, one = val.get("minValue"), val.get("maxValue"), val.get("value")
    else:
        lo = hi = None
        one = val
    # 연봉을 MONTH·HOUR 로 잘못 적어 둔 사이트가 있다 (링커리어: 월 4,500만원). 말이 안 되는 크기면 연봉으로 본다
    # 잡코리아는 상한에 1만원을 더해 둔다 (화면 '월급 500~1,000만원' → 데이터 maxValue 10,010,000)
    if hi and _num(hi) % 100_000 == 10_000 and _num(hi) > 100_000:
        hi = int(_num(hi)) - 10_000
    top = max((_num(x) for x in (lo, hi, one)), default=0)
    if (unit == "MONTH" and top >= 15_000_000) or (unit in ("HOUR", "DAY") and top >= 1_000_000):
        unit = "YEAR"
    prefix = {"MONTH": "월 ", "HOUR": "시급 ", "DAY": "일급 ", "YEAR": "연봉 "}.get(unit, "")

    def won(n):
        try:
            return f"{int(float(n))}원"
        except (TypeError, ValueError):
            return str(n)

    if lo or hi:
        return prefix + (f"{won(lo)}~{won(hi)}" if lo and hi else (f"{won(lo)} 이상" if lo else f"~{won(hi)}"))
    return prefix + won(one) if one not in (None, "") else None


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0


def _experience(v) -> str | None:
    if isinstance(v, dict):
        months = v.get("monthsOfExperience")
        if months not in (None, ""):
            try:
                years = int(float(months)) // 12
            except (TypeError, ValueError):
                return _text(v)
            return "신입" if years == 0 else f"경력 {years}년 이상"
        return _text(v)
    return _text(v)


def _employment(v) -> str | None:
    names = {"FULL_TIME": "정규직", "PART_TIME": "파트타임", "CONTRACTOR": "계약직", "TEMPORARY": "계약직",
             "INTERN": "인턴", "VOLUNTEER": "봉사", "PER_DIEM": "일용직", "OTHER": "기타"}
    if isinstance(v, list):
        return ", ".join(names.get(str(x).upper(), str(x)) for x in v) or None
    return names.get(str(v).upper(), str(v)) if v else None
