/* 브라우저 음성 합성(Web Speech API)으로 듣기 음성을 만든다.
   - 화자 성별(male/female)과 억양(미국·영국·호주·캐나다)에 맞는 목소리를 고른다.
   - 크롬은 긴 문장을 중간에 끊는 버그가 있어 문장 단위로 나눠 읽는다.
   - 같은 성별 목소리가 하나뿐이면 음높이로 화자를 구분한다. */
(function () {
  "use strict";
  const synth = window.speechSynthesis;
  const FEMALE = /female|woman|zira|aria|jenny|michelle|ana\b|samantha|susan|hazel|libby|sonia|mia|natasha|clara|emily|karen|moira|tessa|fiona|victoria|allison|ava|serena|kate|catherine|linda|heather|jessa|elizabeth|nancy|sara|amber|ashley|cora|monica|emma|olivia|salli|joanna|kendra|kimberly|ivy|nicole|google uk english female|google us english/i;
  const MALE = /\bmale|\bman\b|david|mark|guy|ryan|george|daniel|christopher|eric|william|thomas|james|brian|roger|steffan|liam|connor|mitchell|alex|fred|oliver|tom\b|aaron|arthur|gordon|lee\b|matthew|joey|justin|russell|brandon|davis|tony|jason|andrew|google uk english male/i;
  const ACCENTS = { us: ["en-US"], uk: ["en-GB"], au: ["en-AU"], ca: ["en-CA"] };

  let voices = [];
  let koVoices = [];
  let ready = null;
  let runId = 0;

  function loadVoices() {
    if (ready) return ready;
    ready = new Promise(resolve => {
      if (!synth) return resolve([]);
      const done = () => {
        const all = synth.getVoices();
        voices = all.filter(v => /^en[-_]/i.test(v.lang));
        koVoices = all.filter(v => /^ko/i.test(v.lang))
          .sort((a, b) => (/natural|online|neural|google/i.test(a.name) ? 0 : 1) - (/natural|online|neural|google/i.test(b.name) ? 0 : 1));
        if (voices.length) resolve(voices);
      };
      done();
      if (voices.length) return;
      synth.addEventListener("voiceschanged", done);
      setTimeout(() => { done(); resolve(voices); }, 2500);
    });
    return ready;
  }

  function genderOf(v) {
    if (MALE.test(v.name) && !/female/i.test(v.name)) return "male";
    if (FEMALE.test(v.name)) return "female";
    return null;
  }

  function langKey(v) { return v.lang.replace("_", "-"); }

  function availableAccents() {
    const set = new Set();
    for (const v of voices) for (const [k, langs] of Object.entries(ACCENTS)) if (langs.includes(langKey(v))) set.add(k);
    return [...set];
  }

  /* 문제(item)마다 억양을 정한다. mix 면 문제 번호로 돌아가며. */
  function accentFor(setting, seed) {
    const av = availableAccents();
    if (!av.length) return "us";
    if (setting && setting !== "mix") return av.includes(setting) ? setting : av[0];
    let h = 0;
    for (const ch of String(seed)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    return av[h % av.length];
  }

  /* 성별·억양에 맞는 목소리. nth: 같은 성별 두 번째 화자(M2, W2)면 1 */
  function pick(gender, accent, nth) {
    const langs = ACCENTS[accent] || ACCENTS.us;
    const inAccent = voices.filter(v => langs.includes(langKey(v)));
    const pools = [
      inAccent.filter(v => genderOf(v) === gender),
      voices.filter(v => genderOf(v) === gender),
      inAccent,
      voices,
    ];
    // 온라인(자연스러운) 목소리를 먼저
    const score = v => (/natural|online|neural|google/i.test(v.name) ? 0 : 1);
    for (const pool of pools) {
      if (pool.length) {
        const sorted = [...pool].sort((a, b) => score(a) - score(b));
        const voice = sorted[(nth || 0) % sorted.length];
        const exact = genderOf(voice) === gender;
        return { voice, pitch: exact ? 1 : (gender === "male" ? 0.75 : 1.2) * (nth ? 0.93 : 1) };
      }
    }
    return { voice: null, pitch: gender === "male" ? 0.8 : 1.15 };
  }

  function splitSentences(text) {
    return String(text).match(/[^.!?]+[.!?]+["')\]]*|[^.!?]+$/g)?.map(s => s.trim()).filter(Boolean) || [text];
  }

  function sayOne(text, v, rate, lang) {
    return new Promise(resolve => {
      const u = new SpeechSynthesisUtterance(text);
      if (v.voice) { u.voice = v.voice; u.lang = v.voice.lang; } else { u.lang = lang || "en-US"; }
      u.pitch = v.pitch;
      u.rate = rate;
      // 브라우저가 끝났다는 신호(onend)를 안 보내는 경우가 있어, 넉넉한 예상 시간이 지나면 끝난 것으로 본다
      const words = String(text).split(/\s+/).length;
      const limit = 4000 + (words * 650 + String(text).length * 40) / Math.max(rate, 0.5);
      let finished = false;
      const done = () => { if (!finished) { finished = true; clearTimeout(guard); resolve(); } };
      const guard = setTimeout(() => { if (synth.speaking) synth.cancel(); done(); }, limit);
      u.onend = u.onerror = done;
      synth.speak(u);
    });
  }

  const sleep = ms => new Promise(r => setTimeout(r, ms));

  /* 순서대로 읽는다. segs: [{text, gender, nth, pause(ms), onStart()}]  */
  async function play(segs, opts) {
    opts = opts || {};
    await loadVoices();
    stop();
    const my = ++runId;
    const rate = opts.rate || 1;
    const accent = opts.accent || "us";
    for (const seg of segs) {
      if (my !== runId) return false;
      if (seg.onStart) seg.onStart();
      if (seg.pre) await sleep(seg.pre);
      // lang: "ko" 면 한국어 음성으로 (단어 뜻 읽기)
      const v = seg.lang === "ko" ? { voice: koVoices[0] || null, pitch: 1 } : pick(seg.gender || "female", seg.accent || accent, seg.nth || 0);
      const r = seg.rate || rate;
      for (const s of seg.lang === "ko" ? [seg.text] : splitSentences(seg.text)) {
        if (my !== runId) return false;
        await sayOne(s, v, r, seg.lang === "ko" ? "ko-KR" : "en-US");
      }
      if (seg.pause) await sleep(seg.pause / Math.max(rate, 0.5));
    }
    return my === runId;
  }

  function stop() {
    runId++;
    if (synth) synth.cancel();
  }

  window.TTS = {
    supported: !!synth,
    load: loadVoices,
    play, stop, accentFor,
    info() { return { count: voices.length, accents: availableAccents(), korean: koVoices.map(v => v.name), voices: voices.map(v => `${v.name} (${v.lang}, ${genderOf(v) || "?"})`) }; },
    hasKorean() { return koVoices.length > 0; },
  };
  window.addEventListener("pagehide", stop);      // beforeunload 는 "나가시겠습니까?"에서 취소해도 실행돼 재생이 끊긴다
})();
