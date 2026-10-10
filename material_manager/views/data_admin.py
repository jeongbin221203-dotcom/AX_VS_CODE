"""데이터 관리 화면 (업로드 / 백업 / 샘플).

업로드는 두 단계다: 파일을 올리면 정제 결과를 저장소(uploads/<토큰>.json)에 보관하고
미리보기를 보여 준다 → '반영'을 누르면 그 토큰의 결과만 DB에 넣고 파일을 지운다.
저장소를 쓰므로 미리보기와 반영이 서로 다른 서버에서 처리돼도 된다.
"""

import io
import re
import secrets
import zipfile
from datetime import datetime

import pandas as pd
from flask import Blueprint, abort, flash, redirect, request, session, url_for

import config
from core import audit, db, excel_forms, jobs, repository as repo, seed_packs, services, storage, tasks
from core.utils import to_csv_zip_bytes, to_excel_bytes, xlsx_problem
from views.helpers import Table, actor, file_response, form_response, render_page, role_required, xlsx_response

bp = Blueprint("data_admin", __name__, url_prefix="/data")

TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
PREVIEW_FMT = {"안전재고": "{:,.2f}", "단가": "₩{:,.0f}"}


def _page(**ctx):
    ctx.setdefault("learn_summary", None)
    packs = [{"key": p.key, "title": p.title, "summary": p.summary, "done": seed_packs.done(p.key),
              "running": seed_packs.running(p.key)} for p in seed_packs.PACKS]
    return render_page("data_admin.html", "data", learn=excel_forms.learn_state("material_upload"),
                       material_cnt=repo.count_materials(), packs=packs,
                       db_file=not db.is_pg() and config.DB_PATH.exists(), is_pg=db.is_pg(),
                       storage_name=storage.get().name,
                       attach_cnt=len(storage.get().keys("attachments/")), **ctx)


@bp.get("/")
@role_required("ADMIN")
def index():
    return _page()


@bp.get("/template.xlsx")
@role_required("ADMIN")
def template():
    df = pd.DataFrame(
        [["PKG-100", "샘플 자재", "규격", "EA", "포장재", 10, 1000, "A-10", "공급처명", "PKG100", "8801234567893", 7, 100, 50]],
        columns=list(config.MATERIAL_COLS.values()),
    )
    return form_response("material_template", df, "자재마스터_업로드양식.xlsx")


@bp.post("/upload")
@role_required("ADMIN")
def upload():
    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        flash("업로드할 파일을 선택하세요.", "error")
        return redirect(url_for("data_admin.index"))
    name = uploaded.filename.lower()
    if not name.endswith((".xlsx", ".csv")):
        flash("엑셀(.xlsx) 또는 CSV 파일만 올릴 수 있습니다.", "error")
        return redirect(url_for("data_admin.index"))

    jobs.run("cleanup_uploads")                 # 주기가 됐으면 오래된 미리보기 정리 (다른 서버가 하는 중이면 건너뜀)
    data = uploaded.read()
    if name.endswith(".xlsx"):
        problem = xlsx_problem(data, config.XLSX_MAX_UNCOMPRESSED, config.XLSX_MAX_RATIO)
        if problem:
            flash(problem, "error")
            return redirect(url_for("data_admin.index"))
    # 회사 엑셀의 열 이름·머리글 행은 '엑셀 양식' 설정(자재 일괄 업로드)을 따른다
    raw, problem = excel_forms.read_import("material_upload", data, name, config.UPLOAD_MAX_ROWS)
    if problem:
        flash(problem, "error")
        return redirect(url_for("data_admin.index"))
    if len(raw) > config.UPLOAD_MAX_ROWS:
        flash(f"한 번에 {config.UPLOAD_MAX_ROWS:,}행까지 올릴 수 있습니다. 파일을 나눠 올려 주세요.", "error")
        return redirect(url_for("data_admin.index"))

    result = services.normalize_upload(raw)
    for err in result.errors:
        flash(err, "error")
    if result.errors:
        return redirect(url_for("data_admin.index"))
    if result.dropped:
        flash(f"자재코드 또는 자재명이 비어 있는 {result.dropped}행을 제외했습니다.", "warning")
    if result.duplicated:
        flash(f"중복된 자재코드 {result.duplicated}행은 마지막 값만 반영합니다.", "info")
    if result.df.empty:
        flash("반영할 유효한 행이 없습니다.", "error")
        return redirect(url_for("data_admin.index"))
    if result.bad_numbers:
        flash(f"숫자가 아닌 안전재고·단가 {result.bad_numbers}칸은 반영하지 않습니다(기존 자재는 기존 값 유지, 새 자재는 0).",
              "warning")
    flash("이미 있는 자재는 파일에 값이 있는 칸만 바꿉니다. 빈 칸·파일에 없는 열은 기존 값을 그대로 둡니다.", "info")

    token = secrets.token_hex(16)
    session["upload_token"] = token          # 미리보기를 본 사람만 반영할 수 있게 세션에 묶는다
    storage.get().put(f"uploads/{token}.json",
                      result.df.to_json(orient="records", force_ascii=False).encode("utf-8"))
    learn = None
    if request.form.get("learn_form") == "1" and not excel_forms.learn_state("material_upload")["locked"]:   # 이 엑셀의 양식을 기억 (반영 때 저장)
        learn = excel_forms.stage_learn(token, "material_upload", data, uploaded.filename)
        if not learn["ok"]:
            flash(f"양식은 기억하지 않았습니다: {learn['problem']}", "warning")
    return _page(preview=Table(result.df.drop(columns="_blank").rename(columns=config.MATERIAL_COLS), PREVIEW_FMT),
                 token=token, preview_cnt=len(result.df), filename=uploaded.filename,
                 learn_summary=learn["summary"] if learn and learn["ok"] else None)


@bp.post("/upload/apply")
@role_required("ADMIN")
def apply_upload():
    token = request.form.get("token", "")
    if not TOKEN_RE.match(token) or not secrets.compare_digest(session.get("upload_token", ""), token):
        abort(400, "잘못된 업로드 요청입니다. 파일을 다시 올려 주세요.")
    session.pop("upload_token", None)
    key = f"uploads/{token}.json"
    raw = storage.get().get(key)
    if raw is None:
        flash("이미 반영했거나 만료된 업로드입니다. 파일을 다시 올려 주세요.", "warning")
        return redirect(url_for("data_admin.index"))
    # dtype=False: '001' 같은 코드·규격이 숫자로 바뀌지 않게 저장한 그대로 읽는다
    df = pd.read_json(io.StringIO(raw.decode("utf-8")), orient="records", dtype=False)
    if "_blank" not in df.columns:                  # 이 기능 전에 만든 미리보기
        df["_blank"] = ""
    df = df[[*config.MATERIAL_COLS, "_blank"]]
    storage.get().delete(key)
    who = actor()

    def run(progress=None):
        result = services.import_materials(df, who)
        excel_forms.adopt_staged(token, who)         # 양식을 기억하기로 했다면 반영이 끝난 뒤 저장
        return result

    if len(df) > config.BG_ROWS:                    # 자재가 많으면 백그라운드로 (요청 제한에 걸리지 않게)
        tid = tasks.start("material_import", f"자재 마스터 일괄 반영 ({len(df):,}줄)", run, who)
        flash(f"자재 {len(df):,}줄 반영을 시작했습니다. 끝나면 이 화면에 결과가 나옵니다.", "info")
        return redirect(url_for("tasks.detail", task_id=tid))
    staged = excel_forms.staged(token)
    result = run()
    flash(result.message, "success")
    if staged:
        flash(f"'{excel_forms.label(staged['export_key'])}' 내려받기가 올린 파일의 양식으로 바뀌었습니다 — 이제 내려받으면 같은 모양으로 나옵니다.", "success")
    return redirect(url_for("data_admin.index"))


@bp.get("/backup.db")
@role_required("ADMIN")
def backup_db():
    """SQLite 파일 백업. PostgreSQL은 DB 서버에서 pg_dump·스냅샷으로 백업한다."""
    if db.is_pg() or not config.DB_PATH.exists():
        abort(404)
    audit.log(actor(), "BACKUP", "db", config.DB_PATH.name)
    return file_response(config.DB_PATH.read_bytes(),
                         f"materials_backup_{datetime.now():%Y%m%d_%H%M}.db",
                         "application/octet-stream")


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _big_data() -> bool:
    return int(db.scalar("SELECT COUNT(*) FROM transactions") or 0) > config.BG_EXPORT_ROWS


def _background_backup(kind: str, name: str, build, config_mime: str):
    who = actor()

    def work(progress):
        return tasks.Output(f"{name} 를 만들었습니다.", build(repo.dump_all()), name, config_mime)

    tid = tasks.start(f"backup_{kind}", f"전체 백업 {kind.upper()} 만들기", work, who)
    flash("데이터가 많아 백그라운드에서 만드는 중입니다. 끝나면 이 화면에서 내려받으세요.", "info")
    return redirect(url_for("tasks.detail", task_id=tid))


@bp.get("/backup.xlsx")
@role_required("ADMIN")
def backup_xlsx():
    audit.log(actor(), "BACKUP", "xlsx", "전체 데이터")
    name = f"자재관리_전체백업_{datetime.now():%Y%m%d_%H%M}.xlsx"
    if _big_data():                                 # 거래가 많으면 백그라운드로 만들어 작업 화면에서 받는다
        return _background_backup("xlsx", name, to_excel_bytes, config_mime=XLSX_MIME)
    return xlsx_response(to_excel_bytes(repo.dump_all()), name)


@bp.get("/backup-csv.zip")
@role_required("ADMIN")
def backup_csv():
    """전체 데이터 CSV 묶음 — 엑셀 백업보다 빠르고 행 수 제한(시트당 약 100만 행)이 없다. 데이터가 많을 때."""
    audit.log(actor(), "BACKUP", "csv", "전체 데이터")
    name = f"자재관리_전체백업_{datetime.now():%Y%m%d_%H%M}_csv.zip"
    if _big_data():
        return _background_backup("csv", name, to_csv_zip_bytes, config_mime="application/zip")
    return file_response(to_csv_zip_bytes(repo.dump_all()), name, "application/zip")


@bp.get("/backup-attachments.zip")
@role_required("ADMIN")
def backup_attachments():
    """증빙 파일 백업. .db 백업에는 파일이 들어 있지 않으므로 함께 받아 두어야 한다."""
    audit.log(actor(), "BACKUP", "attachments", "증빙 파일")
    buf = io.BytesIO()
    store = storage.get()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:   # 이미지·PDF는 이미 압축돼 있다
        for key in store.keys("attachments/"):
            data = store.get(key)
            if data is not None:
                zf.writestr(key.split("/", 1)[1], data)
    return file_response(buf.getvalue(), f"자재관리_증빙파일_{datetime.now():%Y%m%d_%H%M}.zip",
                         "application/zip")


@bp.post("/seed")
@role_required("ADMIN")
def make_seed():
    """기본 샘플(수량은 적고 금액은 큰 깔끔한 데이터) — 빈 DB에서만."""
    if repo.count_materials():
        flash("기본 샘플 생성은 빈 DB에서만 가능합니다. 복잡한 데이터는 '추가 데이터 목록'에서 더하세요.", "warning")
    else:
        from core import demo, seed_clean
        counts = seed_clean.seed_clean()
        demo._extras()                         # 거래처 마스터 · BOM·작업지시·MRP 샘플
        audit.log(actor(), "SEED", "material", "", counts)
        flash(f"기본 샘플을 생성했습니다 — 자재 {counts.get('materials', 0)}종, 거래 {counts.get('transactions', 0):,}건.", "success")
    return redirect(url_for("data_admin.index"))


@bp.post("/pack/<key>")
@role_required("ADMIN")
def add_pack(key: str):
    """추가 데이터 목록에서 고른 팩 하나를 넣는다 (팩마다 한 번만, 시연·교육용 DB)."""
    if not repo.count_materials():
        flash("먼저 기본 샘플을 생성하거나 자재를 등록하세요.", "warning")
        return redirect(url_for("data_admin.index"))
    ok, msg = (seed_packs.add_background if config.DEMO else seed_packs.add)(key, actor())
    flash(msg, "success" if ok else "warning")
    return redirect(url_for("data_admin.index"))


@bp.post("/seed-mfg")
@role_required("ADMIN")
def make_seed_mfg():
    """제조 공장 샘플 추가 (다른 데이터가 있어도 한 번만). 추가 데이터 목록의 '제조 공장'과 같다."""
    from core import seed_mfg
    if seed_mfg.exists():
        flash("제조 샘플은 이미 추가되어 있습니다.", "info")
        return redirect(url_for("data_admin.index"))
    from core import production
    counts = seed_mfg.seed_manufacturing()
    counts["bom"] = production.seed_sample()
    audit.log(actor(), "SEED", "material", "manufacturing", counts)
    flash(f"제조 샘플을 추가했습니다 — 창원 제조공장 창고 4곳, 자재 {counts['materials'] + 2}종, 거래 {counts['transactions']:,}건, "
          f"거래명세서 {counts['statements']}장, BOM 2개·생산 투입 기록.", "success")
    return redirect(url_for("data_admin.index"))
