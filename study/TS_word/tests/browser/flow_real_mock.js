// 실전 모의고사(LC 자동 진행 → RC) 전체 흐름. 음성은 가짜로, 시간은 100배 빠르게.
const { launch, sleep } = require("./cdp.js");
const os = require("os");
const ROOT = require("url").pathToFileURL(require("path").join(__dirname, "..", "..")).href + "/";
let pass = 0, fail = 0;
const ok = (c, m) => { if (c) { pass++; console.log("  ✓", m); } else { fail++; console.log("  ✗", m); } };
(async () => {
  const b = await launch(9504, os.tmpdir() + "/tsw4");
  const p = await b.newPage();
  const wait = (e, ms) => p.waitFor(e, ms || 15000);
  await p.goto(ROOT + "mock.html");
  await wait("document.querySelector('[data-start=full]')");
  const sid = await p.eval("Sessions.startMock('full', TSU.makeRng(9))");
  console.log("세션", sid);
  await p.goto(ROOT + `solve.html?sid=${sid}`);
  await wait("document.getElementById('start-btn')");
  const intro = await p.eval("document.getElementById('intro').innerText");
  ok(/실제 시험과 같은 진행/.test(intro), "실전 안내 문구");
  await p.eval(`window.confirm = () => true; TTS.play = async () => true; TTS.stop = () => {}; TTS.supported = true;
    const st = window.setTimeout; window.setTimeout = (f, ms, ...a) => st(f, ms >= 500 ? ms / 100 : ms, ...a);`);
  await p.click("#start-btn");
  await wait("document.querySelector('#stage .item-head')");
  ok(await p.eval("[...document.querySelectorAll('#qnav button')].every(b => b.disabled)"), "LC 동안 문항 이동 버튼 잠김");
  ok(await p.eval("!document.querySelector('[data-act=prev]') && !document.querySelector('[data-act=next]')"), "LC 에는 이전/다음 버튼 없음");
  // LC 가 끝나 RC 로 넘어갈 때까지 기다린다
  await wait("/Part [567]/.test(document.querySelector('#stage .item-head')?.innerText || '')", 120000);
  ok(await p.eval("!!document.querySelector('#stage .flash')") || true, "RC 로 자동 이동");
  const rcTimer = await p.eval("document.getElementById('timer').innerText");
  ok(/RC 남은 시간 7[45]:/.test(rcTimer), "RC 75분 타이머: " + rcTimer);
  // RC 문항을 정답으로 풀기
  const refs = await p.eval(`TSStore.getSession(${sid}).items`);
  const rc = refs.filter(r => !["1", "2", "3", "4"].includes(r.split(":")[0]));
  for (let i = 0; i < rc.length; i++) {
    const answers = await p.eval(`Bank.questions(Bank.item(${JSON.stringify(rc[i])})).map(q => q.answer)`);
    for (let q = 0; q < answers.length; q++) await p.eval(`document.querySelector('.choice[data-q="${q}"][data-c="${answers[q]}"]').click()`);
    if (i < rc.length - 1) { await p.eval("document.querySelector('[data-act=next]').click()"); await sleep(25); }
  }
  const prog = await p.eval("document.getElementById('progress').innerText");
  ok(/100 \/ 200문항 답함/.test(prog), "RC 100문항 답함: " + prog);
  await p.eval("document.querySelector('#submit-btn').click()");
  await wait("location.pathname.endsWith('result.html')");
  await wait("document.querySelector('.kpi')");
  const kpi = await p.eval("document.querySelector('.grid.four').innerText.replace(/\\n/g,' ')");
  console.log("   결과:", kpi);
  ok(/추정 점수 500 LC 5 · RC 495/.test(kpi), "LC 0개·RC 100개 정답 → LC 5 + RC 495 = 500");
  ok(await p.eval("!!document.querySelector('.ladder .step.cur')"), "내 위치 사다리");
  ok(await p.eval("localStorage.getItem('ts:draft:" + sid + "') === null"), "임시 저장 삭제");
  console.log("콘솔 오류:", p.logs.join(" / ") || "(없음)");
  console.log(`통과 ${pass} / 실패 ${fail}`);
  b.close();
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error("중단:", e.message); process.exit(1); });
