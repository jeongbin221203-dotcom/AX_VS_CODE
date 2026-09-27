"""단어 듣기용 긴 음성 파일(MP3) 만들기 — 운전·이동 중 휴대폰으로 화면을 끈 채 듣는 용도.

한 단어: 영어 단어 N번(여자·남자 목소리 번갈아) → 한국어 뜻 → (선택) 영어 예문 → 짧은 쉼.
- 음성 조각은 Microsoft 온라인 음성(edge-tts)으로 처음 필요할 때 만들고 data/audio/clips/ 에 보관.
- 조각(MP3 프레임)을 이어 붙여 파일 하나로 만든다. 리눅스 서버(Render)에서도 동작.
- 쉼(무음)은 lameenc 로 같은 형식(24kHz·모노·48kbps)의 MP3 로 만든다.
"""
from __future__ import annotations

import asyncio
import hashlib
import random
import threading
from pathlib import Path

try:
    import edge_tts
except ImportError:                  # pragma: no cover
    edge_tts = None
try:
    import lameenc
except ImportError:                  # pragma: no cover
    lameenc = None

# 반복할 때 미국 → 영국 → 호주 억양 순서, 남녀 목소리는 단어마다 번갈아
ACCENT_VOICES = [("미국", "en-US-JennyNeural", "en-US-GuyNeural"),
                 ("영국", "en-GB-SoniaNeural", "en-GB-RyanNeural"),
                 ("호주", "en-AU-NatashaNeural", "en-AU-WilliamNeural")]


def en_voice(accent_i: int, gender_i: int) -> str:
    return ACCENT_VOICES[accent_i % 3][1 + gender_i % 2]
VOICE_KO = "ko-KR-SunHiNeural"
SAMPLE_RATE = 24000                                    # edge-tts 출력 형식과 같게
BITRATE = 48
FRAME_SEC = 576 / SAMPLE_RATE                          # MPEG-2 Layer III 프레임 하나 = 24ms
GAP_AFTER_MS = 600                                     # 다음 단어 전 쉼 (조각 자체에도 앞뒤 여백이 있음)
CONCURRENCY = 12


class AudioError(RuntimeError):
    pass


def available() -> bool:
    return edge_tts is not None and lameenc is not None


def _clean(text: str) -> str:
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


# ---- 길이 계획 ------------------------------------------------------------------

def seconds_per_word(repeats: int, example: bool) -> float:
    """실측(100단어 평균): 영어 단어 조각 1.9초 × 반복, 뜻 + 쉼 3.5초, 예문 3.3초. 반복 3번이면 약 9.1초."""
    return 1.875 * max(1, min(repeats, 5)) + 3.475 + (3.32 if example else 0)


def plan(words: list[dict], minutes: int, *, repeats: int = 3, example: bool = False,
         fill: bool = True, seed: str = "") -> list[list[dict]]:
    """단어 목록을 파일 하나가 약 minutes 분이 되게 나눈다.
    fill: 마지막 파일(또는 목록 전체)이 짧으면 단어를 다시 섞어 넣어 길이를 채운다."""
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


# ---- MP3 조각 ---------------------------------------------------------------------

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


def _segments(words: list[dict], repeats: int, example: bool) -> list[tuple[str, str]]:
    """파일에 들어갈 (텍스트, 목소리) 순서."""
    segs = []
    for n, w in enumerate(words):
        word = _clean(w["word"]) or w["word"]
        for r in range(repeats):
            segs.append((word, en_voice(r, n + r)))
        segs.append((_clean(w["meaning"]) or w["meaning"], VOICE_KO))
        if example and w.get("example"):
            segs.append((w["example"], en_voice(n, n + 1)))
        segs.append(("", "gap"))
    return segs


def build(words: list[dict], out_dir: Path, *, repeats: int = 3, example: bool = False,
          progress=None) -> Path:
    """words 로 MP3 한 파일을 만들고 경로를 돌려준다 (같은 내용이면 캐시 재사용).
    progress(done, total): 음성 조각을 만드는 동안 호출된다."""
    if not available():
        raise AudioError("음성 파일 기능에 필요한 패키지(edge-tts, lameenc)가 없습니다. pip install -r requirements.txt")
    if not words:
        raise AudioError("단어가 없습니다.")
    repeats = max(1, min(repeats, 5))
    segs = _segments(words, repeats, example)
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


def duration_sec(path: Path) -> float:
    return mp3_frames(path.read_bytes()) * FRAME_SEC


# ---- 백그라운드 작업 (긴 파일은 만드는 데 1~2분) -------------------------------------

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def job_key(words: list[dict], repeats: int, example: bool) -> str:
    return hashlib.sha1(repr(([w["id"] for w in words], repeats, example)).encode()).hexdigest()[:16]


def start_job(words: list[dict], out_dir: Path, *, repeats: int, example: bool, name: str) -> dict:
    key = job_key(words, repeats, example)
    with _lock:
        job = _jobs.get(key)
        if job and job["state"] in ("running", "done") and (job["state"] == "running" or Path(job["path"]).exists()):
            return job
        job = {"key": key, "state": "running", "done": 0, "total": 0, "path": "", "error": "", "name": name}
        _jobs[key] = job

    def progress(d, t):
        job["done"], job["total"] = d, t

    def work():
        try:
            p = build(words, out_dir, repeats=repeats, example=example, progress=progress)
            job["path"] = str(p)
            job["minutes"] = round(duration_sec(p) / 60)
            job["mb"] = round(p.stat().st_size / 1e6, 1)
            job["state"] = "done"
        except Exception as e:                        # 화면에 원인을 보여 준다
            job["error"] = str(e)
            job["state"] = "error"

    threading.Thread(target=work, daemon=True).start()
    return job


def get_job(key: str) -> dict | None:
    return _jobs.get(key)
