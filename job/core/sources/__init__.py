"""공고 출처 어댑터 모음.

각 어댑터는 fetch(query) → 공통 공고 dict 목록(core.postings.build 결과)을 돌려준다.
응답 해석(parse_*)은 네트워크와 떼어 두어 저장해 둔 응답으로 시험할 수 있다.
"""
from __future__ import annotations

from dataclasses import dataclass

import requests

import config


@dataclass
class FetchQuery:
    keyword: str = ""
    sido: str = ""            # 짧은 시·도 이름 ('서울'), 비우면 전국
    career: str = ""          # '' / 신입 / 경력
    pages: int = 1


class SourceError(Exception):
    """출처에서 공고를 받지 못함 (키 없음, 응답 오류 등). 화면에 그대로 보여 준다."""


def http_get(url: str, params: dict, headers: dict | None = None) -> requests.Response:
    h = {"User-Agent": config.USER_AGENT}
    h.update(headers or {})
    try:
        res = requests.get(url, params=params, headers=h, timeout=config.HTTP_TIMEOUT)
    except requests.RequestException as e:
        raise SourceError(f"연결 실패: {e.__class__.__name__}") from e
    if res.status_code >= 400:
        raise SourceError(f"HTTP {res.status_code}: {res.text[:200]}")
    return res


def registry() -> list[dict]:
    """화면에 보여 줄 출처 목록과 사용 가능 여부."""
    from . import saramin, wanted, work24
    return [
        {"key": "saramin", "name": "사람인", "kind": "공식 Open API", "ready": bool(config.SARAMIN_KEY),
         "need": "JOB_SARAMIN_KEY (oapi.saramin.co.kr 에서 access-key 발급)", "fetch": saramin.fetch},
        {"key": "work24", "name": "고용24(워크넷)", "kind": "공식 Open API", "ready": bool(config.WORK24_KEY),
         "need": "JOB_WORK24_KEY (고용24 Open API 인증키 발급)", "fetch": work24.fetch},
        {"key": "wanted", "name": "원티드", "kind": "비공식 JSON", "ready": config.WANTED_ENABLED,
         "need": "JOB_WANTED_ENABLED=1 (공개 API가 아님 — 약관 확인 후 개인 용도로만)", "fetch": wanted.fetch},
    ]


SOURCE_NAMES = {"saramin": "사람인", "work24": "고용24", "wanted": "원티드", "jobkorea": "잡코리아",
                "linkareer": "링커리어", "jasoseol": "자소설닷컴", "remember": "리멤버", "jobplanet": "잡플래닛", "incruit": "인크루트",
                "csv": "파일 가져오기", "manual": "직접 등록", "sample": "샘플"}

# 사이트별로 공고를 받는 방법 (수집 화면 안내표): 키, 이름, 방법, 참고, 사이트 주소
SITE_METHODS = [
    ("saramin", "사람인", "Open API 자동 수집 + 링크 + CSV",
     "링크는 요약(경력·학력·급여·마감)만 읽힘 — 근무지는 원문에서 확인", "https://www.saramin.co.kr/zf_user/"),
    ("jobkorea", "잡코리아", "링크 + CSV", "링크에서 근무지·경력·학력·마감·급여까지 읽힘", "https://www.jobkorea.co.kr/"),
    ("work24", "고용24(워크넷)", "Open API 자동 수집 + 링크", "공식 API(인증키 필요)", "https://www.work24.go.kr/cm/main.do"),
    ("jobplanet", "잡플래닛", "링크 + CSV", "기업 리뷰·평균연봉 참고 — 평균연봉은 공고 상세에 직접 입력",
     "https://www.jobplanet.co.kr/welcome/index"),
    ("linkareer", "링커리어", "링크 + CSV", "대외활동·인턴 공고가 많음. 링크는 제목·설명만 읽힘", "https://linkareer.com/"),
    ("jasoseol", "자소설닷컴", "링크 + CSV", "대기업 공채 달력", "https://jasoseol.com/"),
    ("wanted", "원티드", "비공식 JSON 수집(설정 시) + 링크 + CSV", "목록 수집 시 연봉은 '미공개'", "https://www.wanted.co.kr/"),
    ("remember", "리멤버", "링크 + CSV", "경력직 공고 중심. 링크에서 근무지·경력·마감·자격요건까지 읽힘",
     "https://career.rememberapp.co.kr/job/postings"),
]
