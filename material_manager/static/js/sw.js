/* 오프라인용 서비스 워커 (/sw.js 로 제공). 입출고 화면과 화면 파일(css·js)만 다룬다.
   - 연결되어 있으면 항상 서버에서 받고(최신), 받은 입출고 화면을 이 브라우저에 보관한다.
   - 서버에 닿지 않으면 보관한 화면을 보여 준다 → 끊긴 동안 새로고침해도 입출고 화면에서 오프라인 입력을 계속할 수 있다.
   - 로그아웃·다른 사람 로그인 때 페이지가 'clear'를 보내 보관한 화면을 지운다(공용 PC).
   - 등록(POST)·다른 화면·다른 사이트 요청에는 관여하지 않는다.
   서비스 워커는 HTTPS(또는 이 PC의 localhost)에서만 동작한다. */
"use strict";

const CACHE = "mm-offline-v1";   // 이름을 바꾸면 예전 보관본은 쓰지 않는다

self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (e) { e.waitUntil(self.clients.claim()); });

self.addEventListener("message", function (e) {
  if (e.data === "clear") e.waitUntil(caches.delete(CACHE));
});

function offlinePage() {
  return new Response(
    "<!doctype html><meta charset=utf-8><title>오프라인</title><body style='font-family:sans-serif;padding:2rem'>" +
    "<h1>서버에 연결할 수 없습니다</h1><p>이 화면은 아직 이 브라우저에 보관되지 않았습니다. " +
    "연결이 돌아오면 다시 열어 주세요. 입출고 화면은 한 번 열어 두면 끊긴 동안에도 열립니다.</p>",
    { status: 503, headers: { "Content-Type": "text/html; charset=utf-8" } });
}

self.addEventListener("fetch", function (e) {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  const isStatic = url.pathname.startsWith("/static/");
  // 메뉴·탭으로 연 화면(app.js 가 fetch 로 받아 본문만 바꿈, X-Requested-With: fetch)도 같은 주소로 보관한다
  const swapped = req.headers.get("X-Requested-With") === "fetch";
  const isPage = (req.mode === "navigate" || swapped) && url.pathname.startsWith("/transactions/");
  if (!isStatic && !isPage) return;

  e.respondWith(fetch(req).then(function (res) {
    // 로그인 화면으로 돌려보낸 응답(세션 만료)은 보관하지 않는다. 보관에 실패해도 화면은 그대로 보여 준다.
    if (res.ok && !res.redirected) {
      const copy = res.clone();
      caches.open(CACHE).then(function (c) { return c.put(req, copy); }).catch(function () {});
    }
    return res;
  }, function () {
    return caches.open(CACHE).then(function (c) {
      return c.match(req).then(function (hit) {
        if (hit) return hit;
        if (!isPage) return Response.error();
        return c.match(req, { ignoreSearch: true }).then(function (any) { return any || offlinePage(); });
      });
    }).catch(function () {                       // 브라우저 저장소를 쓸 수 없을 때도 빈 오류 화면 대신 안내
      return isPage ? offlinePage() : Response.error();
    });
  }));
});
