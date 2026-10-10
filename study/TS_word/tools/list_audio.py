"""audio/*.mp3 목록을 data/audio-list.js 로 만든다 (듣기 화면의 '미리 만든 MP3' 카드가 읽음).
실행: python tools/list_audio.py   (MP3 를 새로 만들거나 지운 뒤)"""
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
rows = []
for p in sorted((HERE / "audio").glob("*.mp3")):
    m = re.match(r"(토익|토플)단어_(.+)_(필수|도전)_(\d+)분_(\d+)", p.stem)
    size = p.stat().st_size
    rows.append({"file": p.name, "set": "toeic" if m and m.group(1) == "토익" else "toefl",
                 "label": f"{m.group(1)} {m.group(2)} · {m.group(3)}" if m else p.stem, "mb": round(size / 1048576, 1)})
(HERE / "data" / "audio-list.js").write_text(
    "/* 자동 생성 — tools/list_audio.py */\nwindow.WARD_AUDIO = " + json.dumps(rows, ensure_ascii=False) + ";\n", encoding="utf-8")
print(len(rows), "개 → data/audio-list.js")
