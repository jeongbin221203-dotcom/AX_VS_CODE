"""내 화면 설정: 사이드바 메뉴 순서·즐겨찾기 저장 (사이드바 '메뉴 편집'이 부른다)."""
from flask import Blueprint, g, jsonify, request

from core import prefs

from .helpers import PINNED_MENU, menus_for

bp = Blueprint("prefs", __name__, url_prefix="/prefs")


@bp.post("/menu")
def menu():
    allowed = {m[0] for m in menus_for(g.user)} - {PINNED_MENU}       # 대시보드는 항상 맨 위
    if request.form.get("reset") == "1":
        saved = prefs.save_menu(g.user["id"], [], [], allowed)
    else:
        def split(name: str) -> list[str]:
            return [k for k in (request.form.get(name) or "").split(",") if k]
        saved = prefs.save_menu(g.user["id"], split("order"), split("fav"), allowed)
    return jsonify(ok=True, **saved)
