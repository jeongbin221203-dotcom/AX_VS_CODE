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
  const OPTS = ["repeats", "order", "rate-en", "rate-ko", "gap", "after", "example", "loop"];

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
        <div class="muted small">영어 → 한국어 뜻을 반복해서 읽어 줍니다. 운전·산책 중에 귀로 외우세요. (${words.length}단어)</div></div>
      <div class="row" id="filters">
        ${sel("level", [["", "모든 등급"], ...grades.map(g => [g.level, g.name])], level)}
        ${sel("tier", [["", "필수 + 도전"], ["core", "필수"], ["stretch", "도전"]], tier)}
        ${sel("set", Object.entries(SETS), which)}
      </div>
    </div>
    ${words.length ? `
    <div class="card listen-card">
      <div class="spread small muted"><span id="ls-prog"></span><span id="ls-phase"></span></div>
      <div class="flash-card" style="min-height:0;padding:18px 0">
        <div class="word" id="ls-word"></div>
        <div class="pos"><span id="ls-pos"></span> <span class="tag ok hidden" id="ls-tier">도전</span></div>
        <div class="meaning" id="ls-meaning"></div>
        <div class="muted small" id="ls-ex"></div>
      </div>
      <div class="row" style="justify-content:center">
        <button class="btn" id="ls-prev">⏮</button>
        <button class="btn primary big" id="ls-play">▶ 시작</button>
        <button class="btn" id="ls-next">⏭</button>
        <button class="star" id="ls-star" title="별표">★</button>
      </div>
      <div class="small muted" id="ls-warn" style="text-align:center;margin-top:8px"></div>
    </div>
    <div class="card"><h2 style="margin-top:0">듣기 설정</h2><div class="grid three">
      <label>영어 반복 <select id="opt-repeats">${[1, 2, 3, 4, 5].map(n => `<option value="${n}" ${n === 3 ? "selected" : ""}>${n}번</option>`).join("")}</select></label>
      <label>순서 <select id="opt-order"><option value="seq">차례대로</option><option value="shuffle">섞어서</option></select></label>
      <label>영어 속도 <select id="opt-rate-en">${[0.8, 0.9, 1, 1.1].map(r => `<option value="${r}" ${r === 1 ? "selected" : ""}>${r}배</option>`).join("")}</select></label>
      <label>한국어 속도 <select id="opt-rate-ko">${[0.9, 1, 1.1, 1.25].map(r => `<option value="${r}" ${r === 1 ? "selected" : ""}>${r}배</option>`).join("")}</select></label>
      <label>반복 사이 쉼 <select id="opt-gap">${[0.3, 0.6, 1, 1.5].map(r => `<option value="${r}" ${r === 0.6 ? "selected" : ""}>${r}초</option>`).join("")}</select></label>
      <label>단어 사이 쉼 <select id="opt-after">${[0.5, 1, 1.5, 2.5].map(r => `<option value="${r}" ${r === 1 ? "selected" : ""}>${r}초</option>`).join("")}</select></label>
      <label><input type="checkbox" id="opt-example"> 예문도 읽기</label>
      <label><input type="checkbox" id="opt-loop" checked> 끝나면 처음부터 반복</label>
    </div></div>` : `<div class="card empty">조건에 맞는 단어가 없습니다. <a class="btn" href="listen.html">전체 단어로 듣기</a></div>`}`;

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
    const ACC = [["us", "미국"], ["uk", "영국"], ["au", "호주"]];     // 반복할 때 미국 → 영국 → 호주
    for (let i = 0; i < o.repeats; i++) {
      const [acc, name] = ACC[i % 3];
      segs.push({ text: clean(w.word), gender: (i + order[pos]) % 2 ? "male" : "female", accent: acc, rate: o.rateEn, pause: o.gap,
                  onStart: () => show(`영어 ${i + 1}/${o.repeats} · ${name}`) });
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
  function setButton() { $("ls-play").textContent = playing ? "⏸ 일시정지" : "▶ " + (token ? "계속" : "시작"); }

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
  $("ls-star").addEventListener("click", () => $("ls-star").classList.toggle("on", Ward.toggleStar(cur().id)));
  document.addEventListener("keydown", e => {
    if (e.target.matches("select,input")) return;
    if (e.key === " ") { e.preventDefault(); playing ? pause() : play(); }
    else if (e.key === "ArrowRight") jump(1);
    else if (e.key === "ArrowLeft") jump(-1);
  });
  show();
});
