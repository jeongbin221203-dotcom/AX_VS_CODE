/* 단어 듣기(운전 모드): 영어 단어 N번 → 한국어 뜻 → (예문) → 다음. 정지할 때까지 반복. */
(function () {
  "use strict";
  const D = TS.data("ls-data");
  if (!D) return;
  const $ = id => document.getElementById(id);
  const STORE = "ts-listen";
  const OPTS = ["repeats", "order", "rate-en", "rate-ko", "gap", "after", "example", "spell", "loop"];

  let order = [];          // words 인덱스 순서
  let pos = 0;             // order 안의 위치
  let round = 1;
  let playing = false;
  let token = 0;
  let wakeLock = null;

  // ---- 설정 저장/복원 (이 브라우저에만)
  function load() { try { return JSON.parse(localStorage.getItem(STORE) || "{}"); } catch (e) { return {}; } }
  function save(patch) { try { localStorage.setItem(STORE, JSON.stringify({ ...load(), ...patch })); } catch (e) { /* 저장 불가 */ } }
  const saved = load();
  for (const k of OPTS) {
    const el = $("opt-" + k);
    if (saved.opts && k in saved.opts) el.type === "checkbox" ? (el.checked = saved.opts[k]) : (el.value = saved.opts[k]);
    el.addEventListener("change", () => { save({ opts: readOpts(true) }); if (k === "order") buildOrder(true); updateRepLabel(); });
  }
  function readOpts(raw) {
    const o = {};
    for (const k of OPTS) { const el = $("opt-" + k); o[k] = el.type === "checkbox" ? el.checked : el.value; }
    if (raw) return o;
    return {
      repeats: Number(o.repeats), shuffle: o.order === "shuffle", rateEn: Number(o["rate-en"]), rateKo: Number(o["rate-ko"]),
      gap: Number(o.gap) * 1000, after: Number(o.after) * 1000, example: o.example, spell: o.spell, loop: o.loop,
    };
  }
  function updateRepLabel() { $("ls-rep-label").textContent = $("opt-repeats").value; }
  updateRepLabel();

  function buildOrder(reset) {
    order = D.words.map((_, i) => i);
    if (readOpts().shuffle) for (let i = order.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [order[i], order[j]] = [order[j], order[i]]; }
    if (reset) pos = 0;
  }
  buildOrder(false);
  // 마지막 위치 (같은 등급·범위일 때, 순서대로 모드에서만)
  const lastPos = (saved.pos || {})[D.key];
  if (!readOpts().shuffle && lastPos && lastPos < order.length) pos = lastPos;

  const cur = () => D.words[order[pos]];
  const clean = s => s.replace(/~/g, "").replace(/\([^)]*\)/g, "").replace(/\s+/g, " ").trim();

  function show(phase) {
    const w = cur();
    $("ls-prog").textContent = `${pos + 1} / ${order.length}` + (round > 1 ? ` · ${round}회차` : "");
    $("ls-word").textContent = w.word;
    $("ls-pos").textContent = w.pos;
    $("ls-tier").classList.toggle("hidden", w.tier !== "stretch");
    $("ls-meaning").textContent = w.meaning;
    $("ls-ex").textContent = readOpts().example ? `${w.example}  ${w.example_ko}` : "";
    $("ls-star").classList.toggle("on", !!w.starred);
    $("ls-phase").textContent = phase || "";
  }

  async function speakWord(my) {
    const o = readOpts();
    const w = cur();
    const accent = D.tts.accent === "mix" ? "us" : D.tts.accent;
    const segs = [];
    const ACC = [["us", "미국"], ["uk", "영국"], ["au", "호주"]];     // 반복할 때 미국 → 영국 → 호주
    for (let i = 0; i < o.repeats; i++) {
      const [acc, accName] = ACC[i % 3];
      segs.push({ text: clean(w.word), gender: (i + order[pos]) % 2 ? "male" : "female", accent: acc, rate: o.rateEn,
                  pause: o.gap, onStart: () => show(`영어 ${i + 1}/${o.repeats} · ${accName}`) });
      if (o.spell && i === 0 && !/\s/.test(w.word)) segs.push({ text: clean(w.word).toUpperCase().split("").join(", "), gender: "female", rate: o.rateEn, pause: o.gap });
    }
    segs.push({ text: clean(w.meaning) || w.meaning, lang: "ko", rate: o.rateKo, pause: o.example ? o.gap : 0, onStart: () => show("뜻") });
    if (o.example) segs.push({ text: w.example, gender: "female", accent: ACC[order[pos] % 3][0], rate: o.rateEn, pause: 0, onStart: () => show("예문") });
    await TTS.play(segs, { rate: o.rateEn, accent });
    return my === token;
  }

  async function loop() {
    const my = ++token;
    while (playing && my === token) {
      show();
      save({ pos: { ...(load().pos || {}), [D.key]: pos } });
      const ok = await speakWord(my);
      if (!ok || !playing || my !== token) return;
      await new Promise(r => setTimeout(r, readOpts().after));
      if (!playing || my !== token) return;
      if (pos < order.length - 1) pos++;
      else if (readOpts().loop) { round++; buildOrder(true); }
      else { stop(); $("ls-phase").textContent = "끝"; return; }
    }
  }

  async function lockScreen() {
    try { if ("wakeLock" in navigator && !wakeLock) wakeLock = await navigator.wakeLock.request("screen"); } catch (e) { wakeLock = null; }
  }
  function unlockScreen() { try { wakeLock?.release(); } catch (e) { /* 무시 */ } wakeLock = null; }
  document.addEventListener("visibilitychange", () => { if (playing && document.visibilityState === "visible") { wakeLock = null; lockScreen(); } });

  function setButton() {
    $("ls-play").textContent = playing ? "⏸ 일시정지" : "▶ " + (token ? "계속" : "시작");
    document.body.classList.toggle("listening", playing);
  }

  async function play() {
    await TTS.load();
    if (!TTS.hasKorean()) $("ls-warn").textContent = "한국어 음성이 없어 뜻을 영어 음성으로 읽을 수 있습니다. 엣지를 쓰거나 Windows 설정 → 음성에서 한국어를 추가하세요.";
    playing = true;
    setButton();
    lockScreen();
    loop();
  }
  function pause() { playing = false; token++; TTS.stop(); setButton(); unlockScreen(); $("ls-phase").textContent = "일시정지"; }
  function stop() { pause(); $("ls-phase").textContent = "정지"; }
  function jump(d) {
    pos = Math.min(order.length - 1, Math.max(0, pos + d));
    if (playing) { TTS.stop(); loop(); } else show();
  }

  $("ls-play").addEventListener("click", () => (playing ? pause() : play()));
  $("ls-stop").addEventListener("click", stop);
  $("ls-next").addEventListener("click", () => jump(1));
  $("ls-prev").addEventListener("click", () => jump(-1));
  $("ls-star").addEventListener("click", async () => {
    const w = cur();
    const r = await TS.post(TS.vbase + "/api/vocab/star", { word_id: w.id });
    w.starred = r.starred;
    $("ls-star").classList.toggle("on", r.starred);
  });
  document.addEventListener("keydown", e => {
    if (e.target.closest?.("input, select, textarea") || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === " ") { e.preventDefault(); playing ? pause() : play(); }
    else if (e.key === "ArrowRight") jump(1);
    else if (e.key === "ArrowLeft") jump(-1);
  });

  // 휴대폰 잠금화면·이어폰 버튼 (지원하는 브라우저만)
  if ("mediaSession" in navigator) {
    try {
      navigator.mediaSession.setActionHandler("play", play);
      navigator.mediaSession.setActionHandler("pause", pause);
      navigator.mediaSession.setActionHandler("nexttrack", () => jump(1));
      navigator.mediaSession.setActionHandler("previoustrack", () => jump(-1));
    } catch (e) { /* 미지원 */ }
  }

  // ---- 1시간 음성 파일: 만들기 → 진행률 → 재생·받기
  async function makeFile(btn) {
    const row = btn.closest("tr");
    const box = row.querySelector(".audio-job");
    btn.disabled = true;
    try {
      let job = await TS.post(TS.vbase + "/api/vocab/audio/prepare", { ...D.audio, chunk: Number(btn.dataset.make) });
      while (job.state === "running") {
        box.textContent = job.total ? `음성 만드는 중… ${job.done} / ${job.total}` : "준비 중…";
        await new Promise(r => setTimeout(r, 1500));
        const res = await fetch(`${TS.vbase}/api/vocab/audio/status/${job.key}`);
        job = await res.json();
        if (!res.ok) throw new Error(job.error || "상태 확인 실패");
      }
      if (job.state === "error") throw new Error(job.error);
      box.innerHTML = `<audio controls preload="none" src="${job.url}" style="width:100%;margin-top:6px"></audio>` +
        `<div class="row" style="margin-top:4px"><a class="btn small primary" href="${job.download}">⬇ 받기</a>` +
        `<span class="muted">약 ${job.minutes}분 · ${job.mb}MB · ${TS.esc(job.name)}</span></div>`;
      btn.classList.add("hidden");
    } catch (e) {
      box.textContent = "실패: " + e.message;
      btn.disabled = false;
    }
  }
  document.addEventListener("click", e => {
    const b = e.target.closest("[data-make]");
    if (b) makeFile(b);
  });

  TTS.load();
  show();
  setButton();
  window.addEventListener("beforeunload", () => save({ pos: { ...(load().pos || {}), [D.key]: pos } }));
})();
