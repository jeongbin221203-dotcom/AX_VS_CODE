"""제외 항목: 이 항목에 걸리는 공고는 목록·요약에서 숨기고 '지원 불가'로 본다.

- 미리 정한 항목(PRESETS)은 같은 뜻의 여러 표현을 함께 찾는다. 예) 수리기사 → '출장 수리 엔지니어', 'A/S기사'.
- 그 밖의 단어는 그 단어 자체를 찾는다.
- 제목·회사·키워드·고용형태·사이트 표시(헤드헌팅 목록 등)만 본다. 긴 설명까지 보면 'A/S 접수'처럼
  업무 설명에 스친 단어로 엉뚱하게 걸린다. 띄어쓰기는 무시한다.
"""
from __future__ import annotations

import re

PRESETS: dict[str, tuple[str, ...]] = {
    "헤드헌팅": ("헤드헌팅", "헤드헌터", "서치펌", "채용대행"),
    "수리기사": ("수리기사", "수리 기사", "수리 엔지니어", "수리 서비스", "수리 담당", "수리원", "A/S기사", "AS기사",
              "A/S 엔지니어", "AS 엔지니어", "출장 수리"),
    "파견·도급": ("파견", "도급", "아웃소싱"),
    "교대근무": ("교대근무", "2교대", "3교대", "4조2교대", "4조3교대"),
    "텔레마케팅": ("텔레마케팅", "TM", "아웃바운드"),
    "보험설계사": ("보험설계사", "FC 모집", "재무설계사", "보험영업"),
    "아르바이트": ("아르바이트", "알바"),
    "계약직": ("계약직", "기간제"),
    # '운전'만 찾으면 '시운전·O&M(플랜트)', '현장설비운전' 같은 설비 직무까지 걸려 운전 직무만 찾는다
    "운전": ("운전기사", "운전 기사", "운전직", "운전원 모집", "수행기사", "드라이버", "운전 및 배송"),          # 판단은 _contract_only: 정규직도 함께 뽑으면 제외하지 않음
}


def _norm(s: str) -> str:
    return "".join(str(s).split()).lower()


def matches(p: dict, words: list[str]) -> list[str]:
    """공고가 걸린 제외 항목 이름들."""
    if not words:
        return []
    # 사이트 직무 이름은 보지 않는다: 사람인은 한 공고를 여러 직무에 올려 '고객상담·TM' 목록의 건설 공고가
    # 텔레마케팅으로 걸린다
    text = _norm(" ".join(str(p.get(k) or "") for k in
                          ("title", "company", "keywords", "employment_type", "flags")))
    hits = []
    for w in words:
        if w == "계약직":
            if _contract_only(p):
                hits.append(w)
            continue
        terms = PRESETS.get(w, (w,))
        if any(_hit(text, t) for t in terms):
            hits.append(w)
    return hits


def _contract_only(p: dict) -> bool:
    """계약직(기간제)으로만 뽑는 공고. '정규직, 계약직'처럼 정규직 자리도 있으면 제외하지 않는다.
    '계약직 (정규직 전환 가능)'은 계약직으로 시작하므로 제외한다."""
    emp = str(p.get("employment_type") or "")
    options = [o.strip() for o in emp.replace("·", ",").replace("/", ",").split(",") if o.strip()]
    has_regular = any(o.startswith("정규직") for o in options)
    contract = any(o.startswith(("계약직", "기간제")) for o in options)
    title = _norm(p.get("title") or "")
    if not options:                                  # 고용형태가 없으면 제목으로 판단
        return ("계약직" in title or "기간제" in title) and "정규직" not in title
    if not contract and ("계약직" in title or "기간제" in title):
        contract = True
    return contract and not has_regular


def _hit(text: str, term: str) -> bool:
    t = _norm(term)
    if not t:
        return False
    if t.isascii() and t.isalpha() and len(t) <= 3:          # 'TM' 같은 짧은 영문은 단어로만
        return re.search(rf"(?<![a-z]){re.escape(t)}(?![a-z])", text) is not None
    return t in text
