"""문제 데이터(content/toeic/*.json) 형식 검사.

사용: python tools/validate_content.py              # 전체
      python tools/validate_content.py part5_2.json # 한 파일 (중복 검사는 같은 종류 전체와 비교)
문제가 없으면 파일별 개수와 등급 분포를 출력하고 종료 코드 0.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "content" / "toeic"

P1_TYPES = {"1인 동작", "2인 이상 동작", "사물·배경", "상태(수동태 진행/완료)"}
P2_TYPES = {"Who 의문문", "When 의문문", "Where 의문문", "What·Which 의문문", "Why 의문문", "How 의문문",
            "일반 의문문", "부정·부가 의문문", "선택 의문문", "요청·제안", "평서문", "간접 응답"}
P34_Q_TYPES = {"주제·목적", "화자 정보(직업·장소)", "세부 사항", "요청·제안", "다음 할 일", "의도 파악", "시각 자료 연계"}
P4_TALK = {"전화 메시지", "안내 방송", "회의 발췌", "광고", "방송·뉴스", "연설·소개", "관광·견학 안내"}
P5_TYPES = {"품사", "동사 시제·태", "수 일치", "대명사", "전치사", "접속사", "관계사",
            "준동사(to부정사·동명사·분사)", "비교", "어휘"}
P6_TYPES = P5_TYPES | {"문장 삽입"}
P6_DOCS = {"이메일", "공지", "기사", "광고", "편지", "안내문"}
P7_Q_TYPES = {"주제·목적", "세부 사항", "사실 확인(NOT/TRUE)", "추론", "동의어", "문장 삽입", "의도 파악", "연계 문제"}
POS = {"n.", "v.", "adj.", "adv.", "prep.", "conj.", "phr."}
BLANK = "-------"


class Checker:
    def __init__(self, name: str):
        self.name = name
        self.errors: list[str] = []

    def err(self, where: str, msg: str) -> None:
        self.errors.append(f"{self.name} {where}: {msg}")

    def need(self, obj: dict, where: str, key: str, typ) -> bool:
        if key not in obj:
            self.err(where, f"'{key}' 없음")
            return False
        if not isinstance(obj[key], typ):
            self.err(where, f"'{key}' 형식 오류 ({type(obj[key]).__name__})")
            return False
        if isinstance(obj[key], str) and typ is str and not obj[key].strip() and key not in ("q", "tip"):
            self.err(where, f"'{key}' 비어 있음")
            return False
        return True

    def level(self, obj: dict, where: str) -> None:
        if self.need(obj, where, "level", int) and not 1 <= obj["level"] <= 5:
            self.err(where, "level은 1~5")

    def mcq(self, obj: dict, where: str, n: int, choices_key: str = "choices") -> None:
        if self.need(obj, where, choices_key, list):
            ch = obj[choices_key]
            if len(ch) != n:
                self.err(where, f"선택지 {n}개여야 함 (현재 {len(ch)})")
            if any(not isinstance(c, str) or not c.strip() for c in ch):
                self.err(where, "빈 선택지")
            if len(set(ch)) != len(ch):
                self.err(where, "선택지 중복")
        if self.need(obj, where, "answer", int) and not 0 <= obj["answer"] < n:
            self.err(where, f"answer는 0~{n - 1}")
        self.need(obj, where, "explanation", str)

    def sub_questions(self, item: dict, where: str, types: set, count: int | tuple | None = None) -> None:
        if not self.need(item, where, "questions", list):
            return
        qs = item["questions"]
        if isinstance(count, int) and len(qs) != count:
            self.err(where, f"문항 {count}개여야 함 (현재 {len(qs)})")
        if isinstance(count, tuple) and not count[0] <= len(qs) <= count[1]:
            self.err(where, f"문항 {count[0]}~{count[1]}개여야 함 (현재 {len(qs)})")
        for i, q in enumerate(qs):
            w = f"{where} Q{i + 1}"
            self.need(q, w, "q", str)
            self.mcq(q, w, 4)
            if self.need(q, w, "type", str) and q["type"] not in types:
                self.err(w, f"알 수 없는 type '{q['type']}'")


def _graphic(c: Checker, item: dict, where: str) -> None:
    g = item.get("graphic", "__missing__")
    if g == "__missing__":
        c.err(where, "'graphic' 없음 (없으면 null)")
    elif g is not None:
        if not isinstance(g, dict) or not isinstance(g.get("title"), str) or not isinstance(g.get("rows"), list) \
                or not all(isinstance(r, list) for r in g["rows"]):
            c.err(where, "graphic 형식 오류 ({title, rows:[[...]]})")


def check_part1(c: Checker, item: dict, w: str) -> None:
    c.need(item, w, "scene", str)
    c.mcq(item, w, 4, "statements")
    if c.need(item, w, "type", str) and item["type"] not in P1_TYPES:
        c.err(w, f"알 수 없는 type '{item['type']}'")


def check_part2(c: Checker, item: dict, w: str) -> None:
    c.need(item, w, "question", str)
    c.mcq(item, w, 3)
    c.need(item, w, "translation", str)
    if c.need(item, w, "type", str) and item["type"] not in P2_TYPES:
        c.err(w, f"알 수 없는 type '{item['type']}'")


def check_part3(c: Checker, item: dict, w: str) -> None:
    c.need(item, w, "topic", str)
    c.need(item, w, "translation", str)
    if c.need(item, w, "speakers", dict):
        sp = item["speakers"]
        if not set(sp) <= {"M", "W", "M2", "W2"} or not set(sp.values()) <= {"male", "female"} or len(sp) < 2:
            c.err(w, "speakers 형식 오류")
        if c.need(item, w, "script", list):
            if len(item["script"]) < 4:
                c.err(w, "대화가 너무 짧음 (4턴 이상)")
            for i, line in enumerate(item["script"]):
                if not isinstance(line, dict) or line.get("s") not in sp or not str(line.get("t", "")).strip():
                    c.err(w, f"script[{i}] 형식 오류 (s는 speakers 키)")
    _graphic(c, item, w)
    c.sub_questions(item, w, P34_Q_TYPES, 3)


def check_part4(c: Checker, item: dict, w: str) -> None:
    c.need(item, w, "topic", str)
    c.need(item, w, "script", str)
    c.need(item, w, "translation", str)
    if c.need(item, w, "talk_type", str) and item["talk_type"] not in P4_TALK:
        c.err(w, f"알 수 없는 talk_type '{item['talk_type']}'")
    if item.get("voice") not in ("male", "female"):
        c.err(w, "voice는 male/female")
    _graphic(c, item, w)
    c.sub_questions(item, w, P34_Q_TYPES, 3)


def check_part5(c: Checker, item: dict, w: str) -> None:
    if c.need(item, w, "question", str) and item["question"].count(BLANK) != 1:
        c.err(w, f"빈칸 '{BLANK}'이 정확히 1개여야 함")
    c.mcq(item, w, 4)
    c.need(item, w, "translation", str)
    if c.need(item, w, "type", str) and item["type"] not in P5_TYPES:
        c.err(w, f"알 수 없는 type '{item['type']}'")


def check_part6(c: Checker, item: dict, w: str) -> None:
    c.need(item, w, "title", str)
    c.need(item, w, "translation", str)
    if c.need(item, w, "doc_type", str) and item["doc_type"] not in P6_DOCS:
        c.err(w, f"알 수 없는 doc_type '{item['doc_type']}'")
    if c.need(item, w, "passage", str):
        found = re.findall(r"\{(\d)\}", item["passage"])
        if found != ["1", "2", "3", "4"]:
            c.err(w, f"지문에 {{1}}~{{4}}가 순서대로 한 번씩 있어야 함 (현재 {found})")
    c.sub_questions(item, w, P6_TYPES, 4)
    qs = item.get("questions") or []
    if isinstance(qs, list) and sum(1 for q in qs if isinstance(q, dict) and q.get("type") == "문장 삽입") != 1:
        c.err(w, "문장 삽입 문제가 정확히 1개여야 함")


def check_part7(c: Checker, item: dict, w: str) -> None:
    c.need(item, w, "translation", str)
    kind = item.get("kind")
    if kind not in ("single", "double", "triple"):
        c.err(w, "kind는 single/double/triple")
        return
    if c.need(item, w, "passages", list):
        want = {"single": 1, "double": 2, "triple": 3}[kind]
        if len(item["passages"]) != want:
            c.err(w, f"{kind}는 지문 {want}개")
        for i, p in enumerate(item["passages"]):
            for k in ("doc_type", "title", "text"):
                if not isinstance(p, dict) or not str(p.get(k, "")).strip():
                    c.err(w, f"passages[{i}].{k} 없음")
    c.sub_questions(item, w, P7_Q_TYPES, (2, 4) if kind == "single" else 5)


def check_vocab(c: Checker, item: dict, w: str) -> None:
    for k in ("word", "meaning", "example", "example_ko"):
        c.need(item, w, k, str)
    c.need(item, w, "tip", str)
    if item.get("pos") not in POS:
        c.err(w, f"pos는 {sorted(POS)} 중 하나")
    if item.get("tier", "core") not in ("core", "stretch"):
        c.err(w, "tier는 core(필수) / stretch(도전)")


CHECKS = {"part1.json": check_part1, "part2.json": check_part2, "part3.json": check_part3,
          "part4.json": check_part4, "part5.json": check_part5, "part6.json": check_part6,
          "part7.json": check_part7, "vocab.json": check_vocab}


def kind_of(name: str) -> str | None:
    """part5.json, part5_2.json -> 'part5.json' (검사 규칙 키)."""
    m = re.fullmatch(r"(part[1-7]|vocab)(_[\w-]+)?\.json", name)
    return f"{m.group(1)}.json" if m else None


def validate_file(path: Path, seen_ids: set | None = None, seen_words: dict | None = None) -> tuple[list[str], Counter]:
    c = Checker(path.name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return [f"{path.name}: JSON 읽기 실패 - {e}"], Counter()
    if not isinstance(data, list):
        return [f"{path.name}: 최상위가 배열이 아님"], Counter()
    ids: set[str] = set() if seen_ids is None else seen_ids
    levels: Counter = Counter()
    fn = CHECKS[kind_of(path.name)]
    for n, item in enumerate(data):
        where = f"#{n}"
        if not isinstance(item, dict):
            c.err(where, "항목이 객체가 아님")
            continue
        iid = item.get("id")
        where = f"{iid or '#' + str(n)}"
        if not isinstance(iid, str) or not iid:
            c.err(where, "'id' 없음")
        elif iid in ids:
            c.err(where, "id 중복 (다른 파일 포함)")
        ids.add(iid)
        c.level(item, where)
        levels[item.get("level")] += 1
        fn(c, item, where)
    if kind_of(path.name) == "vocab.json":
        words = seen_words if seen_words is not None else {}
        for i in data:
            if not isinstance(i, dict):
                continue
            w = str(i.get("word", "")).lower().strip()
            if w in words:
                c.err(w, f"단어 중복 ({words[w]}에 이미 있음)")
            else:
                words[w] = f"{path.name} {i.get('id')}"
    return c.errors, levels


def all_files() -> list[Path]:
    return sorted((p for p in ROOT.glob("*.json") if kind_of(p.name)),
                  key=lambda p: (kind_of(p.name), p.name != kind_of(p.name), p.name))


def main(argv: list[str]) -> int:
    """인자로 파일을 주면 그 파일만 출력하지만, 중복 검사는 같은 종류의 모든 파일을 대상으로 한다."""
    only = set(argv)
    total_errors = 0
    seen_ids: dict[str, set] = {}
    seen_words: dict = {}
    for path in all_files():
        kind = kind_of(path.name)
        errors, levels = validate_file(path, seen_ids.setdefault(kind, set()),
                                       seen_words if kind == "vocab.json" else None)
        if only and path.name not in only:
            continue
        dist = " ".join(f"L{k}:{levels[k]}" for k in sorted(k for k in levels if isinstance(k, int)))
        print(f"{path.name}: {sum(levels.values())}개  [{dist}]  오류 {len(errors)}")
        for e in errors[:50]:
            print("   -", e)
        total_errors += len(errors)
    missing = only - {p.name for p in all_files()}
    for name in missing:
        print(f"{name}: 파일 없음")
        total_errors += 1
    return 1 if total_errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
