"""첫 화면(요약)과 평균 연봉 통계."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, url_for

from core import applications, db, postings, profile
from core.normalize import CAREER_TYPES, SIDO_ORDER

bp = Blueprint("main", __name__)
NEW_MIN_FIT = 80          # 새 공고 중 이 점수 이상이고 지원 가능한 것만 '맞는 새 공고'


@bp.get("/")
def dashboard():
    prof = profile.load()
    rows = postings.search(prof, {})
    eligible = [p for p in rows if p["fit"].eligible]
    closing = sorted([p for p in rows if p["dday"] is not None and p["dday"] <= 7 and p["fit"].eligible
                      and p["app_status"] in (None, "관심", "지원 예정")], key=lambda p: p["dday"])
    stats = postings.salary_stats()
    seen = postings.seen_at()
    fresh = [p for p in eligible if p["fit"].score >= NEW_MIN_FIT and postings.is_new(p, seen)]
    return render_template("dashboard.html", prof=prof, top=eligible[:8], total=len(rows),
                           eligible_n=len(eligible), good_n=sum(p["fit"].score >= 80 for p in eligible),
                           closing=closing[:8], counts=applications.counts(), stats=stats,
                           fresh=fresh[:10], fresh_n=len(fresh), seen=seen, new_min_fit=NEW_MIN_FIT,
                           profile_empty=db.get_setting("profile") is None)


@bp.post("/seen")
def mark_seen():
    """'새로 들어온 맞는 공고'를 모두 확인함 — 이 시각 뒤에 들어온 공고만 새 공고로 본다."""
    postings.mark_seen()
    flash("확인했습니다. 다음 수집부터 들어온 공고만 새 공고로 표시합니다.", "ok")
    return redirect(url_for(".dashboard"))


@bp.get("/stats")
def stats():
    st = postings.salary_stats()
    peak = max([r["avg"] or 0 for r in st["regions"]] + [1])
    regions = [r["name"] for r in st["regions"]]
    open_rows = postings.search(profile.load(), {})       # 공고 목록 기본 화면과 같은 기준 (마감·제외 숨김)
    open_n = {"sido": {}, "source": {}, "career": {c: sum(1 for p in open_rows if postings.career_match(p["career_type"], c))
                                                    for c in CAREER_TYPES}}
    for p in open_rows:
        open_n["sido"][p["sido"] or "미상"] = open_n["sido"].get(p["sido"] or "미상", 0) + 1
        open_n["source"][p["source"]] = open_n["source"].get(p["source"], 0) + 1
    return render_template("stats.html", st=st, peak=peak, regions=regions, open_n=open_n,
                           careers=[c["name"] for c in st["careers"]], sido_order=SIDO_ORDER)
