"""출처별 수집 실행과 기록(fetch_runs). 한 출처가 실패해도 나머지는 계속한다."""
from __future__ import annotations

import json
from pathlib import Path

from . import db, postings
from .sources import FetchQuery, SourceError, registry


def run(keys: list[str], q: FetchQuery) -> list[dict]:
    results = []
    sources = {s["key"]: s for s in registry()}
    for key in keys:
        src = sources.get(key)
        if not src:
            continue
        run_id = _start(key, q)
        try:
            items = src["fetch"](q)
            ins, upd = postings.upsert_many(items)
            _finish(run_id, len(items), ins, upd, None)
            results.append({"key": key, "name": src["name"], "ok": True, "fetched": len(items),
                            "inserted": ins, "updated": upd})
        except SourceError as e:
            _finish(run_id, 0, 0, 0, str(e))
            results.append({"key": key, "name": src["name"], "ok": False, "error": str(e)})
        except Exception as e:  # 응답 형식 변경 등 예상 못 한 오류도 화면에 남긴다
            _finish(run_id, 0, 0, 0, f"{e.__class__.__name__}: {e}")
            results.append({"key": key, "name": src["name"], "ok": False,
                            "error": f"처리 중 오류 ({e.__class__.__name__})"})
    return results


def import_links(urls: list[str]) -> list[dict]:
    """링크마다 결과를 따로 남긴다. 같은 링크는 다시 넣어도 갱신만 된다."""
    from .sources import SOURCE_NAMES, linkimport
    results = []
    for url in urls:
        site = linkimport.site_of(url) or "manual"
        name = SOURCE_NAMES.get(site, "링크")
        try:
            item = linkimport.fetch(url)
        except SourceError as e:
            record(site, url[:200], 0, 0, 0, str(e))
            results.append({"name": name, "ok": False, "error": f"{e} — {url}"})
            continue
        except Exception as e:  # 페이지 형식이 예상과 다름
            record(site, url[:200], 0, 0, 0, f"{e.__class__.__name__}: {e}")
            results.append({"name": name, "ok": False, "error": f"읽기 실패({e.__class__.__name__}) — {url}"})
            continue
        ins, upd = postings.upsert_many([item])
        record(site, url[:200], 1, ins, upd)
        results.append({"name": name, "ok": True, "fetched": 1, "inserted": ins, "updated": upd,
                        "title": f"{item['company']} · {item['title']}",
                        "pid": postings.find_id(item["source"], item["source_id"])})
    return results


def record(source: str, query: str, fetched: int, inserted: int, updated: int, error: str | None = None) -> None:
    """파일 가져오기·샘플처럼 fetch 를 거치지 않는 수집도 같은 기록에 남긴다."""
    rid = _start(source, FetchQuery(keyword=query))
    _finish(rid, fetched, inserted, updated, error)


def load_sample(path: Path) -> tuple[int, int]:
    """학습·시연용 가상 공고. 실제 회사가 아니며 출처는 'sample'."""
    from datetime import date, timedelta
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    today = date.today()
    items = []
    for i, r in enumerate(data):
        r = dict(r)
        # 마감일은 오늘 기준 며칠 뒤로 두어 언제 넣어도 살아 있는 공고가 되게 한다
        if "deadline_in" in r:
            d = r.pop("deadline_in")
            r["deadline"] = None if d is None else (today + timedelta(days=d)).isoformat()
        r["posted_at"] = (today - timedelta(days=r.pop("posted_ago", 1))).isoformat()
        items.append(postings.build("sample", f"s{i + 1:03d}", **r))
    ins, upd = postings.upsert_many(items)
    record("sample", "", len(items), ins, upd)
    return ins, upd


def history(limit: int = 30) -> list[dict]:
    with db.connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM fetch_runs ORDER BY id DESC LIMIT ?", (limit,))]


def _start(source: str, q: FetchQuery) -> int:
    label = " ".join(filter(None, [q.keyword, q.sido, q.career]))
    with db.connect() as con:
        return con.execute("INSERT INTO fetch_runs(source, query, started_at) VALUES(?, ?, ?)",
                           (source, label, db.now())).lastrowid


def _finish(run_id: int, fetched: int, inserted: int, updated: int, error: str | None) -> None:
    with db.connect() as con:
        con.execute("UPDATE fetch_runs SET finished_at = ?, fetched = ?, inserted = ?, updated = ?, error = ? "
                    "WHERE id = ?", (db.now(), fetched, inserted, updated, error, run_id))
