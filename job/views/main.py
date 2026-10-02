"""첫 화면(요약)과 평균 연봉 통계."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, url_for

from core import applications, db, postings, profile
from core.normalize import SIDO_ORDER

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
    return render_template("stats.html", st=st, peak=peak, regions=regions,
                           careers=[c["name"] for c in st["careers"]], sido_order=SIDO_ORDER)
