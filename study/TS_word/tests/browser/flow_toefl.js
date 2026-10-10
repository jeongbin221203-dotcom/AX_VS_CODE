// 토플 화면 흐름 점검 (헤드리스 크롬, file:// 로 직접 엶): 영역별 연습 → 기록·밴드 → 오답노트, 가짜 마이크로 말하기, 모의고사 한 번 통째로(영역 단위 저장·새로고침 이어 하기 포함)
// 실행: node tests/browser/flow_toefl.js   (환경변수 CHROME 으로 브라우저 경로 지정 가능). node --test 에는 포함되지 않는다.
const { launch, sleep } = require("./cdp.js");
const os = require("os");
const ROOT = require("url").pathToFileURL(require("path").join(__dirname, "..", "..")).href + "/";
let pass = 0, fail = 0;
const ok = (c, m) => { if (c) { pass++; console.log("  ✓", m); } else { fail++; console.log("  ✗", m); } };

// 모든 페이지에 먼저 넣는 스크립트: 확인 창 자동 승인, 긴 대기 줄이기, 가짜 음성 인식·마이크 (fake=false 면 음성 인식·마이크 없음)
const initScript = fake => `
  window.confirm = () => true; window.alert = () => {};
  const st = window.setTimeout; window.setTimeout = (f, ms, ...a) => st(f, ms > 300 ? ms / 20 : ms, ...a);
  ${fake ? `
  window.SpeechRecognition = window.webkitSpeechRecognition = class {
    start() { setTimeout(() => { const text = window.__heard !== undefined ? window.__heard : (window.__lastSay || "hello world");
      this.onresult && this.onresult({ resultIndex: 0, results: [Object.assign([{ transcript: text }], { isFinal: true })] });
      setTimeout(() => this.onend && this.onend(), 30); }, 50); }
    stop() {}
  };
  if (!navigator.mediaDevices) Object.defineProperty(navigator, "mediaDevices", { value: {}, configurable: true });
  navigator.mediaDevices.getUserMedia = async () => ({ getTracks: () => [{ stop() {} }] });
  window.MediaRecorder = class { constructor() { this.mimeType = "audio/webm"; } start() {} stop() { this.ondataavailable && this.ondataavailable({ data: new Blob(["x"]) }); this.onstop && this.onstop(); } };
  ` : `
  window.SpeechRecognition = undefined; window.webkitSpeechRecognition = undefined; window.MediaRecorder = undefined;
  `}
`;
const helpers = `
  window.__qs = root => [...(root || document).querySelectorAll('.qtext')].map(e => e.innerText.replace(/^\\d+\\.\\s*/, ''));
  window.__find = (task, snippet, qs) => Toefl.itemsOf(task).find(it => (!qs || !qs.length || (it.questions && it.questions.map(q => q.q).join('|') === qs.join('|')))
    && [it.text, it.context, it.situation, it.professor, it.intro, it.prompt, it.choices && it.choices[0], ...(it.questions || []).map(q => q.q)].some(f => f && String(f).includes(snippet)));
  TTS.stop = () => {}; TTS.load = async () => {};
  TTS.play = async segs => { window.__lastSay = segs.map(s => s.text).join(" "); return true; };
`;

(async () => {
  const b = await launch(9521, os.tmpdir() + "/tsw_tf1");
  const p = await b.newPage();
  await p.send("Page.addScriptToEvaluateOnNewDocument", { source: initScript(true) });
  const wait = (e, ms) => p.waitFor(e, ms || 10000);
  const open = async (url, extra) => { await p.goto(ROOT + url); await wait("document.getElementById('app') && !document.getElementById('app').innerText.includes('불러오는 중')"); await p.eval(helpers + (extra || "")); };
  const attempts = () => p.eval("TSStore.col('toefl_attempts').all().length");
  const qtext = (sel = ".qtext") => p.eval(`document.querySelector('${sel}').innerText.replace(/^\\d+\\.\\s*/, '').slice(0, 60)`);

  console.log("1) 토플 홈: 기록이 없을 때");
  await open("toefl.html");
  ok(await p.eval("document.querySelectorAll('.kpi').length === 5"), "종합 + 4영역 카드");
  ok(await p.eval("document.body.innerText.includes('네 영역을 모두 풀면 계산됩니다')"), "기록이 없으면 안내");
  ok(await p.eval("document.querySelectorAll('#app form[action=\"toefl-practice.html\"]').length === 11"), "과제 11개 연습 폼");
  ok(await p.eval("[...document.querySelectorAll('#sub-nav a')].every(a => !a.closest('.soon')) && !document.querySelector('#sub-nav .soon')"), "메뉴에 '준비 중' 없음");
  ok(await p.eval("document.querySelector('#sub-nav a.on').textContent === '홈'"), "메뉴 홈 강조");

  console.log("2) 읽기: 일상 글(세트) 연습 → 기록");
  await open("toefl-practice.html?task=r_daily&level=3&n=2");
  await p.click("#start-btn");
  await wait("document.querySelector('.choice')");
  for (let k = 0; k < 2; k++) {
    await wait("document.querySelector('.choice')");
    const snip = await qtext();
    const right = await p.eval(`(() => { const it = __find('r_daily', ${JSON.stringify(snip)}, __qs()); return it.questions.map((q, i) => (i === 0 && ${k} === 0 ? (q.answer + 1) % 4 : q.answer)); })()`);
    for (let i = 0; i < right.length; i++) await p.eval(`document.querySelector('.choice[data-q="${i}"][data-c="${right[i]}"]').click()`);
    await wait("!document.querySelector('[data-grade]').disabled");
    await p.click("[data-grade]");
    await wait("document.querySelector('.verdict')");
    if (k === 0) { ok(await p.eval("document.querySelector('.verdict.bad')"), "일부러 고른 오답이 '오답'으로 표시"); await p.click("[data-act=next]"); }
  }
  ok(await attempts() >= 4, "문항마다 기록됨 (" + await attempts() + ")");
  await p.click("[data-act=finish]");
  await wait("document.querySelector('#stage h2') && document.querySelector('#stage h2').innerText.includes('끝!')");
  ok(true, "끝 화면");

  console.log("3) 읽기: 빈칸 단어 완성 (채점·틀린 칸 표시)");
  await open("toefl-practice.html?task=r_words&level=2&n=1");
  await p.click("#start-btn");
  await wait("document.querySelector('.tw-in')");
  const rw = await p.eval(`(() => { const it = __find('r_words', document.querySelector('.passage').innerText.slice(0, 25)); const bl = [...it.text.matchAll(/\\[\\[([A-Za-z]+)\\|([A-Za-z]+)\\]\\]/g)].map(m => m[2]); return bl; })()`);
  ok(rw.length === 10, "빈칸 10개");
  await p.eval(`(() => { const want = ${JSON.stringify(rw)}; document.querySelectorAll('.tw-in').forEach((inp, i) => { inp.value = i === 0 ? 'zzzzzzzzzz'.slice(0, inp.maxLength) : want[i]; }); })()`);
  await p.click("[data-check]");
  await wait("document.querySelector('.verdict')");
  ok(await p.eval("document.querySelector('.verdict').innerText.trim().startsWith('9 / 10')"), "9/10 채점");
  ok(await p.eval("document.querySelectorAll('.tw-fix').length === 1"), "틀린 칸 위에 정답 단어");

  console.log("4) 듣기: 응답 고르기·대화 / 쓰기: 문장 만들기");
  await open("toefl-practice.html?task=l_response&level=1&n=1");
  await p.click("#start-btn");
  await wait("document.querySelector('.choice')");
  ok(await p.eval("window.__lastSay.length > 5"), "음성(가짜)으로 문장 재생 요청");
  const lr = await p.eval(`(() => { const it = __find('l_response', document.querySelector('.choice span:last-child').innerText); return it.answer; })()`);
  await p.eval(`document.querySelector('.choice[data-c="${lr}"]').click()`);
  await wait("document.querySelector('.verdict.ok')");
  ok(true, "정답 고르면 바로 채점");
  await open("toefl-practice.html?task=w_sentence&level=2&n=1");
  await p.click("#start-btn");
  await wait("document.querySelector('.chunk')");
  const ws = await p.eval(`(() => { const it = __find('w_sentence', document.querySelector('.sentence').innerText.replace(/[“”]/g, '').replace('🔊', '').trim().slice(0, 20)); return it.chunks; })()`);
  for (const ch of ws) await p.eval(`[...document.querySelectorAll('[data-bank] .chunk')].find(b => b.innerText === ${JSON.stringify(ch)}).click()`);
  await wait("!document.querySelector('[data-check]').disabled");
  await p.click("[data-check]");
  await wait("document.querySelector('.verdict.ok')");
  ok(true, "문장 만들기 정답");

  console.log("5) 쓰기: 이메일 → 모범 답안 → 자기 채점 저장");
  await open("toefl-practice.html?task=w_email&level=3&n=1");
  await p.click("#start-btn");
  await wait("document.querySelector('[data-text]')");
  await p.eval("(() => { const t = document.querySelector('[data-text]'); t.value = 'Dear Ms. Rivera, I would like to ask for help. Thank you.'; t.dispatchEvent(new Event('input')); })()");
  ok(await p.eval("document.querySelector('[data-wc]').innerText.startsWith('12 단어')"), "단어 수 표시");
  const before5 = await attempts();
  await p.click("[data-submit]");
  await wait("document.querySelector('input[name=rb]')");
  ok(await p.eval("document.body.innerText.includes('모범 답안')"), "모범 답안 공개");
  await p.eval("document.querySelector('input[name=rb][value=\"2\"]').click(); document.querySelector('input[name=rb][value=\"2\"]').dispatchEvent(new Event('change'))");
  await p.click("[data-rb]");
  await wait("document.querySelector('[data-act=finish]')");
  ok(await attempts() === before5 + 1, "자기 평가 점수가 기록됨");
  ok(await p.eval("TSStore.col('toefl_attempts').all().slice(-1)[0].score === 0.4"), "2/5 → 0.4");

  console.log("6) 말하기(가짜 마이크): 듣고 따라 말하기 — 음성 인식으로 자동 채점");
  await open("toefl-practice.html?task=s_repeat&level=3&n=1");
  await p.click("#start-btn");
  await wait("document.querySelector('#stage .verdict') && document.querySelector('#stage .explain .verdict') && document.querySelector('[data-act=finish]')", 60000);
  ok(await p.eval("document.querySelector('#stage .explain .verdict').innerText.includes('100%')"), "그대로 따라 말하면 평균 100%");
  ok(await p.eval("TSStore.col('toefl_attempts').all().filter(a => a.task === 's_repeat').length === 7"), "7문장 기록");
  console.log("   인터뷰(녹음 + 인식 + 자기 평가)");
  await open("toefl-practice.html?task=s_interview&level=3&n=1");
  await p.click("#start-btn");
  await wait("document.querySelector('input[name=rb]')", 60000);
  ok(await p.eval("document.querySelectorAll('audio').length === 4"), "질문마다 녹음 재생기 4개");
  ok(await p.eval("document.querySelectorAll('[data-qi]').length === 4"), "질문 4개");
  await p.eval("document.querySelector('input[name=rb][value=\"3\"]').click(); document.querySelector('input[name=rb][value=\"3\"]').dispatchEvent(new Event('change'))");
  await p.click("[data-rb]");
  ok(await p.eval("TSStore.col('toefl_attempts').all().slice(-1)[0].task === 's_interview'"), "인터뷰 자기 평가 기록");

  console.log("7) 틀린 문제 → 오답노트 → 다시 풀기");
  await open("toefl-review.html");
  ok(await p.eval("document.body.innerText.includes('일상 글 읽기') && document.body.innerText.includes('이메일 쓰기')"), "오답 과제 표시");
  const href = await p.eval("document.querySelector('a[href*=\"review=1\"]').getAttribute('href')");
  await open(href);
  ok(await p.eval("document.body.innerText.includes('오답 다시 풀기')"), "오답 다시 풀기 화면");
  ok(await p.eval("!!document.getElementById('start-btn')"), "풀 문제가 있음");
  await open("toefl-history.html");
  ok(await p.eval("document.querySelectorAll('details.reveal-d').length >= 1 && document.body.innerText.includes('문항')"), "기록에 오늘 날짜");
  await open("toefl.html");
  ok(await p.eval("document.querySelector('#app').innerText.includes('최근 기록')"), "홈에 최근 기록");
  console.log("콘솔 오류:", p.logs.join(" / ") || "(없음)");
  ok(p.logs.length === 0, "콘솔 오류 없음");

  // -------------------------------------------------------------------------- 모의고사
  console.log("8) 모의고사: 시작 → Reading(적응형 2모듈) → 새로고침 이어 하기 → Listening → Writing → Speaking → 자기 채점 → 결과");
  p.logs.length = 0;
  await open("toefl-mock.html");
  ok(await p.eval("document.body.innerText.includes('Reading') && document.body.innerText.includes('아직 없습니다')"), "안내 화면");
  await p.click("#go");
  await wait("location.pathname.endsWith('toefl-mock-run.html')");
  await wait("document.getElementById('start-btn')");
  const mid = await p.eval("new URLSearchParams(location.search).get('id')");
  await p.eval(helpers);
  await p.click("#start-btn");

  // 현재 화면의 문제를 정답(or 오답)으로 채우고 다음으로 — 한 모듈이 끝날 때까지 (그 모듈의 답안 비율을 조절)
  async function runModule(rightRatio) {
    let n = 0;
    for (;;) {
      await wait("document.querySelector('[data-go]') || (document.querySelector('#stage .item-head') && document.querySelector('[data-next], [data-submit]'))", 20000);
      if (await p.eval("!!document.querySelector('[data-go]')")) break;
      await wait("!(document.querySelector('[data-next]') || document.querySelector('[data-submit]')).disabled", 15000);
      await p.eval(`(() => {
        const ratio = ${rightRatio}, st = document.querySelector('#stage');
        const task = Toefl.TASK_KEYS.find(t => Toefl.TASKS[t].name === st.querySelector('.item-head b').innerText);
        const first = st.querySelector('.qtext') && st.querySelector('.qtext').innerText.replace(/^\\d+\\.\\s*/, '').slice(0, 40);
        const it = task === 'l_response' ? __find(task, st.querySelector('.choice span:last-child').innerText) : task === 'r_words' ? __find(task, st.querySelector('.passage').innerText.slice(0, 25)) : __find(task, first, __qs(st));
        window.__n = (window.__n || 0) + 1;
        const right = (window.__n % 10) / 10 < ratio;
        if (task === 'r_words') {
          const bl = [...it.text.matchAll(/\\[\\[([A-Za-z]+)\\|([A-Za-z]+)\\]\\]/g)].map(m => m[2]);
          st.querySelectorAll('.tw-in').forEach((inp, i) => { inp.value = right ? bl[i] : ''; inp.dispatchEvent(new Event('input')); });
        } else {
          const qs = task === 'l_response' ? [it] : it.questions;
          qs.forEach((q, i) => { const c = right ? q.answer : (q.answer + 1) % 4; const b = st.querySelector('.choice[data-key$="|' + i + '"][data-c="' + c + '"]'); b && b.click(); });
        }
        (st.querySelector('[data-next]') || st.querySelector('[data-submit]')).click();
      })()`);
      if (++n > 40) throw new Error("모듈이 끝나지 않음");
      await sleep(80);
    }
  }
  await wait("document.querySelector('[data-go]')");
  ok(await p.eval("document.querySelector('#stage h2').innerText === 'Reading'"), "Reading 안내");
  await p.click("[data-go]");
  await runModule(1);                      // 1모듈 전부 정답 → 2모듈은 어려운 쪽
  await wait("document.querySelector('[data-go]') && document.querySelector('#stage h2').innerText.includes('모듈 2')");
  await p.click("[data-go]");
  await runModule(0.5);
  await wait("document.querySelector('[data-go]') && document.querySelector('#stage h2').innerText === 'Listening'");
  const saved = await p.eval(`JSON.parse(localStorage.getItem('ts-tmock-${mid}'))`);
  ok(saved && saved.done.join() === "R" && saved.routes.R === "hard", "Reading 끝낸 뒤 진행 상황 저장(ts-tmock-" + mid + "), 2모듈=어려움");
  ok(Object.keys(saved.results).length >= 4, "읽기 결과 저장: " + Object.keys(saved.results).length + "개 문제");

  console.log("   새로고침 → 이어서 하기");
  await p.eval("window.onbeforeunload = null");                 // 자동 점검에서는 '나갈까요?' 확인 창을 건너뜀
  await p.goto(ROOT + `toefl-mock-run.html?id=${mid}`);
  await wait("document.getElementById('start-btn')");
  await p.eval(helpers);
  await p.click("#start-btn");
  await wait("document.querySelector('[data-go]')");
  ok(await p.eval("document.querySelector('#stage h2').innerText === 'Listening'"), "끝낸 Reading 은 건너뛰고 Listening 부터");
  await p.click("[data-go]");
  await runModule(1);
  await wait("document.querySelector('[data-go]') && document.querySelector('#stage h2').innerText.includes('모듈 2')");
  await p.click("[data-go]");
  await runModule(0);
  console.log("   Writing");
  await wait("document.querySelector('[data-go]') && document.querySelector('#stage h2').innerText === 'Writing'");
  await p.click("[data-go]");
  for (let i = 0; i < 10; i++) {
    await wait("document.querySelector('.ws-bank') && document.querySelector('[data-ok]')");
    const chunks = await p.eval(`(() => { const it = __find('w_sentence', document.querySelector('.sentence').innerText.replace(/[“”]/g, '').trim().slice(0, 20)); return it.chunks; })()`);
    if (i % 2 === 0) for (const ch of chunks) await p.eval(`[...document.querySelectorAll('[data-bank] .chunk')].find(b => b.innerText === ${JSON.stringify(ch)}).click()`);
    await p.click("[data-ok]");
    await sleep(60);
  }
  ok(true, "문장 만들기 10개 (짝수 번째만 정답)");
  for (const text of ["Dear Ms. Rivera, I would like to ask for a refund because the trip was cancelled.", "I agree with Kelly because practice matters."]) {
    await wait("document.querySelector('[data-done]')");
    await p.eval(`(() => { const t = document.querySelector('[data-text]'); t.value = ${JSON.stringify(text)}; t.dispatchEvent(new Event('input')); })()`);
    await p.click("[data-done]");
    await sleep(100);
  }
  console.log("   Speaking (가짜 마이크)");
  await wait("document.querySelector('[data-go]') && document.querySelector('#stage h2').innerText === 'Speaking'", 20000);
  await p.click("[data-go]");
  await wait("document.querySelector('input[type=radio]')", 90000);
  ok(await p.eval("document.querySelector('#stage h2').innerText.includes('자기 채점')") || await p.eval("document.body.innerText.includes('마지막 단계: 자기 채점')"), "자기 채점 화면 (쓰기 2 + 인터뷰)");
  const groups = await p.eval("[...new Set([...document.querySelectorAll('input[type=radio]')].map(r => r.name))]");
  ok(groups.length === 3, "채점할 것 3개 (이메일·토론·인터뷰, 따라 말하기는 인식돼 자동 채점): " + groups.join());
  for (const g of groups) await p.eval(`(() => { const r = document.querySelector('input[name="${g}"][value="4"]'); r.click(); r.dispatchEvent(new Event('change')); })()`);
  await p.click("[data-fin]");
  await wait("location.pathname.endsWith('toefl-mock-result.html')", 15000);
  await wait("document.querySelector('.kpi')");
  ok(await p.eval("document.querySelectorAll('.kpi').length === 5"), "결과: 종합 + 4영역");
  const res = await p.eval(`(() => { const m = Toefl.getMock(${mid}); return { r: m.result, total: m.total, plan: m.plan }; })()`);
  ok(res.total !== null && [res.r.bands.R, res.r.bands.L, res.r.bands.S, res.r.bands.W].every(x => x !== null), "네 영역 밴드와 종합: " + JSON.stringify(res.r.bands) + " → " + res.total);
  ok(res.r.routes.R === "hard" && res.r.routes.L === "hard", "적응형 경로 기록");
  ok(res.plan === null, "끝난 시험은 계획을 비움");
  ok(await p.eval(`localStorage.getItem('ts-tmock-${mid}') === null`), "영역별 임시 저장은 지워짐");
  ok(await p.eval("document.body.innerText.includes('과제별') && document.querySelectorAll('tbody tr').length >= 20"), "과제별 결과 표");

  console.log("9) 모의고사 뒤: 홈·기록·허브 카드");
  await open("toefl.html");
  ok(await p.eval("document.querySelector('.kpi .value').innerText !== '–'"), "홈 종합 밴드 표시");
  await open("toefl-history.html");
  ok(await p.eval("document.body.innerText.includes('모의고사') && document.querySelector('a[href*=\"toefl-mock-result\"]')"), "기록에 모의고사 한 줄");
  await open("toefl-mock.html");
  ok(await p.eval("document.querySelectorAll('a[href*=\"toefl-mock-result\"]').length === 1"), "지난 모의고사 목록");
  await p.goto(ROOT + "index.html");
  await wait("document.querySelector('.hub-card')");
  const card = await p.eval("[...document.querySelectorAll('.hub-card')].find(c => c.innerText.includes('TOEFL')).innerText.replace(/\\n+/g, ' | ')");
  ok(/밴드 \d/.test(card) && card.includes("960문제") && card.includes("어휘 800") && card.includes("마지막 학습 20"), "허브 토플 카드: " + card);
  ok(!card.includes("준비 중"), "허브 카드에 '준비 중' 없음");
  console.log("콘솔 오류:", p.logs.join(" / ") || "(없음)");
  ok(p.logs.length === 0, "콘솔 오류 없음");
  b.close();

  // -------------------------------------------------------------------------- 음성 인식·마이크 없는 브라우저
  console.log("10) 음성 인식·마이크가 없을 때: 스스로 채점으로 넘어간다");
  const b2 = await launch(9522, os.tmpdir() + "/tsw_tf2");
  const q = await b2.newPage();
  await q.send("Page.addScriptToEvaluateOnNewDocument", { source: initScript(false) });
  await q.goto(ROOT + "toefl-practice.html?task=s_repeat&level=2&n=1");
  await q.waitFor("document.getElementById('start-btn')");
  await q.eval(helpers);
  await q.click("#start-btn");
  await q.waitFor("document.querySelector('[data-s=\"1\"]')", 20000);
  ok(await q.eval("document.querySelector('.status').innerText.includes('스스로 채점')"), "안내: 따라 말한 뒤 스스로 채점");
  for (let i = 0; i < 7; i++) {
    await q.waitFor("document.querySelector('[data-s=\"1\"]')", 20000);
    await q.eval(`document.querySelector('[data-s="${i % 3 === 0 ? "0.5" : "1"}"]').click()`);
    await sleep(120);
  }
  await q.waitFor("document.querySelector('[data-act=finish]')", 20000);
  const scores = await q.eval("TSStore.col('toefl_attempts').all().map(a => a.score)");
  ok(scores.length === 7 && scores[0] === 0.5 && scores[1] === 1, "7문장 자기 채점 점수 저장: " + scores.join());
  console.log("콘솔 오류:", q.logs.join(" / ") || "(없음)");
  ok(q.logs.length === 0, "콘솔 오류 없음");
  b2.close();
  console.log(`통과 ${pass} / 실패 ${fail}`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error("중단:", e.message); process.exit(1); });
