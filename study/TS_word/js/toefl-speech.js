/* 토플 말하기 도우미 (window.ToeflSpeech) — 연습·모의고사가 함께 쓴다.
   - 단어 단위 비교(lcs): 따라 말하기 정확도
   - listen(): 브라우저 음성 인식(Web Speech)으로 말한 내용을 글자로
   - recorder(): MediaRecorder 로 녹음해 다시 듣기 (녹음은 이 화면에서만 쓰고 저장하지 않는다)
   음성 인식·마이크를 쓸 수 없으면 null 을 돌려주고, 부르는 쪽이 스스로 채점으로 넘어간다. */
(function (root) {
  "use strict";
  const norm = w => w.toLowerCase().replace(/[’']/g, "'").replace(/[^a-z0-9']/g, "");
  const wordsOf = s => String(s).split(/\s+/).map(w => ({ raw: w, n: norm(w) })).filter(w => w.n);

  /** 두 단어 목록의 가장 긴 공통 순서 → {match: 맞은 개수, hit: 첫째 목록에서 맞은 위치 Set} */
  function lcs(a, b) {
    const dp = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
    for (let x = a.length - 1; x >= 0; x--) for (let y = b.length - 1; y >= 0; y--)
      dp[x][y] = a[x].n === b[y].n ? dp[x + 1][y + 1] + 1 : Math.max(dp[x + 1][y], dp[x][y + 1]);
    const hit = new Set();
    let x = 0, y = 0;
    while (x < a.length && y < b.length) {
      if (a[x].n === b[y].n) { hit.add(x); x++; y++; } else if (dp[x + 1][y] >= dp[x][y + 1]) x++; else y++;
    }
    return { match: dp[0][0], hit };
  }
  /** 따라 말하기 점수 0~1: 원문 단어 중 순서대로 맞게 말한 비율 */
  function repeatScore(target, heard) {
    const a = wordsOf(target);
    return a.length ? lcs(a, wordsOf(heard)).match / a.length : 0;
  }

  const SR = () => root.SpeechRecognition || root.webkitSpeechRecognition;
  const hasRecognition = () => !!SR();

  /** seconds 동안 말한 내용을 글자로. out: 실시간으로 글자를 보여 줄 요소, ctl.stop(): 일찍 끝내기, reg(fn): 화면을 떠날 때 부를 정리 함수 등록.
      인식을 쓸 수 없으면 null. */
  function listen(seconds, out, ctl, reg) {
    return new Promise(resolve => {
      const Rec = SR();
      if (!Rec) return resolve(null);
      const rec = new Rec();
      rec.lang = "en-US";
      rec.continuous = true;
      rec.interimResults = true;
      let finalText = "", interim = "", done = false;
      rec.onresult = e => {
        interim = "";
        for (let i = e.resultIndex; i < e.results.length; i++) {
          if (e.results[i].isFinal) finalText += e.results[i][0].transcript + " ";
          else interim += e.results[i][0].transcript;
        }
        if (out) out.textContent = (finalText + interim).trim();
      };
      const end = () => { if (!done) { done = true; try { rec.stop(); } catch (e) { /* 무시 */ } setTimeout(() => resolve((finalText + interim).trim()), 400); } };
      // 마이크·인식 서비스를 못 쓰면 null → 스스로 채점으로 넘어간다
      rec.onerror = e => { if (["not-allowed", "service-not-allowed", "audio-capture", "network"].includes(e.error)) { done = true; resolve(null); } };
      rec.onend = () => end();
      try { rec.start(); } catch (e) { return resolve(null); }
      if (reg) reg(end);
      if (ctl) ctl.stop = end;
      setTimeout(end, seconds * 1000);
    });
  }

  /** 녹음기: {start(), stop() → 재생 주소(blob URL)}. 마이크를 못 쓰면 null. */
  async function recorder(reg) {
    const md = root.navigator && root.navigator.mediaDevices;
    if (!md || !md.getUserMedia || !root.MediaRecorder) return null;
    try {
      const stream = await md.getUserMedia({ audio: true });
      const mr = new root.MediaRecorder(stream);
      const chunks = [];
      mr.ondataavailable = e => chunks.push(e.data);
      if (reg) reg(() => stream.getTracks().forEach(t => t.stop()));
      return {
        start: () => mr.start(),
        stop: () => new Promise(r => {
          mr.onstop = () => { stream.getTracks().forEach(t => t.stop()); r(URL.createObjectURL(new Blob(chunks, { type: mr.mimeType }))); };
          mr.stop();
        }),
      };
    } catch (e) { return null; }
  }

  root.ToeflSpeech = { norm, wordsOf, lcs, repeatScore, listen, recorder, hasRecognition };
  if (typeof module !== "undefined") module.exports = root.ToeflSpeech;
})(typeof window !== "undefined" ? window : globalThis);
