"""말하기 시험(토익스피킹·오픽) 문제 은행 → data/speaking-tsp.js, data/speaking-opic.js (build_data.py 가 부른다).

단독 실행:  python tools/build_speaking.py            (TS_word 폴더에서, TS 앱은 ../TS — 읽기만 함)

- data/speaking-tsp.js    window.SPK_DATA.tsp  = {read_aloud:[...], describe_picture:[...], respond_questions:[...], respond_info:[...], opinion:[...]}
- data/speaking-opic.js   window.SPK_DATA.opic = {questions:[...], roleplay:[...]}   (questions 는 OPIC_TOPICS 에 있는 주제만 — TS 앱과 같은 규칙)
- 개수 요약(meta)은 build() 가 돌려주고 build_data.py 가 data/meta.js 의 "speaking" 에 합친다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
HEADER = "/* 자동 생성 — tools/build_data.py (tools/build_speaking.py). 직접 고치지 마세요. */\n"
TSP_TASKS = ["read_aloud", "describe_picture", "respond_questions", "respond_info", "opinion"]


def content_files(folder: Path, stem: str) -> list[Path]:
    """stem.json 과 stem_*.json 을 이름순으로 (TS 앱 core/content.py 와 같은 규칙)."""
    main = folder / f"{stem}.json"
    return ([main] if main.exists() else []) + sorted(folder.glob(f"{stem}_*.json"))


def load(folder: Path, stem: str) -> list[dict]:
    out: list[dict] = []
    for p in content_files(folder, stem):
        out += json.loads(p.read_text(encoding="utf-8"))
    return out


def build(out: Path, ts: Path) -> dict:
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ts))
    from core import speaking as S                                  # noqa: E402  (읽기만 함)
    base = ts / "content" / "speaking"
    if not base.exists():
        raise SystemExit(f"없는 폴더: {base}")
    dump = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":"))      # noqa: E731

    tsp, seen = {}, set()
    for task in TSP_TASKS:
        items = load(base / "toeic", task)
        for it in items:
            if (task, it["id"]) in seen:
                raise SystemExit(f"중복 id: {task} {it['id']}")
            seen.add((task, it["id"]))
        tsp[task] = items
    body = dump(tsp)
    (out / "speaking-tsp.js").write_text(HEADER + f"(window.SPK_DATA = window.SPK_DATA || {{}}).tsp = {body};\n", encoding="utf-8")
    print(f"speaking tsp: {sum(len(v) for v in tsp.values())}문제 {dict((k, len(v)) for k, v in tsp.items())} → data/speaking-tsp.js ({len(body) // 1024}KB)")

    questions = [q for q in load(base / "opic", "questions") if q.get("topic") in S.OPIC_TOPICS]
    roleplay = load(base / "opic", "roleplay")
    ids = [q["id"] for q in questions] + [r["id"] for r in roleplay]
    if len(ids) != len(set(ids)):
        raise SystemExit("오픽 id 중복")
    body = dump({"questions": questions, "roleplay": roleplay})
    (out / "speaking-opic.js").write_text(HEADER + f"(window.SPK_DATA = window.SPK_DATA || {{}}).opic = {body};\n", encoding="utf-8")
    print(f"speaking opic: 문항 {len(questions)} · 롤플레이 {len(roleplay)}세트 → data/speaking-opic.js ({len(body) // 1024}KB)")

    topics: dict[str, dict[str, int]] = {}
    for q in questions:
        topics.setdefault(q["topic"], {}).setdefault(q["kind"], 0)
        topics[q["topic"]][q["kind"]] += 1
    for r in roleplay:
        topics.setdefault(r["topic"], {}).setdefault("roleplay", 0)
        topics[r["topic"]]["roleplay"] += 1
    return {"tsp": {"total": sum(len(v) for v in tsp.values()), "tasks": {k: len(v) for k, v in tsp.items()}},
            "opic": {"questions": len(questions), "roleplay": len(roleplay),
                     "intro": sum(1 for q in questions if q["kind"] == "intro"), "topics": topics}}


if __name__ == "__main__":
    out = HERE / "data"
    out.mkdir(exist_ok=True)
    print(json.dumps(build(out, Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else HERE.parent / "TS"), ensure_ascii=False)[:300])
