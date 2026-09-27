"""문제 은행: content/toeic/*.json 을 읽어 파트·등급·유형별로 꺼내 쓴다.

용어
- item: 파일의 항목 하나 (Part 1·2·5는 문항 1개, Part 3·4·6·7은 문항 여러 개인 세트)
- question: 채점 단위 문항. qkey = "파트:item_id:문항번호"
- ref: 세션에 저장하는 item 참조 "파트:item_id"
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from pathlib import Path

LC_PARTS = (1, 2, 3, 4)
RC_PARTS = (5, 6, 7)
PARTS = LC_PARTS + RC_PARTS
SET_PARTS = (3, 4, 6, 7)

PART_INFO = {
    1: {"name": "사진 묘사", "section": "LC", "real_count": 6, "sec_per_q": None},
    2: {"name": "질의응답", "section": "LC", "real_count": 25, "sec_per_q": None},
    3: {"name": "짧은 대화", "section": "LC", "real_count": 39, "sec_per_q": None},
    4: {"name": "짧은 담화", "section": "LC", "real_count": 30, "sec_per_q": None},
    5: {"name": "단문 빈칸", "section": "RC", "real_count": 30, "sec_per_q": 20},
    6: {"name": "장문 빈칸", "section": "RC", "real_count": 16, "sec_per_q": 30},
    7: {"name": "독해", "section": "RC", "real_count": 54, "sec_per_q": 60},
}


@dataclass(frozen=True)
class Question:
    qkey: str
    part: int
    item_id: str
    qidx: int
    level: int
    qtype: str
    answer: int
    explanation: str


class Bank:
    def __init__(self, content_dir: Path):
        self.content_dir = Path(content_dir)
        self.items: dict[int, list[dict]] = {}
        self.by_id: dict[str, dict] = {}          # ref -> item
        self.vocab: list[dict] = []
        self.vocab_by_id: dict[str, dict] = {}
        self._files: dict[str, tuple] = {}        # 파일 이름 -> (수정 시각·크기, 데이터)
        self._signature: tuple = ()
        self._checked_at = 0.0
        self.reload()

    # ---- 불러오기 ----------------------------------------------------------
    def signature(self) -> tuple:
        out = []
        for path in sorted(self.content_dir.glob("*.json")):
            try:
                st = path.stat()
                out.append((path.name, st.st_mtime_ns, st.st_size))
            except OSError:
                pass
        return tuple(out)

    def refresh_if_changed(self, min_interval: float = 2.0) -> bool:
        """문제 파일이 추가·수정됐으면 다시 읽는다 (서버를 다시 켜지 않아도 됨)."""
        now = time.monotonic()
        if now - self._checked_at < min_interval:
            return False
        self._checked_at = now
        if self.signature() == self._signature:
            return False
        self.reload()
        return True

    def reload(self) -> None:
        self._signature = self.signature()
        items, by_id = {}, {}
        for part in PARTS:
            data = [dict(it) for it in self._read_all(f"part{part}")]
            for it in data:
                it["part"] = part
                by_id[f"{part}:{it['id']}"] = it
            items[part] = data
        vocab = self._read_all("vocab")
        # 다 읽은 뒤 한 번에 바꿔 끼운다
        self.items, self.by_id = items, by_id
        self.vocab, self.vocab_by_id = vocab, {v["id"]: v for v in vocab}

    def _read_all(self, stem: str) -> list[dict]:
        """stem.json 과 stem_*.json (추가 문제 파일)을 이름순으로 합친다."""
        out: list[dict] = []
        for path in content_files(self.content_dir, stem):
            out += self._read(path.name)
        return out

    def _read(self, name: str) -> list[dict]:
        """파일 하나 읽기. 작성 중이라 JSON 이 깨져 있으면 직전에 읽은 내용을 그대로 쓴다."""
        path = self.content_dir / name
        try:
            st = path.stat()
        except OSError:
            return []
        sig = (st.st_mtime_ns, st.st_size)
        cached = self._files.get(name)
        if cached and cached[0] == sig:
            return cached[1]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                raise ValueError("최상위가 배열이 아님")
        except (OSError, ValueError):
            return cached[1] if cached else []
        self._files[name] = (sig, data)
        return data

    # ---- 조회 --------------------------------------------------------------
    def item(self, ref: str) -> dict | None:
        return self.by_id.get(ref)

    @staticmethod
    def ref(item: dict) -> str:
        return f"{item['part']}:{item['id']}"

    def questions(self, item: dict) -> list[Question]:
        """item 을 채점 단위 문항 목록으로 편다."""
        part, iid, lvl = item["part"], item["id"], item["level"]
        if part in SET_PARTS:
            return [Question(f"{part}:{iid}:{i}", part, iid, i, lvl, q["type"], q["answer"], q["explanation"])
                    for i, q in enumerate(item["questions"])]
        return [Question(f"{part}:{iid}:0", part, iid, 0, lvl, item["type"], item["answer"], item["explanation"])]

    def question(self, qkey: str) -> Question | None:
        part, iid, qidx = qkey.split(":")
        item = self.item(f"{part}:{iid}")
        if item is None:
            return None
        qs = self.questions(item)
        i = int(qidx)
        return qs[i] if 0 <= i < len(qs) else None

    def count_questions(self, part: int, level: int | None = None, qtype: str | None = None) -> int:
        n = 0
        for it in self.items.get(part, []):
            if level and it["level"] != level:
                continue
            n += sum(1 for q in self.questions(it) if not qtype or q.qtype == qtype)
        return n

    def types(self, part: int) -> list[str]:
        seen: dict[str, None] = {}
        for it in self.items.get(part, []):
            for q in self.questions(it):
                seen.setdefault(q.qtype, None)
        return list(seen)

    # ---- 뽑기 --------------------------------------------------------------
    def pick(self, part: int, n_questions: int, levels: list[int] | None = None, qtype: str | None = None,
             exclude: set[str] | None = None, rng: random.Random | None = None,
             prefer_fresh: set[str] | None = None) -> list[str]:
        """조건에 맞는 item 을 문항 수가 n_questions 에 이를 때까지 뽑아 ref 목록으로 돌려준다.
        prefer_fresh: 이미 푼 ref 집합 — 안 푼 문제를 먼저 낸다."""
        rng = rng or random.Random()
        pool = [it for it in self.items.get(part, [])
                if (not levels or it["level"] in levels)
                and (not qtype or any(q.qtype == qtype for q in self.questions(it)))
                and (not exclude or self.ref(it) not in exclude)]
        rng.shuffle(pool)
        if prefer_fresh:
            pool.sort(key=lambda it: self.ref(it) in prefer_fresh)   # 안정 정렬: 섞인 순서 유지
        out, count = [], 0
        for it in pool:
            if count >= n_questions:
                break
            out.append(self.ref(it))
            count += len(self.questions(it))
        return out

    def pick_p7(self, singles_q: int, doubles: int, triples: int, levels: list[int] | None,
                rng: random.Random, exclude: set[str] | None = None) -> list[str]:
        """Part 7 은 단일/이중/삼중 지문을 나눠 뽑는다."""
        def pool(kind: str) -> list[dict]:
            p = [it for it in self.items.get(7, []) if it["kind"] == kind
                 and (not levels or it["level"] in levels) and (not exclude or self.ref(it) not in exclude)]
            rng.shuffle(p)
            return p
        out, count = [], 0
        for it in pool("single"):
            if count >= singles_q:
                break
            out.append(self.ref(it))
            count += len(it["questions"])
        out += [self.ref(it) for it in pool("double")[:doubles]]
        out += [self.ref(it) for it in pool("triple")[:triples]]
        return out

    # ---- 화면에 보낼 형태 ----------------------------------------------------
    def public_item(self, ref: str) -> dict:
        """정답·해설·번역·스크립트 텍스트를 제외하고 브라우저로 보낼 형태.
        듣기 스크립트는 음성 재생에 필요하므로 포함하되, 화면에는 채점 뒤에만 보인다."""
        it = self.by_id[ref]
        hidden = {"answer", "explanation", "translation"}
        out = {k: v for k, v in it.items() if k not in hidden}
        out["ref"] = ref
        if "questions" in it:
            out["questions"] = [{k: v for k, v in q.items() if k not in hidden} for q in it["questions"]]
        return out

    def reveal(self, ref: str) -> dict:
        """채점 뒤 보여 줄 정답·해설·번역."""
        it = self.by_id[ref]
        qs = self.questions(it)
        return {
            "ref": ref,
            "translation": it.get("translation", ""),
            "questions": [{"qidx": q.qidx, "answer": q.answer, "explanation": q.explanation, "type": q.qtype}
                          for q in qs],
        }


def content_files(content_dir: Path, stem: str) -> list[Path]:
    main = Path(content_dir) / f"{stem}.json"
    extra = sorted(Path(content_dir).glob(f"{stem}_*.json"))
    return ([main] if main.exists() else []) + extra


def split_ref(ref: str) -> tuple[int, str]:
    part, iid = ref.split(":", 1)
    return int(part), iid
