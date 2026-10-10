"""TS 앱의 토플 문제(content/toefl/*.json)를 서버 없이 쓰는 data/toefl-<과제>.js 로 바꾼다.

build_data.py 가 부른다 (직접 실행해도 된다:  python tools/build_toefl.py [../TS]).
- 과제 11개마다 파일 하나: data/toefl-r_words.js … toefl-w_discussion.js  →  window.TS_DATA.tf_<과제> = [...]
  (정답·해설 포함 — 개인 학습용. 필요한 과제만 화면에서 <script> 로 읽는다: js/toefl-core.js 의 loadTask)
- 개수 요약은 build() 가 돌려주는 dict 를 build_data.py 가 data/meta.js 의 TS_META.toefl 로 합친다.
  {"items": 묶음 수, "questions": 문항 수, "vocab": 어휘 수, "tasks": {과제: {"items", "questions", "levels": {난이도: 묶음 수}}}}
과제 이름·순서는 TS 앱 core/toefl.py 의 TASKS 와 같다(그 파일을 읽어 온다).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
HEADER = "/* 자동 생성 — tools/build_toefl.py. 직접 고치지 마세요. */\n"


def content_files(folder: Path, stem: str) -> list[Path]:
    """stem.json 과 stem_*.json 을 이름순으로 (TS 앱 core/content.py 와 같은 규칙)."""
    main = folder / f"{stem}.json"
    return ([main] if main.exists() else []) + sorted(folder.glob(f"{stem}_*.json"))


def n_questions(task: str, it: dict) -> int:
    if "questions" in it:
        return len(it["questions"])
    if task == "r_words":
        return 1
    if task == "s_repeat":
        return len(it["sentences"])
    if task == "s_interview":
        return len(it["questions"])
    return 1


def build(out: Path, ts: Path) -> dict:
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ts))
    from core.toefl import TASKS                                   # noqa: E402  (읽기만 함)
    folder = ts / "content" / "toefl"
    meta_tasks: dict = {}
    total_items = total_q = 0
    for task in TASKS:
        items, seen = [], set()
        for path in content_files(folder, task):
            for it in json.loads(path.read_text(encoding="utf-8")):
                if it["id"] in seen:
                    raise SystemExit(f"중복 id: {it['id']} ({path.name})")
                seen.add(it["id"])
                items.append(it)
        levels: dict = {}
        for it in items:
            levels[it["level"]] = levels.get(it["level"], 0) + 1
        q = sum(n_questions(task, it) for it in items)
        meta_tasks[task] = {"items": len(items), "questions": q, "levels": dict(sorted(levels.items()))}
        total_items += len(items)
        total_q += q
        body = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
        (out / f"toefl-{task}.js").write_text(
            HEADER + f"(window.TS_DATA = window.TS_DATA || {{}}).tf_{task} = {body};\n", encoding="utf-8")
        print(f"toefl {task}: 묶음 {len(items)}개 {dict(sorted(levels.items()))} → data/toefl-{task}.js ({len(body) // 1024}KB)")
    vocab = sum(len(json.loads(p.read_text(encoding="utf-8"))) for p in content_files(folder, "vocab"))
    print(f"토플 문제 묶음 합계 {total_items}개 · 어휘 {vocab}개")
    return {"items": total_items, "questions": total_q, "vocab": vocab, "tasks": meta_tasks}


if __name__ == "__main__":
    ts_dir = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else HERE.parent / "TS"
    out_dir = HERE / "data"
    out_dir.mkdir(exist_ok=True)
    build(out_dir, ts_dir)
    print("meta.js 는 build_data.py 를 실행해야 갱신됩니다.")
