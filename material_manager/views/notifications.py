"""🔔 알림함: 결재 요청·결과·독촉이 사람마다 쌓인다 (메일·메신저와 별개로 늘). 열면 읽음으로 바뀌고 그 화면으로 간다."""

from flask import Blueprint, abort, flash, g, redirect, request, url_for

from core import db, notify
from views.helpers import as_id, render_page

bp = Blueprint("notifications", __name__, url_prefix="/notifications")


@bp.get("/")
def index():
    only_unread = request.args.get("all") != "1"
    rows = notify.inbox_df(g.user["id"], only_unread=only_unread).to_dict("records")
    return render_page("notifications.html", "notifications", rows=rows, only_unread=only_unread, title_override="🔔 알림")


@bp.get("/<int:nid>/open")
def open_(nid: int):
    row = db.query_df("SELECT link FROM notifications WHERE id = ? AND channel = 'inbox' AND to_user_id = ?",
                      (nid, g.user["id"]))
    if row.empty:
        abort(404)
    notify.mark_read(g.user["id"], [nid])
    link = row.iloc[0]["link"] or "/"
    return redirect(link if link.startswith("/") and not link.startswith("//") else "/")


@bp.post("/read")
def read():
    ids = [i for i in (as_id(v) for v in request.form.getlist("id")) if i is not None]
    n = notify.mark_read(g.user["id"], ids or None)
    flash(f"{n}건을 읽음으로 바꿨습니다.", "success")
    return redirect(url_for("notifications.index"))
