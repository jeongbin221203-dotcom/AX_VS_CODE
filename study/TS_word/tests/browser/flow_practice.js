const { launch, sleep } = require("./cdp.js");
const os = require("os");
const ROOT = require("url").pathToFileURL(require("path").join(__dirname, "..", "..")).href + "/";
let pass = 0, fail = 0;
const ok = (c, m) => { if (c) { pass++; console.log("  ✓", m); } else { fail++; console.log("  ✗", m); } };
(async () => {
  const b = await launch(9502, os.tmpdir() + "/tsw2");
  const p = await b.newPage();
  const wait = (e, ms) => p.waitFor(e, ms || 10000);
  const stats = () => p.eval(`(() => { const d = TSStore.data(); return { sessions: d.sessions.length, attempts: d.attempts.length, notes: Object.keys(d.notes).length, open: TSStore.wrongNotes('open').length }; })()`);

  console.log("1) 파트 5 연습 (5문항)");
  await p.goto(ROOT + "practice.html?start=1&part=5&level=2&n=5");
  await wait("location.pathname.endsWith('solve.html')");
  await wait("document.getElementById('start-btn') || document.getElementById('stage').innerHTML.length > 50");
  if (await p.eval("!!document.getElementById('start-btn') && !document.getElementById('quiz-main').offsetParent")) await p.click("#start-btn");
  for (let i = 0; i < 5; i++) {
    await wait("document.querySelector('.choice')");
    await p.eval(`document.querySelectorAll('.choice')[${i % 3}].click()`);       // 일부러 섞어서 고름
    await wait("document.querySelector('.explain')");
    const last = i === 4;
    await p.eval(`document.querySelector('[data-act=${last ? "finish" : "next"}]').click()`);
    if (!last) await sleep(150);
  }
  await wait("location.pathname.endsWith('result.html')");
  await wait("document.getElementById('stage').innerHTML.length > 50");
  let st = await stats();
  ok(st.sessions === 1 && st.attempts === 5, `세션 1·문항 5건 저장 (${JSON.stringify(st)})`);
  const resText = await p.eval("document.querySelector('.kpi').innerText.replace(/\\n/g,' ')");
  ok(/정답/.test(resText), "결과 화면 KPI: " + resText);
  console.log("   로그:", p.logs.join(" / ") || "(없음)");

  console.log("2) 새로고침 후 유지");
  await p.goto(ROOT + "history.html");
  await wait("document.querySelector('tbody tr')");
  ok(await p.eval("document.querySelectorAll('tbody tr').length") === 1, "전체 기록에 1건");
  await p.goto(ROOT + "review.html");
  await wait("document.getElementById('filters')");
  const openN = (await stats()).open;
  ok(await p.eval("document.querySelectorAll('tbody tr').length") === openN, `오답노트 ${openN}건 표시`);

  console.log("3) 파트 3 연습 (세트 문항 3개 채점하기)");
  await p.goto(ROOT + "practice.html?start=1&part=3&n=3");
  await wait("location.pathname.endsWith('solve.html')");
  await wait("document.getElementById('start-btn')");
  await p.click("#start-btn");
  await wait("document.querySelector('.choice')");
  for (let q = 0; q < 3; q++) await p.eval(`document.querySelectorAll('.q')[${q}].querySelectorAll('.choice')[1].click()`);
  await wait("document.querySelector('[data-act=grade]:not([disabled])')");
  await p.click("[data-act=grade]");
  await wait("document.querySelector('.explain')");
  ok(await p.eval("document.querySelectorAll('.explain').length") === 3, "해설 3개 표시");
  ok(await p.eval("!!document.querySelector('.reveal')"), "스크립트/해석 표시");
  st = await stats();
  ok(st.attempts === 8, "문항 기록 8건 (" + st.attempts + ")");
  await p.eval("TTS.stop()");

  console.log("4) 진단 테스트");
  await p.goto(ROOT + "diagnostic.html");
  await wait("document.getElementById('start')");
  await p.click("#start");
  await wait("location.pathname.endsWith('solve.html')");
  await wait("document.getElementById('start-btn')");
  await p.click("#start-btn");
  const total = await p.eval("document.querySelectorAll('#qnav button').length || 0");
  // 진단: 모든 문항에 첫 보기 선택 후 제출
  for (let guard = 0; guard < 80; guard++) {
    await wait("document.querySelector('.choice')");
    await p.eval(`document.querySelectorAll('.q, .card .choices').length; document.querySelectorAll('.choices').forEach(c => { if (!c.querySelector('.sel')) c.querySelector('.choice').click(); })`);
    const hasNext = await p.eval("!!document.querySelector('[data-act=next]')");
    if (!hasNext) break;
    await p.eval("document.querySelector('[data-act=next]').click()");
    await sleep(60);
  }
  await p.eval("TTS.stop()");
  p.eval("window.confirm = () => true");
  await p.eval("window.onbeforeunload = null; document.querySelector('#submit-btn').click()");
  await wait("location.pathname.endsWith('result.html')");
  await wait("document.querySelector('.kpi')");
  const diag = await p.eval("document.querySelector('.grid.four').innerText.replace(/\\n/g,' ')");
  console.log("   진단 결과:", diag, "| 문항수", total);
  st = await stats();
  ok(/추정 점수/.test(diag), "추정 점수 표시");
  ok(st.sessions === 3, "세션 3개");

  console.log("5) 오늘 화면 / 통계 / 가이드");
  await p.goto(ROOT + "toeic.html");
  await wait("document.querySelector('.kpi')");
  const kpi = await p.eval("document.querySelector('.grid.four').innerText.replace(/\\n/g,' ')");
  console.log("   KPI:", kpi);
  ok(/현재 점수/.test(kpi) && !/현재 점수 –/.test(kpi), "현재 점수가 채워짐");
  ok(await p.eval("!!document.getElementById('score-chart') && !!document.querySelector('#score-chart').getContext"), "점수 차트 canvas");
  await p.goto(ROOT + "stats.html");
  await wait("document.querySelector('table')");
  ok(await p.eval("document.querySelectorAll('canvas').length") === 2, "차트 2개");
  console.log("   로그:", p.logs.join(" / ") || "(없음)");

  console.log("6) 백업 내보내기/불러오기 왕복");
  await p.goto(ROOT + "settings.html");
  await wait("document.getElementById('export')");
  const dump = await p.eval("Backup.exportText()");
  const parsed = JSON.parse(dump);
  ok(parsed.ts && parsed.ts.attempts.length >= 8 && parsed.cards, "백업에 ts·단어 둘 다 포함");
  await p.eval("TSStore.reset()");
  ok((await stats()).sessions === 0, "초기화됨");
  await p.eval(`Backup.importText(${JSON.stringify(dump)}, 'replace')`);
  st = await stats();
  ok(st.sessions === 3 && st.attempts === parsed.ts.attempts.length, "복원 후 동일 " + JSON.stringify(st));
  await p.eval(`Backup.importText(${JSON.stringify(dump)}, 'merge')`);
  ok((await stats()).sessions === 3 && (await stats()).attempts === parsed.ts.attempts.length, "합치기는 중복 없음");

  console.log(`\n통과 ${pass} / 실패 ${fail}`);
  console.log("콘솔 오류:", p.logs.join(" / ") || "(없음)");
  b.close();
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error("중단:", e.message); process.exit(1); });
