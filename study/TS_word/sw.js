/* 오프라인 사용: 한 번 열어 두면 인터넷이 없어도 단어 공부가 된다 (https 또는 localhost 에서만 동작).
   화면·스크립트·단어와 문제 데이터를 보관하고, 열 때마다 뒤에서 새 것으로 갱신한다(stale-while-revalidate).
   학습 기록은 localStorage 에 있어 이 파일과 상관없이 보존된다. */
const CACHE = "ts-word-v5";
const ASSETS = [
  "./", "index.html", "words.html", "study.html", "quiz.html", "list.html", "listen.html", "settings.html",
  "toeic.html", "practice.html", "diagnostic.html", "mock.html", "solve.html", "result.html", "review.html", "dictation.html",
  "stats.html", "history.html", "guide.html", "toefl.html", "toeic-speaking.html", "opic.html",
  "css/app.css", "css/ward.css",
  "js/theme.js", "js/tsutil.js", "js/scoring.js", "js/exams.js", "js/store.js", "js/tsstore.js", "js/tts.js", "js/ui.js", "js/bank.js", "js/stats.js",
  "js/planner.js", "js/sessions.js", "js/engine.js", "js/charts.js", "js/backup.js", "js/hub.js", "js/hub-extra.js", "js/soon.js",
  "js/home.js", "js/study.js", "js/quiz.js", "js/list.js", "js/listen.js", "js/settings.js",
  "js/toeic.js", "js/practice.js", "js/diagnostic.js", "js/mock.js", "js/solve.js", "js/result.js", "js/review.js",
  "js/dictation.js", "js/stats-page.js", "js/history.js", "js/guide.js",
  "vendor/chart.umd.min.js",
  "data/toeic.js", "data/toefl.js", "data/meta.js", "data/guide.js",
  "data/toeic-p1.js", "data/toeic-p2.js", "data/toeic-p3.js", "data/toeic-p4.js", "data/toeic-p5.js", "data/toeic-p6.js", "data/toeic-p7.js",
  "tsp-guide.html", "tsp-mock.html", "tsp-mock-result.html", "tsp-practice.html", "tsp-mock-run.html", "opic-survey.html", "opic-guide.html", "opic-mock.html", "opic-mock-result.html", "opic-practice.html", "opic-mock-run.html", "speaking-history.html",
  "js/spk-core.js", "js/spk-pages.js", "js/spk-run.js", "data/speaking-tsp.js", "data/speaking-opic.js",
  "manifest.webmanifest", "favicon.svg", "favicon.ico", "icon-192.png", "icon-512.png", "apple-touch-icon.png",
  "toefl-practice.html", "toefl-mock.html", "toefl-mock-run.html", "toefl-mock-result.html", "toefl-review.html", "toefl-history.html", "js/toefl-core.js", "js/toefl-speech.js", "js/toefl-home.js", "js/toefl-practice.js", "js/toefl-mock.js", "js/toefl-mock-run.js", "js/toefl-mock-result.js", "js/toefl-review.js", "js/toefl-history.js", "data/toefl-r_words.js", "data/toefl-r_daily.js", "data/toefl-r_academic.js", "data/toefl-l_response.js", "data/toefl-l_conversation.js", "data/toefl-l_talk.js", "data/toefl-s_repeat.js", "data/toefl-s_interview.js", "data/toefl-w_sentence.js", "data/toefl-w_email.js", "data/toefl-w_discussion.js",
];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET" || new URL(req.url).origin !== location.origin) return;
  e.respondWith(caches.open(CACHE).then(async cache => {
    const hit = await cache.match(req, { ignoreSearch: true });          // study.html?level=2 도 study.html 로 찾는다
    const fresh = fetch(req).then(res => { if (res && res.ok) cache.put(req, res.clone()); return res; }).catch(() => null);
    return hit || (await fresh) || new Response("오프라인입니다. 한 번 열어 둔 화면만 쓸 수 있습니다.", { status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" } });
  }));
});
