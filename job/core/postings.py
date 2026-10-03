"""공고 저장(중복 제거)·조회·필터·평균 연봉 통계."""
from __future__ import annotations

import json
import statistics
from datetime import date, timedelta

from . import db, fit, jobgroups, salary
from .normalize import (CAREER_TYPES, SIDO_ORDER, clean, parse_career, parse_education,
                        parse_region, to_date)

FIELDS = ("url", "title", "company", "sido", "sigungu", "location_raw", "career_type", "career_min",
          "career_max", "career_raw", "education", "employment_type", "salary_raw", "salary_min",
          "salary_max", "salary_negotiable", "company_avg_salary", "company_info", "job_category", "keywords",
          "description", "posted_at", "deadline")


def build(source: str, source_id, *, title, company, url=None, location=None, career=None,
          career_min=None, career_max=None, education=None, employment_type=None, salary_text=None,
          pay_type=None, salary_min=None, salary_max=None, company_avg_salary=None, job_category=None,
          keywords=None, description=None, posted_at=None, deadline=None, company_info=None) -> dict:
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
        "description": _clean_body(description)[:8000] or None,
        # None = 상세를 아직 안 읽음, '{}' = 읽었지만 기업정보가 없는 사이트·공고
        "company_info": json.dumps(company_info, ensure_ascii=False) if company_info is not None else None,
        "posted_at": to_date(posted_at),
        "deadline": to_date(deadline),
    }


def _clean_body(text) -> str:
    """본문은 줄바꿈을 살린다 (공백만 정리)."""
    if text is None:
        return ""
    lines = [clean(line) for line in str(text).replace("\r", "").split("\n")]
    out, blank = [], False
    for line in lines:
        if line:
            out.append(line)
            blank = False
        elif not blank and out:
            out.append("")
            blank = True
    return "\n".join(out).strip()


def keep_existing(item: dict) -> dict:
    """같은 공고를 다시 읽었을 때 새 페이지에 없는 값(목록에서 채운 근무지·직무, 직접 적은 값)은 그대로 둔다."""
    pid = find_id(item["source"], item["source_id"])
    if not pid:
        return item
    old = get(pid)
    for k in FIELDS:
        if item.get(k) in (None, "") and old.get(k) not in (None, ""):
            item[k] = old[k]
    if item.get("career_type") == "무관" and not item.get("career_raw") and old.get("career_type") not in (None, "무관"):
        item["career_type"], item["career_min"], item["career_max"] = old["career_type"], old["career_min"], old["career_max"]
    return item


def upsert_many(items: list[dict]) -> tuple[int, int]:
    """저장하고, 저장한 공고의 점수·직무를 바로 계산해 둔다."""
    ins, upd = _upsert(items)
    for it in items:
        for flag in it.get("_flags") or []:
            add_flags(it["source"], [it["source_id"]], flag)
    ids = [i for i in (find_id(it["source"], it["source_id"]) for it in items) if i]
    if ids:
        recompute(ids)
    return ins, upd


def _upsert(items: list[dict]) -> tuple[int, int]:
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


STALE_DAYS = 60


def purge_stale(days: int = STALE_DAYS) -> int:
    """마감일 없는(상시) 공고 중 처음 받은 지 오래된 것 — 저장·지원 기록이 없으면 지운다 (데이터가 끝없이 늘지 않게)."""
    from datetime import datetime
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with db.connect() as con:
        return con.execute(
            "DELETE FROM postings WHERE deadline IS NULL AND saved = 0 AND fetched_at < ? "
            "AND id NOT IN (SELECT posting_id FROM applications)", (cutoff,)).rowcount


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


_SELECT = """SELECT p.*, a.status AS app_status, a.memo AS app_memo, a.applied_at, a.next_at,
                    (SELECT group_concat(flag, ',') FROM post_flags g
                     WHERE g.source = p.source AND g.source_id = p.source_id) AS flags
             FROM postings p LEFT JOIN applications a ON a.posting_id = p.id"""


def add_flags(source: str, ids, flag: str) -> int:
    with db.connect() as con:
        return con.executemany("INSERT OR REPLACE INTO post_flags(source, source_id, flag, seen_at) VALUES(?, ?, ?, ?)",
                               [(source, str(i), flag, db.now()) for i in ids]).rowcount


def flagged(source: str, flag: str) -> set[str]:
    with db.connect() as con:
        return {r[0] for r in con.execute("SELECT source_id FROM post_flags WHERE source = ? AND flag = ?",
                                          (source, flag))}


def all_rows(include_hidden: bool = False) -> list[dict]:
    sql = _SELECT + ("" if include_hidden else " WHERE p.hidden = 0")
    with db.connect() as con:
        return [dict(r) for r in con.execute(sql + " ORDER BY p.id DESC")]


def fit_fields(p: dict, prof: dict) -> tuple[int, int, int, str, str]:
    """공고마다 미리 계산해 두는 값: (점수, 지원 가능, 제외 항목 걸림, 직무들, 세부 직무들).
    '지원 가능'에서 마감 여부는 빼고 저장한다 — 마감은 날짜가 지나며 바뀌므로 조회할 때 deadline 으로 따로 거른다."""
    r = fit.evaluate(p, prof, date(2000, 1, 1))                 # 마감 판단이 끼지 않는 날짜
    ok = 0 if r.blockers else 1
    groups = sorted(jobgroups.groups_of(p))
    subs = sorted(f"{g}/{s}" for g in groups for s in jobgroups.subs_of(p, g))
    return r.score, ok, 1 if r.excluded else 0, "|" + "|".join(groups) + "|", "|" + "|".join(subs) + "|"


def recompute(ids: list[int] | None = None, prof: dict | None = None) -> int:
    """미리 계산한 점수·직무를 다시 계산한다. ids 가 없으면 전부 (내 조건을 바꿨을 때, 수집이 끝났을 때)."""
    from . import profile as profile_mod
    prof = prof or profile_mod.load()
    sql = _SELECT + (" WHERE p.id IN (%s)" % ",".join("?" * len(ids)) if ids else "")
    with db.connect() as con:
        rows = [dict(r) for r in con.execute(sql, ids or [])]
        con.executemany("UPDATE postings SET fit_score = ?, fit_ok = ?, fit_excl = ?, grp = ?, subgrp = ? WHERE id = ?",
                        [(*fit_fields(r, prof), r["id"]) for r in rows])
    return len(rows)


def recompute_missing() -> int:
    """아직 점수·직무를 계산하지 않은 공고 (열을 새로 더한 뒤 처음 켤 때)."""
    with db.connect() as con:
        ids = [r[0] for r in con.execute("SELECT id FROM postings WHERE grp IS NULL")]
    return recompute(ids) if ids else 0


_SORTS = {
    "fit": "p.fit_score DESC, p.deadline IS NULL, p.deadline",
    "salary": "COALESCE((p.salary_min + p.salary_max) / 2, p.salary_min, p.salary_max) IS NULL, "
              "COALESCE((p.salary_min + p.salary_max) / 2, p.salary_min, p.salary_max) DESC",
    "deadline": "p.deadline IS NULL, p.deadline",
    "new": "p.posted_at IS NULL, p.posted_at DESC, p.id DESC",
}
_CAREER_IN = {"신입": ("신입", "신입·경력", "무관"), "경력": ("경력", "신입·경력", "무관"), "무관": ("무관",)}


def _where(f: dict, today: date, *, group: bool = True, excluded: bool = True) -> tuple[list[str], list]:
    """목록 조건 → SQL. group/excluded=False 는 선택칸 옆 건수(직무별·제외 건수)를 셀 때 그 조건만 빼려고."""
    w, a = [], []
    w.append("p.hidden = 1" if f.get("hidden") == "1" else "p.hidden = 0")
    if f.get("saved") == "1":
        w.append("p.saved = 1")
    elif f.get("show_closed") != "1":
        w.append("(p.deadline IS NULL OR p.deadline >= ?)")
        a.append(today.isoformat())
    if f.get("new") == "1":
        w.append("p.fetched_at > ?")
        a.append(f.get("_seen") or seen_at())
    q = (f.get("q") or "").strip()
    if q:
        w.append("LOWER(p.title || ' ' || p.company || ' ' || COALESCE(p.keywords, '') || ' ' || "
                 "COALESCE(p.job_category, '')) LIKE ?")
        a.append(f"%{q.lower()}%")
    if f.get("sido"):
        if f["sido"] == "미상":                                     # 근무지를 못 읽은 공고
            w.append("p.sido IS NULL")
        else:
            w.append("p.sido = ?")
            a.append(f["sido"])
    if f.get("career") in _CAREER_IN:
        kinds = _CAREER_IN[f["career"]]
        w.append(f"COALESCE(p.career_type, '무관') IN ({','.join('?' * len(kinds))})")
        a += list(kinds)
    if f.get("source"):
        w.append("p.source = ?")
        a.append(f["source"])
    min_sal = _int(f.get("min_salary"))
    if min_sal:
        w.append("COALESCE(p.salary_max, p.salary_min, 0) >= ?")
        a.append(min_sal)
    if f.get("salary_known") == "1":
        w.append("(p.salary_min IS NOT NULL OR p.salary_max IS NOT NULL)")
    if f.get("eligible") == "1":
        w.append("p.fit_ok = 1")
    min_fit = _int(f.get("min_fit"))
    if min_fit:
        w.append("p.fit_score >= ?")
        a.append(min_fit)
    if excluded and f.get("show_excluded") != "1":
        w.append("p.fit_excl = 0")
    if group and f.get("category"):
        w.append("p.grp LIKE ?")
        a.append(f"%|{f['category']}|%")
        subs = _subs(f)
        if subs:
            w.append("(" + " OR ".join("p.subgrp LIKE ?" for _ in subs) + ")")
            a += [f"%|{f['category']}/{s}|%" for s in subs]
    return w, a


def _subs(f: dict) -> list[str]:
    raw = f.get("sub") or []
    return [x for x in ([raw] if isinstance(raw, str) else raw) if x]


def _count(where: list[str], args: list) -> int:
    with db.connect() as con:
        return con.execute("SELECT COUNT(*) FROM postings p WHERE " + " AND ".join(where), args).fetchone()[0]


def query(prof: dict, f: dict, today: date | None = None, page: int = 1, per: int | None = 30,
          facets: dict | None = None) -> tuple[list[dict], int]:
    """조건에 맞는 공고 (한 쪽 분량)와 전체 건수. 화면에 보일 행만 적합성 이유를 자세히 계산한다."""
    today = today or date.today()
    where, args = _where(f, today)
    total = _count(where, args)
    order = _SORTS.get(f.get("sort") or "fit", _SORTS["fit"])
    sql = _SELECT + " WHERE " + " AND ".join(where) + f" ORDER BY {order}, p.id DESC"
    if per:
        sql += f" LIMIT {int(per)} OFFSET {max(0, (page - 1) * int(per))}"
    with db.connect() as con:
        rows = [dict(r) for r in con.execute(sql, args)]
    for p in rows:
        p["fit"] = fit.evaluate(p, prof, today)
        p["dday"] = (date.fromisoformat(p["deadline"]) - today).days if p["deadline"] else None
        p["salary_mid"] = salary.midpoint(p["salary_min"], p["salary_max"])

    if facets is not None:
        # 직무·세부 직무 건수는 한 번 읽어 세어 본다 (직무마다 따로 세면 수만 건에서 느림)
        base, base_args = _where(f, today, group=False)
        groups: dict[str, int] = {}
        subs: dict[str, int] = {}
        g_sel = f.get("category") or ""
        with db.connect() as con:
            for grp, subgrp in con.execute("SELECT p.grp, p.subgrp FROM postings p WHERE " + " AND ".join(base),
                                           base_args):
                gs = (grp or "").strip("|").split("|")
                for g in gs:
                    if g:
                        groups[g] = groups.get(g, 0) + 1
                if g_sel and g_sel in gs:
                    for gs_ in (subgrp or "").strip("|").split("|"):
                        if gs_.startswith(g_sel + "/"):
                            name = gs_.split("/", 1)[1]
                            subs[name] = subs.get(name, 0) + 1
        facets["groups"] = groups
        facets["subs"] = subs
        if f.get("show_excluded") != "1":
            ex, ex_args = _where(f, today, excluded=False)
            facets["excluded"] = _count(ex + ["p.fit_excl = 1"], ex_args)
    return rows, total


def open_counts(today: date | None = None) -> dict:
    """공고 목록 기본 화면과 같은 기준(마감·제외·숨김 뺌)의 지역·출처·신입/경력별 공고 수."""
    where, args = _where({}, today or date.today())
    out = {"sido": {}, "source": {}, "career": {}}
    with db.connect() as con:
        for sido, source, career, n in con.execute(
                "SELECT COALESCE(p.sido, '미상'), p.source, COALESCE(p.career_type, '무관'), COUNT(*) FROM postings p "
                "WHERE " + " AND ".join(where) + " GROUP BY 1, 2, 3", args):
            out["sido"][sido] = out["sido"].get(sido, 0) + n
            out["source"][source] = out["source"].get(source, 0) + n
            for want, kinds in _CAREER_IN.items():
                if career in kinds:
                    out["career"][want] = out["career"].get(want, 0) + n
            if career not in _CAREER_IN:                    # 신입·경력 칸 이름 그대로도 셈
                out["career"][career] = out["career"].get(career, 0) + n
    return out


def search(prof: dict, f: dict, today: date | None = None, facets: dict | None = None) -> list[dict]:
    """조건에 맞는 공고 전부 (내보내기·테스트용). 화면 목록은 query 로 한 쪽씩."""
    return query(prof, f, today, per=None, facets=facets)[0]


def career_match(kind: str | None, want: str) -> bool:
    """목록의 신입/경력 필터와 같은 기준 (신입 = 신입·신입경력·무관)."""
    return _career_match(kind, want)


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
    if rows is None:                              # 필요한 열만 (본문까지 읽으면 수만 건에서 느림)
        with db.connect() as con:
            rows = [dict(r) for r in con.execute(
                "SELECT sido, career_type, source, salary_min, salary_max, company_avg_salary, employment_type, title "
                "FROM postings WHERE hidden = 0")]
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
        mid = None if salary.is_commission(p) else salary.midpoint(p.get("salary_min"), p.get("salary_max"))
        if not mid:                                 # 미공개·성과급 직군은 평균에서 뺌
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
