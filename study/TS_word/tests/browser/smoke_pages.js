const { launch, sleep } = require("./cdp.js");
const os = require("os");
const ROOT = require("url").pathToFileURL(require("path").join(__dirname, "..", "..")).href + "/";
(async () => {
  const b = await launch(9501, os.tmpdir() + "/tsw1");
  const p = await b.newPage();
  const pages = process.argv.slice(2).length ? process.argv.slice(2) : ["index.html", "words.html", "toeic.html", "practice.html", "diagnostic.html", "mock.html", "review.html", "dictation.html", "stats.html", "history.html", "guide.html", "settings.html", "toefl.html", "toefl-mock.html", "toefl-review.html", "toefl-history.html", "toefl-practice.html?task=s_interview&level=3", "toeic-speaking.html", "opic.html", "study.html", "quiz.html", "list.html", "listen.html"];
  for (const pg of pages) {
    p.logs.length = 0;
    await p.goto(ROOT + pg);
    await sleep(1200);
    const info = await p.eval(`({ app: document.getElementById('app').innerText.slice(0, 120).replace(/\\n/g,' | '), nav: [...document.querySelectorAll('#sub-nav a.on, .exam-tabs a.on')].map(a=>a.textContent).join(',') })`);
    console.log(pg.padEnd(22), JSON.stringify(info), p.logs.length ? "\n   !! " + p.logs.join("\n   !! ") : "");
  }
  b.close();
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
