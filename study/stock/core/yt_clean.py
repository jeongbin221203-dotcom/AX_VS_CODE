"""자막 정리: 원문 조각(yt_segments, 보존) → 문단(yt_paragraphs) + 기법 태그(yt_tags).

원문은 지우지 않는다. 정리본은 언제든 `python manage.py yt-clean` 으로 다시 만들 수 있다.
"""
import json
import re
from pathlib import Path

import config
from core import yt

TERMS = json.loads((config.BASE / "content" / "yt_terms.json").read_text(encoding="utf-8"))
_FIX = [(re.compile(p), r) for p, r in TERMS["fix"]]
_NOISE = re.compile(r"\[(?:음악|박수|웃음|효과음|Music|Applause)\]|♪+|>>+|\(음악\)")
_END = re.compile(r"(다|요|죠|네요|까요|니다|니까|세요|어요|아요|거든요|데요|죠\?|까)\s*$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS yt_paragraphs(
  video_id TEXT NOT NULL, seq INTEGER NOT NULL, start REAL, text TEXT,
  PRIMARY KEY(video_id, seq)) WITHOUT ROWID;
CREATE VIRTUAL TABLE IF NOT EXISTS yt_para_fts USING fts5(text, video_id UNINDEXED, start UNINDEXED, tokenize='trigram');
CREATE TABLE IF NOT EXISTS yt_tags(video_id TEXT NOT NULL, tag TEXT NOT NULL, hits INTEGER, PRIMARY KEY(video_id, tag)) WITHOUT ROWID;
"""


def fix_terms(text: str) -> str:
    for rx, rep in _FIX:
        text = rx.sub(rep, text)
    return text


def clean_segment(text: str) -> str:
    text = _NOISE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


_SENT_END = re.compile(
    r"(니다|입니다|어요|아요|해요|에요|예요|죠|지요|네요|세요|거든요|데요|잖아요|봐요|가요|래요|돼요|까요|"
    r"[했았었겠였]다|된다|한다|이다|있다|없다)$")
_QUESTION = re.compile(r"(까요|나요|니까)$")


def _drop_overlap(prev: str, t: str, min_len: int = 6) -> str:
    """t 의 앞부분이 prev 의 끝과 (단어 경계에서) 같으면 그만큼 잘라 낸다."""
    for k in range(min(len(prev), len(t)), min_len - 1, -1):
        head = t[:k]
        if (k == len(t) or t[k] == " ") and prev.endswith(head) and (len(prev) == k or prev[-k - 1] == " "):
            return t[k:].strip()
    return t


def paragraphs(segs, gap: float = 2.5, max_chars: int = 200) -> list[tuple[float, str]]:
    """segs: [(start, dur, text)] → [(문단 시작초, 문단 글)].
    1) 겹쳐 반복되는 조각 제거  2) 어미(…요·…다·…죠)에서 문장을 끊어 마침표를 붙임
    3) 문장이 끝난 자리에서 침묵(gap초)이 있거나 글자 수가 max_chars 를 넘으면 문단을 나눈다."""
    words = []          # (단어, 조각 시작초)
    seg_gap_after = {}  # 조각 마지막 단어 인덱스 → 다음 조각까지의 침묵
    prev, last_end = "", 0.0
    for s0, d, raw in segs:
        t = clean_segment(raw)
        if not t or t == prev:
            continue
        t = _drop_overlap(prev, t)  # 앞 조각 끝이 다시 나오는 자동 자막 특성
        if not t:
            continue
        if words and s0 - last_end > gap:
            seg_gap_after[len(words) - 1] = s0 - last_end
        for w in t.split():
            words.append((w, s0))
        last_end = s0 + (d or 0)
        prev = t

    paras, cur, cur_start, sent = [], [], None, []
    sent_start = None

    def close_sentence():
        nonlocal sent, sent_start, cur, cur_start
        if not sent:
            return
        text = fix_terms(" ".join(sent))
        text += "?" if _QUESTION.search(text) else "."
        if cur_start is None:
            cur_start = sent_start
        cur.append(text)
        sent, sent_start = [], None

    def close_paragraph():
        nonlocal cur, cur_start
        if cur:
            paras.append((cur_start, " ".join(cur)))
        cur, cur_start = [], None

    for i, (w, st) in enumerate(words):
        if sent_start is None:
            sent_start = st
        sent.append(w)
        ended = bool(_SENT_END.search(w))
        if ended:
            close_sentence()
            if sum(len(x) for x in cur) >= max_chars or seg_gap_after.get(i, 0) > gap:
                close_paragraph()
        elif seg_gap_after.get(i, 0) > gap * 2:  # 어미 없이 길게 멈추면 거기서 끊는다
            close_sentence()
            close_paragraph()
    close_sentence()
    close_paragraph()
    return paras


def tag_counts(text: str) -> dict[str, int]:
    res = {}
    for tag, kws in TERMS["techniques"].items():
        n = sum(text.count(k) for k in kws)
        if n:
            res[tag] = n
    return res


def clean_all(log=print, only_missing: bool = True) -> dict:
    yt.init()
    with yt.conn() as c:
        c.executescript(SCHEMA)
        ids = [r["id"] for r in c.execute("SELECT id FROM yt_videos WHERE caption_status='ok'")]
        done = {r[0] for r in c.execute("SELECT DISTINCT video_id FROM yt_paragraphs")} if only_missing else set()
    n = 0
    for vid in ids:
        if vid in done:
            continue
        with yt.conn() as c:
            segs = [(r["start"], r["dur"], r["text"]) for r in
                    c.execute("SELECT start,dur,text FROM yt_segments WHERE video_id=? ORDER BY seq", (vid,))]
            paras = paragraphs(segs)
            c.execute("DELETE FROM yt_paragraphs WHERE video_id=?", (vid,))
            c.execute("DELETE FROM yt_para_fts WHERE video_id=?", (vid,))
            c.executemany("INSERT INTO yt_paragraphs VALUES(?,?,?,?)", [(vid, i, s, t) for i, (s, t) in enumerate(paras)])
            c.executemany("INSERT INTO yt_para_fts(text,video_id,start) VALUES(?,?,?)", [(t, vid, s) for s, t in paras])
        n += 1
    retag()
    log(f"정리 {n}개 영상")
    return {"cleaned": n}


def retag() -> int:
    """제목·설명·정리된 자막에서 기법 키워드를 세어 yt_tags 를 다시 만든다."""
    with yt.conn() as c:
        c.executescript(SCHEMA)
        c.execute("DELETE FROM yt_tags")
        vids = c.execute("SELECT id,COALESCE(title,'') t,COALESCE(description,'') d FROM yt_videos WHERE meta_status='ok'").fetchall()
        rows = []
        for v in vids:
            body = " ".join(r[0] for r in c.execute("SELECT text FROM yt_paragraphs WHERE video_id=? ORDER BY seq", (v["id"],)))
            counts = tag_counts(fix_terms(v["t"]) + "\n" + v["d"].split("\n\n")[0] + "\n" + body)
            title_counts = tag_counts(fix_terms(v["t"]))
            for tag, n in counts.items():
                # 제목에 나오면 가중치, 자막에서는 3번 이상 나온 기법만 태그로 인정
                if title_counts.get(tag) or n >= 3:
                    rows.append((v["id"], tag, n + 10 * title_counts.get(tag, 0)))
        c.executemany("INSERT INTO yt_tags VALUES(?,?,?)", rows)
    return len(rows)
