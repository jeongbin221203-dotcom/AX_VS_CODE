"""첫 화면(요약)과 평균 연봉 통계."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, url_for

from core import applications, db, postings, profile, sync
from core.normalize import SIDO_ORDER

bp = Blueprint("main", __name__)
NEW_MIN_FIT = 80          # 새 공고 중 이 점수 이상이고 지원 가능한 것만 '맞는 새 공고'


@bp.get("/")
def dashboard():
    # 전체를 훑지 않고 건수·상위 몇 건만 DB 에서 (공고가 수만 건이어도 빠르게)
    prof = profile.load()
    seen = postings.seen_at()
    _, total = postings.query(prof, {}, per=1)
    top, eligible_n = postings.query(prof, {"eligible": "1"}, per=8)
    _, good_n = postings.query(prof, {"eligible": "1", "min_fit": "80"}, per=1)
    fresh, fresh_n = postings.query(prof, {"eligible": "1", "min_fit": str(NEW_MIN_FIT), "new": "1", "_seen": seen},
                                    per=10)
    soon, _ = postings.query(prof, {"eligible": "1", "sort": "deadline"}, per=60)
    closing = [p for p in soon if p["dday"] is not None and p["dday"] <= 7
               and p["app_status"] in (None, "관심", "지원 예정")][:8]
    stats = postings.salary_stats()
    return render_template("dashboard.html", prof=prof, top=top, total=total,
                           eligible_n=eligible_n, good_n=good_n,
                           closing=closing, counts=applications.counts(), stats=stats,
                           fresh=fresh, fresh_n=fresh_n, seen=seen, new_min_fit=NEW_MIN_FIT,
                           profile_empty=db.get_setting("profile") is None)


@bp.post("/seen")
def mark_seen():
    """'새로 들어온 맞는 공고'를 모두 확인함 — 이 시각 뒤에 들어온 공고만 새 공고로 본다."""
    postings.mark_seen()
    sync.note("seen", value=db.get_setting("seen_at"))
    flash("확인했습니다. 다음 수집부터 들어온 공고만 새 공고로 표시합니다.", "ok")
    return redirect(url_for(".dashboard"))


@bp.get("/stats")
def stats():
    st = postings.salary_stats()
    peak = max([r["avg"] or 0 for r in st["regions"]] + [1])
    regions = [r["name"] for r in st["regions"]]
    open_n = postings.open_counts()                 # 공고 목록 기본 화면과 같은 기준 (마감·제외 숨김), DB 에서 셈
    return render_template("stats.html", st=st, peak=peak, regions=regions, open_n=open_n,
                           careers=[c["name"] for c in st["careers"]], career_labels=postings.CAREER_LABELS,
                           sido_order=SIDO_ORDER)
