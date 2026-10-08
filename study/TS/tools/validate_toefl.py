"""토플 문제 데이터(content/toefl/*.json) 형식 검사.

사용: python tools/validate_toefl.py              # 전체
      python tools/validate_toefl.py r_daily.json # 한 파일 (id 중복은 같은 종류 전체와 비교)
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "content" / "toefl"
BLANK = re.compile(r"\[\[([A-Za-z]+)\|([A-Za-z]+)\]\]")

READ_TYPES = {"주제·목적", "세부 사항", "추론", "어휘", "사실 확인", "글의 구조"}
LISTEN_TYPES = {"주제·목적", "세부 사항", "추론", "화자 의도", "다음 할 일", "글의 구조"}
DOC_TYPES = {"공지", "이메일", "문자 메시지", "안내문", "광고", "일정표", "웹페이지"}


class C:
    def __init__(self, name):
        self.name, self.errors = name, []

    def err(self, where, msg):
        self.errors.append(f"{self.name} {where}: {msg}")

    def s(self, o, w, k, allow_empty=False):
        v = o.get(k)
        if not isinstance(v, str) or (not allow_empty and not v.strip()):
            self.err(w, f"'{k}' 문자열 필요")
            return False
        return True

    def strlist(self, o, w, k, n=None, lo=None, hi=None):
        v = o.get(k)
        if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
            self.err(w, f"'{k}' 문자열 배열 필요")
            return False
        if n is not None and len(v) != n:
            self.err(w, f"'{k}' {n}개여야 함 (현재 {len(v)})")
        if lo is not None and not lo <= len(v) <= hi:
            self.err(w, f"'{k}' {lo}~{hi}개여야 함 (현재 {len(v)})")
        return True

    def mcq(self, q, w, types):
        self.s(q, w, "q")
        if self.strlist(q, w, "choices", 4) and len(set(q["choices"])) != 4:
            self.err(w, "선택지 중복")
        if not isinstance(q.get("answer"), int) or not 0 <= q["answer"] <= 3:
            self.err(w, "answer 0~3")
        self.s(q, w, "explanation")
        if types and q.get("type") not in types:
            self.err(w, f"type '{q.get('type')}' 허용 안 됨")

    def questions(self, o, w, types, lo, hi):
        qs = o.get("questions")
        if not isinstance(qs, list) or not lo <= len(qs) <= hi:
            self.err(w, f"questions {lo}~{hi}개 필요")
            return
        for i, q in enumerate(qs):
            self.mcq(q, f"{w} Q{i + 1}", types)


def words(text):
    return len(re.findall(r"[A-Za-z']+", BLANK.sub(lambda m: m.group(1) + m.group(2), text)))


def r_words(c, o, w):
    if not c.s(o, w, "text"):
        return
    c.s(o, w, "topic"); c.s(o, w, "translation")
    blanks = BLANK.findall(o["text"])
    if len(blanks) != 10:
        c.err(w, f"빈칸 [[보이는|채울]] 정확히 10개 (현재 {len(blanks)})")
    if o["text"].count("[[") != len(blanks):
        c.err(w, "빈칸 형식 오류 ([[영문자|영문자]])")
    first = re.split(r"(?<=[.!?])\s", o["text"], maxsplit=1)[0]
    if "[[" in first:
        c.err(w, "첫 문장에는 빈칸 금지")
    if not 60 <= words(o["text"]) <= 130:
        c.err(w, f"단어 수 60~130 (현재 {words(o['text'])})")


def r_daily(c, o, w):
    for k in ("title", "text", "translation"):
        c.s(o, w, k)
    if o.get("doc_type") not in DOC_TYPES:
        c.err(w, f"doc_type '{o.get('doc_type')}'")
    c.questions(o, w, READ_TYPES, 2, 3)


def r_academic(c, o, w):
    for k in ("title", "text", "translation"):
        c.s(o, w, k)
    if isinstance(o.get("text"), str) and not 150 <= words(o["text"]) <= 300:
        c.err(w, f"단어 수 150~300 (현재 {words(o['text'])})")
    c.questions(o, w, READ_TYPES, 5, 5)


def l_response(c, o, w):
    c.s(o, w, "prompt"); c.s(o, w, "translation")
    if o.get("voice") not in ("male", "female"):
        c.err(w, "voice male/female")
    c.mcq({**o, "q": "-"}, w, None)


def l_conversation(c, o, w):
    c.s(o, w, "topic"); c.s(o, w, "translation")
    sp = o.get("speakers")
    if not isinstance(sp, dict) or not sp or not set(sp.values()) <= {"male", "female"}:
        c.err(w, "speakers 형식")
        sp = {}
    sc = o.get("script")
    if not isinstance(sc, list) or not 6 <= len(sc) <= 14:
        c.err(w, "script 6~14턴")
    else:
        for i, ln in enumerate(sc):
            if not isinstance(ln, dict) or ln.get("s") not in sp or not str(ln.get("t", "")).strip():
                c.err(w, f"script[{i}] 형식")
    c.questions(o, w, LISTEN_TYPES, 2, 2)


def l_talk(c, o, w):
    for k in ("topic", "script", "translation"):
        c.s(o, w, k)
    if o.get("voice") not in ("male", "female"):
        c.err(w, "voice male/female")
    kind = o.get("kind")
    if kind == "announcement":
        c.questions(o, w, LISTEN_TYPES, 2, 2)
    elif kind == "academic":
        c.questions(o, w, LISTEN_TYPES, 4, 4)
    else:
        c.err(w, "kind announcement/academic")


def w_sentence(c, o, w):
    for k in ("context", "answer", "explanation", "translation"):
        c.s(o, w, k)
    if c.strlist(o, w, "chunks", lo=4, hi=8) and isinstance(o.get("answer"), str):
        joined = " ".join(o["chunks"])
        target = re.sub(r"[.?!]$", "", o["answer"].strip())
        if joined != target:
            c.err(w, f"chunks 합이 answer 와 다름: '{joined}' ≠ '{target}'")
        for alt in o.get("alts") or []:                      # 다른 정답 순서 — 같은 낱말을 다른 순서로 놓은 것이어야 한다
            if not isinstance(alt, str) or sorted(alt.split()) != sorted(joined.split()):
                c.err(w, f"alts 가 chunks 와 같은 낱말이 아님: {alt!r}")


def w_email(c, o, w):
    for k in ("situation", "to", "sample", "sample_ko"):
        c.s(o, w, k)
    c.strlist(o, w, "tasks", 3)
    c.strlist(o, w, "tips", lo=1, hi=6)


def w_discussion(c, o, w):
    for k in ("course", "professor", "sample", "sample_ko"):
        c.s(o, w, k)
    st = o.get("students")
    if not isinstance(st, list) or len(st) != 2 or not all(isinstance(x, dict) and x.get("name") and x.get("post") for x in st):
        c.err(w, "students 2명 {name, post}")
    c.strlist(o, w, "tips", lo=1, hi=6)


def s_repeat(c, o, w):
    c.s(o, w, "topic")
    if o.get("voice") not in ("male", "female"):
        c.err(w, "voice male/female")
    if c.strlist(o, w, "sentences", 7):
        lens = [len(x.split()) for x in o["sentences"]]
        if lens[-1] <= lens[0]:
            c.err(w, "문장이 점점 길어져야 함")
    c.strlist(o, w, "translations", 7)


def s_interview(c, o, w):
    c.s(o, w, "topic"); c.s(o, w, "intro")
    c.strlist(o, w, "questions", 4)
    c.strlist(o, w, "samples", 4)
    c.strlist(o, w, "samples_ko", 4)
    c.strlist(o, w, "tips", lo=1, hi=6)


def vocab(c, o, w):
    """토익 단어와 같은 형식 (content/SCHEMA.md 의 vocab) + tier."""
    for k in ("word", "meaning", "example", "example_ko"):
        c.s(o, w, k)
    c.s(o, w, "tip", allow_empty=True)
    if o.get("pos") not in {"n.", "v.", "adj.", "adv.", "prep.", "conj.", "phr."}:
        c.err(w, "pos 오류")
    if o.get("tier") not in ("core", "stretch"):
        c.err(w, "tier core/stretch")


CHECKS = {"vocab": vocab, "r_words": r_words, "r_daily": r_daily, "r_academic": r_academic, "l_response": l_response,
          "l_conversation": l_conversation, "l_talk": l_talk, "w_sentence": w_sentence, "w_email": w_email,
          "w_discussion": w_discussion, "s_repeat": s_repeat, "s_interview": s_interview}


def kind_of(name: str) -> str | None:
    m = re.fullmatch(r"([a-z]_[a-z]+|vocab)(_[\w-]+)?\.json", name)
    return m.group(1) if m and m.group(1) in CHECKS else None


def validate_file(path: Path, seen: set) -> tuple[list[str], Counter]:
    c = C(path.name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return [f"{path.name}: JSON 읽기 실패 - {e}"], Counter()
    if not isinstance(data, list):
        return [f"{path.name}: 최상위가 배열이 아님"], Counter()
    levels = Counter()
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
        if not isinstance(o.get("level"), int) or not 1 <= o["level"] <= 5:
            c.err(w, "level 1~5")
        levels[o.get("level")] += 1
        CHECKS[kind_of(path.name)](c, o, w)
        if kind_of(path.name) == "vocab":            # 단어 중복 (같은 종류 모든 파일)
            key = "word:" + str(o.get("word", "")).lower().strip()
            if key in seen:
                c.err(w, f"단어 중복 '{o.get('word')}'")
            seen.add(key)
    return c.errors, levels


def all_files():
    return sorted(p for p in ROOT.glob("*.json") if kind_of(p.name))


def main(argv):
    only = set(argv)
    seen: dict[str, set] = {}
    bad = 0
    for p in all_files():
        errors, levels = validate_file(p, seen.setdefault(kind_of(p.name), set()))
        if only and p.name not in only:
            continue
        dist = " ".join(f"L{k}:{levels[k]}" for k in sorted(k for k in levels if isinstance(k, int)))
        print(f"{p.name}: {sum(levels.values())}개  [{dist}]  오류 {len(errors)}")
        for e in errors[:40]:
            print("   -", e)
        bad += len(errors)
    for name in only - {p.name for p in all_files()}:
        print(f"{name}: 파일 없음")
        bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
