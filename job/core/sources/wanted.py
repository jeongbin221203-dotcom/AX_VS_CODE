"""원티드 채용 목록 (웹이 쓰는 비공식 JSON).

공개 API가 아니므로 기본은 꺼져 있다(JOB_WANTED_ENABLED=1 로 켬). 형식이 바뀌면 parse 가 SourceError 를 낸다.
연봉은 목록에 없어 '미공개'로 들어간다.
"""
from __future__ import annotations

import config
from core.postings import build

from . import FetchQuery, SourceError, http_get

PAGE_SIZE = 100
# 원티드 지역 코드
LOCATIONS = {"서울": "seoul.all", "경기": "gyeonggi.all", "인천": "incheon.all", "부산": "busan.all",
             "대구": "daegu.all", "대전": "daejeon.all", "광주": "gwangju.all", "울산": "ulsan.all",
             "세종": "sejong.all", "강원": "gangwon.all", "충북": "chungbuk.all", "충남": "chungnam.all",
             "전북": "jeonbuk.all", "전남": "jeonnam.all", "경북": "gyeongbuk.all", "경남": "gyeongnam.all",
             "제주": "jeju.all"}


def fetch(q: FetchQuery) -> list[dict]:
    if not config.WANTED_ENABLED:
        raise SourceError("원티드는 꺼져 있습니다 (JOB_WANTED_ENABLED=1)")
    out: list[dict] = []
    for page in range(max(1, min(q.pages, config.MAX_PAGES))):
        params = {"country": "kr", "job_sort": "job.latest_order", "limit": PAGE_SIZE,
                  "offset": page * PAGE_SIZE, "locations": LOCATIONS.get(q.sido, "all"),
                  "years": 0 if q.career == "신입" else -1}
        items = parse(http_get(config.WANTED_URL, params, {"Accept": "application/json"}).json())
        if q.keyword:
            kw = q.keyword.lower()
            items = [i for i in items if kw in (i["title"] + " " + (i["keywords"] or "")).lower()]
        out.extend(items)
        if not items and page:
            break
    return out


def parse(data: dict) -> list[dict]:
    rows = data.get("data")
    if not isinstance(rows, list):
        raise SourceError("원티드 응답 형식이 바뀌었습니다")
    out = []
    for j in rows:
        addr = j.get("address") or {}
        lo, hi = j.get("annual_from"), j.get("annual_to")
        if lo in (None, "") and hi in (None, ""):
            career = ""
        elif not lo:
            career = "신입" if hi in (0, 1) else f"신입/경력 ~{hi}년"
        else:
            career = f"경력 {lo}~{hi}년" if hi and hi < 100 else f"경력 {lo}년 이상"
        out.append(build(
            "wanted", j.get("id"),
            title=j.get("position"),
            company=(j.get("company") or {}).get("name"),
            url=f"https://www.wanted.co.kr/wd/{j.get('id')}",
            location=" ".join(filter(None, [addr.get("location"), addr.get("district")])) or addr.get("full_location"),
            career=career,
            keywords=[t.get("title") for t in (j.get("category_tags") or j.get("skill_tags") or [])
                      if isinstance(t, dict)],
            deadline=j.get("due_time"),
        ))
    return out
