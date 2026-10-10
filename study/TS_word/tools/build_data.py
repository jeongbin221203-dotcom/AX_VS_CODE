"""TS 앱의 단어·문제(content/*)를 서버 없이 쓰는 data/*.js 로 바꾼다.

실행:  python tools/build_data.py            (TS_word 폴더에서)
       python tools/build_data.py ../TS      (TS 앱 위치를 직접 줄 때)

만드는 파일
- data/toeic.js, data/toefl.js        단어 (window.WARD_DATA.toeic / .toefl)
- data/toeic-p1.js ~ toeic-p7.js      토익 문제 은행 파트별 (window.TS_DATA.p1 ~ .p7, 정답·해설 포함)
- data/guide.js                       등급별 학습 가이드·풀이 전략 (window.TS_GUIDE)
- data/toefl-<과제>.js (11개)          토플 문제 은행 과제별 (window.TS_DATA.tf_<과제>, tools/build_toefl.py)
- data/speaking-tsp.js, speaking-opic.js  토익스피킹·오픽 문제 (window.SPK_DATA.tsp / .opic)
- data/meta.js                        문제 개수 요약 (window.TS_META — 문제 데이터를 읽지 않고도 개수를 보여 줌)

HTML 을 file:// 로 열어도 되게(fetch 는 file:// 에서 막힘) JSON 이 아니라 <script> 로 읽는 .js 로 만든다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_speaking                                     # noqa: E402
import build_toefl                                        # noqa: E402
TS = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else HERE.parent / "TS"

SETS = {
    "toeic": TS / "content" / "toeic",
    "toefl": TS / "content" / "toefl",
}
FIELDS = ("id", "level", "tier", "word", "pos", "meaning", "example", "example_ko", "tip")
META: dict = {}                                  # data/meta.js 에 들어갈 시험별 개수 요약 (main 이 채우고 write_meta 가 씀)
HEADER = "/* 자동 생성 — tools/build_data.py. 직접 고치지 마세요. */\n"


def load(folder: Path) -> list[dict]:
    words, seen = [], set()
    for path in sorted(folder.glob("vocab*.json")):
        for w in json.loads(path.read_text(encoding="utf-8")):
            if w["id"] in seen:
                raise SystemExit(f"중복 id: {w['id']} ({path.name})")
            seen.add(w["id"])
            words.append({k: w.get(k, "core" if k == "tier" else "") for k in FIELDS})
    return words


def content_files(folder: Path, stem: str) -> list[Path]:
    """stem.json 과 stem_*.json 을 이름순으로 (TS 앱 core/content.py 와 같은 규칙)."""
    main = folder / f"{stem}.json"
    return ([main] if main.exists() else []) + sorted(folder.glob(f"{stem}_*.json"))


def build_questions(out: Path) -> None:
    """토익 문제 은행 → data/toeic-p1.js ~ toeic-p7.js (파트별 파일, 필요한 화면에서만 읽는다)."""
    folder = TS / "content" / "toeic"
    total = 0
    meta = {}
    for part in range(1, 8):
        items, seen = [], set()
        for path in content_files(folder, f"part{part}"):
            for it in json.loads(path.read_text(encoding="utf-8")):
                if it["id"] in seen:
                    raise SystemExit(f"중복 id: {it['id']} ({path.name})")
                seen.add(it["id"])
                it = dict(it)
                it["part"] = part
                items.append(it)
        n = sum(len(it["questions"]) if "questions" in it else 1 for it in items)
        total += n
        levels, types = {}, {}
        for it in items:
            for q in (it["questions"] if "questions" in it else [it]):
                levels[it["level"]] = levels.get(it["level"], 0) + 1
                types[q["type"]] = types.get(q["type"], 0) + 1
        meta[part] = {"items": len(items), "questions": n, "levels": dict(sorted(levels.items())),
                      "types": [[t, c] for t, c in types.items()]}          # 처음 나온 순서 그대로
        body = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
        (out / f"toeic-p{part}.js").write_text(
            HEADER + f"(window.TS_DATA = window.TS_DATA || {{}}).p{part} = {body};\n", encoding="utf-8")
        print(f"toeic part{part}: 묶음 {len(items)}개 · 문항 {n}개 → data/toeic-p{part}.js ({len(body) // 1024}KB)")
    print(f"토익 문항 합계 {total}개")
    # 문제 데이터를 읽지 않고도 개수를 보여 주려고(허브·파트 연습 화면) 요약을 모은다 — main() 끝에서 write_meta 가 meta.js 로 쓴다
    META["toeic"] = {"questions": total, "parts": meta}


def write_meta(out: Path) -> None:
    """시험별 개수 요약을 data/meta.js 한 파일로 (window.TS_META = {toeic, speaking, ...}). 시험 담당이 META[키] 에 넣는다."""
    (out / "meta.js").write_text(HEADER + "window.TS_META = " + json.dumps(META, ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")


def build_guide(out: Path) -> None:
    """등급별 학습 가이드·파트별 풀이 전략 (TS 앱 core/guide.py) → data/guide.js"""
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(TS))
    from core import guide as g                                    # noqa: E402  (읽기만 함)
    data = {"GUIDE": g.GUIDE, "PART_TIPS": g.PART_TIPS, "TARGET_SEC": g.TARGET_SEC}
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    (out / "guide.js").write_text(HEADER + f"window.TS_GUIDE = {body};\n", encoding="utf-8")
    print(f"guide: 등급 {len(g.GUIDE)}개 → data/guide.js")


def main() -> None:
    out = HERE / "data"
    out.mkdir(exist_ok=True)
    for key, folder in SETS.items():
        if not folder.exists():
            raise SystemExit(f"없는 폴더: {folder}")
        words = load(folder)
        body = json.dumps(words, ensure_ascii=False, separators=(",", ":"))
        (out / f"{key}.js").write_text(
            HEADER + f"(window.WARD_DATA = window.WARD_DATA || {{}}).{key} = {body};\n", encoding="utf-8")
        levels = {}
        for w in words:
            levels[w["level"]] = levels.get(w["level"], 0) + 1
        print(f"{key}: {len(words)}개 {dict(sorted(levels.items()))} → data/{key}.js")
    build_questions(out)
    build_guide(out)
    META["toefl"] = build_toefl.build(out, TS)               # 토플 (tools/build_toefl.py)
    META["speaking"] = build_speaking.build(out, TS)          # 토익스피킹·오픽 (tools/build_speaking.py)
    write_meta(out)


if __name__ == "__main__":
    main()
