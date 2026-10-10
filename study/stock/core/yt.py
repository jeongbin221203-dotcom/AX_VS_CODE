"""주식단테 유튜브 채널 정보 저장 (개인 학습용 로컬 보관, data/yt.db — git 제외·재배포 금지).

- 일반 영상·라이브: 메타데이터(제목·설명·업로드일·길이·조회수·챕터·태그) + 자막(수동 우선, 없으면 자동 생성)
- 쇼츠: 메타데이터만 (자막 수집 안 함)
- 이어 받기: 이미 저장한 것은 건너뛴다. 차단·오류가 나면 멈추고 다음 실행에서 이어 받는다.
"""
import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime

import config

DB_PATH = config.DATA_DIR / "yt.db"

CHANNELS = {
    "UC6ij59Gy_HnqO4pFu9A_zgQ": "주식단테_20년차트고수",
    "UCI9oLnJOXVgcvtvsmzl04gQ": "1분주식",
}
TABS = (("videos", "video"), ("streams", "live"), ("shorts", "short"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS yt_channels(id TEXT PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS yt_videos(
  id TEXT PRIMARY KEY, channel_id TEXT, kind TEXT NOT NULL,            -- video | live | short
  title TEXT, description TEXT, upload_date TEXT, duration INTEGER,
  view_count INTEGER, like_count INTEGER, comment_count INTEGER,
  tags TEXT, chapters TEXT, categories TEXT, thumbnail TEXT, url TEXT,
  meta_status TEXT DEFAULT 'ok',                                       -- ok | unavailable
  meta_error TEXT, fetched_at TEXT,
  caption_status TEXT, caption_lang TEXT, caption_kind TEXT,           -- NULL(미수집) | ok | none | skipped | error
  caption_error TEXT, caption_at TEXT);
CREATE INDEX IF NOT EXISTS ix_vid_chan ON yt_videos(channel_id, kind, upload_date);
CREATE TABLE IF NOT EXISTS yt_segments(
  video_id TEXT NOT NULL, seq INTEGER NOT NULL, start REAL, dur REAL, text TEXT,
  PRIMARY KEY(video_id, seq)) WITHOUT ROWID;
CREATE VIRTUAL TABLE IF NOT EXISTS yt_fts USING fts5(text, video_id UNINDEXED, start UNINDEXED, tokenize='trigram');
CREATE VIRTUAL TABLE IF NOT EXISTS yt_meta_fts USING fts5(title, description, video_id UNINDEXED, tokenize='trigram');
"""


@contextmanager
def conn():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init():
    with conn() as c:
        c.executescript(SCHEMA)
        for cid, name in CHANNELS.items():
            c.execute("INSERT OR REPLACE INTO yt_channels VALUES(?,?)", (cid, name))


def _ydl(**extra):
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    opts.update(extra)
    return yt_dlp.YoutubeDL(opts)


# ---- 1. 목록 -------------------------------------------------------------
def list_ids(channel_id: str) -> dict[str, str]:
    """{video_id: kind}. 같은 영상이 여러 탭에 나오면 쇼츠 > 라이브 > 일반 순으로 우선."""
    out: dict[str, str] = {}
    rank = {"video": 0, "live": 1, "short": 2}
    with _ydl(extract_flat=True, ignoreerrors=True) as y:
        for tab, kind in TABS:
            try:
                r = y.extract_info(f"https://www.youtube.com/channel/{channel_id}/{tab}", download=False)
            except Exception:
                continue  # 해당 탭이 없는 채널
            for e in (r or {}).get("entries", []) or []:
                if e and e.get("id") and (e["id"] not in out or rank[kind] > rank[out[e["id"]]]):
                    out[e["id"]] = kind
    return out


def sync_list() -> dict:
    """채널별 영상 목록을 받아 새 영상만 yt_videos 에 뼈대로 추가(meta_status='todo')."""
    init()
    added = {}
    for cid in CHANNELS:
        ids = list_ids(cid)
        with conn() as c:
            have = {r["id"] for r in c.execute("SELECT id FROM yt_videos WHERE channel_id=?", (cid,))}
            new = [(i, cid, k, f"https://www.youtube.com/watch?v={i}", "todo") for i, k in ids.items() if i not in have]
            c.executemany("INSERT INTO yt_videos(id,channel_id,kind,url,meta_status) VALUES(?,?,?,?,?)", new)
            # 종류가 바뀐 영상(예: 일반 → 라이브) 반영
            c.executemany("UPDATE yt_videos SET kind=? WHERE id=? AND kind<>?", [(k, i, k) for i, k in ids.items()])
        added[CHANNELS[cid]] = {"listed": len(ids), "new": len(new)}
    return added


# ---- 2. 메타데이터 ---------------------------------------------------------
def _iso(d):
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if d and len(d) == 8 else None


def fetch_meta(video_id: str) -> dict:
    with _ydl() as y:
        info = y.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    return {
        "title": info.get("title"), "description": info.get("description"),
        "upload_date": _iso(info.get("upload_date")), "duration": info.get("duration"),
        "view_count": info.get("view_count"), "like_count": info.get("like_count"),
        "comment_count": info.get("comment_count"),
        "tags": json.dumps(info.get("tags") or [], ensure_ascii=False),
        "chapters": json.dumps(info.get("chapters") or [], ensure_ascii=False),
        "categories": json.dumps(info.get("categories") or [], ensure_ascii=False),
        "thumbnail": info.get("thumbnail"),
    }


def sync_meta(limit: int = 0, delay: float = 1.0, kinds=("video", "live", "short"), log=print) -> dict:
    init()
    ok = bad = 0
    with conn() as c:
        rows = c.execute(
            f"SELECT id,kind FROM yt_videos WHERE meta_status='todo' AND kind IN ({','.join('?' * len(kinds))}) "
            "ORDER BY CASE kind WHEN 'video' THEN 0 WHEN 'live' THEN 1 ELSE 2 END, id", kinds).fetchall()
    if limit:
        rows = rows[:limit]
    for n, r in enumerate(rows, 1):
        now = datetime.now().isoformat(timespec="seconds")
        try:
            m = fetch_meta(r["id"])
            with conn() as c:
                c.execute("UPDATE yt_videos SET title=:title,description=:description,upload_date=:upload_date,"
                          "duration=:duration,view_count=:view_count,like_count=:like_count,comment_count=:comment_count,"
                          "tags=:tags,chapters=:chapters,categories=:categories,thumbnail=:thumbnail,"
                          "meta_status='ok',meta_error=NULL,fetched_at=:now WHERE id=:id", {**m, "now": now, "id": r["id"]})
                c.execute("DELETE FROM yt_meta_fts WHERE video_id=?", (r["id"],))
                c.execute("INSERT INTO yt_meta_fts(title,description,video_id) VALUES(?,?,?)",
                          (m["title"] or "", m["description"] or "", r["id"]))
            ok += 1
        except Exception as e:
            msg = str(e)[:300]
            # 삭제·비공개·회원 전용은 영구 실패, 그 외(네트워크 등)는 다음에 다시
            perm = any(k in msg for k in ("Private video", "removed", "unavailable", "members-only", "Join this channel"))
            with conn() as c:
                c.execute("UPDATE yt_videos SET meta_status=?,meta_error=?,fetched_at=? WHERE id=?",
                          ("unavailable" if perm else "todo", msg, now, r["id"]))
            bad += 1
            if "HTTP Error 429" in msg or "Sign in to confirm" in msg:
                log(f"차단 의심 — 중단합니다: {msg[:100]}")
                break
        if n % 25 == 0:
            log(f"[메타 {n}/{len(rows)}] 성공 {ok} 실패 {bad}")
        time.sleep(delay)
    return {"ok": ok, "fail": bad}


# ---- 3. 자막 ---------------------------------------------------------------
def fetch_transcript(video_id: str):
    """(segments, lang, kind). 한국어 수동 자막 > 한국어 자동 생성 > 없으면 None."""
    from youtube_transcript_api import YouTubeTranscriptApi
    api = YouTubeTranscriptApi()
    tl = api.list(video_id)
    try:
        t, kind = tl.find_manually_created_transcript(["ko"]), "manual"
    except Exception:
        try:
            t, kind = tl.find_generated_transcript(["ko"]), "auto"
        except Exception:
            return None
    segs = [{"start": s.start, "dur": s.duration, "text": s.text} for s in t.fetch()]
    return segs, t.language_code, kind


def sync_captions(limit: int = 0, delay: float = 4.0, log=print, ids=None) -> dict:
    """쇼츠 제외. 메타를 받은 일반 영상·라이브의 자막을 받는다."""
    init()
    from youtube_transcript_api import _errors as E
    blocked_types = tuple(getattr(E, n) for n in ("RequestBlocked", "IpBlocked") if hasattr(E, n))
    none_types = tuple(getattr(E, n) for n in ("TranscriptsDisabled", "NoTranscriptFound", "VideoUnavailable") if hasattr(E, n))
    with conn() as c:
        c.execute("UPDATE yt_videos SET caption_status='skipped' WHERE kind='short' AND caption_status IS NULL")
        rows = c.execute("SELECT id FROM yt_videos WHERE kind<>'short' AND meta_status='ok' "
                         "AND (caption_status IS NULL OR caption_status='error') ORDER BY upload_date DESC").fetchall()
    if ids is not None:  # 지정한 영상만(우선 분석용)
        want = set(ids)
        rows = [r for r in rows if r["id"] in want]
    if limit:
        rows = rows[:limit]
    res = {"ok": 0, "none": 0, "error": 0}
    for n, r in enumerate(rows, 1):
        vid, now = r["id"], datetime.now().isoformat(timespec="seconds")
        try:
            got = fetch_transcript(vid)
            with conn() as c:
                if got is None:
                    c.execute("UPDATE yt_videos SET caption_status='none',caption_at=? WHERE id=?", (now, vid))
                    res["none"] += 1
                else:
                    segs, lang, kind = got
                    c.execute("DELETE FROM yt_segments WHERE video_id=?", (vid,))
                    c.execute("DELETE FROM yt_fts WHERE video_id=?", (vid,))
                    c.executemany("INSERT INTO yt_segments VALUES(?,?,?,?,?)",
                                  [(vid, i, s["start"], s["dur"], s["text"]) for i, s in enumerate(segs)])
                    c.executemany("INSERT INTO yt_fts(text,video_id,start) VALUES(?,?,?)",
                                  [(s["text"], vid, s["start"]) for s in segs])
                    c.execute("UPDATE yt_videos SET caption_status='ok',caption_lang=?,caption_kind=?,caption_error=NULL,caption_at=? WHERE id=?",
                              (lang, kind, now, vid))
                    res["ok"] += 1
        except blocked_types as e:
            log(f"IP 차단으로 중단합니다 — 잠시 뒤 다시 실행하면 이어 받습니다. ({type(e).__name__})")
            break
        except none_types:
            with conn() as c:
                c.execute("UPDATE yt_videos SET caption_status='none',caption_at=? WHERE id=?", (now, vid))
            res["none"] += 1
        except Exception as e:
            with conn() as c:
                c.execute("UPDATE yt_videos SET caption_status='error',caption_error=?,caption_at=? WHERE id=?",
                          (f"{type(e).__name__}: {str(e)[:200]}", now, vid))
            res["error"] += 1
        if n % 25 == 0:
            log(f"[자막 {n}/{len(rows)}] {res}")
        time.sleep(delay)
    return res


def stats() -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT ch.name, v.kind, COUNT(*) total, SUM(v.meta_status='ok') meta_ok, SUM(v.meta_status='unavailable') unavailable, "
            "SUM(v.meta_status='todo') meta_todo, SUM(v.caption_status='ok') cap_ok, SUM(v.caption_status='none') cap_none, "
            "SUM(v.caption_status='error') cap_err FROM yt_videos v JOIN yt_channels ch ON ch.id=v.channel_id "
            "GROUP BY ch.name, v.kind ORDER BY ch.name, v.kind")]
