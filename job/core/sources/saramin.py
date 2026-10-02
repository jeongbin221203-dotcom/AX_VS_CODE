"""사람인 Open API (https://oapi.saramin.co.kr/guide/job-search).

access-key 필요. 한 번에 최대 110건, 하루 호출 수 제한이 있어 페이지 수를 config.MAX_PAGES 로 묶는다.
"""
from __future__ import annotations

import config
from core.postings import build

from . import FetchQuery, SourceError, http_get

# 사람인 근무지 상위 코드(loc_mcd)
LOC_MCD = {"서울": "101000", "경기": "102000", "광주": "103000", "대구": "104000", "대전": "105000",
           "부산": "106000", "울산": "107000", "인천": "108000", "강원": "109000", "경남": "110000",
           "경북": "111000", "전남": "112000", "전북": "113000", "충북": "114000", "충남": "115000",
           "제주": "116000", "세종": "118000"}
# 경력 코드: 1 신입, 2 경력, 3 신입/경력, 0 경력무관
EXP_NAME = {"1": "신입", "2": "경력", "3": "신입/경력", "0": "경력무관"}
PAGE_SIZE = 110


def fetch(q: FetchQuery) -> list[dict]:
    if not config.SARAMIN_KEY:
        raise SourceError("사람인 access-key 가 없습니다 (환경변수 JOB_SARAMIN_KEY)")
    out: list[dict] = []
    for page in range(max(1, min(q.pages, config.MAX_PAGES))):
        params = {"access-key": config.SARAMIN_KEY, "count": PAGE_SIZE, "start": page, "sort": "pd",
                  "fields": "posting-date,expiration-date,keyword-code,count"}
        if q.keyword:
            params["keywords"] = q.keyword
        if q.sido in LOC_MCD:
            params["loc_mcd"] = LOC_MCD[q.sido]
        if q.career == "신입":
            params["exp_cd"] = "1"
        elif q.career == "경력":
            params["exp_cd"] = "2"
        data = http_get(config.SARAMIN_URL, params, {"Accept": "application/json"}).json()
        items, total = parse(data)
        out.extend(items)
        if len(out) >= total or not items:
            break
    return out


def parse(data: dict) -> tuple[list[dict], int]:
    if "jobs" not in data:
        msg = data.get("message") or data.get("error") or str(data)[:200]
        raise SourceError(f"사람인 응답 오류: {msg}")
    jobs = data["jobs"]
    rows = jobs.get("job") or []
    if isinstance(rows, dict):
        rows = [rows]
    out = []
    for j in rows:
        pos = j.get("position") or {}
        exp = pos.get("experience-level") or {}
        company = ((j.get("company") or {}).get("detail") or {}).get("name")
        sal = j.get("salary") or {}
        close = (j.get("close-type") or {}).get("code")
        out.append(build(
            "saramin", j.get("id"),
            title=pos.get("title"),
            company=company,
            url=j.get("url"),
            location=(pos.get("location") or {}).get("name"),
            career=exp.get("name") or EXP_NAME.get(str(exp.get("code")), ""),
            career_min=exp.get("min"), career_max=exp.get("max"),
            education=(pos.get("required-education-level") or {}).get("name"),
            employment_type=(pos.get("job-type") or {}).get("name"),
            salary_text=sal.get("name"), pay_type="연봉",
            job_category=", ".join(filter(None, [(pos.get("job-mid-code") or {}).get("name"),
                                                 (pos.get("industry") or {}).get("name")])),
            keywords=j.get("keyword") or (pos.get("job-code") or {}).get("name"),
            posted_at=j.get("posting-timestamp") or j.get("posting-date"),
            # 상시(3)·채용시 마감(4)은 마감일 없음으로 둔다
            deadline=None if str(close) in ("3", "4") else (j.get("expiration-timestamp") or j.get("expiration-date")),
        ))
    try:
        total = int(jobs.get("total") or len(out))
    except (TypeError, ValueError):
        total = len(out)
    return out, total
