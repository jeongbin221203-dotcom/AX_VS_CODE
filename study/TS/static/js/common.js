/* 공통 도우미: CSRF 포함 POST, HTML 이스케이프, 페이지 데이터 읽기 */
(function () {
  "use strict";
  const token = () => document.querySelector('meta[name="csrf-token"]').content;

  const vb = document.querySelector('meta[name="vocab-base"]');
  window.TS = {
    vbase: vb ? vb.content : "",          // 토플 단어 화면이면 "/toefl"
    async post(url, body) {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": token() },
        body: JSON.stringify(body || {}),
      });
      let data = {};
      try { data = await res.json(); } catch (e) { /* 본문 없음 */ }
      if (!res.ok) throw new Error(data.error || `요청 실패 (${res.status})`);
      return data;
    },
    esc(s) {
      return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    },
    data(id) {
      const el = document.getElementById(id);
      return el ? JSON.parse(el.textContent) : null;
    },
    fmtTime(sec) {
      sec = Math.max(0, Math.round(sec));
      const m = Math.floor(sec / 60), s = sec % 60;
      return `${m}:${String(s).padStart(2, "0")}`;
    },
  };

  // 머리글(두 줄) 높이를 CSS 변수로 — 풀이 화면 상단 바가 그 아래에 붙도록
  const setTopbar = () => {
    const h = document.querySelector(".topbar");
    if (h) document.documentElement.style.setProperty("--topbar-h", (getComputedStyle(h).position === "sticky" ? h.offsetHeight : 0) + "px");
  };
  setTopbar();
  window.addEventListener("resize", setTopbar);

  // 세부 메뉴 줄: '읽기·듣기…'처럼 같은 화면의 구역으로 가는 항목이면, 지금 보고 있는 구역을 강조한다
  const subLinks = [...document.querySelectorAll("#sub-nav a")];
  const here = subLinks.filter(a => new URL(a.href, location.href).pathname === location.pathname);
  const spots = here.map(a => [a, document.getElementById(new URL(a.href, location.href).hash.slice(1))]).filter(x => x[1]);
  if (spots.length) {
    const base = here.find(a => !new URL(a.href, location.href).hash) || null;
    const mark = a => subLinks.forEach(x => {
      x.classList.toggle("on", x === a);
      if (x === a) x.setAttribute("aria-current", "page"); else x.removeAttribute("aria-current");
    });
    let pinned = 0;                       // 방금 누른 항목은 바로 이어지는 자동 스크롤 동안 그대로 둔다
    spots.forEach(([a]) => a.addEventListener("click", () => { mark(a); pinned = Date.now() + 900; }));
    const spy = () => {
      if (Date.now() < pinned) return;
      const line = (document.querySelector(".topbar")?.offsetHeight || 0) + 24;
      let cur = base;
      for (const [a, el] of spots) if (el.getBoundingClientRect().top <= line) cur = a;
      if (innerHeight + scrollY >= document.documentElement.scrollHeight - 4 && scrollY > 0) cur = spots[spots.length - 1][0];
      if (cur) mark(cur);
    };
    addEventListener("scroll", spy, { passive: true });
    addEventListener("hashchange", spy);
    spy();
  }
  // 세부 메뉴 줄: 휴대폰처럼 좁으면 지금 항목이 보이게 가로로 밀어 둔다
  const subOn = document.querySelector("#sub-nav a.on");
  if (subOn) { const n = subOn.parentElement; n.scrollLeft = subOn.offsetLeft - (n.clientWidth - subOn.offsetWidth) / 2; }

  // 상단 시험 메뉴: 하나만 펼치고, 바깥을 누르거나 Esc 로 닫는다
  const menus = () => document.querySelectorAll(".exam-menu");
  document.addEventListener("toggle", e => {
    if (e.target.matches?.(".exam-menu") && e.target.open) menus().forEach(m => { if (m !== e.target) m.open = false; });
  }, true);
  document.addEventListener("click", e => {
    if (!e.target.closest(".exam-menu")) menus().forEach(m => { m.open = false; });
  });
  document.addEventListener("keydown", e => { if (e.key === "Escape") menus().forEach(m => { m.open = false; }); });

  // 자동 제출 select (인라인 이벤트 대신 data 속성)
  document.addEventListener("change", e => {
    if (e.target.matches("[data-autosubmit]")) e.target.form.submit();
  });
  // 확인 필요한 버튼
  document.addEventListener("click", e => {
    const b = e.target.closest("[data-confirm]");
    if (b && !confirm(b.dataset.confirm)) e.preventDefault();
  });
})();
