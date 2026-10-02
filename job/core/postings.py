"""공고 저장(중복 제거)·조회·필터·평균 연봉 통계."""
from __future__ import annotations

import statistics
from datetime import date, timedelta

from . import db, fit, jobgroups, salary
from .normalize import (CAREER_TYPES, SIDO_ORDER, clean, parse_career, parse_education,
                        parse_region, to_date)

FIELDS = ("url", "title", "company", "sido", "sigungu", "location_raw", "career_type", "career_min",
          "career_max", "career_raw", "education", "employment_type", "salary_raw", "salary_min",
          "salary_max", "salary_negotiable", "company_avg_salary", "job_category", "keywords",
          "description", "posted_at", "deadline")


def build(source: str, source_id, *, title, company, url=None, location=None, career=None,
          career_min=None, career_max=None, education=None, employment_type=None, salary_text=None,
          pay_type=None, salary_min=None, salary_max=None, company_avg_salary=None, job_category=None,
          keywords=None, description=None, posted_at=None, deadline=None) -> dict:
    """출처 어댑터가 넘긴 값을 공통 공고 dict 로 만든다. 연봉 숫자를 직접 주면 문구 해석보다 우선."""
    sido, sigungu = parse_region(location)
    kind, cmin, cmax = parse_career(career, career_min, career_max)
    lo, hi, nego = salary.parse(salary_text, pay_type)
    if salary_min or salary_max:
        lo, hi = salary_min or None, salary_max or None
    if isinstance(keywords, (list, tuple)):
        keywords = ", ".join(clean(k) for k in keywords if clean(k))
    return {
        "source": source,
        "source_id": str(source_id),
        "url": clean(url) or None,
        "title": clean(title)[:300] or "(제목 없음)",
        "company": clean(company)[:200] or "(회사명 없음)",
        "sido": sido,
        "sigungu": sigungu,
        "location_raw": clean(location)[:300] or None,
        "career_type": kind,
        "career_min": cmin,
        "career_max": cmax,
        "career_raw": clean(career)[:100] or None,
        "education": parse_education(education),
        "employment_type": clean(employment_type)[:100] or None,
        "salary_raw": clean(salary_text)[:200] or None,
        "salary_min": lo,
        "salary_max": hi,
        "salary_negotiable": 1 if nego else 0,
        "company_avg_salary": _int(company_avg_salary),
        "job_category": clean(job_category)[:200] or None,
        "keywords": clean(keywords)[:1000] or None,
        "description": clean(description)[:5000] or None,
        "posted_at": to_date(posted_at),
        "deadline": to_date(deadline),
    }


def upsert_many(items: list[dict]) -> tuple[int, int]:
    """(새로 넣은 수, 갱신한 수). 회사 평균연봉을 직접 입력해 둔 값은 비어 있는 값으로 덮지 않는다."""
    inserted = updated = 0
    now = db.now()
    with db.connect() as con:
        for it in items:
            row = con.execute("SELECT id, company_avg_salary FROM postings WHERE source = ? AND source_id = ?",
                              (it["source"], it["source_id"])).fetchone()
            if row:
                data = dict(it)
                if data.get("company_avg_salary") is None:
                    data["company_avg_salary"] = row["company_avg_salary"]
                sets = ", ".join(f"{f} = ?" for f in FIELDS)
                con.execute(f"UPDATE postings SET {sets}, updated_at = ? WHERE id = ?",
                            [data.get(f) for f in FIELDS] + [now, row["id"]])
                updated += 1
            else:
                cols = ("source", "source_id") + FIELDS + ("fetched_at", "updated_at")
                con.execute(f"INSERT INTO postings({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                            [it["source"], it["source_id"]] + [it.get(f) for f in FIELDS] + [now, now])
                inserted += 1
    return inserted, updated


def get(pid: int) -> dict | None:
    with db.connect() as con:
        row = con.execute(_SELECT + " WHERE p.id = ?", (pid,)).fetchone()
    return dict(row) if row else None


def find_id(source: str, source_id: str) -> int | None:
    with db.connect() as con:
        row = con.execute("SELECT id FROM postings WHERE source = ? AND source_id = ?", (source, source_id)).fetchone()
    return row["id"] if row else None


def set_saved(pid: int, saved: bool) -> None:
    with db.connect() as con:
        con.execute("UPDATE postings SET saved = ? WHERE id = ?", (1 if saved else 0, pid))


def purge_closed(today: date | None = None) -> int:
    """마감일이 지난 공고 중 저장하지 않았고 지원 기록도 없는 것을 지운다. 지운 수."""
    today_s = (today or date.today()).isoformat()
    with db.connect() as con:
        return con.execute(
            "DELETE FROM postings WHERE deadline IS NOT NULL AND deadline < ? AND saved = 0 "
            "AND id NOT IN (SELECT posting_id FROM applications)", (today_s,)).rowcount


def mark_closed(pid: int) -> None:
    """원문이 사라진 공고: 마감일을 어제로 둔다 (지원 기록은 남김)."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    with db.connect() as con:
        con.execute("UPDATE postings SET deadline = ?, updated_at = ? WHERE id = ? "
                    "AND (deadline IS NULL OR deadline > ?)", (yesterday, db.now(), pid, yesterday))


def set_company_avg(pid: int, value: int | None) -> None:
    with db.connect() as con:
        con.execute("UPDATE postings SET company_avg_salary = ?, updated_at = ? WHERE id = ?",
                    (value, db.now(), pid))


def set_hidden(pid: int, hidden: bool) -> None:
    with db.connect() as con:
        con.execute("UPDATE postings SET hidden = ? WHERE id = ?", (1 if hidden else 0, pid))


_SELECT = """SELECT p.*, a.status AS app_status, a.memo AS app_memo, a.applied_at, a.next_at
             FROM postings p LEFT JOIN applications a ON a.posting_id = p.id"""


def all_rows(include_hidden: bool = False) -> list[dict]:
    sql = _SELECT + ("" if include_hidden else " WHERE p.hidden = 0")
    with db.connect() as con:
        return [dict(r) for r in con.execute(sql + " ORDER BY p.id DESC")]


def search(prof: dict, f: dict, today: date | None = None, facets: dict | None = None) -> list[dict]:
    """필터·정렬한 공고 목록. 각 행에 'fit'(FitResult), 'dday' 를 붙인다.
    facets 를 넘기면 다른 조건을 통과한 공고로 직무·세부 직무별 건수를 채운다 (선택칸 옆 숫자)."""
    today = today or date.today()
    rows = all_rows(include_hidden=f.get("hidden") == "1")
    q = (f.get("q") or "").strip().lower()
    group = f.get("category") or ""
    raw_subs = f.get("sub") or []
    subs = [x for x in ([raw_subs] if isinstance(raw_subs, str) else raw_subs) if x]
    saved_only = f.get("saved") == "1"
    if facets is not None:
        facets.update({"groups": {}, "subs": {}})
    out = []
    for p in rows:
        if f.get("hidden") == "1" and not p["hidden"]:
            continue
        if saved_only and not p["saved"]:
            continue
        if f.get("new") == "1" and not is_new(p, f.get("_seen") or seen_at()):
            continue
        if q and q not in " ".join(str(p.get(k) or "") for k in
                                   ("title", "company", "keywords", "job_category")).lower():
            continue
        if f.get("sido") and p["sido"] != f["sido"]:
            continue
        if f.get("career") and not _career_match(p["career_type"], f["career"]):
            continue
        if f.get("source") and p["source"] != f["source"]:
            continue
        min_sal = _int(f.get("min_salary"))
        if min_sal and (p["salary_max"] or p["salary_min"] or 0) < min_sal:
            continue
        if f.get("salary_known") == "1" and not (p["salary_min"] or p["salary_max"]):
            continue
        if not saved_only and f.get("show_closed") != "1" and p["deadline"] and p["deadline"] < today.isoformat():
            continue
        p_groups = jobgroups.groups_of(p)
        if facets is not None:
            for g in p_groups:
                facets["groups"][g] = facets["groups"].get(g, 0) + 1
        if group:
            if group not in p_groups:
                continue
            p_subs = jobgroups.subs_of(p, group)
            if facets is not None:
                for sname in p_subs:
                    facets["subs"][sname] = facets["subs"].get(sname, 0) + 1
            if subs and not p_subs.intersection(subs):
                continue
        r = fit.evaluate(p, prof, today)
        if f.get("eligible") == "1" and not r.eligible:
            continue
        min_fit = _int(f.get("min_fit"))
        if min_fit and r.score < min_fit:
            continue
        p["fit"] = r
        p["dday"] = (date.fromisoformat(p["deadline"]) - today).days if p["deadline"] else None
        p["salary_mid"] = salary.midpoint(p["salary_min"], p["salary_max"])
        out.append(p)

    sort = f.get("sort") or "fit"
    if sort == "salary":
        out.sort(key=lambda p: (p["salary_mid"] is None, -(p["salary_mid"] or 0)))
    elif sort == "deadline":
        out.sort(key=lambda p: (p["dday"] is None, p["dday"] if p["dday"] is not None else 0))
    elif sort == "new":
        out.sort(key=lambda p: (p["posted_at"] or "", p["id"]), reverse=True)
    else:
        out.sort(key=lambda p: (-p["fit"].score, p["dday"] if p["dday"] is not None else 9999))
    return out


def _career_match(kind: str | None, want: str) -> bool:
    kind = kind or "무관"
    if want == "신입":
        return kind in ("신입", "신입·경력", "무관")
    if want == "경력":
        return kind in ("경력", "신입·경력", "무관")
    return kind == want


# ── 평균 연봉 ─────────────────────────────────────────────

def _summary(values: list[int]) -> dict:
    if not values:
        return {"n": 0, "avg": None, "median": None, "min": None, "max": None}
    return {"n": len(values), "avg": round(statistics.fmean(values)), "median": round(statistics.median(values)),
            "min": min(values), "max": max(values)}


def salary_stats(rows: list[dict] | None = None) -> dict:
    """공고에 적힌 연봉(범위의 가운데 값)으로 지역·경력·출처별 평균을 낸다. 미공개 공고는 평균에서 뺀다."""
    rows = rows if rows is not None else all_rows()
    by_region: dict[str, list[int]] = {}
    by_career: dict[str, list[int]] = {}
    by_source: dict[str, list[int]] = {}
    by_region_career: dict[tuple[str, str], list[int]] = {}
    total_count: dict[str, int] = {}
    company_avg: list[int] = []
    mids = []
    for p in rows:
        region = p.get("sido") or "미상"
        total_count[region] = total_count.get(region, 0) + 1
        if p.get("company_avg_salary"):
            company_avg.append(p["company_avg_salary"])
        mid = salary.midpoint(p.get("salary_min"), p.get("salary_max"))
        if not mid:
            continue
        mids.append(mid)
        career = p.get("career_type") or "무관"
        by_region.setdefault(region, []).append(mid)
        by_career.setdefault(career, []).append(mid)
        by_source.setdefault(p.get("source") or "-", []).append(mid)
        by_region_career.setdefault((region, career), []).append(mid)

    # 500만원 단위 연봉 구간 (3,000만원 미만 / 3,000~3,499 … / 8,000만원 이상)
    edges = list(range(3000, 8001, 500))
    band_counts = [0] * (len(edges) + 1)
    for m in mids:
        band_counts[sum(1 for e in edges if m >= e)] += 1
    labels = ["3,000만원 미만"] + [f"{e:,}~{e + 499:,}만원" for e in edges[:-1]] + ["8,000만원 이상"]
    bands = [{"name": n, "count": c, "pct": round(c * 100 / len(mids)) if mids else 0} for n, c in zip(labels, band_counts)]

    order = {name: i for i, name in enumerate(SIDO_ORDER + ["미상"])}
    regions = sorted(total_count, key=lambda r: order.get(r, 99))
    return {
        "overall": _summary(mids),
        "total": len(rows),
        "disclosed": len(mids),
        "company_avg": _summary(company_avg),
        "regions": [{"name": r, "count": total_count[r], **_summary(by_region.get(r, []))} for r in regions],
        "careers": [{"name": c, **_summary(by_career.get(c, []))} for c in CAREER_TYPES if c in by_career],
        "sources": [{"name": s, **_summary(v)} for s, v in sorted(by_source.items())],
        "matrix": {f"{r}|{c}": _summary(v)["avg"] for (r, c), v in by_region_career.items()},
        "bands": bands,
    }


def compare(p: dict, stats: dict) -> dict:
    """이 공고 연봉을 같은 지역·같은 경력 구분 평균과 비교한다."""
    mid = salary.midpoint(p.get("salary_min"), p.get("salary_max"))
    region = next((r for r in stats["regions"] if r["name"] == (p.get("sido") or "미상")), None)
    career = next((c for c in stats["careers"] if c["name"] == (p.get("career_type") or "무관")), None)
    both = stats["matrix"].get(f"{p.get('sido') or '미상'}|{p.get('career_type') or '무관'}")

    def diff(avg):
        return None if (mid is None or not avg) else mid - avg

    return {
        "mid": mid,
        "region_avg": region["avg"] if region else None, "region_n": region["n"] if region else 0,
        "career_avg": career["avg"] if career else None, "career_n": career["n"] if career else 0,
        "both_avg": both,
        "overall_avg": stats["overall"]["avg"],
        "diff_region": diff(region["avg"] if region else None),
        "diff_career": diff(career["avg"] if career else None),
        "diff_overall": diff(stats["overall"]["avg"]),
    }


def seen_at() -> str:
    """마지막으로 '확인함'을 누른 시각. 없으면 하루 전 (처음에는 최근 하루치를 새 공고로)."""
    from datetime import datetime
    return db.get_setting("seen_at") or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")


def mark_seen() -> None:
    db.set_setting("seen_at", db.now())


def is_new(p: dict, seen: str) -> bool:
    return (p.get("fetched_at") or "") > seen


def saved_count() -> int:
    with db.connect() as con:
        return con.execute("SELECT COUNT(*) FROM postings WHERE saved = 1").fetchone()[0]


def sources_in_db() -> list[str]:
    with db.connect() as con:
        return [r[0] for r in con.execute("SELECT DISTINCT source FROM postings ORDER BY source")]


def delete_source(source: str) -> int:
    with db.connect() as con:
        return con.execute("DELETE FROM postings WHERE source = ?", (source,)).rowcount


def _int(v) -> int | None:
    try:
        return int(float(str(v).replace(",", ""))) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None
