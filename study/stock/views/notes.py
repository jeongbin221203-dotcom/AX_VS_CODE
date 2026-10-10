"""주식단테 유튜브 노트 검색 (data/yt.db). 개인 학습용."""
import json
import re

from flask import Blueprint, abort, render_template, request
from markupsafe import Markup, escape

from core import yt, yt_clean

bp = Blueprint("notes", __name__, url_prefix="/notes")
PER_PAGE = 30


def _fts_phrase(q: str) -> str:
    return '"' + q.replace('"', '""') + '"'


@bp.app_template_filter("hl")
def hl(text, q):
    """검색어를 <mark> 로 강조(나머지는 이스케이프)."""
    text = text or ""
    if not q:
        return escape(text)
    parts = re.split("(" + re.escape(q) + ")", text, flags=re.IGNORECASE)
    return Markup("".join(f"<mark>{escape(p)}</mark>" if i % 2 else str(escape(p)) for i, p in enumerate(parts)))


@bp.app_template_filter("mmss")
def mmss(sec):
    sec = int(sec or 0)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


def _ready():
    yt.init()
    with yt.conn() as c:
        c.executescript(yt_clean.SCHEMA)


@bp.get("/")
def index():
    _ready()
    q = request.args.get("q", "").strip()[:60]
    tag = request.args.get("tag", "")
    channel = request.args.get("ch", "")
    kind = request.args.get("kind", "long")  # long=일반+라이브, short, all
    page = max(request.args.get("page", 1, type=int), 1)
    where, args = ["v.meta_status='ok'"], []
    if kind == "long":
        where.append("v.kind<>'short'")
    elif kind == "short":
        where.append("v.kind='short'")
    if channel:
        where.append("v.channel_id=?")
        args.append(channel)
    if tag:
        where.append("EXISTS(SELECT 1 FROM yt_tags t WHERE t.video_id=v.id AND t.tag=?)")
        args.append(tag)

    hits: dict[str, list] = {}
    with yt.conn() as c:
        if q:
            ids = set()
            if len(q) >= 3:  # trigram 은 3글자 이상
                for r in c.execute("SELECT video_id FROM yt_meta_fts WHERE yt_meta_fts MATCH ? LIMIT 2000", (_fts_phrase(q),)):
                    ids.add(r[0])
                for r in c.execute("SELECT video_id,start,text FROM yt_para_fts WHERE yt_para_fts MATCH ? LIMIT 4000", (_fts_phrase(q),)):
                    ids.add(r[0])
                    hits.setdefault(r[0], []).append((r[1], r[2]))
            else:
                like = "%" + q.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
                for r in c.execute("SELECT id FROM yt_videos WHERE title LIKE ? ESCAPE '!'", (like,)):
                    ids.add(r[0])
                for r in c.execute("SELECT video_id,start,text FROM yt_paragraphs WHERE text LIKE ? ESCAPE '!' LIMIT 4000", (like,)):
                    ids.add(r[0])
                    hits.setdefault(r[0], []).append((r[1], r[2]))
            if not ids:
                where.append("0")
            else:
                c.execute("CREATE TEMP TABLE IF NOT EXISTS _hit(id TEXT PRIMARY KEY)")
                c.execute("DELETE FROM _hit")
                c.executemany("INSERT OR IGNORE INTO _hit VALUES(?)", [(i,) for i in ids])
                where.append("v.id IN (SELECT id FROM _hit)")
        w = " AND ".join(where)
        total = c.execute(f"SELECT COUNT(*) FROM yt_videos v WHERE {w}", args).fetchone()[0]
        order = "v.upload_date DESC" if not q else "v.upload_date DESC"
        rows = c.execute(
            f"SELECT v.*, ch.name channel FROM yt_videos v JOIN yt_channels ch ON ch.id=v.channel_id WHERE {w} "
            f"ORDER BY {order} LIMIT ? OFFSET ?", args + [PER_PAGE, (page - 1) * PER_PAGE]).fetchall()
        tags = {r["id"]: [] for r in rows}
        for r in c.execute(f"SELECT video_id,tag,hits FROM yt_tags WHERE video_id IN ({','.join('?' * len(rows))}) ORDER BY hits DESC",
                           list(tags)) if rows else []:
            tags[r["video_id"]].append(r["tag"])
        tag_list = c.execute("SELECT t.tag, COUNT(*) n FROM yt_tags t JOIN yt_videos v ON v.id=t.video_id "
                             "WHERE v.kind<>'short' GROUP BY t.tag ORDER BY n DESC").fetchall()
        chans = c.execute("SELECT id,name FROM yt_channels").fetchall()
    items = []
    for r in rows:
        h = sorted(hits.get(r["id"], []))[:3]
        items.append({"v": r, "hits": h, "nhits": len(hits.get(r["id"], [])), "tags": tags.get(r["id"], [])[:4]})
    return render_template("notes.html", items=items, q=q, tag=tag, ch=channel, kind=kind, page=page,
                           pages=(total + PER_PAGE - 1) // PER_PAGE, total=total, tag_list=tag_list, chans=chans)


@bp.get("/<video_id>")
def detail(video_id):
    if not re.fullmatch(r"[\w-]{6,20}", video_id):
        abort(404)
    _ready()
    q = request.args.get("q", "").strip()[:60]
    raw = request.args.get("raw") == "1"
    with yt.conn() as c:
        v = c.execute("SELECT v.*, ch.name channel FROM yt_videos v JOIN yt_channels ch ON ch.id=v.channel_id WHERE v.id=?", (video_id,)).fetchone()
        if not v:
            abort(404)
        if raw:
            paras = [(r["start"], r["text"]) for r in c.execute("SELECT start,text FROM yt_segments WHERE video_id=? ORDER BY seq", (video_id,))]
        else:
            paras = [(r["start"], r["text"]) for r in c.execute("SELECT start,text FROM yt_paragraphs WHERE video_id=? ORDER BY seq", (video_id,))]
        tags = [r["tag"] for r in c.execute("SELECT tag FROM yt_tags WHERE video_id=? ORDER BY hits DESC", (video_id,))]
    chapters = json.loads(v["chapters"] or "[]")
    return render_template("note_detail.html", v=v, paras=paras, tags=tags, chapters=chapters, q=q, raw=raw)
