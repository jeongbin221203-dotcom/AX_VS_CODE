"""공고 목록·상세·지원 기록·직접 등록·내보내기."""
from __future__ import annotations

import csv
import hashlib
import io
from datetime import date

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for

from core import applications, fit, jobgroups, postings, profile, sync
from core.normalize import CAREER_TYPES, EDUCATION_LEVELS, SIDO_ORDER

bp = Blueprint("jobs", __name__, url_prefix="/jobs")

FILTER_KEYS = ("q", "sido", "career", "source", "category", "saved", "new", "show_excluded", "min_salary", "min_fit", "eligible", "salary_known",
               "show_closed", "hidden", "sort")


def _filters() -> dict:
    f = {k: request.args.get(k, "") for k in FILTER_KEYS}
    # 직무를 바꾸면 이전 직무의 세부 직무 체크는 버린다
    f["sub"] = [s for s in request.args.getlist("sub") if s in jobgroups.sub_names(f["category"])]
    return f


@bp.get("")
def index():
    f = _filters()
    prof = profile.load()
    facets: dict = {}
    seen = postings.seen_at()
    page = max(1, request.args.get("page", 1, type=int))
    per = 30
    rows, total = postings.query(prof, {**f, "_seen": seen}, page=page, per=per, facets=facets)
    for p in rows:
        p["is_new"] = postings.is_new(p, seen)
    return render_template("jobs.html", rows=rows, total=total, page=page,
                           pages=max(1, -(-total // per)), f=f, sidos=SIDO_ORDER + ["미상"],
                           sources=postings.sources_in_db(), prof=prof, facets=facets,
                           groups=[(g, facets["groups"].get(g, 0)) for g in jobgroups.NAMES],
                           sub_options=[(s, facets["subs"].get(s, 0)) for s in jobgroups.sub_names(f["category"])],
                           saved_n=postings.saved_count(), salary_steps=profile.SALARY_STEPS)


@bp.get("/<int:pid>")
def detail(pid: int):
    p = postings.get(pid)
    if not p:
        abort(404)
    prof = profile.load()
    stats = postings.salary_stats()
    p["dday"] = (date.fromisoformat(p["deadline"]) - date.today()).days if p["deadline"] else None
    return render_template("job_detail.html", p=p, r=fit.evaluate(p, prof), prof=prof,
                           cmp=postings.compare(p, stats), events=applications.events(pid))


@bp.post("/<int:pid>/apply")
def apply(pid: int):
    if not postings.get(pid):
        abort(404)
    status = request.form.get("status", "")
    p = postings.get(pid)
    key = {"source": p["source"], "source_id": p["source_id"]}
    if request.form.get("remove") == "1":
        applications.remove(pid)
        sync.note("app_remove", **key)
        flash("지원 기록을 지웠습니다.", "ok")
    else:
        try:
            applications.upsert(pid, status, memo=request.form.get("memo"),
                                applied_at=request.form.get("applied_at") or None,
                                next_at=request.form.get("next_at") or None,
                                note=request.form.get("note", "").strip()[:200])
            sync.note("app_upsert", **key, status=status, memo=request.form.get("memo"),
                      applied_at=request.form.get("applied_at") or None, next_at=request.form.get("next_at") or None,
                      note=request.form.get("note", "").strip()[:200])
        except ValueError as e:
            abort(400, str(e))
        flash(f"'{status}'(으)로 기록했습니다.", "ok")
    return redirect(_back(url_for(".detail", pid=pid)))


@bp.post("/<int:pid>/company-avg")
def company_avg(pid: int):
    raw = request.form.get("company_avg_salary", "").replace(",", "").strip()
    value = int(raw) if raw.isdigit() and int(raw) > 0 else None
    postings.set_company_avg(pid, value)
    p = postings.get(pid)
    if p:
        sync.note("company_avg", source=p["source"], source_id=p["source_id"], value=value)
    flash("회사 평균연봉을 저장했습니다." if value else "회사 평균연봉을 지웠습니다.", "ok")
    return redirect(url_for(".detail", pid=pid))


@bp.post("/<int:pid>/save")
def save(pid: int):
    if not postings.get(pid):
        abort(404)
    saved = request.form.get("saved") == "1"
    postings.set_saved(pid, saved)
    p = postings.get(pid)
    sync.note("save", source=p["source"], source_id=p["source_id"], value=saved)
    flash("저장했습니다. 마감돼도 '저장한 공고'에서 다시 볼 수 있습니다." if saved
          else "저장을 풀었습니다. 마감되면 자동으로 지워집니다.", "ok")
    return redirect(_back(url_for(".detail", pid=pid)))


@bp.post("/<int:pid>/hide")
def hide(pid: int):
    hidden = request.form.get("hidden") == "1"
    postings.set_hidden(pid, hidden)
    p = postings.get(pid)
    if p:
        sync.note("hide", source=p["source"], source_id=p["source_id"], value=hidden)
    flash("목록에서 숨겼습니다." if hidden else "다시 목록에 보입니다.", "ok")
    return redirect(_back(url_for(".index")))


@bp.route("/new", methods=["GET", "POST"])
def new():
    """API 없는 사이트의 공고를 직접 옮겨 적는다."""
    if request.method == "POST":
        form = request.form
        if not form.get("title", "").strip() or not form.get("company", "").strip():
            flash("회사와 제목은 꼭 적어 주세요.", "err")
            return render_template("job_new.html", form=form, sidos=SIDO_ORDER), 400
        ident = form.get("url") or f"{form['company']}|{form['title']}"
        item = postings.build(
            form.get("site") or "manual", hashlib.sha1(ident.encode("utf-8")).hexdigest()[:16],
            title=form["title"], company=form["company"], url=form.get("url"),
            location=form.get("location"), career=form.get("career"), education=form.get("education"),
            employment_type=form.get("employment_type"), salary_text=form.get("salary_text"),
            company_avg_salary=form.get("company_avg_salary"), job_category=form.get("job_category"),
            keywords=form.get("keywords"), description=form.get("description"), deadline=form.get("deadline"),
            posted_at=date.today())
        postings.upsert_many([item])
        flash("공고를 등록했습니다.", "ok")
        return redirect(url_for(".detail", pid=postings.find_id(item["source"], item["source_id"])))
    return render_template("job_new.html", form={}, sidos=SIDO_ORDER)


@bp.get("/export.csv")
def export():
    prof = profile.load()
    rows = postings.search(prof, _filters())
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["적합도", "판정", "지원가능", "출처", "회사", "제목", "지역", "경력", "학력", "고용형태",
                "연봉(최소,만원)", "연봉(최대,만원)", "연봉 문구", "회사평균연봉", "마감일", "지원상태", "링크", "지원 불가 사유"])
    for p in rows:
        r = p["fit"]
        w.writerow([r.score, r.grade, "O" if r.eligible else "X", p["source"], _safe(p["company"]), _safe(p["title"]),
                    " ".join(filter(None, [p["sido"], p["sigungu"]])), p["career_type"], p["education"],
                    _safe(p["employment_type"]), p["salary_min"], p["salary_max"], _safe(p["salary_raw"]),
                    p["company_avg_salary"], p["deadline"] or "상시", p["app_status"] or "", p["url"] or "",
                    _safe(" / ".join(r.blockers))])
    return Response(buf.getvalue().encode("utf-8-sig"), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=job_fit.csv"})


def _back(default: str) -> str:
    """폼이 알려 준 돌아갈 주소. 같은 사이트 안의 경로만 받는다."""
    back = request.form.get("back", "")
    return back if back.startswith("/") and not back.startswith(("//", "/\\")) else default


def _safe(v) -> str:
    """엑셀에서 수식으로 실행되지 않게 = + - @ 로 시작하는 값 앞에 ' 를 붙인다."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


# 템플릿에서 선택지로 쓴다
bp.add_app_template_global(CAREER_TYPES, "CAREER_TYPES")
bp.add_app_template_global(EDUCATION_LEVELS, "EDUCATION_LEVELS")
