/* 단어 듣기(운전 모드): 영어 단어 N번 → 한국어 뜻 → (예문) → 다음. 정지할 때까지 반복. 브라우저 음성 합성(Web Speech)으로 읽는다. */
UI.boot("listen", () => {
  const { esc, $ } = UI;
  const P = UI.params();
  const level = UI.intParam("level");
  const tier = ["core", "stretch"].includes(P.get("tier")) ? P.get("tier") : null;
  const SETS = { all: "전체", new: "아직 안 본 단어", learning: "학습 중", weak: "자주 잊는 단어(2번 이상 틀림)", starred: "★ 별표" };
  const which = SETS[P.get("set")] ? P.get("set") : "all";
  const grades = Ward.grades();
  const STORE = "ward:listen";
  const OPTS = ["repeats", "order", "rate-en", "rate-ko", "gap", "after", "example", "spell", "loop"];

  const fails = which === "weak" ? Ward.failCounts() : {};
  const words = Ward.words().slice().sort((a, b) => a.level - b.level || Number(a.tier !== "core") - Number(b.tier !== "core")).filter(w => {
    if (level && w.level !== level) return false;
    if (tier && w.tier !== tier) return false;
    const c = Ward.cardOf(w.id), st = Ward.stateOf(c);
    if (which === "new" && st !== "new") return false;
    if (which === "learning" && st !== "learning") return false;
    if (which === "weak" && (fails[w.id] || 0) < Ward.WEAK_MIN_FAILS) return false;
    if (which === "starred" && !(c && c.starred)) return false;
    return true;
  });

  const sel = (name, items, cur) => `<select data-q="${name}">${items.map(([v, l]) => `<option value="${v}" ${String(cur ?? "") === String(v) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
  $("app").innerHTML = `
    <div class="page-head">
      <div><h1>🎧 단어 듣기 ${level ? UI.gradeBadge(level) : ""}</h1>
        <div class="muted small">영어 단어 <b id="ls-rep-label">3</b>번(미국 → 영국 → 호주 발음) → 한국어 뜻 → 다음 단어. <b>정지</b>를 누를 때까지 계속 반복합니다. (${words.length}단어)</div></div>
      <div class="row" id="filters">
        ${sel("level", [["", "모든 등급"], ...grades.map(g => [g.level, g.name])], level)}
        ${sel("tier", [["", "필수 + 도전"], ["core", "필수"], ["stretch", "도전"]], tier)}
        ${sel("set", Object.entries(SETS), which)}
      </div>
    </div>
    ${words.length ? `
    <div class="card listen-card">
      <div class="spread small muted"><span id="ls-prog"></span><span id="ls-phase"></span></div>
      <div class="ls-word" id="ls-word">준비</div>
      <div class="ls-pos muted"><span id="ls-pos"></span> <span class="tag ok hidden" id="ls-tier">도전</span></div>
      <div class="ls-meaning" id="ls-meaning">${words.length}개 단어</div>
      <div class="ls-ex muted" id="ls-ex"></div>
      <div class="ls-controls">
        <button class="btn" id="ls-prev" title="이전 단어">⏮</button>
        <button class="btn primary ls-main" id="ls-play">▶ 시작</button>
        <button class="btn" id="ls-next" title="다음 단어">⏭</button>
        <button class="btn" id="ls-stop" title="정지">■ 정지</button>
        <button class="star" id="ls-star" title="별표">★</button>
      </div>
      <div class="small muted" id="ls-warn" style="text-align:center;margin-top:8px"></div>
      <p class="small muted" style="margin-top:10px">재생하는 동안 화면이 꺼지지 않게 유지합니다. 단축키: <span class="kbd">Space</span> 재생/일시정지 · <span class="kbd">→</span> 다음 · <span class="kbd">←</span> 이전. 마지막 위치는 이 브라우저에 기억됩니다.</p>
    </div>
    <div class="card"><h2 style="margin-top:0">듣기 설정</h2><div class="grid three">
      <label>영어 반복 <select id="opt-repeats">${[1, 2, 3, 4, 5].map(n => `<option value="${n}" ${n === 3 ? "selected" : ""}>${n}번</option>`).join("")}</select></label>
      <label>순서 <select id="opt-order"><option value="seq">차례대로</option><option value="shuffle">섞어서</option></select></label>
      <label>영어 속도 <select id="opt-rate-en">${[0.7, 0.8, 0.9, 1, 1.1, 1.2].map(r => `<option value="${r}" ${r === 0.9 ? "selected" : ""}>${r}배</option>`).join("")}</select></label>
      <label>한국어 속도 <select id="opt-rate-ko">${[0.9, 1, 1.1, 1.2, 1.3].map(r => `<option value="${r}" ${r === 1.1 ? "selected" : ""}>${r}배</option>`).join("")}</select></label>
      <label>반복 사이 쉼 <select id="opt-gap">${[0.5, 0.8, 1.2, 1.8].map(r => `<option value="${r}" ${r === 0.8 ? "selected" : ""}>${r}초</option>`).join("")}</select></label>
      <label>단어 사이 쉼 <select id="opt-after">${[1, 1.5, 2.5, 4].map(r => `<option value="${r}" ${r === 1.5 ? "selected" : ""}>${r}초</option>`).join("")}</select></label>
      <label><input type="checkbox" id="opt-example"> 뜻 다음에 영어 예문도 읽기</label>
      <label><input type="checkbox" id="opt-spell"> 단어 철자도 읽기 (s-u-b-m-i-t)</label>
      <label><input type="checkbox" id="opt-loop" checked> 끝나면 처음부터 반복</label>
    </div></div>
    <div class="card"><h2 style="margin-top:0">🎧 긴 MP3 파일로 만들기 <span class="small muted">(화면을 끄고 듣는 용도)</span></h2>
      <p class="small muted">브라우저만으로는 음성 파일을 만들 수 없어서 컴퓨터에서 한 줄을 실행합니다. (Python, <code>pip install edge-tts lameenc</code> 필요)
      지금 고른 조건 그대로의 명령이 아래에 만들어집니다. 만든 파일은 <code>audio/</code> 폴더에 생기고, 휴대폰에 옮겨 들으세요.</p>
      <pre class="cmd" id="mp3-cmd" style="white-space:pre-wrap;word-break:break-all;background:var(--surface-2);padding:10px;border-radius:8px"></pre>
      <div class="row"><label class="small">길이 <select id="mp3-min"><option>10</option><option>30</option><option selected>60</option></select>분</label>
        <button class="btn small" id="mp3-copy">명령 복사</button></div></div>` : `<div class="card empty">조건에 맞는 단어가 없습니다. <a class="btn" href="listen.html">전체 단어로 듣기</a></div>`}`;

  $("filters").addEventListener("change", () => {
    const q = {};
    $("filters").querySelectorAll("select").forEach(s => { q[s.dataset.q] = s.value; });
    location.href = UI.url("listen.html", q);
  });
  if (!words.length) return;

  let order = [], pos = 0, round = 1, playing = false, token = 0, wakeLock = null;
  const load = () => { try { return JSON.parse(localStorage.getItem(STORE) || "{}"); } catch (e) { return {}; } };
  const save = patch => { try { localStorage.setItem(STORE, JSON.stringify({ ...load(), ...patch })); } catch (e) { /* 저장 불가 */ } };
  const saved = load();
  const key = `${Ward.currentSet()}|${level || ""}|${tier || ""}|${which}`;
  for (const k of OPTS) {
    const el = $("opt-" + k);
    if (saved.opts && k in saved.opts) el.type === "checkbox" ? (el.checked = saved.opts[k]) : (el.value = saved.opts[k]);
    el.addEventListener("change", () => { save({ opts: readOpts(true) }); if (k === "order") buildOrder(true); });
  }
  function readOptsRaw() { return readOpts(true); }
  function readOpts(raw) {
    const o = {};
    for (const k of OPTS) { const el = $("opt-" + k); o[k] = el.type === "checkbox" ? el.checked : el.value; }
    if (raw) return o;
    return { repeats: Number(o.repeats), shuffle: o.order === "shuffle", rateEn: Number(o["rate-en"]), rateKo: Number(o["rate-ko"]),
             gap: Number(o.gap) * 1000, after: Number(o.after) * 1000, example: o.example, loop: o.loop };
  }
  function buildOrder(reset) {
    order = words.map((_, i) => i);
    if (readOpts().shuffle) order = UI.shuffle(order);
    if (reset) pos = 0;
  }
  buildOrder(false);
  const lastPos = (saved.pos || {})[key];
  if (!readOpts().shuffle && lastPos && lastPos < order.length) pos = lastPos;

  const cur = () => words[order[pos]];
  const clean = s => s.replace(/~/g, "").replace(/\([^)]*\)/g, "").replace(/\s+/g, " ").trim();

  function show(phase) {
    const w = cur();
    $("ls-prog").textContent = `${pos + 1} / ${order.length}` + (round > 1 ? ` · ${round}회차` : "");
    $("ls-word").textContent = w.word;
    $("ls-pos").textContent = w.pos;
    $("ls-tier").classList.toggle("hidden", w.tier !== "stretch");
    $("ls-meaning").textContent = w.meaning;
    $("ls-ex").textContent = readOpts().example ? `${w.example}  ${w.example_ko}` : "";
    $("ls-star").classList.toggle("on", !!(Ward.cardOf(w.id) || {}).starred);
    $("ls-phase").textContent = phase || "";
  }

  async function speakWord(my) {
    const o = readOpts(), w = cur(), segs = [];
    const spell = readOptsRaw().spell;
    const ACC = [["us", "미국"], ["uk", "영국"], ["au", "호주"]];     // 반복할 때 미국 → 영국 → 호주
    for (let i = 0; i < o.repeats; i++) {
      const [acc, name] = ACC[i % 3];
      segs.push({ text: clean(w.word), gender: (i + order[pos]) % 2 ? "male" : "female", accent: acc, rate: o.rateEn, pause: o.gap,
                  onStart: () => show(`영어 ${i + 1}/${o.repeats} · ${name}`) });
      if (spell && i === 0 && !/\s/.test(w.word)) segs.push({ text: clean(w.word).toUpperCase().split("").join(", "), gender: "female", rate: o.rateEn, pause: o.gap });
    }
    segs.push({ text: clean(w.meaning) || w.meaning, lang: "ko", rate: o.rateKo, pause: o.example ? o.gap : 0, onStart: () => show("뜻") });
    if (o.example) segs.push({ text: w.example, gender: "female", accent: ACC[order[pos] % 3][0], rate: o.rateEn, pause: 0, onStart: () => show("예문") });
    await TTS.play(segs, { rate: o.rateEn, accent: UI.tts().accent });
    return my === token;
  }

  async function loop() {
    const my = ++token;
    while (playing && my === token) {
      show();
      save({ pos: { ...(load().pos || {}), [key]: pos } });
      const ok = await speakWord(my);
      if (!ok || !playing || my !== token) return;
      await new Promise(r => setTimeout(r, readOpts().after));
      if (!playing || my !== token) return;
      if (pos < order.length - 1) pos++;
      else if (readOpts().loop) { round++; buildOrder(true); }
      else { stop(); $("ls-phase").textContent = "끝"; return; }
    }
  }

  async function lockScreen() { try { if ("wakeLock" in navigator && !wakeLock) wakeLock = await navigator.wakeLock.request("screen"); } catch (e) { wakeLock = null; } }
  function unlockScreen() { try { wakeLock?.release(); } catch (e) { /* 무시 */ } wakeLock = null; }
  document.addEventListener("visibilitychange", () => { if (playing && document.visibilityState === "visible") { wakeLock = null; lockScreen(); } });
  function setButton() { $("ls-play").textContent = playing ? "⏸ 일시정지" : "▶ " + (token ? "계속" : "시작"); document.body.classList.toggle("listening", playing); }

  async function play() {
    await TTS.load();
    if (!TTS.hasKorean()) $("ls-warn").textContent = "한국어 음성이 없어 뜻을 영어 음성으로 읽을 수 있습니다. 엣지를 쓰거나 Windows 설정 → 음성에서 한국어를 추가하세요.";
    playing = true; setButton(); lockScreen(); loop();
  }
  function pause() { playing = false; token++; TTS.stop(); setButton(); unlockScreen(); $("ls-phase").textContent = "일시정지"; }
  function stop() { pause(); $("ls-phase").textContent = "정지"; }
  function jump(d) {
    pos = Math.min(order.length - 1, Math.max(0, pos + d));
    if (playing) { TTS.stop(); loop(); } else show();
  }

  $("ls-play").addEventListener("click", () => (playing ? pause() : play()));
  $("ls-prev").addEventListener("click", () => jump(-1));
  $("ls-next").addEventListener("click", () => jump(1));
  $("ls-stop").addEventListener("click", stop);
  $("ls-star").addEventListener("click", () => $("ls-star").classList.toggle("on", Ward.toggleStar(cur().id)));
  document.addEventListener("keydown", e => {
    if (e.target.closest?.("input, select, textarea") || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === " ") { e.preventDefault(); playing ? pause() : play(); }
    else if (e.key === "ArrowRight") jump(1);
    else if (e.key === "ArrowLeft") jump(-1);
  });
  // MP3 만들기 명령: 지금 고른 등급·범위·반복·예문을 그대로 담는다
  function mp3Command() {
    const o = readOpts(true);
    let c = `python tools/make_audio.py --set ${Ward.currentSet()}`;
    if (level) c += ` --level ${level}`;
    if (tier) c += ` --tier ${tier}`;
    if (which !== "all") c += ` --which ${which} --backup 백업파일.json`;
    c += ` --minutes ${$("mp3-min").value} --repeats ${o.repeats}`;
    if (o.example) c += " --example";
    return c;
  }
  const drawCmd = () => { $("mp3-cmd").textContent = mp3Command(); };
  drawCmd();
  $("mp3-min").addEventListener("change", drawCmd);
  for (const k of OPTS) $("opt-" + k).addEventListener("change", drawCmd);
  $("mp3-copy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(mp3Command()); $("mp3-copy").textContent = "복사했습니다"; } catch (e) { $("mp3-copy").textContent = "직접 선택해서 복사하세요"; }
    setTimeout(() => { $("mp3-copy").textContent = "명령 복사"; }, 1800);
  });
  const repLabel = () => { $("ls-rep-label").textContent = $("opt-repeats").value; };
  $("opt-repeats").addEventListener("change", repLabel);
  repLabel();
  if ("mediaSession" in navigator) {                          // 휴대폰 잠금화면·이어폰 버튼 (지원하는 브라우저만)
    try {
      navigator.mediaSession.setActionHandler("play", play);
      navigator.mediaSession.setActionHandler("pause", pause);
      navigator.mediaSession.setActionHandler("nexttrack", () => jump(1));
      navigator.mediaSession.setActionHandler("previoustrack", () => jump(-1));
    } catch (e) { /* 미지원 */ }
  }
  addEventListener("beforeunload", () => save({ pos: { ...(load().pos || {}), [key]: pos } }));
  show();
});
