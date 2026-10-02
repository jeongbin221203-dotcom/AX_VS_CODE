"""내 구직 조건."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for

from core import jobgroups, profile
from core.normalize import EDUCATION_LEVELS, SIDO_ORDER

bp = Blueprint("profile", __name__, url_prefix="/profile")


@bp.route("", methods=["GET", "POST"])
def edit():
    if request.method == "POST":
        profile.save(profile.from_form(request.form))
        flash("내 조건을 저장했습니다. 적합성 점수가 새 조건으로 다시 계산됩니다.", "ok")
        return redirect(url_for("jobs.index"))
    groups = [{"name": g, "subs": jobgroups.sub_names(g)} for g in jobgroups.NAMES]
    return render_template("profile.html", prof=profile.load(), sidos=SIDO_ORDER[:-1],
                           edus=EDUCATION_LEVELS, emp_types=profile.EMPLOYMENT_TYPES,
                           salary_steps=profile.SALARY_STEPS, groups=groups)
