"""말하기 문제 데이터(content/speaking/{toeic,opic}/*.json) 형식 검사. 형식은 content/speaking/SCHEMA.md.

사용: python tools/validate_speaking.py                  # 전체
      python tools/validate_speaking.py opinion_2.json   # 한 파일 (id 중복은 같은 종류 전체와 비교)
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "content" / "speaking"
sys.path.insert(0, str(ROOT.parent.parent))

from core.speaking import OPIC_TOPICS  # noqa: E402

RA_TYPES = {"안내 방송", "광고", "뉴스", "자동 응답 메시지", "소개", "일기 예보", "교통 정보", "행사 안내"}
WHERE = {"가운데", "왼쪽", "오른쪽", "앞쪽", "뒤쪽", "배경", "전체"}
DOC_TYPES = {"행사 일정표", "면접 일정", "출장 일정", "이력서", "수업 시간표", "예약표", "여행 일정", "강연 일정", "회의 안건", "교육 일정"}
OP_TOPICS = {"직장", "교육", "기술", "생활", "사회", "리더십", "소비", "건강", "환경", "지역 사회"}
KINDS = {"intro", "describe", "routine", "past", "compare", "issue"}
RP_KINDS = ["ask", "solve", "exp"]
BAD_CHARS = re.compile(r"[\(\)\[\]{}<>]|[\U0001F300-\U0001FAFF]")


def words(s: str) -> int:
    return len(re.findall(r"[A-Za-z0-9']+", s or ""))


class C:
    def __init__(self, name):
        self.name, self.errors, self.warns = name, [], []

    def err(self, where, msg):
        self.errors.append(f"{self.name} {where}: {msg}")

    def s(self, o, w, k, lo=None, hi=None, spoken=False):
        v = o.get(k)
        if not isinstance(v, str) or not v.strip():
            self.err(w, f"'{k}' 문자열 필요")
            return False
        if lo is not None and not lo <= words(v) <= hi:
            self.err(w, f"'{k}' {lo}~{hi}단어여야 함 (현재 {words(v)})")
        if spoken and BAD_CHARS.search(v):
            self.err(w, f"'{k}' 에 괄호·기호·이모지 금지 (음성으로 읽음)")
        return True

    def arr(self, o, w, k, n, ranges=None, spoken=False):
        v = o.get(k)
        if not isinstance(v, list) or len(v) != n or not all(isinstance(x, str) and x.strip() for x in v):
            self.err(w, f"'{k}' 문자열 {n}개 배열 필요")
            return
        for i, x in enumerate(v):
            if ranges and not ranges[i][0] <= words(x) <= ranges[i][1]:
                self.err(w, f"'{k}'[{i}] {ranges[i][0]}~{ranges[i][1]}단어여야 함 (현재 {words(x)})")
            if spoken and BAD_CHARS.search(x):
                self.err(w, f"'{k}'[{i}] 에 괄호·기호 금지")

    def tips(self, o, w):
        v = o.get("tips")
        if not isinstance(v, list) or not 1 <= len(v) <= 4 or not all(isinstance(x, str) and x.strip() for x in v):
            self.err(w, "'tips' 1~4개 필요")

    def samples(self, o, w, basic, adv):
        self.s(o, w, "sample", *basic, spoken=True)
        self.s(o, w, "sample_ko")
        self.s(o, w, "sample_adv", *adv, spoken=True)
        self.s(o, w, "sample_adv_ko")
        if words(o.get("sample_adv", "")) <= words(o.get("sample", "")):
            self.err(w, "sample_adv 가 sample 보다 길어야 함")


# ---- 토익스피킹 --------------------------------------------------------------------

def read_aloud(c, o, w):
    if o.get("type") not in RA_TYPES:
        c.err(w, f"type '{o.get('type')}' 허용 안 됨")
    if c.s(o, w, "text", 45, 75, spoken=True) and c.s(o, w, "marked"):
        plain = re.sub(r"\s*/\s*", " ", o["marked"].replace("*", ""))
        if " ".join(plain.split()) != " ".join(o["text"].split()):
            c.err(w, "marked 에서 / 와 * 를 지우면 text 와 같아야 함")
        if o["marked"].count("*") % 2 or "*" not in o["marked"] or "/" not in o["marked"]:
            c.err(w, "marked 에 / 끊어 읽기와 *강세* 표시 필요")
    t = o.get("tricky")
    if not isinstance(t, list) or not 2 <= len(t) <= 4:
        c.err(w, "tricky 2~4개")
    c.s(o, w, "translation")
    c.tips(o, w)


def describe_picture(c, o, w):
    c.s(o, w, "setting")
    c.s(o, w, "scene_ko")
    el = o.get("elements")
    if not isinstance(el, list) or not 4 <= len(el) <= 6:
        c.err(w, "elements 4~6개")
    else:
        for i, e in enumerate(el):
            if not isinstance(e, dict) or e.get("where") not in WHERE:
                c.err(w, f"elements[{i}] where 는 {sorted(WHERE)} 중 하나")
                continue
            c.s(e, f"{w} el{i}", "ko")
            c.s(e, f"{w} el{i}", "en", 4, 25)
    c.samples(o, w, (45, 75), (70, 100))
    c.tips(o, w)


def respond_questions(c, o, w):
    c.s(o, w, "topic")
    c.s(o, w, "intro", 15, 50, spoken=True)
    c.s(o, w, "intro_ko")
    c.arr(o, w, "questions", 3, [(5, 30)] * 3, spoken=True)
    c.arr(o, w, "questions_ko", 3)
    c.arr(o, w, "samples", 3, [(18, 45), (18, 45), (40, 80)], spoken=True)
    c.arr(o, w, "samples_ko", 3)
    c.arr(o, w, "samples_adv", 3, [(28, 55), (28, 55), (60, 100)], spoken=True)
    c.arr(o, w, "samples_adv_ko", 3)
    c.tips(o, w)


def respond_info(c, o, w):
    if o.get("doc_type") not in DOC_TYPES:
        c.err(w, f"doc_type '{o.get('doc_type')}' 허용 안 됨")
    if o.get("voice") not in ("male", "female"):
        c.err(w, "voice male|female")
    info = o.get("info")
    if not isinstance(info, dict):
        c.err(w, "info 객체 필요")
    else:
        c.s(info, w + " info", "title")
        c.s(info, w + " info", "subtitle")
        rows = info.get("rows")
        if not isinstance(rows, list) or not 4 <= len(rows) <= 9:
            c.err(w, "info.rows 4~9줄")
        elif not all(isinstance(r, list) and 2 <= len(r) <= 4 and all(isinstance(x, str) for x in r) for r in rows):
            c.err(w, "info.rows 각 줄은 문자열 2~4칸")
        if not isinstance(info.get("notes", []), list):
            c.err(w, "info.notes 배열")
    c.s(o, w, "intro", 10, 50, spoken=True)
    c.arr(o, w, "questions", 3, [(5, 35)] * 3, spoken=True)
    c.arr(o, w, "questions_ko", 3)
    c.arr(o, w, "samples", 3, [(10, 50), (10, 50), (25, 90)], spoken=True)
    c.arr(o, w, "samples_ko", 3)
    c.tips(o, w)


def opinion(c, o, w):
    if o.get("topic") not in OP_TOPICS:
        c.err(w, f"topic '{o.get('topic')}' 허용 안 됨")
    c.s(o, w, "question", 12, 60, spoken=True)
    c.s(o, w, "question_ko")
    ol = o.get("outline")
    if not isinstance(ol, list) or not 3 <= len(ol) <= 6:
        c.err(w, "outline 3~6개")
    c.samples(o, w, (95, 150), (130, 185))
    c.tips(o, w)


# ---- 오픽 -----------------------------------------------------------------------------

def opic_question(c, o, w, kinds=KINDS):
    if o.get("kind") not in kinds:
        c.err(w, f"kind '{o.get('kind')}' 허용 안 됨")
    c.s(o, w, "question", 8, 80, spoken=True)
    c.s(o, w, "question_ko")
    c.samples(o, w, (70, 150), (140, 260))
    c.tips(o, w)


def questions(c, o, w):
    if o.get("topic") not in OPIC_TOPICS:
        c.err(w, f"topic '{o.get('topic')}' 없음 (core/speaking.py OPIC_TOPICS)")
    if (o.get("kind") == "intro") != (o.get("topic") == "intro"):
        c.err(w, "kind intro ↔ topic intro")
    if not isinstance(o.get("level"), int) or not 1 <= o["level"] <= 6:
        c.err(w, "level 1~6")
    opic_question(c, o, w)


def roleplay(c, o, w):
    if o.get("topic") not in OPIC_TOPICS or o.get("topic") == "intro":
        c.err(w, f"topic '{o.get('topic')}' 없음")
    if not isinstance(o.get("level"), int) or not 3 <= o["level"] <= 6:
        c.err(w, "level 3~6")
    c.s(o, w, "situation_ko")
    steps = o.get("steps")
    if not isinstance(steps, list) or [s.get("kind") if isinstance(s, dict) else None for s in steps] != RP_KINDS:
        c.err(w, "steps 3개 (ask → solve → exp)")
        return
    for i, s in enumerate(steps):
        opic_question(c, s, f"{w} step{i + 1}", set(RP_KINDS))


CHECKS = {
    ("toeic", "read_aloud"): read_aloud, ("toeic", "describe_picture"): describe_picture,
    ("toeic", "respond_questions"): respond_questions, ("toeic", "respond_info"): respond_info,
    ("toeic", "opinion"): opinion, ("opic", "questions"): questions, ("opic", "roleplay"): roleplay,
}


def kind_of(path: Path) -> tuple[str, str] | None:
    for (folder, stem) in CHECKS:
        if path.parent.name == folder and re.fullmatch(rf"{stem}(_[\w-]+)?\.json", path.name):
            return folder, stem
    return None


def validate_file(path: Path, seen: set) -> tuple[list[str], Counter]:
    c = C(f"{path.parent.name}/{path.name}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return [f"{path.name}: JSON 읽기 실패 - {e}"], Counter()
    if not isinstance(data, list):
        return [f"{path.name}: 최상위가 배열이 아님"], Counter()
    stats = Counter()
    check = CHECKS[kind_of(path)]
    for n, o in enumerate(data):
        if not isinstance(o, dict):
            c.err(f"#{n}", "객체 아님")
            continue
        w = str(o.get("id") or f"#{n}")
        if not o.get("id"):
            c.err(w, "id 없음")
        elif o["id"] in seen:
            c.err(w, "id 중복")
        seen.add(o.get("id"))
        check(c, o, w)
        stats[o.get("topic") or o.get("type") or o.get("setting") or o.get("doc_type") or "-"] += 1
    return c.errors, stats


def all_files() -> list[Path]:
    return sorted(p for p in ROOT.glob("*/*.json") if kind_of(p))


def main(argv):
    only = set(argv)
    seen: dict = {}
    bad = 0
    for p in all_files():
        errors, stats = validate_file(p, seen.setdefault(kind_of(p), set()))
        if only and p.name not in only:
            continue
        print(f"{p.parent.name}/{p.name}: {sum(stats.values())}개  오류 {len(errors)}")
        for e in errors[:40]:
            print("   -", e)
        bad += len(errors)
    for name in only - {p.name for p in all_files()}:
        print(f"{name}: 파일 없음")
        bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
