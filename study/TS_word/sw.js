/* 오프라인 사용: 한 번 열어 두면 인터넷이 없어도 단어 공부가 된다 (https 또는 localhost 에서만 동작).
   화면·스크립트·단어 데이터를 보관하고, 열 때마다 뒤에서 새 것으로 갱신한다(stale-while-revalidate).
   학습 기록은 localStorage 에 있어 이 파일과 상관없이 보존된다. */
const CACHE = "ts-word-v1";
const ASSETS = [
  "./", "index.html", "study.html", "quiz.html", "list.html", "listen.html", "settings.html",
  "css/app.css", "css/ward.css",
  "js/store.js", "js/tts.js", "js/ui.js", "js/home.js", "js/study.js", "js/quiz.js", "js/list.js", "js/listen.js", "js/settings.js",
  "data/toeic.js", "data/toefl.js",
  "manifest.webmanifest", "favicon.svg", "favicon.ico", "icon-192.png", "icon-512.png", "apple-touch-icon.png",
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
