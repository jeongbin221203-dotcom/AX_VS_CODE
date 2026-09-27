/* 공통 도우미: CSRF 포함 POST, HTML 이스케이프, 페이지 데이터 읽기 */
(function () {
  "use strict";
  const token = () => document.querySelector('meta[name="csrf-token"]').content;

  window.TS = {
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
