/* 영업관리 서비스 워커 — 자재관리와 같은 생각, 영업에 맞게 좁힘.
 *  - 정적 파일(CSS·JS·글꼴·그림)은 먼저 보관본으로 빠르게 (외근 중 느린 회선)
 *  - 화면(HTML)은 항상 서버에서 받는다 — 고객·매출 화면은 보관하지 않는다(개인정보·최신 숫자).
 *    연결이 끊겨 화면을 못 받으면 '연결 끊김' 안내 화면(/offline, 업무 데이터 없음)을 보여 준다.
 *    입력하던 내용은 app.js 가 브라우저에 보관하므로 연결이 돌아오면 다시 저장하면 된다.
 *  - 로그아웃·로그인 화면에서 보관본을 비운다(app.js 가 'clear' 메시지).
 */
const CACHE = "sales-static-v1";
const OFFLINE = "/offline";

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll([OFFLINE])).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("message", (event) => {
  if (event.data === "clear") {
    event.waitUntil(caches.delete(CACHE).then(() => caches.open(CACHE)).then((c) => c.add(OFFLINE)));
  }
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;                       // 저장(POST)은 건드리지 않는다
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(caches.open(CACHE).then((cache) => cache.match(req).then((hit) => {
      const fresh = fetch(req).then((res) => {
        if (res.ok) cache.put(req, res.clone());
        return res;
      }).catch(() => hit);
      return hit || fresh;
    })));
    return;
  }
  if (req.mode === "navigate") {
    event.respondWith(fetch(req).catch(() => caches.match(OFFLINE)));
  }
});
