"""단어 듣기용 긴 MP3 만들기 — 화면을 끄고 운전·이동 중에 듣는 용도. (TS 앱의 core/audio.py 와 같은 방식)

한 단어: 영어 N번(미국 → 영국 → 호주 억양, 여자·남자 번갈아) → 한국어 뜻 → (선택) 영어 예문 → 짧은 쉼.
음성은 Microsoft 온라인 음성(edge-tts)으로 만들고, 조각을 audio/clips/ 에 보관했다가 이어 붙여 파일 하나로 만든다.

준비:   pip install edge-tts lameenc
예시:   python tools/make_audio.py --set toeic --level 1 --tier core --minutes 60 --repeats 3
        python tools/make_audio.py --set toeic --which weak --backup ts-word-backup.json   (자주 잊는 단어만)

--which 가 all 이 아니면(안 본·학습 중·자주 잊는·별표) 설정 화면에서 내려받은 백업 파일(--backup)이 필요하다.
듣기 화면(listen.html)이 지금 고른 조건 그대로의 명령을 만들어 준다.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import sys
from pathlib import Path

try:
    import edge_tts
except ImportError:                  # pragma: no cover
    edge_tts = None
try:
    import lameenc
except ImportError:                  # pragma: no cover
    lameenc = None

HERE = Path(__file__).resolve().parent.parent

# 반복할 때 미국 → 영국 → 호주 억양 순서, 남녀 목소리는 단어마다 번갈아
ACCENT_VOICES = [("미국", "en-US-JennyNeural", "en-US-GuyNeural"),
                 ("영국", "en-GB-SoniaNeural", "en-GB-RyanNeural"),
                 ("호주", "en-AU-NatashaNeural", "en-AU-WilliamNeural")]
VOICE_KO = "ko-KR-SunHiNeural"
SAMPLE_RATE = 24000                                    # edge-tts 출력 형식과 같게
BITRATE = 48
FRAME_SEC = 576 / SAMPLE_RATE                          # MPEG-2 Layer III 프레임 하나 = 24ms
GAP_AFTER_MS = 600                                     # 다음 단어 전 쉼
CONCURRENCY = 12
MASTERED_DAYS = 21
WEAK_MIN_FAILS = 2
GRADE_NAMES = {"toeic": ["Orange", "Brown", "Green", "Blue", "Gold"], "toefl": [f"밴드{b}" for b in (2, 3, 4, 5, 6)]}
TIER_NAMES = {"core": "필수", "stretch": "도전"}
WHICH = ("all", "new", "learning", "weak", "starred")


class AudioError(RuntimeError):
    pass


def en_voice(accent_i: int, gender_i: int) -> str:
    return ACCENT_VOICES[accent_i % 3][1 + gender_i % 2]


def available() -> bool:
    return edge_tts is not None and lameenc is not None


def clean(text: str) -> str:
    """읽기 좋게: '~' 제거, 괄호 속 보충 설명은 뺀다."""
    out, depth = [], 0
    for ch in text.replace("~", ""):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif not depth:
            out.append(ch)
    return " ".join("".join(out).split())


# ---- 단어 고르기 (듣기 화면과 같은 규칙) ---------------------------------------------------

def load_words(set_name: str, root: Path = HERE) -> list[dict]:
    text = (root / "data" / f"{set_name}.js").read_text(encoding="utf-8")
    return json.loads(text[text.index("= [") + 2:].rstrip().rstrip(";"))


def load_backup(path: Path | None) -> dict:
    if not path:
        return {"cards": {}, "log": []}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw.get("cards"), dict):
        raise AudioError("백업 파일 모양이 아닙니다 (설정 화면에서 내려받은 파일이어야 합니다)")
    return {"cards": raw["cards"], "log": raw.get("log") or []}


def state_of(card: dict | None) -> str:
    if not card or card.get("reps", 0) + card.get("lapses", 0) == 0:
        return "new"
    return "mastered" if card.get("interval", 0) >= MASTERED_DAYS else "learning"


def select(words: list[dict], *, level: int | None, tier: str | None, which: str, backup: dict) -> list[dict]:
    if which not in WHICH:
        raise AudioError(f"--which 는 {', '.join(WHICH)} 중 하나")
    cards = backup["cards"]
    fails: dict[str, int] = {}
    for l in backup["log"]:
        if l.get("g") == 0:
            fails[l["i"]] = fails.get(l["i"], 0) + 1
    out = []
    for w in sorted(words, key=lambda w: (w["level"], w["tier"] != "core")):
        if level and w["level"] != level:
            continue
        if tier and w["tier"] != tier:
            continue
        c = cards.get(w["id"])
        st = state_of(c)
        if which == "new" and st != "new":
            continue
        if which == "learning" and st != "learning":
            continue
        if which == "weak" and fails.get(w["id"], 0) < WEAK_MIN_FAILS:
            continue
        if which == "starred" and not (c and c.get("starred")):
            continue
        out.append(w)
    return out


# ---- 길이 계획 ---------------------------------------------------------------------------

def seconds_per_word(repeats: int, example: bool) -> float:
    """실측(100단어 평균): 영어 단어 조각 1.9초 × 반복, 뜻 + 쉼 3.5초, 예문 3.3초. 반복 3번이면 약 9.1초."""
    return 1.875 * max(1, min(repeats, 5)) + 3.475 + (3.32 if example else 0)


def plan(words: list[dict], minutes: int, *, repeats: int = 3, example: bool = False,
         fill: bool = True, seed: str = "") -> list[list[dict]]:
    """단어 목록을 파일 하나가 약 minutes 분이 되게 나눈다. fill: 마지막 파일이 짧으면 단어를 다시 섞어 채운다."""
    if not words:
        return []
    per = max(1, int(minutes * 60 / seconds_per_word(repeats, example)))
    files = [words[i:i + per] for i in range(0, len(words), per)]
    if fill and len(files[-1]) < per:
        rng = random.Random(seed or "fill")
        extra: list[dict] = []
        while len(files[-1]) + len(extra) < per:
            again = list(words)
            rng.shuffle(again)                        # 다시 들려줄 때는 순서를 바꾼다
            extra += again
        files[-1] = files[-1] + extra[:per - len(files[-1])]
    return files


def file_name(set_name: str, level: int | None, tier: str | None, minutes: int, chunk: int) -> str:
    grade = (GRADE_NAMES[set_name][level - 1] if level else "전체") + (f"_{TIER_NAMES[tier]}" if tier else "")
    return f"{'토플' if set_name == 'toefl' else '토익'}단어_{grade}_{minutes}분_{chunk:02d}.mp3"


# ---- MP3 만들기 --------------------------------------------------------------------------

def mp3_frames(data: bytes) -> int:
    """MPEG 오디오 프레임 수 (길이 계산용)."""
    n = i = 0
    while i + 4 <= len(data):
        h = int.from_bytes(data[i:i + 4], "big")
        if (h >> 21) & 0x7FF != 0x7FF:
            i += 1
            continue
        ver, br_i, sr_i, pad = (h >> 19) & 3, (h >> 12) & 15, (h >> 10) & 3, (h >> 9) & 1
        mpeg1 = ver == 3
        rates = ([0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320] if mpeg1 else
                 [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160])
        srs = [44100, 48000, 32000] if mpeg1 else ([22050, 24000, 16000] if ver == 2 else [11025, 12000, 8000])
        if br_i in (0, 15) or sr_i == 3:
            i += 1
            continue
        size = (144 if mpeg1 else 72) * rates[br_i] * 1000 // srs[sr_i] + pad
        n += 1
        i += size
    return n


_silence_cache: dict[int, bytes] = {}


def silence(ms: int) -> bytes:
    if ms not in _silence_cache:
        enc = lameenc.Encoder()
        enc.set_bit_rate(BITRATE)
        enc.set_in_sample_rate(SAMPLE_RATE)
        enc.set_channels(1)
        enc.set_quality(7)
        _silence_cache[ms] = enc.encode(b"\x00\x00" * int(SAMPLE_RATE * ms / 1000)) + enc.flush()
    return _silence_cache[ms]


def _clip_path(clip_dir: Path, text: str, voice: str) -> Path:
    return clip_dir / (hashlib.sha1(f"{voice}|{text}".encode()).hexdigest()[:20] + ".mp3")


async def _synth(text: str, voice: str, path: Path, sem: asyncio.Semaphore) -> None:
    async with sem:
        for attempt in range(4):
            try:
                tmp = path.with_suffix(".part")
                await edge_tts.Communicate(text, voice).save(str(tmp))
                if tmp.stat().st_size < 500:
                    raise AudioError("빈 음성")
                tmp.replace(path)
                return
            except Exception as e:                    # 네트워크 오류는 잠시 뒤 다시
                if attempt == 3:
                    raise AudioError(f"음성 서비스 연결 실패: {e}") from None
                await asyncio.sleep(1.5 * (attempt + 1))


def segments(words: list[dict], repeats: int, example: bool) -> list[tuple[str, str]]:
    """파일에 들어갈 (텍스트, 목소리) 순서."""
    segs = []
    for n, w in enumerate(words):
        word = clean(w["word"]) or w["word"]
        for r in range(repeats):
            segs.append((word, en_voice(r, n + r)))
        segs.append((clean(w["meaning"]) or w["meaning"], VOICE_KO))
        if example and w.get("example"):
            segs.append((w["example"], en_voice(n, n + 1)))
        segs.append(("", "gap"))
    return segs


def build(words: list[dict], out_dir: Path, *, repeats: int = 3, example: bool = False, progress=None) -> Path:
    """words 로 MP3 한 파일을 만들고 경로를 돌려준다 (같은 내용이면 캐시 재사용)."""
    if not available():
        raise AudioError("필요한 패키지가 없습니다.  pip install edge-tts lameenc")
    if not words:
        raise AudioError("단어가 없습니다.")
    repeats = max(1, min(repeats, 5))
    segs = segments(words, repeats, example)
    key = hashlib.sha1(repr((segs, GAP_AFTER_MS)).encode()).hexdigest()[:16]
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{key}.mp3"
    if out.exists() and out.stat().st_size > 1000:
        return out
    clip_dir = out_dir / "clips"
    clip_dir.mkdir(exist_ok=True)
    need = {(t, v): _clip_path(clip_dir, t, v) for t, v in segs if v != "gap"}
    todo = [(t, v, p) for (t, v), p in need.items() if not p.exists()]
    total, done = len(todo), 0
    if progress:
        progress(0, total)

    async def run():
        nonlocal done
        sem = asyncio.Semaphore(CONCURRENCY)

        async def one(t, v, p):
            nonlocal done
            await _synth(t, v, p, sem)
            done += 1
            if progress:
                progress(done, total)
        await asyncio.gather(*(one(t, v, p) for t, v, p in todo))

    if todo:
        asyncio.run(run())
    gap = silence(GAP_AFTER_MS)
    part = out.with_suffix(".part")
    with open(part, "wb") as f:
        for t, v in segs:
            f.write(gap if v == "gap" else need[(t, v)].read_bytes())
    part.replace(out)
    return out


# ---- 명령줄 ------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="단어 듣기용 긴 MP3 만들기")
    ap.add_argument("--set", dest="set_name", choices=["toeic", "toefl"], default="toeic")
    ap.add_argument("--level", type=int, choices=[1, 2, 3, 4, 5])
    ap.add_argument("--tier", choices=["core", "stretch"])
    ap.add_argument("--which", choices=WHICH, default="all")
    ap.add_argument("--backup", type=Path, help="설정 화면에서 내려받은 백업 파일 (--which 가 all 이 아닐 때)")
    ap.add_argument("--minutes", type=int, choices=[10, 30, 60], default=60, help="파일 하나의 길이")
    ap.add_argument("--repeats", type=int, default=3, help="영어 단어 반복 횟수 (1~5)")
    ap.add_argument("--example", action="store_true", help="뜻 다음에 영어 예문도 읽기")
    ap.add_argument("--no-fill", action="store_true", help="마지막 파일이 짧아도 채우지 않기")
    ap.add_argument("--chunk", type=int, help="이 번호의 파일만 만들기 (1부터)")
    ap.add_argument("--limit", type=int, help="앞쪽 N단어만 (시험용, 채우기 끔)")
    ap.add_argument("--out", type=Path, default=HERE / "audio")
    a = ap.parse_args(argv)
    try:
        if a.which != "all" and not a.backup:
            raise AudioError("--which 를 쓰려면 --backup 백업파일.json 이 필요합니다")
        words = select(load_words(a.set_name), level=a.level, tier=a.tier, which=a.which, backup=load_backup(a.backup))
        if a.limit:
            words = words[:a.limit]
        files = plan(words, a.minutes, repeats=a.repeats, example=a.example, fill=not (a.no_fill or a.limit),
                     seed=f"{a.level}-{a.which}-{a.tier}")
        if not files:
            raise AudioError("조건에 맞는 단어가 없습니다")
        print(f"{len(words)}단어 → 파일 {len(files)}개 (각 약 {a.minutes}분)")
        for n, chunk in enumerate(files, 1):
            if a.chunk and n != a.chunk:
                continue
            name = file_name(a.set_name, a.level, a.tier, a.minutes, n)
            last = [-1]

            def progress(d, t, name=name):
                if t and d != last[0] and (d == t or d % 25 == 0):
                    last[0] = d
                    print(f"  {name}: 음성 조각 {d}/{t}", flush=True)
            path = build(chunk, a.out, repeats=a.repeats, example=a.example, progress=progress)
            target = a.out / name
            path.replace(target)                      # 같은 파일을 두 벌 두지 않는다 (음성 조각은 남아 있어 다시 만들 때 빠르다)
            secs = mp3_frames(target.read_bytes()) * FRAME_SEC
            length = f"약 {secs / 60:.0f}분" if secs >= 90 else f"{secs:.0f}초"
            print(f"완료: {target}  ({len(chunk)}단어, {length}, {target.stat().st_size / 1e6:.1f}MB)")
    except AudioError as e:
        print("오류:", e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
