"""단어 듣기용 음성 파일(WAV) 만들기 — Windows 내장 음성(System.Speech)으로 오프라인 생성.

한 단어: 영어 단어 N번(사이 쉼) → 한국어 뜻 → (선택) 영어 예문 → 다음 단어 전 쉼.
휴대폰에 옮겨 화면을 끈 채 들을 수 있다. 만든 파일은 data/audio/ 에 캐시.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

PS_SCRIPT = r"""
param([string]$Job, [string]$Out)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$cfg = Get-Content -Raw -Encoding UTF8 $Job | ConvertFrom-Json
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voices = $s.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object { $_.VoiceInfo }
$en = ($voices | Where-Object { $_.Culture.Name -like 'en-*' } | Select-Object -First 1)
$ko = ($voices | Where-Object { $_.Culture.Name -like 'ko-*' } | Select-Object -First 1)
if (-not $en) { throw 'NO_EN_VOICE' }
if (-not $ko) { throw 'NO_KO_VOICE' }
$pb = New-Object System.Speech.Synthesis.PromptBuilder
$gap = [TimeSpan]::FromMilliseconds([int]$cfg.gap_ms)
$after = [TimeSpan]::FromMilliseconds([int]$cfg.after_ms)
foreach ($w in $cfg.words) {
  $pb.StartVoice($en)
  $pb.StartStyle((New-Object System.Speech.Synthesis.PromptStyle([System.Speech.Synthesis.PromptRate]::Medium)))
  for ($i = 0; $i -lt [int]$cfg.repeats; $i++) { $pb.AppendText($w.word); $pb.AppendBreak($gap) }
  $pb.EndStyle()
  $pb.EndVoice()
  $pb.StartVoice($ko); $pb.AppendText($w.meaning); $pb.EndVoice()
  if ($cfg.example -and $w.example) { $pb.AppendBreak($gap); $pb.StartVoice($en); $pb.AppendText($w.example); $pb.EndVoice() }
  $pb.AppendBreak($after)
}
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$s.Rate = [int]$cfg.rate
$s.SetOutputToWaveFile($Out, $fmt)
$s.Speak($pb)
$s.Dispose()
"""


class AudioError(RuntimeError):
    pass


def available() -> bool:
    return sys.platform == "win32"


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


def build(words: list[dict], out_dir: Path, *, repeats: int = 3, example: bool = False,
          gap_ms: int = 700, after_ms: int = 1500, rate: int = 0) -> Path:
    """words 로 WAV 를 만들고 경로를 돌려준다 (같은 내용이면 캐시 재사용)."""
    if not available():
        raise AudioError("음성 파일 만들기는 Windows에서만 됩니다.")
    if not words:
        raise AudioError("단어가 없습니다.")
    cfg = {
        "repeats": max(1, min(repeats, 5)), "example": bool(example), "gap_ms": gap_ms, "after_ms": after_ms,
        "rate": max(-5, min(rate, 5)),
        "words": [{"word": _clean(w["word"]), "meaning": _clean(w["meaning"]) or w["meaning"],
                   "example": w.get("example", "")} for w in words],
    }
    key = hashlib.sha1(json.dumps(cfg, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{key}.wav"
    if out.exists() and out.stat().st_size > 1000:
        return out
    with tempfile.TemporaryDirectory() as tmp:
        job = Path(tmp) / "job.json"
        ps1 = Path(tmp) / "make.ps1"
        job.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        ps1.write_text(PS_SCRIPT, encoding="utf-8-sig")        # PowerShell 5.1 은 BOM 이 있어야 UTF-8 로 읽음
        part = out.with_suffix(".part.wav")
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1),
                                "-Job", str(job), "-Out", str(part)],
                               capture_output=True, text=True, timeout=1800)
        except subprocess.TimeoutExpired:
            raise AudioError("음성 파일을 만드는 데 너무 오래 걸립니다. 단어 수를 줄여 보세요.") from None
        if r.returncode != 0 or not part.exists():
            msg = (r.stderr or r.stdout or "").strip()
            if "NO_KO_VOICE" in msg:
                raise AudioError("한국어 음성이 없습니다. Windows 설정 → 시간 및 언어 → 음성에서 한국어 음성을 추가하세요.")
            if "NO_EN_VOICE" in msg:
                raise AudioError("영어 음성이 없습니다. Windows 설정 → 시간 및 언어 → 음성에서 영어 음성을 추가하세요.")
            raise AudioError("음성 파일 만들기 실패: " + msg[-300:])
        part.replace(out)
    return out
