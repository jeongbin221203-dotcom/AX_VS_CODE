"""고용24(옛 워크넷) 채용정보 목록 Open API. XML 응답.

authKey 필요. 한 번에 최대 100건. 기관 사정으로 주소가 바뀌면 JOB_WORK24_URL 로 바꾼다.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import config
from core import salary
from core.postings import build

from . import FetchQuery, SourceError, http_get

# 근무지역 코드(시·도)
REGION = {"서울": "11000", "부산": "26000", "대구": "27000", "인천": "28000", "광주": "29000",
          "대전": "30000", "울산": "31000", "세종": "36110", "경기": "41000", "강원": "51000",
          "충북": "43000", "충남": "44000", "전북": "52000", "전남": "46000", "경북": "47000",
          "경남": "48000", "제주": "50000"}
PAGE_SIZE = 100


def fetch(q: FetchQuery) -> list[dict]:
    if not config.WORK24_KEY:
        raise SourceError("고용24 인증키가 없습니다 (환경변수 JOB_WORK24_KEY)")
    out: list[dict] = []
    for page in range(1, max(1, min(q.pages, config.MAX_PAGES)) + 1):
        params = {"authKey": config.WORK24_KEY, "callTp": "L", "returnType": "XML",
                  "startPage": page, "display": PAGE_SIZE}
        if q.keyword:
            params["keyword"] = q.keyword
        if q.sido in REGION:
            params["region"] = REGION[q.sido]
        if q.career == "신입":
            params["career"] = "N"
        elif q.career == "경력":
            params["career"] = "E"
        items, total = parse(http_get(config.WORK24_URL, params).text)
        out.extend(items)
        if len(out) >= total or not items:
            break
    return out


def parse(xml_text: str) -> tuple[list[dict], int]:
    try:
        root = ET.fromstring(xml_text.strip().encode("utf-8"))
    except ET.ParseError as e:
        raise SourceError(f"고용24 응답을 읽을 수 없습니다: {xml_text[:200]}") from e
    err = (root.text or "").strip() if root.tag == "error" else (root.findtext("error") or root.findtext(".//messageCd"))
    if err and root.find("wanted") is None and root.find(".//wanted") is None:
        raise SourceError(f"고용24 응답 오류: {err} {root.findtext('.//message') or ''}".strip())

    def t(node, tag):
        return (node.findtext(tag) or "").strip()

    out = []
    for w in root.iter("wanted"):
        pay_type = t(w, "salTpNm")
        sal = t(w, "sal")
        # minSal·maxSal 은 원 단위 숫자 — 있으면 문구보다 정확하다
        lo, hi = _won(t(w, "minSal"), pay_type), _won(t(w, "maxSal"), pay_type)
        out.append(build(
            "work24", t(w, "wantedAuthNo"),
            title=t(w, "title"),
            company=t(w, "company"),
            url=t(w, "wantedInfoUrl") or t(w, "wantedMobileInfoUrl"),
            location=t(w, "region") or t(w, "basicAddr"),
            career=t(w, "career"),
            education=t(w, "minEdubg"),
            employment_type=t(w, "empTpNm") or _emp(t(w, "empTpCd")),
            salary_text=sal, pay_type=pay_type,
            salary_min=lo, salary_max=hi,
            job_category=t(w, "jobsNm") or t(w, "indTpNm"),
            keywords=t(w, "jobsNm"),
            posted_at=t(w, "regDt"),
            deadline=t(w, "closeDt"),
        ))
    try:
        total = int(root.findtext("total") or len(out))
    except ValueError:
        total = len(out)
    return out, total


def _won(value: str, pay_type: str) -> int | None:
    """원 단위 숫자를 연봉 만원으로. 0·빈 값은 None."""
    if not value.isdigit() or int(value) == 0:
        return None
    return salary.parse(f"{int(value)}원", pay_type)[0]


def _emp(code: str) -> str:
    # 10 기간의 정함이 없는 근로계약, 11 (시간선택제), 20 기간의 정함이 있는 근로계약, 4 파견, 21 (시간선택제)
    return {"10": "정규직", "11": "정규직(시간선택제)", "20": "계약직", "21": "계약직(시간선택제)",
            "4": "파견직"}.get(code, "")
