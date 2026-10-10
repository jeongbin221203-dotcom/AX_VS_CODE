"""TS 앱의 단어(content/*/vocab*.json)를 서버 없이 쓰는 data/*.js 로 바꾼다.

실행:  python tools/build_data.py            (TS_ward 폴더에서)
       python tools/build_data.py ../TS      (TS 앱 위치를 직접 줄 때)

HTML 을 file:// 로 열어도 되게(fetch 는 file:// 에서 막힘) JSON 이 아니라 <script> 로 읽는 .js 로 만든다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
TS = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else HERE.parent / "TS"

SETS = {
    "toeic": TS / "content" / "toeic",
    "toefl": TS / "content" / "toefl",
}
FIELDS = ("id", "level", "tier", "word", "pos", "meaning", "example", "example_ko", "tip")


def load(folder: Path) -> list[dict]:
    words, seen = [], set()
    for path in sorted(folder.glob("vocab*.json")):
        for w in json.loads(path.read_text(encoding="utf-8")):
            if w["id"] in seen:
                raise SystemExit(f"중복 id: {w['id']} ({path.name})")
            seen.add(w["id"])
            words.append({k: w.get(k, "core" if k == "tier" else "") for k in FIELDS})
    return words


def main() -> None:
    out = HERE / "data"
    out.mkdir(exist_ok=True)
    for key, folder in SETS.items():
        if not folder.exists():
            raise SystemExit(f"없는 폴더: {folder}")
        words = load(folder)
        body = json.dumps(words, ensure_ascii=False, separators=(",", ":"))
        (out / f"{key}.js").write_text(
            f"/* 자동 생성 — tools/build_data.py. 직접 고치지 마세요. */\n"
            f"(window.WARD_DATA = window.WARD_DATA || {{}}).{key} = {body};\n", encoding="utf-8")
        levels = {}
        for w in words:
            levels[w["level"]] = levels.get(w["level"], 0) + 1
        print(f"{key}: {len(words)}개 {dict(sorted(levels.items()))} → data/{key}.js")


if __name__ == "__main__":
    main()
