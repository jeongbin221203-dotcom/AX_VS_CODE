"""내 화면 설정: 사이드바 메뉴 순서·즐겨찾기 저장 (사이드바 '메뉴 편집'이 부른다)."""

from flask import Blueprint, g, jsonify, request

from core import prefs
from views.helpers import PINNED_MENU, f_str, menus_for_user

bp = Blueprint("prefs", __name__, url_prefix="/prefs")


@bp.post("/menu")
def menu():
    allowed = {m[0] for m in menus_for_user()} - {PINNED_MENU}       # 대시보드는 항상 맨 위
    if f_str("reset") == "1":
        saved = prefs.save_menu(g.user["id"], [], [], allowed)
    else:
        split = lambda name: [k for k in f_str(name).split(",") if k]   # noqa: E731
        saved = prefs.save_menu(g.user["id"], split("order"), split("fav"), allowed)
    return jsonify(ok=True, **saved)
