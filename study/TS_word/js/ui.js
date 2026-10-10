/* 모든 페이지 공통: 위쪽 메뉴, 시험(토익/토플) 전환, 데이터 읽기, 도우미. */
(function () {
  "use strict";
  const PAGES = [
    ["home", "index.html", "홈"], ["study", "study.html", "📘 카드"], ["quiz", "quiz.html", "시험"],
    ["list", "list.html", "단어장"], ["listen", "listen.html", "🎧 듣기"], ["settings", "settings.html", "설정"],
  ];
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const $ = id => document.getElementById(id);
  const params = () => new URLSearchParams(location.search);
  const intParam = (name) => { const v = parseInt(params().get(name) || "", 10); return Number.isFinite(v) ? v : null; };

  function header(active) {
    const cur = Ward.currentSet();
    const tabs = ["toeic", "toefl"].map(k =>
      `<a href="#" data-set="${k}" class="${k === cur ? "on" : ""}" ${k === cur ? "aria-current=page" : ""}>${k === "toeic" ? "토익" : "토플"}</a>`).join("");
    const nav = PAGES.filter(p => p[0] !== "settings").map(([key, href, label]) =>
      `<a href="${href}" class="${key === active ? "on" : ""}${key === "study" ? " emph" : ""}" ${key === active ? "aria-current=page" : ""}>${label}</a>`).join("");
    $("hdr").innerHTML = `<header class="topbar"><div class="topbar-row">
        <a class="brand" href="index.html" aria-label="단어 홈"><span class="brand-mark">TS</span> <b>단어</b></a>
        <nav class="exam-tabs" aria-label="시험 전환">${tabs}</nav>
        <div class="crumb"></div>
        <a class="settings-link${active === "settings" ? " on" : ""}" href="settings.html">⚙ 설정</a>
      </div>
      <nav class="sub-nav nav" aria-label="메뉴">${nav}</nav></header>`;
    $("hdr").querySelector(".exam-tabs").addEventListener("click", e => {
      const a = e.target.closest("[data-set]");
      if (!a) return;
      e.preventDefault();
      if (a.dataset.set !== Ward.currentSet()) { Ward.setSetting({ set: a.dataset.set }); location.href = location.pathname.split("/").pop().split("?")[0] || "index.html"; }
    });
  }

  function gradeBadge(level) {
    const g = Ward.grades().find(x => x.level === level);
    return g ? `<span class="badge g${level}">${esc(g.name)}</span>` : "";
  }

  function fatal(msg) {
    $("app").innerHTML = `<div class="card empty"><h2>열 수 없습니다</h2><p>${esc(msg)}</p></div>`;
  }

  /* 페이지 시작: 데이터를 읽고 메뉴를 그린 뒤 start() 를 부른다 */
  async function boot(active, start) {
    if (/^https?:$/.test(location.protocol)) {              // 서버로 열었을 때만: 휴대폰 설치(manifest) + 오프라인(서비스 워커). file:// 에서는 브라우저가 막아 오류만 난다
      if (!document.querySelector("link[rel=manifest]")) {
        const l = document.createElement("link");
        l.rel = "manifest"; l.href = "manifest.webmanifest";
        document.head.appendChild(l);
      }
      if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => { /* 오프라인 저장 없이도 동작 */ });
    }
    try {
      await Ward.init();
      header(active);
      if (Ward.saveFailed()) $("app").insertAdjacentHTML("beforebegin", `<div class="flash error">브라우저 저장소에 쓸 수 없어 기록이 저장되지 않습니다.</div>`);
      start();
    } catch (e) {
      header(active);
      fatal(e.message);
    }
  }

  const tts = () => {
    const s = Ward.settings();
    return { rate: Number(s.tts_rate) || 1, accent: s.tts_accent === "mix" ? "us" : s.tts_accent };
  };
  const cleanWord = w => String(w).replace(/~/g, "");
  function sayWord(word) {
    const t = tts();
    TTS.play([{ text: cleanWord(word), gender: "female" }], { rate: t.rate, accent: t.accent });
  }
  function shuffle(a) { a = a.slice(); for (let k = a.length - 1; k > 0; k--) { const j = Math.floor(Math.random() * (k + 1)); [a[k], a[j]] = [a[j], a[k]]; } return a; }

  /* 등급·필수/도전 선택 막대 (쿼리 문자열로 이동) */
  function seg(items, current, hrefFor) {
    return `<div class="seg">${items.map(([val, label]) => `<a href="${hrefFor(val)}" class="${String(current ?? "") === String(val) ? "on" : ""}">${esc(label)}</a>`).join("")}</div>`;
  }
  function url(base, q) {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(q)) if (v !== null && v !== undefined && v !== "") p.set(k, v);
    const s = p.toString();
    return s ? `${base}?${s}` : base;
  }

  window.UI = { esc, $, params, intParam, boot, gradeBadge, sayWord, tts, shuffle, seg, url, cleanWord };
})();
