/* 설정: 설치된 영어 음성 목록과 남녀 목소리 들어 보기 */
(function () {
  "use strict";
  const btn = document.getElementById("voice-test");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    await TTS.load();
    const info = TTS.info();
    const box = document.getElementById("voice-info");
    box.innerHTML = info.count
      ? `영어 음성 ${info.count}개 · 억양: ${info.accents.join(", ") || "-"}<br>` + info.voices.map(TS.esc).join("<br>")
      : "영어 음성이 없습니다. Windows 설정 → 시간 및 언어 → 음성에서 영어 음성을 추가하세요.";
    const rate = Number(document.querySelector('[name="tts_rate"]').value);
    const acc = document.querySelector('[name="tts_accent"]').value;
    TTS.play([
      { text: "Hello. Could you send me the quarterly report by Friday?", gender: "female", pause: 400 },
      { text: "Sure. I'll e-mail it to you this afternoon.", gender: "male" },
    ], { rate, accent: acc === "mix" ? "us" : acc });
  });
})();
