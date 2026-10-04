"""거래처 마스터 화면: 목록(실적) · 등록 · 미등록 이름 정리(새로 등록하거나 기존 거래처에 연결) · 상세(수정·다른 이름·최근 거래).
규칙은 core/partners.py. 보기는 모두, 바꾸기는 관리자 이상."""

import pandas as pd
from flask import Blueprint, abort, flash, g, redirect, request, url_for

from core import audit, bulk, db, partners
from views import bulk_ui
from views.helpers import (Table, actor, as_id, can_master, f_str, form_response, log_export, master_required,
                           page_arg, pager, render_page)

bp = Blueprint("partners", __name__, url_prefix="/partners")

TABS = [("list", "📋 거래처 목록"), ("unknown", "🧹 미등록 이름 정리"), ("merge", "🔀 중복 · 병합"), ("new", "➕ 거래처 등록")]


BIG_OPTIONS = 300                       # 거래처가 이보다 많으면 고르기 대신 입력해서 찾기


def _options() -> dict:
    """연결·병합에서 고를 거래처. 많으면 datalist 하나('[코드] 이름')를 함께 쓰고 서버가 코드로 찾는다."""
    opts = db.query_df("SELECT id, name, code FROM partners WHERE active = 1 ORDER BY name").to_dict("records")
    return {"options": opts, "big_options": len(opts) > BIG_OPTIONS}


def _pick(field: str):
    """폼에서 고른 거래처: 번호(select) 또는 '[코드] 이름'(입력해서 찾기). 없으면 None."""
    pid = as_id(f_str(field))
    if pid is not None:
        return pid
    ref = f_str(field + "_ref")
    code = ref[1:ref.index("]")] if ref.startswith("[") and "]" in ref else ref.strip()
    if not code:
        return None
    row = db.query_df("SELECT id FROM partners WHERE UPPER(code) = ? OR name = ?", (code.upper(), ref.strip()))
    return int(row.iloc[0]["id"]) if len(row) == 1 else None


def _form() -> dict:
    return {f: f_str(f) for f in partners.FIELDS}


def _tabs():
    return TABS if can_master() else TABS[:2]



@bp.get("/")
def index():
    tab = request.args.get("tab", "list")
    if tab in ("new", "merge") and not can_master():
        tab = "list"
    if tab == "merge":
        return render_page("partners.html", "partners", tabs=_tabs(), tab="merge", candidates=partners.merge_candidates(),
                           **_options())
    if tab == "new":
        return render_page("partners.html", "partners", tabs=_tabs(), tab="new", form={"kind": "SUPPLIER"})
    if tab == "unknown":
        df = partners.unknown_names()
        return render_page("partners.html", "partners", tabs=_tabs(), tab="unknown", unknown=df.to_dict("records"),
                           **_options())
    q = request.args.get("q", "").strip()[:60]
    inactive = request.args.get("inactive") == "1"
    df = partners.list_df(q, include_inactive=inactive, wh_ids=g.wh_ids)
    if request.args.get("export") == "xlsx":
        view = _view(df)
        log_export("partners", len(view))
        return form_response("partners", view, "거래처.xlsx")
    pg = pager(len(df), page_arg())                     # 화면은 100건씩 (내려받기는 전부)
    df = df.iloc[pg["first"] - 1:pg["last"]] if len(df) else df
    view = _view(df)
    return render_page("partners.html", "partners", tabs=_tabs(), tab="list", q=q, inactive=inactive,
                       unknown_cnt=partners.unknown_count(), pg=pg,
                       grid=Table(view, {"입고금액": "₩{:,.0f}", "출고금액": "₩{:,.0f}", "거래 수": "{:,.0f}"},
                                  links=[url_for("partners.detail", pid=int(i)) for i in df["id"]] if len(df) else [],
                                  tones=["muted" if not a else None for a in df["active"]] if len(df) else []))


def _view(df):
    if df.empty:
        return df.reindex(columns=["거래처코드", "거래처명", "구분", "사업자등록번호", "담당자", "전화", "메일", "다른 이름",
                                   "입고금액", "출고금액", "거래 수", "마지막 거래", "상태"])
    merged = df["merged_into"] if "merged_into" in df.columns else pd.Series([None] * len(df))
    out = df.assign(kind=df["kind"].map(partners.KINDS), biz_no=df["biz_no"].map(partners.biz_fmt),
                    active=["병합됨" if m == m and m is not None else ("사용" if a else "중지")
                            for a, m in zip(df["active"], merged)])
    out = out[["code", "name", "kind", "biz_no", "contact", "phone", "email", "aliases", "in_amt", "out_amt", "tx_cnt",
               "last_tx", "active"]]
    out.columns = ["거래처코드", "거래처명", "구분", "사업자등록번호", "담당자", "전화", "메일", "다른 이름", "입고금액", "출고금액",
                   "거래 수", "마지막 거래", "상태"]
    return out


@bp.get("/<int:pid>")
def detail(pid: int):
    p = partners.get(pid) or abort(404)
    recent = partners.recent_tx(pid, wh_ids=g.wh_ids)
    view = recent.rename(columns={"id": "ID", "tx_date": "일자", "tx_type": "구분", "code": "자재코드", "name": "자재명",
                                  "qty": "수량", "unit": "단위", "unit_price": "단가", "amount": "금액", "partner": "적힌 이름",
                                  "ref_no": "문서번호", "wh_code": "창고"})
    if len(view):
        view["구분"] = view["구분"].map({"IN": "입고", "OUT": "출고"})
        view = view[["ID", "일자", "구분", "창고", "자재코드", "자재명", "수량", "단위", "단가", "금액", "적힌 이름", "문서번호"]]
    return render_page("partner_detail.html", "partners", p=p, form=p, aliases=partners.aliases_df(pid).to_dict("records"),
                       grid=Table(view, {"ID": "{}", "수량": "{:,.2f}", "단가": "₩{:,.0f}", "금액": "₩{:,.0f}"}),
                       biz=partners.biz_fmt(p["biz_no"]), history=audit.entity_history("partner", pid))


@bp.post("/new")
@master_required
def create():
    r = partners.create(_form(), actor(), code=f_str("code"))
    flash(r.message, "success" if r.ok else "error")
    if not r.ok:
        return render_page("partners.html", "partners", tabs=_tabs(), tab="new", form={**_form(), "code": f_str("code")})
    return redirect(url_for("partners.detail", pid=r.id))


@bp.post("/<int:pid>")
@master_required
def update(pid: int):
    r = partners.update(pid, _form(), actor(), expected=f_str("updated_at") or None)
    flash(r.message, "success" if r.ok else "error")
    return redirect(url_for("partners.detail", pid=pid))


@bp.post("/<int:pid>/active")
@master_required
def set_active(pid: int):
    r = partners.set_active(pid, f_str("active") == "1", actor())
    flash(r.message, "success" if r.ok else "error")
    return redirect(url_for("partners.detail", pid=pid))


@bp.post("/link")
@master_required
def link():
    """미등록 이름 → 기존 거래처에 연결, 또는 그 이름으로 새 거래처 등록."""
    name = f_str("name")
    target = f_str("partner_id")
    if target == "new" or (not target and not f_str("partner_id_ref")):
        r = partners.create({"name": name, "kind": f_str("kind") or "SUPPLIER"}, actor())
    elif _pick("partner_id") is not None:
        r = partners.link(name, _pick("partner_id"), actor())
    else:
        flash("연결할 거래처를 고르세요.", "error")
        return redirect(url_for("partners.index", tab="unknown"))
    flash(r.message, "success" if r.ok else "error")
    return redirect(url_for("partners.index", tab="unknown"))


@bp.post("/alias/<int:alias_id>/delete")
@master_required
def unlink(alias_id: int):
    r = partners.unlink(alias_id, actor())
    flash(r.message, "success" if r.ok else "error")
    return redirect(url_for("partners.detail", pid=r.id) if r.ok else url_for("partners.index"))


@bp.post("/<int:pid>/alias")
@master_required
def add_alias(pid: int):
    r = partners.link(f_str("alias"), pid, actor())
    flash(r.message, "success" if r.ok else "error")
    return redirect(url_for("partners.detail", pid=pid))


# ── 엑셀 일괄 등록 ───────────────────────────────────────────
TITLE = "거래처 엑셀 일괄 등록"


@bp.get("/import")
@master_required
def import_page():
    return bulk_ui.page("partners", "partners", TITLE)


@bp.get("/import/template.xlsx")
@master_required
def import_template():
    return bulk_ui.template("partner_template", [["", "(주)대한팔레트", "공급처", "124-81-00998", "김구매", "051-000-0000",
                                                  "buy@example.com", "", "대한팔레트 부산지점, 대한PLT"]], "거래처_일괄등록_양식.xlsx")


@bp.post("/import")
@master_required
def import_upload():
    return bulk_ui.upload("partners", "partner_upload", bulk.preview_partners, url_for("partners.import_page"), "partners", TITLE)


@bp.post("/import/apply")
@master_required
def import_apply():
    return bulk_ui.apply("partners", bulk.apply_partners, url_for("partners.import_page"))


@bp.post("/merge")
@master_required
def merge():
    src, dst = _pick("src"), _pick("dst")
    if src is None or dst is None:
        flash("합칠 거래처와 남길 거래처를 고르세요.", "error")
        return redirect(url_for("partners.index", tab="merge"))
    r = partners.merge(src, dst, actor())
    flash(r.message, "success" if r.ok else "error")
    return redirect(url_for("partners.detail", pid=r.id) if r.ok else url_for("partners.index", tab="merge"))
