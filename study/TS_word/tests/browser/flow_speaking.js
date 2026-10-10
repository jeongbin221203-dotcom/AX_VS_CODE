// 토익스피킹·오픽 흐름 점검 (헤드리스 크롬, file://, 가짜 마이크, 시간 100배속, 음성 합성은 가짜)
//  1) 토익스피킹: 유형별 연습(질문 3개 세트) → 자기 채점 → 저장 → 새로고침해도 기록 유지
//  2) 토익스피킹 실전 모의고사 11문항 → 결과 → 새로고침
//  3) 오픽: 설문 저장 → 모의고사(15문항) → 결과 → 새로고침 → 홈 기록 / 약한 문항 연습
// 실행: node tests/browser/flow_speaking.js   (환경변수 CHROME 으로 브라우저 경로 지정 가능)
const { launch, sleep } = require("./cdp.js");
const os = require("os");
const ROOT = require("url").pathToFileURL(require("path").join(__dirname, "..", "..")).href + "/";
let pass = 0, fail = 0;
const ok = (c, m) => { if (c) { pass++; console.log("  ✓", m); } else { fail++; console.log("  ✗", m); } };

(async () => {
  const b = await launch(9508, os.tmpdir() + "/tsw8", ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"]);
  const p = await b.newPage();
  // 새 문서마다: 시간 배속, 음성 합성(TTS)은 바로 끝나는 가짜로
  await p.send("Page.addScriptToEvaluateOnNewDocument", { source: `
    window.SPK_SPEED = 100;
    let real; Object.defineProperty(window, "TTS", { configurable: true,
      set(v) { v.play = async () => true; v.load = async () => {}; v.stop = () => {}; real = v; }, get() { return real; } });` });
  const wait = (e, ms) => p.waitFor(e, ms || 15000);

  /** 진행 화면: 시작 → (다음 버튼이 보이면 누르며) 채점 화면까지 */
  async function playToReview(max = 120000) {
    await wait("document.getElementById('start-btn')");
    if (await p.eval("!document.getElementById('intro').classList.contains('hidden')")) await p.click("#start-btn");
    const t0 = Date.now();
    for (;;) {
      if (await p.eval("!!document.querySelector('.spk-review')")) return;
      await p.eval("(() => { const b = [...document.querySelectorAll('.spk-actions button')].find(x => /다음/.test(x.textContent)); if (b) b.click(); })()");
      if (Date.now() - t0 > max) throw new Error("채점 화면까지 오지 못함: " + await p.eval("document.getElementById('progress').innerText + ' / ' + document.querySelector('[data-st]')?.innerText"));
      await sleep(80);
    }
  }
  /** 채점 화면: 모든 답변에 점수를 고르고 제출 */
  async function gradeAll(point) {
    const n = await p.eval(`(() => { const cards = [...document.querySelectorAll('.spk-review')];
      cards.forEach(c => { const r = [...c.querySelectorAll('input[type=radio]')]; const t = r.find(x => x.value === '${point}') || r[0]; t.checked = true; });
      document.querySelector('.spk-review input').dispatchEvent(new Event('change', { bubbles: true }));
      return cards.length; })()`);
    ok(await p.eval("!document.querySelector('[data-submit]').disabled"), `채점 ${n}개 모두 고르면 제출 버튼이 켜진다`);
    await p.eval("document.querySelector('[data-submit]').click()");
    await wait("!document.querySelector('.spk-review')");      // 다음 화면으로 바뀔 때까지 (다음 문제 / 연습 끝 / 결과 화면)
    return n;
  }

  console.log("1) 토익스피킹 유형별 연습");
  await p.goto(ROOT + "toeic-speaking.html");
  await wait("document.querySelector('#tasks table')");
  ok(await p.eval("document.querySelectorAll('#tasks tbody tr').length") === 5, "유형 5개 표");
  ok(await p.eval("document.body.innerText.includes('토익스피킹') && !document.querySelector('.soon')"), "메뉴가 '준비 중'이 아님");
  const cnt = await p.eval("document.querySelector('#tasks tbody tr td.r.num').innerText");
  ok(Number(cnt) > 100, "읽기 문제 수 " + cnt);
  await p.goto(ROOT + "tsp-practice.html?task=respond_questions&n=1");
  await playToReview();
  ok(await p.eval("document.querySelectorAll('.spk-review').length") === 3, "질문에 답하기 1세트 = 답변 3개");
  ok(await p.eval("!!document.querySelector('.spk-review audio')"), "가짜 마이크로 녹음이 만들어져 다시 들을 수 있다");
  ok(await p.eval("document.body.innerText.includes('기본 답변') && document.body.innerText.includes('고득점 답변')"), "모범 답안(기본·고득점)이 보인다");
  await gradeAll(2);
  await wait("document.getElementById('practice-done')");
  ok(await p.eval("document.getElementById('practice-done').innerText.includes('67%')"), "자기 채점 평균 2/3 = 67% 표시");
  ok(await p.eval("TSStore.col('speaking_attempts').all().length") === 3, "답변 3개가 저장됨");
  await p.goto(ROOT + "toeic-speaking.html");
  await wait("document.querySelector('.kpi')");
  ok((await p.eval("[...document.querySelectorAll('.kpi .value')].map(x => x.innerText).join('|')")).endsWith("|3"), "새로고침 뒤 홈의 답변 기록 3");
  ok(await p.eval("document.body.innerText.includes('3회 · 67%')"), "유형별 내 기록 '3회 · 67%'");
  // 다섯 유형을 모두 연습 → 추정 점수
  for (const task of ["read_aloud", "describe_picture", "respond_info", "opinion"]) {
    await p.goto(ROOT + `tsp-practice.html?task=${task}&n=1`);
    await playToReview();
    await gradeAll(task === "opinion" ? 4 : 2);
    await wait("document.getElementById('practice-done')");
  }
  await p.goto(ROOT + "toeic-speaking.html");
  await wait("document.querySelector('.kpi')");
  const est = await p.eval("document.querySelector('.kpi .value').innerText");
  ok(/^\d+$/.test(est), "다섯 유형을 마치면 추정 점수가 나온다: " + est);
  ok(await p.eval("document.body.innerText.includes('약한 문항')") === false, "67% 이상이면 약한 문항 없음");
  await p.goto(ROOT + "tsp-practice.html?task=opinion&n=1");
  await playToReview();
  await gradeAll(1);                                     // 의견 1/5 → 약한 문항
  await wait("document.getElementById('practice-done')");
  await p.goto(ROOT + "toeic-speaking.html");
  await wait("document.querySelector('.kpi')");
  ok(await p.eval("document.body.innerText.includes('약한 문항 1')"), "마지막 점수가 낮은 문제가 '약한 문항 1' 로 모인다");
  await p.goto(ROOT + "tsp-practice.html?task=opinion&weak=1&n=1");
  await wait("document.getElementById('start-btn')");
  ok(await p.eval("document.body.innerText.includes('약한 문항')"), "약한 문항 연습 화면이 열린다");

  console.log("2) 토익스피킹 실전 모의고사");
  await p.goto(ROOT + "tsp-mock.html");
  await wait("document.getElementById('make')");
  await p.click("#make");
  await wait("location.pathname.endsWith('tsp-mock-run.html')");
  ok((await p.eval("location.search")) === "?mid=1", "모의고사 번호 1");
  await playToReview(180000);
  const n11 = await gradeAll(3);
  ok(n11 === 11, "11문항 채점");
  await wait("location.pathname.endsWith('tsp-mock-result.html')");
  await wait("document.querySelector('.kpi')");
  const tspRes = await p.eval("document.querySelector('.kpi .value').innerText");
  ok(/^\d+$/.test(tspRes), "결과 화면에 추정 점수: " + tspRes);
  ok(await p.eval("document.querySelectorAll('details.reveal-d').length") === 11, "문항별 11개");
  await p.goto(ROOT + "tsp-mock-result.html?mid=1");
  await wait("document.querySelector('.kpi')");
  ok(await p.eval("document.querySelector('.kpi .value').innerText") === tspRes, "새로고침해도 같은 결과");
  await p.goto(ROOT + "tsp-mock-run.html?mid=1");
  await wait("location.pathname.endsWith('tsp-mock-result.html')");
  ok(true, "끝난 모의고사의 진행 주소는 결과로 이동");
  await p.goto(ROOT + "speaking-history.html?exam=toeic");
  await wait("document.querySelector('details.reveal-d')");
  ok(await p.eval("document.querySelectorAll('details.reveal-d').length") === 21, "기록 화면에 연습 10 + 모의고사 11 답변");

  console.log("3) 오픽");
  await p.goto(ROOT + "opic-survey.html");
  await wait("document.getElementById('survey')");
  await p.eval("document.querySelector('#survey button[type=submit]').click()");
  await sleep(300);
  ok(await p.eval("!!document.querySelector('.flash.error')"), "주제를 안 고르면 오류 안내");
  await p.eval(`(() => { for (const v of ['movie','concert','music','jogging','travel_dom']) document.querySelector('input[name=topic][value=' + v + ']').checked = true;
    document.querySelector('input[name=level][value="4"]').checked = true; document.querySelector('input[name=target][value=IH]').checked = true;
    document.querySelector('#survey').requestSubmit(); })()`);
  await wait("location.pathname.endsWith('opic.html') && window.TSStore && document.querySelector('.kpi')");
  const saved = await p.eval("TSStore.settings()");
  ok(saved.opic_survey.split(",").includes("home") && saved.opic_survey.includes("movie") && saved.opic_level === "4" && saved.opic_target === "IH", "설문 저장: " + saved.opic_survey);
  await wait("document.querySelector('.kpi')");
  ok(await p.eval("document.body.innerText.includes('설문을 저장했습니다')"), "저장 안내");
  await p.goto(ROOT + "opic-mock.html");
  await wait("document.getElementById('make')");
  await p.click("#make");
  await wait("location.pathname.endsWith('opic-mock-run.html')");
  await playToReview(240000);
  const n15 = await gradeAll(4);
  ok(n15 === 15, "오픽 4~6단계 모의고사는 15문항: " + n15);
  await wait("location.pathname.endsWith('opic-mock-result.html')");
  await wait("document.querySelector('.kpi')");
  const grade = await p.eval("document.querySelector('.kpi .value').innerText");
  ok(["IH", "IM3", "IM2", "IM1"].includes(grade), "추정 등급: " + grade + " (평균 4점)");
  const omid = await p.eval("new URLSearchParams(location.search).get('mid')");
  ok(omid === "2", "오픽 모의고사 번호는 토익스피킹과 이어서 2: " + omid);
  await p.goto(ROOT + "opic-mock-result.html?mid=" + omid);
  await wait("document.querySelector('.kpi')");
  ok(await p.eval("document.querySelector('.kpi .value').innerText") === grade, "새로고침해도 같은 결과");
  await p.goto(ROOT + "opic.html");
  await wait("document.querySelector('#topics table')");
  ok((await p.eval("[...document.querySelectorAll('.kpi .value')].map(x => x.innerText).join('|')")).endsWith("|15"), "홈의 답변 기록 15");
  ok(await p.eval("document.body.innerText.includes('모의고사 기록')"), "모의고사 기록 표");
  await p.goto(ROOT + "opic-practice.html?topic=movie&n=3&text=0");
  await wait("document.getElementById('start-btn')");
  for (let u = 0; u < 3; u++) {                           // 오픽 연습은 문제 하나(단계 1개)가 끝날 때마다 채점
    await playToReview();
    ok(await p.eval("document.querySelectorAll('.spk-review').length") === 1, `주제별 연습 ${u + 1}/3 문항 채점 화면`);
    await gradeAll(2);                                    // 2점 → 약한 문항
  }
  await wait("document.getElementById('practice-done')");
  await p.goto(ROOT + "opic.html");
  await wait("document.querySelector('#topics table')");
  ok(await p.eval("document.body.innerText.includes('약한 문항 3개 다시')"), "3점 이하는 약한 문항 3개로 모인다");
  await p.goto(ROOT + "speaking-history.html?exam=opic");
  await wait("document.querySelector('details.reveal-d')");
  ok(await p.eval("document.querySelectorAll('details.reveal-d').length") === 18, "오픽 기록 18개");
  await p.goto(ROOT + "opic-practice.html?topic=nope");
  await wait("document.querySelector('.empty')");
  ok(await p.eval("document.body.innerText.includes('없는 주제')"), "잘못된 주제는 안내 화면");

  console.log("4) 백업에 말하기 기록 포함");
  const back = await p.eval("(() => { const d = TSStore.exportData(); return [d.ext.speaking_attempts.length, d.ext.speaking_mocks.length]; })()");
  ok(back[0] === 39 && back[1] === 2, "백업에 답변 " + back[0] + "개·모의고사 " + back[1] + "개");

  const errs = p.logs.filter(l => !/SpeechRecognition|speech|NotAllowed/i.test(l));
  console.log("콘솔 오류:", errs.join(" / ") || "(없음)");
  ok(errs.length === 0, "콘솔 오류 없음");
  console.log(`통과 ${pass} / 실패 ${fail}`);
  b.close();
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error("중단:", e.message); process.exit(1); });
