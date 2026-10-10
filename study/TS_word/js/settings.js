/* 설정: 하루 새 단어 수, 음성, 백업·복원·초기화 (학습 기록은 이 브라우저에만 있다) */
UI.boot("settings", () => {
  const { esc, $ } = UI;
  const s = Ward.settings();
  const opt = (v, cur, label) => `<option value="${v}" ${String(cur) === String(v) ? "selected" : ""}>${label}</option>`;
  const total = Object.keys(JSON.parse(Ward.exportJSON()).cards).length;
  const grades = Ward.grades();
  const isToeic = Ward.currentSet() === "toeic";

  $("app").innerHTML = `
    <div class="page-head"><div><h1>설정</h1><div class="muted small">학습 기록은 이 브라우저(이 기기)에만 저장됩니다. 기기를 바꾸거나 브라우저 데이터를 지우기 전에 백업하세요.</div></div></div>
    <div class="card" style="max-width:640px">
      <h2 style="margin-top:0">학습</h2>
      <p><label>하루 새 단어 수
        <select id="daily_new">${[5, 10, 15, 20, 30, 40, 50].map(n => opt(n, s.daily_new, n + "개")).join("")}</select></label>
        <span class="muted small">복습할 단어는 한도와 상관없이 모두 나옵니다.</span></p>
      <p><label>내 등급
        <select id="my_level">${opt(0, s.my_level, "자동 (1등급부터)")}${grades.map(g => opt(g.level, s.my_level, `${g.name} (${g.range})`)).join("")}</select></label>
        <span class="muted small">새 단어를 이 등급부터 보여 줍니다.</span></p>
      ${isToeic ? `<p class="small"><label>토익 점수로 정하기 <input type="number" id="score" min="10" max="990" step="5" placeholder="예: 650" style="width:90px"></label>
        <span class="muted">점수를 넣으면 위 등급이 자동으로 바뀝니다.</span></p>` : ""}
      <h2>음성</h2>
      <p><label>발음 억양
        <select id="tts_accent">${[["mix", "섞어서"], ["us", "미국"], ["uk", "영국"], ["au", "호주"]].map(([v, l]) => opt(v, s.tts_accent, l)).join("")}</select></label>
      <label style="margin-left:14px">읽는 속도
        <select id="tts_rate">${[0.7, 0.85, 1, 1.15, 1.3].map(r => opt(r, s.tts_rate, r + "배")).join("")}</select></label>
      <button class="btn small" id="test-voice">🔊 들어 보기</button></p>
      <div class="small muted" id="voice-info"></div>
      <div id="saved" class="small" style="color:var(--ok)"></div>
    </div>
    <div class="card" style="max-width:640px;margin-top:14px">
      <h2 style="margin-top:0">백업</h2>
      <p class="small muted">지금 ${total}개 단어의 학습 기록이 있습니다. 파일로 내려받아 두면 다른 기기·브라우저에서 불러올 수 있습니다.
        <b>합치기</b>는 지금 기록을 지키면서 파일의 기록을 더하고, <b>바꾸기</b>는 파일 내용으로 통째로 바꿉니다.
        TS 앱의 기록은 <code>tools/import_ts_db.py</code> 로 백업 파일을 만들어 불러오세요.</p>
      <div class="row">
        <button class="btn primary" id="export">📥 백업 파일 내려받기</button>
        <label class="btn" for="import-file">📤 백업 불러오기</label><input type="file" id="import-file" accept=".json,application/json" class="hidden">
        <label class="small" style="align-self:center"><input type="radio" name="imode" value="merge" checked> 합치기 <input type="radio" name="imode" value="replace" style="margin-left:8px"> 바꾸기</label>
        <button class="btn" id="reset" style="color:var(--bad)">모든 기록 지우기</button>
      </div>
      <div id="msg" class="small" style="margin-top:8px"></div>
    </div>`;

  const flash = (m, bad) => { $("saved").textContent = m; $("saved").style.color = bad ? "var(--bad)" : "var(--ok)"; setTimeout(() => { $("saved").textContent = ""; }, 2000); };
  for (const k of ["daily_new", "tts_accent", "tts_rate", "my_level"]) {
    $(k).addEventListener("change", () => { Ward.setSetting({ [k]: k === "tts_accent" ? $(k).value : Number($(k).value) }); flash("저장했습니다"); });
  }
  if ($("score")) $("score").addEventListener("change", () => {
    const v = Number($("score").value);
    if (!(v >= 10 && v <= 990)) return flash("10~990 사이로 넣어 주세요", true);
    const lv = Ward.levelFromScore(v);
    $("my_level").value = String(lv);
    Ward.setSetting({ my_level: lv });
    flash(`${v}점 → ${grades.find(g => g.level === lv).name} 등급으로 저장했습니다`);
  });
  $("test-voice").addEventListener("click", async () => { await TTS.load(); UI.sayWord("vocabulary"); });
  TTS.load().then(() => {
    const i = TTS.info();
    $("voice-info").textContent = TTS.supported
      ? `영어 음성 ${i.count}개 · 사용 가능한 억양: ${i.accents.join(", ") || "없음"} · 한국어 음성: ${i.korean.length ? "있음" : "없음(듣기 모드에서 뜻을 영어 음성으로 읽을 수 있음)"}`
      : "이 브라우저는 음성 합성을 지원하지 않습니다.";
  });

  $("export").addEventListener("click", () => {
    const blob = new Blob([Ward.exportJSON()], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `ts-ward-backup-${Ward.todayStr()}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    $("msg").textContent = "백업 파일을 내려받았습니다.";
  });
  $("import-file").addEventListener("change", async e => {
    const f = e.target.files[0];
    if (!f) return;
    try {
      const mode = document.querySelector("input[name=imode]:checked").value;
      if (mode === "replace" && !confirm("지금의 학습 기록을 이 파일의 내용으로 바꿉니다. 계속할까요?")) return;
      const r = Ward.importJSON(await f.text(), mode);
      $("msg").textContent = mode === "merge" ? `합쳤습니다. (새로 들어온 단어 ${r.added}개, 전체 ${r.cards}개)` : `불러왔습니다. (${r.cards}개 단어)`;
      setTimeout(() => location.reload(), 600);
    } catch (err) { $("msg").style.color = "var(--bad)"; $("msg").textContent = "불러오지 못했습니다: " + err.message; }
    e.target.value = "";
  });
  $("reset").addEventListener("click", () => {
    if (!confirm("모든 단어 학습 기록(복습 일정·별표·시험 기록)을 지웁니다. 되돌릴 수 없어요. 먼저 백업하셨나요?")) return;
    Ward.reset();
    location.reload();
  });
});
