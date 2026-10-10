/* 설정: 시험별 목표·시험일, 하루 학습량, 단어 설정(내 등급), 음성, 백업·복원·초기화
   (학습 기록은 이 브라우저에만 있다. 시험 기록 = TSStore `ts:v1`, 단어 기록 = Ward `ward:v1`) */
UI.boot("settings", () => {
  const { esc, $ } = UI;
  const w = Ward.settings(), t = TSStore.settings();
  const opt = (v, cur, label) => `<option value="${v}" ${String(cur) === String(v) ? "selected" : ""}>${label}</option>`;
  const toeicGrades = Score.GRADES;
  const OPIC_NAMES = { AL: "Advanced Low", IH: "Intermediate High", IM3: "Intermediate Mid 3", IM2: "Intermediate Mid 2", IM1: "Intermediate Mid 1" };
  const OPIC_LEVELS = { 1: "1 · 아주 쉬움", 2: "2 · 쉬움", 3: "3 · 보통", 4: "4 · 보통+", 5: "5 · 어려움", 6: "6 · 아주 어려움" };
  const surveyN = (t.opic_survey || "").split(",").filter(Boolean).length;
  const sum = Backup.summary();

  $("app").innerHTML = `
    <div class="page-head"><div><h1>설정</h1><div class="muted small">시험마다 목표와 시험일을 정하면 메인 화면 카드에 남은 날짜(D-day)가 나옵니다</div></div></div>
    <form id="exam-form"><div class="grid two">
      <div class="card"><h2>토익</h2>
        <label class="field"><span>목표 점수 (10~990)</span><input type="number" name="target_score" min="10" max="990" step="5" value="${esc(t.target_score)}" required></label>
        <label class="field"><span>시험일</span><input type="date" name="exam_date" value="${esc(t.exam_date)}"></label>
        <label class="field"><span>현재 점수 직접 입력 (비우면 진단·모의고사 추정치 사용)</span>
          <input type="number" name="current_score" min="10" max="990" step="5" value="${esc(t.current_score)}" placeholder="예: 650"></label>
        <p class="small muted">직접 입력한 뒤에 진단·모의고사를 보면 그 추정치가 새 현재 점수가 됩니다.</p></div>
      <div class="card"><h2>토플</h2>
        <label class="field"><span>목표 밴드 (1~6)</span><select name="toefl_target">${[2, 2.5, 3, 3.5, 4, 4.5, 5, 5.5, 6].map(b => opt(b, Number(t.toefl_target), b)).join("")}</select></label>
        <label class="field"><span>시험일</span><input type="date" name="toefl_exam_date" value="${esc(t.toefl_exam_date)}"></label>
        <p class="small muted">2026년 1월 개편 형식 기준 · 밴드 4.5 ≈ 예전 점수 80점대 후반</p></div>
      <div class="card"><h2>토익스피킹</h2>
        <label class="field"><span>목표 점수</span><select name="tsp_target">${[110, 120, 130, 140, 150, 160, 170, 180].map(v => opt(v, t.tsp_target, v + "점")).join("")}</select></label>
        <label class="field"><span>시험일</span><input type="date" name="tsp_exam_date" value="${esc(t.tsp_exam_date)}"></label></div>
      <div class="card"><h2>오픽</h2>
        <label class="field"><span>목표 등급</span><select name="opic_target">${Object.entries(OPIC_NAMES).reverse().map(([g, n]) => opt(g, t.opic_target, `${g} · ${n}`)).join("")}</select></label>
        <label class="field"><span>설문 난이도 (모의고사 기본값)</span><select name="opic_level">${Object.entries(OPIC_LEVELS).map(([v, n]) => opt(v, t.opic_level, n)).join("")}</select></label>
        <label class="field"><span>시험일</span><input type="date" name="opic_exam_date" value="${esc(t.opic_exam_date)}"></label>
        <p class="small muted">설문 주제 ${surveyN}개 선택됨</p></div>
      <div class="card"><h2>하루 학습량 <span class="small muted">토익</span></h2>
        <label class="field"><span>하루 문항 수 목표</span><input type="number" name="daily_questions" min="5" max="300" value="${esc(t.daily_questions)}"></label>
        <p class="small muted">하루 새 단어 수는 아래 '단어'에서 정합니다.</p></div>
    </div>
    <div class="settings-save"><button class="btn primary">시험 설정 저장</button> <span id="exam-msg" class="small"></span></div></form>

    <div class="card" style="max-width:640px;margin-top:14px">
      <h2 style="margin-top:0">단어</h2>
      <p><label>하루 새 단어 수
        <select id="daily_new">${[5, 10, 15, 20, 30, 40, 50].map(n => opt(n, w.daily_new, n + "개")).join("")}</select></label>
        <span class="muted small">복습할 단어는 한도와 상관없이 모두 나옵니다.</span></p>
      <p><label>내 등급 (토익 단어)
        <select id="my_level">${opt(0, w.my_level, "자동 (1등급부터)")}${toeicGrades.map(g => opt(g.level, w.my_level, `${g.name} (${Score.rangeText(g)})`)).join("")}</select></label>
        <span class="muted small">새 단어를 이 등급부터 보여 줍니다.</span></p>
      <p class="small"><label>토익 점수로 정하기 <input type="number" id="score" min="10" max="990" step="5" placeholder="예: 650" style="width:90px"></label>
        <span class="muted">점수를 넣으면 위 등급이 자동으로 바뀝니다.</span></p>
      <h2>음성 <span class="small muted">모든 시험</span></h2>
      <p><label>발음 억양
        <select id="tts_accent">${[["mix", "섞어서"], ["us", "미국"], ["uk", "영국"], ["au", "호주"]].map(([v, l]) => opt(v, w.tts_accent, l)).join("")}</select></label>
      <label style="margin-left:14px">읽는 속도
        <select id="tts_rate">${[0.7, 0.85, 1, 1.15, 1.3].map(r => opt(r, w.tts_rate, r + "배")).join("")}</select></label>
      <button class="btn small" id="test-voice">🔊 들어 보기</button></p>
      <div class="small muted" id="voice-info"></div>
      <div id="saved" class="small" style="color:var(--ok)"></div>
    </div>

    <div class="card" style="max-width:640px;margin-top:14px">
      <h2 style="margin-top:0">백업</h2>
      <p class="small muted">지금 단어 ${sum.words}개 · 풀이 ${sum.sessions}번 · 문항 기록 ${sum.attempts}건 · 오답노트 ${sum.notes}개가 이 브라우저에 저장돼 있습니다
        (시험 기록 약 ${Math.round(sum.bytes / 1024).toLocaleString()}KB — 브라우저 저장 한도는 보통 5~10MB, 문항 기록은 ${TSStore.ATTEMPT_CAP.toLocaleString()}건이 넘으면 오래된 것부터 정리됩니다).
        파일로 내려받아 두면 다른 기기·브라우저에서 불러올 수 있습니다.
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

  // ---- 시험 목표 저장 (검사 규칙은 TS 앱 views/main.py settings 와 같다) ----
  $("exam-form").addEventListener("submit", e => {
    e.preventDefault();
    const f = $("exam-form").elements, errors = [], v = {};
    const intField = (name, lo, hi, label, blank) => {
      const raw = f[name].value.trim();
      if (blank && raw === "") { v[name] = ""; return; }
      const n = /^[+-]?\d+$/.test(raw) ? parseInt(raw, 10) : NaN;
      if (!(n >= lo && n <= hi)) errors.push(`${label}은(는) ${lo}~${hi} 사이 숫자로 입력하세요.`); else v[name] = String(n);
    };
    intField("target_score", 10, 990, "목표 점수");
    intField("current_score", 10, 990, "현재 점수", true);
    intField("daily_questions", 5, 300, "하루 문항 수");
    for (const [name, label] of [["exam_date", "토익"], ["toefl_exam_date", "토플"], ["tsp_exam_date", "토익스피킹"], ["opic_exam_date", "오픽"]]) {
      const d = f[name].value.trim();
      if (d && !/^\d{4}-\d{2}-\d{2}$/.test(d)) errors.push(`${label} 시험일 형식이 올바르지 않습니다.`);
      v[name] = d;
    }
    for (const name of ["toefl_target", "tsp_target", "opic_target", "opic_level"]) v[name] = f[name].value;
    const msg = $("exam-msg");
    if (errors.length) { msg.style.color = "var(--bad)"; msg.textContent = errors.join(" "); return; }
    if (v.current_score && v.current_score !== t.current_score) v.current_score_at = TSU.nowStr();
    TSStore.setSettings(v);
    Object.assign(t, v);
    msg.style.color = "var(--ok)"; msg.textContent = "저장했습니다.";
    setTimeout(() => { msg.textContent = ""; }, 2500);
  });

  // ---- 단어·음성 (바꾸면 바로 저장) ----
  const flash = (m, bad) => { $("saved").textContent = m; $("saved").style.color = bad ? "var(--bad)" : "var(--ok)"; setTimeout(() => { $("saved").textContent = ""; }, 2000); };
  for (const k of ["daily_new", "tts_accent", "tts_rate", "my_level"]) {
    $(k).addEventListener("change", () => { Ward.setSetting({ [k]: k === "tts_accent" ? $(k).value : Number($(k).value) }); flash("저장했습니다"); });
  }
  $("score").addEventListener("change", () => {
    const val = Number($("score").value);
    if (!(val >= 10 && val <= 990)) return flash("10~990 사이로 넣어 주세요", true);
    const lv = Ward.levelFromScore(val);
    $("my_level").value = String(lv);
    Ward.setSetting({ my_level: lv });
    flash(`${val}점 → ${toeicGrades.find(g => g.level === lv).name} 등급으로 저장했습니다`);
  });
  $("test-voice").addEventListener("click", async () => { await TTS.load(); UI.sayWord("vocabulary"); });
  TTS.load().then(() => {
    const i = TTS.info();
    $("voice-info").textContent = TTS.supported
      ? `영어 음성 ${i.count}개 · 사용 가능한 억양: ${i.accents.join(", ") || "없음"} · 한국어 음성: ${i.korean.length ? "있음" : "없음(듣기 모드에서 뜻을 영어 음성으로 읽을 수 있음)"}`
      : "이 브라우저는 음성 합성을 지원하지 않습니다.";
  });

  // ---- 백업 ----
  $("export").addEventListener("click", () => {
    const blob = new Blob([Backup.exportText()], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `ts-word-backup-${Ward.todayStr()}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    $("msg").style.color = ""; $("msg").textContent = "백업 파일을 내려받았습니다.";
  });
  $("import-file").addEventListener("change", async e => {
    const f = e.target.files[0];
    if (!f) return;
    try {
      const mode = document.querySelector("input[name=imode]:checked").value;
      if (mode === "replace" && !confirm("지금의 학습 기록을 이 파일의 내용으로 바꿉니다. 계속할까요?")) return;
      const r = Backup.importText(await f.text(), mode);
      const parts = [];
      if (r.ward) parts.push(mode === "merge" ? `단어 ${r.ward.cards}개(새로 ${r.ward.added}개)` : `단어 ${r.ward.cards}개`);
      if (r.ts) parts.push(mode === "merge" && r.ts.added ? `풀이 ${r.ts.sessions}번(새로 ${r.ts.added.sessions}번)·문항 기록 ${r.ts.attempts}건·오답노트 ${r.ts.notes}개`
                                                        : `풀이 ${r.ts.sessions}번·문항 기록 ${r.ts.attempts}건·오답노트 ${r.ts.notes}개`);
      $("msg").style.color = ""; $("msg").textContent = `${mode === "merge" ? "합쳤습니다" : "불러왔습니다"}. (${parts.join(" · ")})`;
      setTimeout(() => location.reload(), 900);
    } catch (err) { $("msg").style.color = "var(--bad)"; $("msg").textContent = "불러오지 못했습니다: " + err.message; }
    e.target.value = "";
  });
  $("reset").addEventListener("click", () => {
    if (!confirm("모든 학습 기록(단어 복습 일정·별표·시험 기록·풀이·오답노트)을 지웁니다. 되돌릴 수 없어요. 먼저 백업하셨나요?")) return;
    Ward.reset();
    TSStore.reset();
    location.reload();
  });
});
