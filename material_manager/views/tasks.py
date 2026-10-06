"""백그라운드 작업 화면 — 큰 업로드 반영·큰 엑셀 추출의 진행 상황과 결과 (core/tasks.py)."""
from __future__ import annotations

from flask import Blueprint, abort, g, jsonify, redirect, url_for

from core import tasks
from views.helpers import Table, file_response, render_page, role_required

bp = Blueprint("tasks", __name__, url_prefix="/tasks")


def _mine(task_id: int) -> dict:
    task = tasks.get(task_id)
    if not tasks.visible(task, g.user):
        abort(404, "없는 작업이거나 볼 권한이 없습니다.")
    return task


def _view(task: dict) -> dict:
    total, done = int(task["total"] or 0), int(task["done"] or 0)
    return {"id": task["id"], "title": task["title"], "status": task["status"], "status_label": tasks.STATUS_LABEL.get(task["status"], task["status"]),
            "message": task["message"] or "", "total": total, "done": done, "percent": int(done * 100 / total) if total else None,
            "file": task["result_name"] or "", "finished": task["status"] in ("DONE", "ERROR"),
            "created_at": task["created_at"], "finished_at": task["finished_at"] or ""}


@bp.get("/")
@role_required("VIEWER")
def index():
    df = tasks.list_for(g.user)
    view = df[["id", "created_at", "title", "status", "message", "user_name"]].copy() if len(df) else df
    if len(view):
        view["status"] = view["status"].map(lambda s: tasks.STATUS_LABEL.get(s, s))
        view["message"] = view["message"].map(lambda m: (m or "")[:120])
        view = view.rename(columns={"id": "번호", "created_at": "시작", "title": "작업", "status": "상태", "message": "결과", "user_name": "요청자"})
    links = [url_for("tasks.detail", task_id=int(i)) for i in df["id"]] if len(df) else []
    return render_page("tasks.html", "data", title_override="백그라운드 작업", task=None,
                       grid=Table(view, {"번호": "{}"}, links=links))


@bp.get("/<int:task_id>")
@role_required("VIEWER")
def detail(task_id: int):
    tasks.mark_stale()
    return render_page("tasks.html", "data", title_override="백그라운드 작업", task=_view(_mine(task_id)), grid=None)


@bp.get("/<int:task_id>.json")
@role_required("VIEWER")
def status(task_id: int):
    tasks.mark_stale()
    return jsonify(_view(_mine(task_id)))


@bp.get("/<int:task_id>/download")
@role_required("VIEWER")
def download(task_id: int):
    task = _mine(task_id)
    data = tasks.result_bytes(task) if task["status"] == "DONE" else None
    if data is None:
        abort(404, "내려받을 파일이 없습니다 (만들지 않았거나 24시간이 지나 정리됐습니다).")
    return file_response(data, task["result_name"], task["result_mime"] or "application/octet-stream")
