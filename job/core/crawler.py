"""정기 크롤링 (기본 4시간마다).

한 번 실행하면:
  1) 저장한 검색어로 사람인·잡코리아·링커리어 검색 결과 페이지를 읽어 공고 번호를 모은다.
  2) 처음 보는 공고만 상세 페이지를 읽어(linkimport.parse) 저장한다. 목록에만 있는 근무지·경력 등은 보충값으로 쓴다.
  3) 이미 저장한 공고 중 아직 마감 전인 것을 다시 읽어 내용·마감을 갱신하고, 사라진 공고(404·410)는 마감 처리한다.
  4) API 키가 있으면 사람인·고용24 Open API 도 같은 검색어로 받는다.

예의 규칙: robots.txt 를 따르고, 같은 사이트 요청 사이에 config.CRAWL_DELAY 초를 쉬며(사이트끼리는 동시에 읽음), 사이트마다 한 번에 읽는 상세 페이지 수를 제한한다.
여러 프로세스가 동시에 돌지 않도록 DB 잠금(settings.crawl_lock)을 쓴다.
"""
from __future__ import annotations

import html
import json
import logging
import re
import threading
import time
import urllib.robotparser
from datetime import date, datetime, timedelta
from urllib.parse import quote, urlparse

import requests

import config

from . import collect, db, postings
from .normalize import SIDO_ORDER, clean
from .sources import FetchQuery, SourceError, linkimport, registry

log = logging.getLogger("job.crawler")

# 검색 결과 페이지에서 공고 번호를 모을 수 있는 사이트 (자소설닷컴·잡플래닛·리멤버는 목록을 브라우저에서 그려 제외)
LIST_SITES = {
    "saramin": {
        "search": "https://www.saramin.co.kr/zf_user/search/recruit?searchword={kw}&recruitPage={page}&recruitSort=reg_dt",
        # 검색어 없는 검색은 페이지를 넘겨도 같은 20건만 나와서, 모든 직무일 때는 채용정보 목록(페이지당 약 70건)을 쓴다
        "latest": "https://www.saramin.co.kr/zf_user/jobs/list/domestic?page={page}&sort=RD&page_count=100",
        "link": r'rec_idx=(\d+)',
        "item": r'class="item_recruit"',                       # 공고 한 건의 시작 표시 (보충값을 이 구간에서 읽음)
        "detail": "https://www.saramin.co.kr/zf_user/jobs/view?rec_idx={id}",   # 요약·본문·기업정보가 다 있는 주소
        "category": "https://www.saramin.co.kr/zf_user/jobs/list/job-category?cat_mcls={code}&page={page}&page_count=100&sort=RD",
        "category_pages": 10,
        "categories": [
            ("2", "IT개발·데이터"), ("3", "회계·세무·재무"), ("4", "총무·법무·사무"), ("5", "인사·노무·HRD"),
            ("6", "의료"), ("7", "운전·운송·배송"), ("8", "영업·판매·무역"), ("9", "연구·R&D"), ("10", "서비스"),
            ("11", "생산"), ("12", "상품기획·MD"), ("13", "미디어·문화·스포츠"), ("14", "마케팅·홍보·조사"),
            ("15", "디자인"), ("16", "기획·전략"), ("17", "금융·보험"), ("18", "구매·자재·물류"), ("19", "교육"),
            ("20", "공공·복지"), ("21", "고객상담·TM"), ("22", "건설·건축"),
        ],
    },
    "jobkorea": {
        "search": "https://www.jobkorea.co.kr/Search/?stext={kw}&tabType=recruit&Page_No={page}&Ord=RegDtDesc",
        "link": r'/Recruit/GI_Read/(\d+)',
        "item": None,
        "detail": "https://www.jobkorea.co.kr/Recruit/GI_Read/{id}",
        # 직무 목록은 페이지 번호를 바꿔도 결과가 거의 같아 직무마다 1페이지(약 170건)만 읽는다
        "category": "https://www.jobkorea.co.kr/recruit/joblist?menucode=duty&dutyCtgr={code}",
        "category_pages": 1,
        # 그래서 전체 최신순 검색(페이지당 20건, 페이지가 넘어감)으로 나머지를 모은다 — newest_scan
        "newest": "https://www.jobkorea.co.kr/Search/?stext=&tabType=recruit&Page_No={page}&Ord=RegDtDesc",
        "categories": [
            ("10026", "기획·전략"), ("10027", "법무·사무·총무"), ("10028", "인사·HR"), ("10029", "회계·세무"),
            ("10030", "마케팅·광고·MD"), ("10031", "AI·개발·데이터"), ("10032", "디자인"), ("10033", "물류·무역"),
            ("10034", "운전·운송·배송"), ("10035", "영업"), ("10036", "고객상담·TM"), ("10037", "금융·보험"),
            ("10038", "식·음료"), ("10039", "고객서비스·리테일"), ("10040", "엔지니어링·설계"), ("10041", "제조·생산"),
            ("10042", "교육"), ("10043", "건축·시설"), ("10044", "의료·바이오"), ("10045", "미디어·문화·스포츠"),
            ("10046", "공공·복지"),
        ],
    },
    "linkareer": {
        "search": "https://linkareer.com/list/recruit?filterType=CATEGORY&page={page}&filterBy_q={kw}",
        "link": r'/activity/(\d+)',
        "item": None,
        "detail": "https://linkareer.com/activity/{id}",
        "category": "https://linkareer.com/list/recruit?filterType=CATEGORY&page={page}&filterBy_categoryIDs={code}",
        "category_pages": 10,
        "categories": [
            ("100001", "기획/경영"), ("100002", "마케팅/광고"), ("100003", "IT/개발"), ("100004", "디자인"),
            ("100005", "영업/CS"), ("100006", "생산/제조"), ("100007", "연구·엔지니어링"), ("100008", "금융"),
            ("100009", "미디어/콘텐츠"), ("100010", "물류/유통"), ("100011", "건설"), ("100012", "의료/바이오"),
            ("100013", "교육"), ("100014", "기타"),
        ],
    },
}
# 이미 저장한 공고를 다시 읽어 갱신할 수 있는 사이트 (원티드는 프로그램 요청을 막아 제외).
# 고용24 상세는 infoTypeCd 등 붙은 원래 주소로 읽어야 내용이 나온다 — 저장된 링크를 그대로 쓴다.
REFRESH_SITES = ("saramin", "jobkorea", "work24", "linkareer", "jasoseol", "jobplanet", "remember")
API_SITES = ("saramin", "work24")
# 사이트가 따로 모아 둔 목록 → 공고 표시. 제외 항목에 같은 이름이 있으면 그 공고는 상세를 읽지도 않는다
FLAG_LISTS = {
    # 잡코리아 헤드헌팅 화면은 사이트 공통 광고·추천 공고 링크가 같은 모양으로 섞여 있어(S-OIL 공채 등) 쓰지 않는다
    "헤드헌팅": [
        ("saramin", "https://www.saramin.co.kr/zf_user/jobs/list/headhunting?page={page}&page_count=100&sort=RD",
         r"rec_idx=(\d+)", 10),
    ],
}
# 사이트맵에 전체 공고를 올려 두는 사이트: 매번 사이트맵 한 장만 읽어 비교하고, 새 번호만 상세를 읽는다.
# 사이트맵에서 빠진 번호는 요청 없이 마감 처리. 처음 채울 때는 번호가 큰(최근) 공고부터 sitemap_max 건씩.
SITEMAP_SITES = {
    "remember": {
        "sitemap": "https://career.rememberapp.co.kr/sitemap-jobs.xml",
        "link": r"/job/posting/(\d+)",
        "detail": "https://career.rememberapp.co.kr/job/posting/{id}",
    },
}

UNLIMITED = 10 ** 9        # '0 = 모두'일 때 상한
RUN_SHARE = 0.85           # 한 번 실행이 쓸 수 있는 시간 = 실행 간격의 85% (다음 실행과 겹치지 않게)
DETAIL_SITES = ("saramin", "jobkorea", "linkareer", "remember")     # 상세(본문·기업정보)까지 읽는 사이트
BACKFILL_FIRST = 200      # 그중 새 공고보다 먼저 읽을 수 (회마다)
BACKFILL_PER_RUN = 5000    # 예전에 요약만 읽어 둔 공고를 한 번에 몇 건까지 다시 읽어 상세를 채울지 (새 공고 뒤에)

DEFAULT = {
    "enabled": False,
    "interval_hours": 4,
    "keywords": [],
    "sites": ["saramin", "jobkorea", "linkareer"],
    "list_every_hours": 4,      # 목록 페이지 전체를 다시 읽는 주기. 그 사이 실행은 저장해 둔 대기열에서 이어 읽음
    "pages": 1,                 # 검색어·직무마다 읽을 목록 페이지 수
    "by_category": True,        # 검색어가 없을 때 사이트의 직무 분류를 하나씩 모두 돈다 (끄면 최근 등록순 목록만)
    "max_new": 30,              # 사이트마다 한 번에 새로 읽을 상세 페이지 수 (0 = 모두, 시간 예산까지)
    "max_refresh": 30,          # 한 번에 다시 읽을 저장 공고 수
    "use_api": True,
    "sitemap_sites": ["remember"],
    "sitemap_max": 500,         # 사이트맵 사이트에서 한 번에 읽을 상세 페이지 수 (0 = 모두, 시간 예산까지)
}


# ── 설정·상태 ─────────────────────────────────────────────

def load_settings() -> dict:
    data = dict(DEFAULT)
    raw = db.get_setting("crawl")
    if raw:
        try:
            data.update(json.loads(raw))
        except json.JSONDecodeError:
            pass
    return data


def save_settings(data: dict) -> dict:
    merged = dict(DEFAULT)
    merged.update({k: v for k, v in data.items() if k in DEFAULT})
    merged["interval_hours"] = min(24, max(1, int(merged["interval_hours"] or 4)))
    merged["pages"] = min(10, max(1, int(merged["pages"] or 1)))
    merged["list_every_hours"] = min(24, max(1, int(merged["list_every_hours"] or 4)))
    merged["max_new"] = min(5000, max(0, int(merged["max_new"] or 0)))
    merged["max_refresh"] = min(200, max(0, int(merged["max_refresh"] or 0)))
    merged["sites"] = [s for s in merged["sites"] if s in LIST_SITES]
    merged["sitemap_sites"] = [s for s in merged["sitemap_sites"] if s in SITEMAP_SITES]
    # 0 = 모두. 한 번 실행은 시간 예산(간격의 85%)에서 멈추므로 다음 실행과 겹치지 않는다
    merged["sitemap_max"] = min(20000, max(0, int(merged["sitemap_max"] or 0)))
    db.set_setting("crawl", json.dumps(merged, ensure_ascii=False))
    return merged


def status() -> dict:
    s = load_settings()
    last = db.get_setting("crawl_last_run")
    nxt = None
    if s["enabled"]:
        nxt = (datetime.fromisoformat(last) + timedelta(hours=s["interval_hours"])).strftime("%Y-%m-%d %H:%M") \
            if last else "곧"
    lock = db.get_setting("crawl_lock", "")
    running = bool(lock) and lock > _now_iso()
    return {"last": (last or "")[:16].replace("T", " ") or None, "next": nxt, "running": running,
            "summary": db.get_setting("crawl_last_summary")}


def due() -> bool:
    s = load_settings()
    if not s["enabled"]:
        return False
    last = db.get_setting("crawl_last_run")
    return not last or datetime.now() >= datetime.fromisoformat(last) + timedelta(hours=s["interval_hours"])


# ── 잠금 (여러 프로세스 중 하나만 실행) ─────────────────────

def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def acquire_lock(minutes: int = 60) -> bool:
    until = (datetime.now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    with db.connect() as con:
        cur = con.execute("INSERT INTO settings(key, value) VALUES('crawl_lock', ?) "
                          "ON CONFLICT(key) DO UPDATE SET value = excluded.value WHERE settings.value < ?",
                          (until, _now_iso()))
        return cur.rowcount == 1


def release_lock() -> None:
    db.set_setting("crawl_lock", "")


# ── HTTP (robots.txt·간격·재시도) ─────────────────────────

class BlockedError(SourceError):
    """사이트가 막았거나 연결이 계속 끊김 — 이번 실행에서 그 사이트는 그만 읽는다."""

class Fetcher:
    def __init__(self, delay: float | None = None):
        self.delay = config.CRAWL_DELAY if delay is None else delay
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": linkimport.BROWSER_UA, "Accept-Language": "ko-KR,ko;q=0.9",
                                     "Accept": "text/html,application/xhtml+xml"})
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last: dict[str, float] = {}            # 사이트(호스트)마다 마지막 요청 시각 — 간격은 사이트마다 따로
        self._host_locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()
        self.requests = 0
        self._fails: dict[str, int] = {}

    def _host_lock(self, host: str) -> threading.Lock:
        with self._lock:
            return self._host_locks.setdefault(host, threading.Lock())

    def allowed(self, url: str) -> bool:
        host = "{0.scheme}://{0.netloc}".format(urlparse(url))
        with self._host_lock("robots:" + host):
            self._load_robots(host)
        rp = self._robots[host]
        return bool(rp and rp.can_fetch("*", url))

    def _load_robots(self, host: str) -> None:
        if host not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                res = self._get(host + "/robots.txt")
                if res.status_code >= 500:
                    self._robots[host] = None           # robots.txt 를 못 읽으면 이번에는 그 사이트를 건너뛴다
                else:
                    rp.parse(res.text.splitlines() if res.status_code < 400 else [])
                    self._robots[host] = rp
            except requests.RequestException:
                self._robots[host] = None

    RETRY_WAITS = (10, 30)          # 연결이 끊기면 10초, 30초 쉬고 다시
    MAX_FAILS = 3                   # 한 사이트에서 연달아 이만큼 실패하면 이번 실행에서는 그 사이트를 그만 읽는다

    def get(self, url: str) -> requests.Response:
        if not self.allowed(url):
            raise SourceError(f"robots.txt 가 허용하지 않는 주소: {url}")
        host = urlparse(url).netloc
        if self._fails.get(host, 0) >= self.MAX_FAILS:
            raise BlockedError(f"{host} 연결이 계속 끊겨 이번 실행에서는 중단")
        for attempt, wait in enumerate((0, *self.RETRY_WAITS)):
            if wait:
                time.sleep(wait)
            try:
                res = self._get(url)
                self._fails[host] = 0
                return res
            except (requests.ConnectionError, requests.Timeout) as e:
                self._fails[host] = self._fails.get(host, 0) + 1
                log.warning("연결 실패 %s (%d번째): %s", url, attempt + 1, e.__class__.__name__)
                if self._fails[host] >= self.MAX_FAILS:
                    raise BlockedError(f"{host} 연결이 계속 끊겨 이번 실행에서는 중단 ({e.__class__.__name__})") from e
        raise BlockedError(f"{host} 연결 실패")

    def _get(self, url: str) -> requests.Response:
        host = urlparse(url).netloc
        with self._host_lock(host):                  # 같은 사이트는 한 번에 하나씩, 간격을 두고
            wait = self.delay - (time.monotonic() - self._last.get(host, 0.0))
            if wait > 0:
                time.sleep(wait)
            try:
                return self.session.get(url, timeout=config.HTTP_TIMEOUT)
            finally:
                self._last[host] = time.monotonic()
                with self._lock:
                    self.requests += 1


# ── 목록 해석 ─────────────────────────────────────────────

_EMP = ("정규직", "계약직", "인턴", "파견직", "프리랜서", "아르바이트", "전환형 인턴")


def list_items(site: str, page: str) -> list[dict]:
    """검색 결과 페이지 → [{'id', 'hint': {location, career, employment, deadline}}] (나온 순서, 중복 제거)."""
    cfg = LIST_SITES[site]
    out, seen = [], set()
    matches = list(re.finditer(cfg["link"], page))
    for i, m in enumerate(matches):
        pid = m.group(1)
        if pid in seen:
            continue
        seen.add(pid)
        # 다음 공고 링크 전까지(최대 3,000자)를 이 공고의 목록 정보로 본다
        end = min(len(page), m.end() + 3000)
        for n in matches[i + 1:]:
            if n.group(1) != pid:
                end = min(end, n.start())
                break
        out.append({"id": pid, "hint": _hints(page[m.start():end])})
    return out


def _hints(block: str) -> dict:
    text = html.unescape(re.sub(r"<[^>]+>", "|", block))
    tokens = [t.strip() for t in re.split(r"\|", text) if t.strip()]
    hint: dict = {}
    for t in tokens:
        if len(t) > 40:
            continue
        if "location" not in hint and any(re.match(rf"^{s}(\s|전체|$)", t) for s in SIDO_ORDER):
            hint["location"] = t.replace("전체", "").strip()          # 사람인 목록: '서울전체'
        elif "career" not in hint and re.match(r"^(신입|경력|경력무관|신입\s*[·/,]\s*경력|\d+년\s*↑?$)", t):
            hint["career"] = t
        elif "employment" not in hint and any(t.startswith(e) for e in _EMP):
            hint["employment"] = t
        elif "education" not in hint and re.match(r"^(학력무관|고졸|초대졸|대졸|석사|박사)", t):
            hint["education"] = t
    return hint


# ── 실행 ─────────────────────────────────────────────────

def run_once(force: bool = False, fetcher: Fetcher | None = None) -> dict | None:
    """정해진 시각이 되었거나 force 이면 한 번 실행한다. 다른 프로세스가 실행 중이면 None."""
    if not force and not due():
        return None
    s = load_settings()
    if not acquire_lock(minutes=s["interval_hours"] * 60):
        return None
    try:
        return _run(s, fetcher or Fetcher())
    finally:
        release_lock()


_PROGRESS: dict = {}


def progress(**kw) -> None:
    """지금 실행 중인 단계를 settings.crawl_progress 에 남긴다 (수집 현황 화면이 읽음). 너무 자주 쓰지 않게 단계가
    바뀔 때와 몇 건마다만."""
    _PROGRESS.update(kw)
    _PROGRESS["updated_at"] = _now_iso()
    db.set_setting("crawl_progress", json.dumps(_PROGRESS, ensure_ascii=False))


def current_progress() -> dict | None:
    raw = db.get_setting("crawl_progress")
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return None


def _run(s: dict, f: Fetcher) -> dict:
    """한 번 실행: ① 표시 목록(헤드헌팅) ② 모든 목록·사이트맵에서 새 공고 번호 모으기
    ③ 사이트를 돌아가며 하나씩 상세 읽기 — 시간 예산(간격의 85%)이 다할 때까지, 남은 것은 다음 실행에
    ④ 저장 공고 갱신·API·마감 공고 정리."""
    started = _now_iso()
    summary = {"new": 0, "updated": 0, "closed": 0, "errors": [], "sites": {}}
    keywords = s["keywords"] or [""]
    run_deadline = time.monotonic() + s["interval_hours"] * 3600 * RUN_SHARE
    # 저장 공고 갱신·API 몫은 남겨 둔다 — 이번에 실제로 다시 읽을 공고 수만큼만
    refresh_n = len(refresh_candidates(s["max_refresh"]))
    detail_deadline = run_deadline - refresh_n * max(f.delay, 0.5) * 1.3 - 60

    _PROGRESS.clear()
    progress(running=True, started_at=started, phase="헤드헌팅 목록", site=None, step=None,
             new=0, filled=0, requests=0, interval_hours=s["interval_hours"])
    summary["flagged"] = collect_flags(f, s["sites"], summary["errors"])
    from . import profile as profile_mod
    excluded_flags = set(profile_mod.load().get("exclude") or []) & set(FLAG_LISTS)

    queues: dict[str, list[tuple[str, str, dict | None]]] = {}     # 사이트 → [(공고번호, 주소, 목록 보충값)]
    stats: dict[str, dict] = {}

    # ② 목록
    for site in s["sites"]:
        label = ", ".join(k for k in keywords if k) or \
            ("모든 직무(직무별)" if s["by_category"] and LIST_SITES[site].get("categories") else "모든 직무(최신순)")
        st = stats[site] = {"label": label, "listed": 0, "new": 0, "updated": 0, "errors": []}
        skip = set().union(*(postings.flagged(site, fl) for fl in excluded_flags)) if excluded_flags else set()
        try:
            if list_due(site, s):
                progress(phase="목록 읽기", site=site, step=None, requests=f.requests)
                groups, errors = collect_lists(f, site, s, keywords)
                st["errors"] += errors
                summary["errors"] += errors
                ids = {i: h for g in groups.values() for i, h in g.items()}
                st["listed"] = len(ids)
                _fill_missing_from_list(site, ids)
                _mark_seen(site, ids)
                order = pick_round_robin(groups, lambda i: i not in skip and not postings.find_id(site, i), UNLIMITED)
                st["queued"] = queue_add(site, [(i, ids[i]) for i in order])
                db.set_setting(f"list_at:{site}", _now_iso())
            else:
                st["label"] += " · 대기열에서 이어 읽기"
        except (SourceError, requests.RequestException) as e:
            st["errors"].append(str(e))
            summary["errors"].append(f"{site}: {e}")
        pending = queue_load(site, skip, s["max_new"] or UNLIMITED)
        fresh = [(i, LIST_SITES[site]["detail"].format(id=i), h) for i, h in pending]
        again = [(i, LIST_SITES[site]["detail"].format(id=i), None) for i in _needs_backfill(site)]
        # 다시 읽기로 표시한 공고(상세가 비었거나 잘못 읽은 것)는 회마다 조금씩 먼저 — 새 공고가 많아도 계속 밀리지 않게
        queues[site] = again[:BACKFILL_FIRST] + fresh + again[BACKFILL_FIRST:]

    for site in s["sitemap_sites"]:
        try:
            info, pending = sitemap_prepare(f, site, s["sitemap_max"] or UNLIMITED)
        except (SourceError, requests.RequestException) as e:
            summary["errors"].append(f"{site} 사이트맵: {e}")
            collect.record(f"crawl:{site}", "사이트맵 전체", 0, 0, 0, str(e))
            continue
        summary["closed"] += info["gone"]
        stats[site] = {"label": f"사이트맵 전체 {info['total']:,}건", "listed": info["total"], "new": 0, "updated": 0,
                       "errors": [], "sitemap": info}
        queues[site] = [(i, SITEMAP_SITES[site]["detail"].format(id=i), None) for i in pending]
        queues[site] += [(i, SITEMAP_SITES[site]["detail"].format(id=i), None) for i in _needs_backfill(site)]

    # ③ 상세: 사이트마다 따로 동시에 읽는다 (같은 사이트 안에서는 간격을 지킴 — Fetcher 가 사이트마다 따로 쉼)
    queued_total = sum(len(v) for v in queues.values())
    progress(phase="상세 읽기", site=None, step=f"0/{queued_total:,}", requests=f.requests)
    fetched = {k: 0 for k in queues}
    timed_out: set[str] = set()
    write_lock = threading.Lock()                    # DB 쓰기는 한 번에 하나씩

    def read_site(site: str) -> None:
        while queues[site]:
            if time.monotonic() > detail_deadline:
                timed_out.add(site)
                return
            post_id, url, hint = queues[site].pop(0)
            try:
                item, error = _fetch_item(f, site, url, hint)
            except BlockedError as e:
                with write_lock:
                    stats[site]["errors"].append(str(e))
                    summary["errors"].append(f"{site}: {e}")
                return
            except Exception as e:                   # 한 사이트 오류가 다른 사이트를 멈추지 않게
                log.exception("상세 읽기 오류 %s", url)
                item, error = None, f"{e.__class__.__name__}: {e}"[:200]
            with write_lock:
                try:
                    if item:
                        a, b = postings.upsert_many([postings.keep_existing(item)])
                        stats[site]["new"] += a
                        stats[site]["updated"] += b
                except Exception as e:                   # DB 잠김 등 — 이 공고만 건너뛰고 사이트 읽기는 계속
                    log.exception("저장 실패 %s", url)
                    stats[site]["errors"].append(f"저장 실패 {post_id}: {e.__class__.__name__}")
                    continue
                if site in LIST_SITES:
                    queue_done(site, post_id)
                if site in SITEMAP_SITES:
                    with db.connect() as con:
                        con.execute("UPDATE sitemap_ids SET fetched_at = ?, fetch_error = ? WHERE site = ? AND post_id = ?",
                                    (db.now(), error, site, post_id))
                fetched[site] += 1
                done = sum(fetched.values())
                if done % 10 == 0:
                    progress(site=site, step=f"{done:,}/{queued_total:,}", requests=f.requests,
                             new=sum(v["new"] for v in stats.values()),
                             filled=sum(v["updated"] for v in stats.values()))

    workers = [threading.Thread(target=read_site, args=(k,), name=f"crawl-{k}", daemon=True)
               for k in queues if queues[k]]
    for t in workers:
        t.start()
    for t in workers:
        t.join()
    stopped_by_time = bool(timed_out)

    for site, st in stats.items():
        collect.record(f"crawl:{site}", st["label"], st["listed"], st["new"], st["updated"],
                       "; ".join(st["errors"][:2]) or None)
        entry = {"new": st["new"], "fetched": fetched.get(site, 0), "left": len(queues.get(site, []))}
        if "sitemap" in st:
            entry.update(total=st["sitemap"]["total"], new_ids=st["sitemap"]["new_ids"], inserted=st["new"],
                         updated=st["updated"], gone=st["sitemap"]["gone"], remaining=_sitemap_remaining(site))
        summary["sites"][site] = entry
        summary["new"] += st["new"]
        summary["updated"] += st["updated"]
    summary["stopped_by_time"] = stopped_by_time

    # ④ 저장 공고 갱신·API·정리
    progress(phase="저장 공고 다시 읽기", site=None, step=None, requests=f.requests,
             new=summary["new"], filled=summary["updated"])
    r_upd, r_closed, r_err = refresh(f, s["max_refresh"])
    summary["updated"] += r_upd
    summary["closed"] += r_closed
    summary["errors"] += r_err

    if s["use_api"]:
        ready = [x["key"] for x in registry() if x["ready"] and x["key"] in API_SITES]
        for kw in keywords:                            # '' = 최근 공고 전체
            if not ready:
                break
            for r in collect.run(ready, FetchQuery(keyword=kw, pages=1)):
                if r["ok"]:
                    summary["new"] += r["inserted"]
                    summary["updated"] += r["updated"]
                else:
                    summary["errors"].append(f"{r['name']} API: {r['error']}")

    # 마감된 공고·60일 지난 상시 공고 중 저장·지원 기록이 없는 것은 지운다
    summary["purged"] = postings.purge_closed() + postings.purge_stale()
    postings.recompute()                      # 헤드헌팅 표시 등 바뀐 것을 점수·제외 여부에 반영
    summary["requests"] = f.requests
    db.set_setting("crawl_last_run", started)
    db.set_setting("crawl_last_summary", json.dumps(summary, ensure_ascii=False))
    finished = _now_iso()
    with db.connect() as con:
        con.execute("INSERT INTO crawl_runs(started_at, finished_at, seconds, new, updated, closed, purged, requests, "
                    "stopped, errors, sites) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (started, finished, int((datetime.fromisoformat(finished) - datetime.fromisoformat(started))
                                            .total_seconds()),
                     summary["new"], summary["updated"], summary["closed"], summary.get("purged", 0),
                     summary["requests"], 1 if summary.get("stopped_by_time") else 0,
                     json.dumps(summary["errors"][:50], ensure_ascii=False),
                     json.dumps(summary["sites"], ensure_ascii=False)))
    progress(running=False, phase="끝", finished_at=finished, requests=summary["requests"],
             new=summary["new"], filled=summary["updated"])
    log.info("crawl done %s", summary)
    return summary


def _reread_sql() -> tuple[str, list]:
    """다시 읽을 공고 조건: 상세를 아직 안 읽었거나(company_info 가 NULL), 읽기 규칙을 고친 시각(reread_before)
    전에 읽은 마감 전 공고. 다시 읽어도 새 페이지에 없는 값은 지우지 않는다(keep_existing)."""
    before = db.get_setting("reread_before") or ""
    return ("source = ? AND hidden = 0 AND (deadline IS NULL OR deadline >= ?) "
            "AND (company_info IS NULL OR updated_at < ?)", [date.today().isoformat(), before])


def _needs_backfill(site: str) -> list[str]:
    """다시 읽을 공고 — 상세를 안 읽은 것 먼저, 그다음 오래전에 읽은 것부터. 상세 읽기를 갖춘 사이트만."""
    if site not in DETAIL_SITES:
        return []
    where, args = _reread_sql()
    with db.connect() as con:
        return [r[0] for r in con.execute(
            f"SELECT source_id FROM postings WHERE {where} "
            "ORDER BY company_info IS NOT NULL, updated_at, id DESC LIMIT ?",
            (site, *args, BACKFILL_PER_RUN))]


def _fetch_item(f: Fetcher, site: str, url: str, hint: dict | None) -> tuple[dict | None, str | None]:
    """상세 한 건 → (공고, 실패 이유). 사이트가 막으면 BlockedError."""
    try:
        res = f.get(url)
    except BlockedError:
        raise
    except SourceError as e:
        return None, str(e)[:200]
    if res.status_code in (403, 429):
        raise BlockedError(f"{site} 가 요청을 막았습니다 (HTTP {res.status_code}) — 이번 실행에서 그 사이트는 멈춤")
    if res.status_code != 200:
        return None, f"HTTP {res.status_code}"
    try:
        item = linkimport.parse(res.text, url, site)
    except SourceError as e:
        return None, str(e)[:200]
    return (_apply_hint(item, hint) if hint else item), None


def sitemap_prepare(f: Fetcher, site: str, max_fetch: int) -> tuple[dict, list[str]]:
    """사이트맵 한 장을 읽어 번호 목록을 맞추고(빠진 번호는 마감 처리), 아직 안 읽은 번호를 큰(최근) 것부터 돌려준다."""
    cfg = SITEMAP_SITES[site]
    res = f.get(cfg["sitemap"])
    if res.status_code != 200:
        raise SourceError(f"사이트맵 HTTP {res.status_code}")
    ids = list(dict.fromkeys(re.findall(cfg["link"], res.text)))
    now = datetime.now().isoformat(timespec="microseconds")        # 이번 실행 표시 (빠진 번호 = 이보다 옛날)
    with db.connect() as con:
        active_before = con.execute("SELECT COUNT(*) FROM sitemap_ids WHERE site = ? AND gone = 0", (site,)).fetchone()[0]
        con.executemany(
            "INSERT INTO sitemap_ids(site, post_id, first_seen, last_seen) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(site, post_id) DO UPDATE SET last_seen = excluded.last_seen, gone = 0",
            [(site, i, now, now) for i in ids])
        # 사이트맵이 갑자기 크게 줄었으면(오류 페이지 등) 빠진 번호를 마감 처리하지 않는다
        gone_ids = []
        if ids and len(ids) >= active_before * 0.8:
            gone_ids = [r[0] for r in con.execute(
                "SELECT post_id FROM sitemap_ids WHERE site = ? AND gone = 0 AND last_seen < ?", (site, now))]
            con.execute("UPDATE sitemap_ids SET gone = 1 WHERE site = ? AND gone = 0 AND last_seen < ?", (site, now))
        pending = [r[0] for r in con.execute(
            "SELECT post_id FROM sitemap_ids WHERE site = ? AND gone = 0 AND fetched_at IS NULL "
            "ORDER BY CAST(post_id AS INTEGER) DESC LIMIT ?", (site, max_fetch))]
    closed = 0
    for pid_ in gone_ids:
        pid = postings.find_id(site, pid_)
        if pid:
            postings.mark_closed(pid)
            closed += 1
    info = {"total": len(ids), "new_ids": max(0, len(ids) - active_before) if active_before else len(ids),
            "gone": closed}
    return info, pending


def _sitemap_remaining(site: str) -> int:
    with db.connect() as con:
        return con.execute("SELECT COUNT(*) FROM sitemap_ids WHERE site = ? AND gone = 0 AND fetched_at IS NULL",
                           (site,)).fetchone()[0]


def overview() -> dict:
    """수집 현황 화면: 지금 실행·사이트별 데이터·실행 기록·오류를 한데."""
    s = load_settings()
    today = date.today().isoformat()
    sites = {b["site"]: b for b in backlog_status()["sites"]}
    quality = {}
    with db.connect() as con:
        for row in con.execute(
                "SELECT source, COUNT(*), SUM(company_info IS NOT NULL), SUM(sido IS NOT NULL), "
                "SUM(salary_min IS NOT NULL OR salary_max IS NOT NULL), SUM(deadline IS NOT NULL), "
                "SUM(LENGTH(COALESCE(description, '')) >= 300), SUM(fit_excl = 1), SUM(deadline = ?), "
                "SUM(fetched_at > ?) FROM postings WHERE hidden = 0 GROUP BY source",
                (today, (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S"))):
            src, n, det, loc, sal, dl, body, excl, today_dl, day_new = row
            pct = (lambda x: round((x or 0) * 100 / n)) if n else (lambda x: 0)
            quality[src] = {"n": n, "detail": pct(det), "detail_n": det or 0, "loc": pct(loc), "salary": pct(sal),
                            "deadline": pct(dl), "body": pct(body), "excluded": excl or 0,
                            "closing_today": today_dl or 0, "saved_24h": day_new or 0}
        flags = dict(con.execute("SELECT source, COUNT(*) FROM post_flags GROUP BY source").fetchall())
        runs = [dict(r) for r in con.execute("SELECT * FROM crawl_runs ORDER BY id DESC LIMIT 24")]
        errors = [dict(r) for r in con.execute(
            "SELECT started_at, source, query, error FROM fetch_runs WHERE error IS NOT NULL AND started_at > ? "
            "ORDER BY id DESC LIMIT 30", ((datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"),))]
    for r in runs:
        r["errors"] = json.loads(r["errors"] or "[]")
        r["sites"] = json.loads(r["sites"] or "{}")
    order = list(s["sites"]) + [x for x in s["sitemap_sites"] if x not in s["sites"]]
    order += [x for x in quality if x not in order]
    rows = []
    for key in order:
        b = sites.get(key, {})
        rows.append({"site": key, "name": linkimport.SITES.get(key, (key,))[0], **quality.get(key, {"n": 0}),
                     "queue": b.get("left", 0), "backfill": b.get("backfill", 0), "new_day": b.get("new_day", 0),
                     "last_fetched": b.get("last_fetched", 0), "list_at": b.get("list_at"),
                     "flags": flags.get(key, 0)})
    prog = current_progress()
    st = status()
    if prog and prog.get("running") and not st["running"]:          # 실행이 끊긴 채 남은 기록
        prog["running"] = False
        prog["phase"] = "중단됨 (앱이 다시 켜짐)"
    if prog and prog.get("started_at"):
        # 끊긴 실행에는 끝난 시각이 없다 — 마지막으로 기록한 시각까지
        end = _now_iso() if prog.get("running") else (prog.get("finished_at") or prog.get("updated_at") or prog["started_at"])
        prog["elapsed_min"] = round((datetime.fromisoformat(end) - datetime.fromisoformat(prog["started_at"]))
                                    .total_seconds() / 60)
    bl = backlog_status()
    return {"settings": s, "status": st, "progress": prog, "sites": rows, "runs": runs, "errors": errors,
            "keep_awake": db.get_setting("keep_awake") == "1",
            "backlog": {k: bl[k] for k in ("left", "per_run", "per_hour", "eta")},
            "total": sum(r.get("n", 0) for r in rows)}


def backlog_status() -> dict:
    """사이트별 밀린 공고·지난 실행 처리 수·하루 새 공고 수(실측)·예상 완료 시각."""
    s = load_settings()
    last = json.loads(db.get_setting("crawl_last_summary") or "{}")
    since = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    rows, total_left, total_rate = [], 0, 0
    with db.connect() as con:
        for site in list(s["sites"]) + list(s["sitemap_sites"]):
            if site in SITEMAP_SITES:
                left = con.execute("SELECT COUNT(*) FROM sitemap_ids WHERE site = ? AND gone = 0 AND fetched_at IS NULL",
                                   (site,)).fetchone()[0]
                first = con.execute("SELECT MIN(first_seen) FROM sitemap_ids WHERE site = ?", (site,)).fetchone()[0]
                base = (datetime.fromisoformat(first) + timedelta(hours=1)).isoformat() if first else since
                new_day = con.execute("SELECT COUNT(*) FROM sitemap_ids WHERE site = ? AND first_seen > ? AND first_seen > ?",
                                      (site, since.replace(" ", "T"), base)).fetchone()[0]
                backfill = 0
            else:
                left = con.execute("SELECT COUNT(*) FROM crawl_queue WHERE site = ?", (site,)).fetchone()[0]
                first = db.get_setting(f"list_first:{site}")
                base = (datetime.strptime(first, "%Y-%m-%d %H:%M:%S") + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")                     if first else "9999"
                new_day = con.execute("SELECT COUNT(*) FROM list_seen WHERE site = ? AND first_seen > ? AND first_seen > ?",
                                      (site, since, base)).fetchone()[0]
                where, args = _reread_sql()
                backfill = con.execute(f"SELECT COUNT(*) FROM postings WHERE {where}", (site, *args)).fetchone()[0]                     if site in DETAIL_SITES else 0
            done = (last.get("sites", {}).get(site) or {}).get("fetched", 0)
            rows.append({"site": site, "name": linkimport.SITES[site][0], "left": left, "backfill": backfill,
                         "last_fetched": done, "new_day": new_day,
                         "list_at": (db.get_setting(f"list_at:{site}") or "")[:16].replace("T", " ") or None})
            total_left += left + backfill
            total_rate += done
    eta = None
    if total_left and total_rate:
        eta = (datetime.now() + timedelta(hours=s["interval_hours"] * total_left / total_rate)).strftime("%m-%d %H:%M")
    return {"sites": rows, "left": total_left, "per_run": total_rate, "eta": eta,
            "per_hour": round(total_rate / max(s["interval_hours"], 1))}


def sitemap_progress() -> list[dict]:
    s = load_settings()
    out = []
    with db.connect() as con:
        for site in SITEMAP_SITES:
            row = con.execute(
                "SELECT COUNT(*) AS total, SUM(fetched_at IS NOT NULL) AS fetched, "
                "SUM(fetch_error IS NOT NULL) AS errors FROM sitemap_ids WHERE site = ? AND gone = 0", (site,)).fetchone()
            total, fetched = row["total"] or 0, row["fetched"] or 0
            remaining = total - fetched
            # 한 번에 읽는 수: 정해 두었으면 그 수, '모두'면 시간 예산을 사이트 수로 나눈 만큼
            per_run = s["sitemap_max"] or int(s["interval_hours"] * 3600 * RUN_SHARE / max(config.CRAWL_DELAY, 0.5)
                                              / (len(s["sites"]) + len(s["sitemap_sites"]) or 1))
            runs = -(-remaining // per_run) if per_run else None
            out.append({"site": site, "name": linkimport.SITES[site][0], "on": site in s["sitemap_sites"],
                        "total": total, "fetched": fetched, "errors": row["errors"] or 0, "remaining": remaining,
                        "eta_hours": runs * s["interval_hours"] if runs else 0})
    return out


# ── 대기열 (아직 상세를 읽지 않은 공고 번호) ──────────────────

def list_due(site: str, s: dict) -> bool:
    """목록 전체를 다시 읽을 때인가: 처음이거나, 주기가 지났거나, 대기열이 비었을 때."""
    last = db.get_setting(f"list_at:{site}")
    if not last:
        return True
    if datetime.now() >= datetime.fromisoformat(last) + timedelta(hours=s.get("list_every_hours", 4)):
        return True
    with db.connect() as con:
        return con.execute("SELECT COUNT(*) FROM crawl_queue WHERE site = ?", (site,)).fetchone()[0] == 0


def queue_add(site: str, items: list[tuple[str, dict]]) -> int:
    """새로 찾은 공고를 대기열 맨 앞에 (주어진 순서 그대로). 이미 있거나 저장된 공고는 뺀다. 넣은 수."""
    with db.connect() as con:
        have = {r[0] for r in con.execute("SELECT post_id FROM crawl_queue WHERE site = ?", (site,))}
        fresh = [(i, h) for i, h in items if i not in have]
        if not fresh:
            return 0
        low = con.execute("SELECT COALESCE(MIN(seq), 0) FROM crawl_queue WHERE site = ?", (site,)).fetchone()[0]
        start = low - len(fresh)
        now = db.now()
        con.executemany("INSERT INTO crawl_queue(site, post_id, seq, hint, added_at) VALUES(?, ?, ?, ?, ?)",
                        [(site, i, start + n, json.dumps(h or {}, ensure_ascii=False), now)
                         for n, (i, h) in enumerate(fresh)])
    return len(fresh)


def queue_load(site: str, skip: set[str], limit: int) -> list[tuple[str, dict]]:
    """대기열에서 읽을 순서대로. 그사이 저장된 공고는 대기열에서 지운다."""
    with db.connect() as con:
        rows = con.execute("SELECT post_id, hint FROM crawl_queue WHERE site = ? ORDER BY seq", (site,)).fetchall()
        saved = {r[0] for r in con.execute("SELECT source_id FROM postings WHERE source = ?", (site,))}
        stale = [(site, r[0]) for r in rows if r[0] in saved]
        if stale:
            con.executemany("DELETE FROM crawl_queue WHERE site = ? AND post_id = ?", stale)
    out = [(r[0], json.loads(r[1] or "{}")) for r in rows if r[0] not in saved and r[0] not in skip]
    return out[:limit]


def queue_done(site: str, post_id: str) -> None:
    with db.connect() as con:
        con.execute("DELETE FROM crawl_queue WHERE site = ? AND post_id = ?", (site, post_id))


def _mark_seen(site: str, ids) -> None:
    """목록에서 처음 본 시각을 남긴다 — 하루 새 공고 수(실측)."""
    now = db.now()
    with db.connect() as con:
        con.executemany("INSERT OR IGNORE INTO list_seen(site, post_id, first_seen) VALUES(?, ?, ?)",
                        [(site, i, now) for i in ids])
    if not db.get_setting(f"list_first:{site}"):
        db.set_setting(f"list_first:{site}", now)


def collect_flags(f: Fetcher, sites: list[str], errors: list[str]) -> dict:
    """헤드헌팅 같은 사이트 목록을 읽어 공고 번호에 표시를 남긴다. 사이트가 막으면 그 표시만 건너뛴다."""
    out: dict[str, int] = {}
    for flag, lists in FLAG_LISTS.items():
        for site, tpl, pat, pages in lists:
            if site not in sites:
                continue
            ids: list[str] = []
            try:
                for page_no in range(1, pages + 1):
                    res = f.get(tpl.replace("{page}", str(page_no)))
                    if res.status_code != 200:
                        break
                    found = [i for i in dict.fromkeys(re.findall(pat, res.text)) if i not in ids]
                    if not found:
                        break
                    ids += found
                    if "{page}" not in tpl:
                        break
            except SourceError as e:
                errors.append(f"{flag} 목록 {site}: {e}")
            if ids:
                postings.add_flags(site, ids, flag)
                out[f"{flag}:{site}"] = out.get(f"{flag}:{site}", 0) + len(ids)
    return out


def collect_lists(f: Fetcher, site: str, s: dict, keywords: list[str]) -> tuple[dict, list[str]]:
    """목록 페이지들을 읽어 {묶음 이름: {공고번호: 보충값}} 으로 돌려준다.
    검색어가 있으면 검색어별, 없으면 직무별(by_category) 또는 최근 등록순 하나."""
    cfg = LIST_SITES[site]
    jobs: list[tuple[str, str, int, str | None]] = []           # (묶음 이름, 주소 틀, 페이지 수, 직무 이름)
    if any(keywords):
        for kw in keywords:
            if kw:
                jobs.append((kw, cfg["search"].replace("{kw}", quote(kw)), s["pages"], None))
    elif s["by_category"] and cfg.get("categories"):
        pages = min(s["pages"], cfg.get("category_pages", 1))
        for code, name in cfg["categories"]:
            jobs.append((name, cfg["category"].replace("{code}", code), pages, name))
    else:
        jobs.append(("최신", (cfg.get("latest") or cfg["search"]).replace("{kw}", ""), s["pages"], None))

    groups: dict[str, dict] = {}
    errors: list[str] = []
    for n_job, (name, tpl, pages, category) in enumerate(jobs, start=1):
        if len(jobs) > 1:
            progress(step=f"{name} ({n_job}/{len(jobs)})", requests=f.requests)
        group = groups.setdefault(name, {})
        for page_no in range(1, pages + 1):
            res = f.get(tpl.replace("{page}", str(page_no)))
            if res.status_code in (403, 429):
                raise BlockedError(f"목록 요청이 막혔습니다 (HTTP {res.status_code})")
            if res.status_code != 200:
                errors.append(f"{site} {name}: 목록 HTTP {res.status_code}")
                break
            found = list_items(site, res.text)
            fresh = [it for it in found if it["id"] not in group]
            for it in fresh:
                if category:
                    it["hint"]["category"] = category
                group[it["id"]] = it["hint"]
            if not fresh:
                break                                 # 다음 페이지가 같은 내용이면 그만 읽는다
    _drop_promoted_category(site, groups)
    if not any(keywords) and cfg.get("newest"):
        newest_scan(f, site, cfg["newest"], groups, errors)
    return groups, errors


NEWEST_MAX_PAGES = 300     # 최신순 앞쪽: 이미 본 공고만 나오는 페이지까지, 최대 이만큼
DEEP_PAGES = 100           # 최신순 뒤쪽: 목록을 읽을 때마다 지난번에 멈춘 곳부터 이만큼 더 (예전 공고 모으기)


def newest_scan(f: Fetcher, site: str, tpl: str, groups: dict[str, dict], errors: list[str]) -> None:
    """전체 최신순 목록. 앞쪽은 이미 본 공고만 나올 때까지(새 공고 모두), 뒤쪽은 페이지 위치를 기억해 조금씩 더 깊이."""
    with db.connect() as con:
        known = {r[0] for r in con.execute("SELECT post_id FROM list_seen WHERE site = ?", (site,))}
        known |= {r[0] for r in con.execute("SELECT source_id FROM postings WHERE source = ?", (site,))}
    group = groups.setdefault("최신순", {})

    def read(page_no: int) -> list[dict] | None:
        res = f.get(tpl.replace("{page}", str(page_no)))
        if res.status_code in (403, 429):
            raise BlockedError(f"목록 요청이 막혔습니다 (HTTP {res.status_code})")
        if res.status_code != 200:
            errors.append(f"{site} 최신순 {page_no}쪽: HTTP {res.status_code}")
            return None
        found = list_items(site, res.text)
        for it in found:
            group.setdefault(it["id"], it["hint"])
        return found

    page_no = 0
    for page_no in range(1, NEWEST_MAX_PAGES + 1):
        if page_no % 10 == 1:
            progress(step=f"최신순 {page_no}쪽", requests=f.requests)
        found = read(page_no)
        if found == []:                               # 목록 끝까지 다 읽음 — 더 깊이 갈 곳이 없다
            db.set_setting(f"deep_page:{site}", "1")
            return
        if not found or all(it["id"] in known for it in found):
            break
    start = max(page_no + 1, int(db.get_setting(f"deep_page:{site}") or 1))
    for deep in range(start, start + DEEP_PAGES):
        if deep % 10 == 0:
            progress(step=f"최신순 깊이 {deep}쪽", requests=f.requests)
        found = read(deep)
        if found is None:
            break
        if not found:                                 # 끝까지 갔으면 다음에는 처음부터
            deep = 0
            break
    db.set_setting(f"deep_page:{site}", str(deep + 1))


PROMOTED_MIN_GROUPS = 3


def _drop_promoted_category(site: str, groups: dict[str, dict]) -> None:
    """여러 직무 목록에 똑같이 끼는 광고(TOP100·인기 배너)는 직무를 알 수 없으므로 직무 이름을 붙이지 않는다.
    예전에 첫 목록의 직무로 잘못 붙은 값도 지운다."""
    if len(groups) < PROMOTED_MIN_GROUPS:
        return
    count: dict[str, int] = {}
    for g in groups.values():
        for i in g:
            count[i] = count.get(i, 0) + 1
    promoted = [i for i, n in count.items() if n >= PROMOTED_MIN_GROUPS]
    for g in groups.values():
        for i in promoted:
            if i in g:
                g[i].pop("category", None)
    names = [n for _, n in LIST_SITES[site].get("categories", [])]
    if promoted and names:
        with db.connect() as con:
            con.executemany(
                f"UPDATE postings SET job_category = NULL WHERE source = ? AND source_id = ? "
                f"AND job_category IN ({','.join('?' * len(names))})",
                [(site, i, *names) for i in promoted])


def pick_round_robin(groups: dict[str, dict], keep, limit: int) -> list[str]:
    """묶음(직무)마다 돌아가며 하나씩 골라 한 직무가 상한을 다 쓰지 않게 한다. 중복 번호는 한 번만."""
    queues = [[i for i in g if keep(i)] for g in groups.values()]
    out, seen = [], set()
    while len(out) < limit and any(queues):
        for q in queues:
            while q and q[0] in seen:
                q.pop(0)
            if q:
                i = q.pop(0)
                seen.add(i)
                out.append(i)
                if len(out) >= limit:
                    break
    return out


def _fill_missing_from_list(site: str, hints: dict[str, dict]) -> None:
    """이미 저장했지만 근무지·직무·학력이 비어 있는 공고는 목록 값으로 채운다 (상세를 다시 읽지 않음)."""
    from .normalize import parse_education, parse_region
    with db.connect() as con:
        for post_id, hint in hints.items():
            if hint.get("location"):
                sido, sigungu = parse_region(hint["location"])
                if sido:
                    con.execute("UPDATE postings SET sido = ?, sigungu = ?, location_raw = COALESCE(location_raw, ?) "
                                "WHERE source = ? AND source_id = ? AND sido IS NULL",
                                (sido, sigungu, hint["location"], site, post_id))
            if hint.get("category"):
                con.execute("UPDATE postings SET job_category = ? WHERE source = ? AND source_id = ? "
                            "AND (job_category IS NULL OR job_category = '')", (hint["category"], site, post_id))
            edu = parse_education(hint.get("education"))
            if edu != "무관":                           # 상세에서 학력을 못 읽은 공고('무관')는 목록의 학력으로
                con.execute("UPDATE postings SET education = ? WHERE source = ? AND source_id = ? AND education = '무관'",
                            (edu, site, post_id))


def _apply_hint(item: dict, hint: dict) -> dict:
    """상세 페이지에 없던 값만 목록 정보로 채운다 (사람인 상세에는 근무지가 없음)."""
    from .normalize import parse_career, parse_education, parse_region
    if not item.get("sido") and hint.get("location"):
        item["sido"], item["sigungu"] = parse_region(hint["location"])
        item["location_raw"] = clean(hint["location"])
    if item.get("career_type") in (None, "무관") and not item.get("career_raw") and hint.get("career"):
        item["career_type"], item["career_min"], item["career_max"] = parse_career(hint["career"])
        item["career_raw"] = hint["career"]
    if item.get("education") in (None, "무관") and hint.get("education"):
        item["education"] = parse_education(hint["education"])
    if not item.get("employment_type") and hint.get("employment"):
        item["employment_type"] = hint["employment"]
    if not item.get("job_category") and hint.get("category"):
        item["job_category"] = hint["category"]
    return item


def refresh_candidates(limit: int) -> list[dict]:
    """다시 읽을 공고: 저장·지원한 공고는 하루에 한 번, 마감일 없는(상시) 공고는 일주일에 한 번.
    나머지는 마감일로 저절로 정리되므로 다시 읽지 않는다 (수만 건을 매일 읽을 수 없음)."""
    if limit <= 0:
        return []
    today = date.today().isoformat()
    day = (datetime.now() - timedelta(hours=20)).strftime("%Y-%m-%d %H:%M:%S")
    week = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    marks = ",".join("?" * len(REFRESH_SITES))
    with db.connect() as con:
        return [dict(r) for r in con.execute(
            f"SELECT * FROM postings WHERE source IN ({marks}) AND url IS NOT NULL AND hidden = 0 "
            "AND (deadline IS NULL OR deadline >= ?) AND ("
            "  ((saved = 1 OR id IN (SELECT posting_id FROM applications)) AND updated_at < ?)"
            "  OR (deadline IS NULL AND updated_at < ?)"
            ") ORDER BY saved DESC, updated_at LIMIT ?",
            (*REFRESH_SITES, today, day, week, limit))]


def refresh(f: Fetcher, limit: int) -> tuple[int, int, list[str]]:
    """refresh_candidates 를 다시 읽어 갱신하고, 사라진 공고(404·410)는 마감 처리. (갱신 수, 마감 처리 수, 오류)."""
    rows = refresh_candidates(limit)
    updated = closed = 0
    errors: list[str] = []
    for row in rows:
        if not linkimport.is_posting_url(row["url"], row["source"]):
            continue
        try:
            res = f.get(linkimport.detail_url(row["url"], row["source"]))
        except (SourceError, requests.RequestException) as e:
            errors.append(f"갱신 {row['source']}: {e}")
            continue
        if res.status_code in (404, 410):
            postings.mark_closed(row["id"])
            closed += 1
            continue
        if res.status_code != 200:
            continue
        try:
            item = linkimport.parse(res.text, row["url"], row["source"])
        except SourceError:
            continue
        # 새로 읽은 페이지에 없는 값(목록에서 채운 근무지, 직접 적은 값 등)은 그대로 둔다
        for k in postings.FIELDS:
            if item.get(k) in (None, "") and row.get(k) not in (None, ""):
                item[k] = row[k]
        if item.get("career_type") == "무관" and row.get("career_type") not in (None, "무관") and not item.get("career_raw"):
            item["career_type"], item["career_min"], item["career_max"] = \
                row["career_type"], row["career_min"], row["career_max"]
        item["source_id"] = row["source_id"]
        postings.upsert_many([item])
        updated += 1
    if rows:
        collect.record("crawl:refresh", "", len(rows), 0, updated, "; ".join(errors[:3]) or None)
    return updated, closed, errors
