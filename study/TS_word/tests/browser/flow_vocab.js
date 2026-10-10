// 단어 화면(홈·시험·단어장·듣기·설정) 흐름 점검 — node tests/browser/flow_vocab.js
const { launch, sleep } = require("./cdp.js");
const os = require("os");
const ROOT = require("url").pathToFileURL(require("path").join(__dirname, "..", "..")).href + "/";
let ok = 0, bad = 0;
const check = (n, c) => { c ? ok++ : (bad++, console.log("FAIL", n)); };
(async () => {
  const b = await launch(9503, os.tmpdir() + "/tsw3");
  const p = await b.newPage();
  await p.goto(ROOT + "words.html"); await sleep(1000);
  check("home header", await p.eval(`document.body.innerText.includes('간격 반복') && document.body.innerText.includes('오늘 학습')`));
  await p.goto(ROOT + "list.html"); await sleep(1000);
  check("list tip column", await p.eval(`[...document.querySelectorAll('th')].some(t=>t.textContent==='팁')`));
  await p.goto(ROOT + "quiz.html?n=15"); await sleep(1000);
  await p.eval(`document.dispatchEvent(new KeyboardEvent('keydown',{key:'a'}))`); await sleep(200);
  check("quiz letter key", await p.eval(`document.querySelectorAll('#vq-choices .right,#vq-choices .wrong').length>0 && !!document.querySelector('#vq-explain .verdict')`));
  await p.goto(ROOT + "listen.html"); await sleep(1000);
  check("listen layout", await p.eval(`!!document.querySelector('.ls-controls') && !!document.getElementById('ls-rep-label')`));
  await p.goto(ROOT + "settings.html"); await sleep(1000);
  check("settings", await p.eval(`document.getElementById('daily_new').type==='number' && !!document.querySelector('a[href="opic-survey.html"]')`));
  await p.eval(`document.getElementById('test-voice').click()`); await sleep(800);
  check("voice info", await p.eval(`document.getElementById('voice-info').textContent.length>5`));
  check("no console errors", p.logs.length === 0);
  if (p.logs.length) console.log(p.logs);
  console.log(`통과 ${ok} 실패 ${bad}`);
  b.close(); process.exit(bad ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
