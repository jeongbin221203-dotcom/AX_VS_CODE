"""공고 수집: API 출처 실행, CSV·엑셀 가져오기, 샘플 공고."""
from __future__ import annotations

import re

from flask import Blueprint, Response, current_app, flash, redirect, render_template, request, url_for

from core import collect, crawler, postings, scheduler
from core.normalize import clean
from core.normalize import SIDO_ORDER
from core.sources import SITE_METHODS, FetchQuery, SourceError, fileimport, linkimport, registry

bp = Blueprint("collect", __name__, url_prefix="/collect")


@bp.get("")
def index():
    return _page(None)


def _page(results):
    return render_template("collect.html", sources=registry(), history=collect.history(),
                           sidos=SIDO_ORDER[:17], in_db=postings.sources_in_db(), results=results,
                           methods=SITE_METHODS, link_sites=[n for n, _ in linkimport.SITES.values()],
                           crawl=crawler.load_settings(), crawl_status=crawler.status(),
                           crawl_sites=[(k, linkimport.SITES[k][0]) for k in crawler.LIST_SITES],
                           sitemap_progress=crawler.sitemap_progress(), backlog=crawler.backlog_status())


@bp.post("/crawl")
def crawl_settings():
    form = request.form
    keywords = [clean(k) for k in re.split(r"[,\n]", form.get("keywords", "")) if clean(k)][:10]
    s = crawler.save_settings({
        "enabled": form.get("enabled") == "1",
        "interval_hours": form.get("interval_hours", 4, type=int),
        "keywords": keywords,
        "sites": form.getlist("sites"),
        "pages": form.get("pages", 1, type=int),
        "list_every_hours": form.get("list_every_hours", 4, type=int),
        "by_category": form.get("by_category") == "1",
        "max_new": form.get("max_new", 30, type=int),
        "max_refresh": form.get("max_refresh", 30, type=int),
        "use_api": form.get("use_api") == "1",
        "sitemap_sites": form.getlist("sitemap_sites"),
        "sitemap_max": form.get("sitemap_max", 500, type=int),
    })
    if s["enabled"] and not s["keywords"]:
        flash("검색어가 없어 사람인·잡코리아·링커리어의 모든 직무를 직무별로 돌며 읽습니다." if s["by_category"]
              else "검색어가 없어 사람인·잡코리아·링커리어는 최근 등록 공고를 읽습니다.", "ok")
    flash(f"자동 수집을 {'켰습니다 — ' + str(s['interval_hours']) + '시간마다 실행' if s['enabled'] else '껐습니다'}.", "ok")
    return redirect(url_for(".index") + "#crawl")


@bp.post("/crawl/run")
def crawl_run():
    if scheduler.run_now():
        flash("크롤링을 시작했습니다. 요청 사이를 쉬어 가며 읽으므로 몇 분 걸릴 수 있습니다 — 잠시 뒤 새로고침하세요.", "ok")
    else:
        flash("이미 크롤링이 진행 중입니다.", "warn")
    return redirect(url_for(".index") + "#crawl")


@bp.post("/run")
def run():
    keys = request.form.getlist("sources")
    if not keys:
        flash("수집할 사이트를 하나 이상 고르세요.", "err")
        return redirect(url_for(".index"))
    q = FetchQuery(keyword=request.form.get("keyword", "").strip()[:100],
                   sido=request.form.get("sido", ""), career=request.form.get("career", ""),
                   pages=request.form.get("pages", 1, type=int) or 1)
    return _page(collect.run(keys, q))


@bp.post("/links")
def links():
    """붙여 넣은 공고 링크를 한 장씩 읽어 온다 (사람인·잡코리아·링커리어·자소설닷컴·잡플래닛·원티드)."""
    urls = linkimport.split_links(request.form.get("links", ""))
    if not urls:
        flash("공고 링크(https://…)를 한 줄에 하나씩 붙여 넣으세요.", "err")
        return redirect(url_for(".index") + "#links")
    results = collect.import_links(urls[:linkimport.MAX_LINKS])
    if len(urls) > linkimport.MAX_LINKS:
        results.append({"name": "안내", "ok": False, "error": f"한 번에 {linkimport.MAX_LINKS}개까지 — 나머지 {len(urls) - linkimport.MAX_LINKS}개는 다시 넣어 주세요"})
    return _page(results)


@bp.post("/upload")
def upload():
    file = request.files.get("file")
    if not file or not file.filename:
        flash("파일을 고르세요.", "err")
        return redirect(url_for(".index"))
    try:
        rows = fileimport.read_table(file.filename, file.read())
        items, skipped = fileimport.to_postings(rows, request.form.get("site") or "csv")
    except SourceError as e:
        collect.record("csv", file.filename, 0, 0, 0, str(e))
        flash(str(e), "err")
        return redirect(url_for(".index"))
    ins, upd = postings.upsert_many(items)
    collect.record("csv", file.filename, len(items), ins, upd)
    msg = f"{file.filename}: 새 공고 {ins}건, 갱신 {upd}건"
    if skipped:
        msg += f", 건너뜀 {len(skipped)}행 ({'; '.join(skipped[:3])}{' …' if len(skipped) > 3 else ''})"
    flash(msg, "ok")
    return redirect(url_for("jobs.index"))


@bp.get("/template.csv")
def template():
    return Response(fileimport.template_csv(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=job_postings_template.csv"})


@bp.post("/sample")
def sample():
    ins, upd = collect.load_sample(current_app.config["SAMPLE_PATH"])
    flash(f"샘플(가상) 공고 {ins + upd}건을 넣었습니다. 실제 회사가 아닙니다.", "ok")
    return redirect(url_for("jobs.index"))


@bp.post("/delete-source")
def delete_source():
    source = request.form.get("source", "")
    n = postings.delete_source(source)
    flash(f"'{source}' 공고 {n}건과 그 지원 기록을 지웠습니다.", "ok")
    return redirect(url_for(".index"))
