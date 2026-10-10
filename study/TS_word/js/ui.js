/* 모든 페이지 공통 (window.UI): 위쪽 메뉴(시험 4개 + 시험별 메뉴), 데이터 읽기, 도우미.

   페이지 시작:
     UI.boot({ exam: "toeic", page: "practice" }, async () => { ... UI.$("app").innerHTML = ...; });
       exam  : "toeic" | "toefl" | "toeic-speaking" | "opic" | null(허브·설정처럼 어느 시험도 아님)
       page  : 위쪽 메뉴 강조용 이름 (TSNav.MENUS 의 pages). 풀이 화면은 "solve:practice" 처럼 세션 종류를 붙인다.
     예전 단어 화면은 UI.boot("study", fn) 처럼 문자열도 받는다 (home/study/quiz/list/listen = 단어 화면, settings = 설정).
   boot 는 (1) 토익/토플 단어 세트를 시험에 맞추고 (2) 단어 데이터를 읽고 (3) 메뉴를 그린 뒤 (4) start() 를 부른다.
   start 가 async 여도 되고, 던진 오류는 화면에 "열 수 없습니다" 로 보인다. */
(function () {
  "use strict";
  const { EXAMS, MENUS, EXAM_KEYS, WORD_PAGES, matches } = TSNav;
  const esc = TSU.esc;
  const $ = id => document.getElementById(id);
  const params = () => new URLSearchParams(location.search);
  const intParam = name => { const v = parseInt(params().get(name) || "", 10); return Number.isFinite(v) ? v : null; };

  const WORD_NAV = [["words", "words.html", "단어 홈"], ["study", "study.html", "📘 카드"], ["quiz", "quiz.html", "시험"],
                    ["list", "list.html", "단어장"], ["listen", "listen.html", "🎧 듣기"]];

  function normalize(arg) {
    if (arg && typeof arg === "object") return { exam: arg.exam ?? null, page: arg.page || "", word: WORD_PAGES.includes(arg.page) };
    const a = String(arg || "");
    if (a === "settings") return { exam: null, page: "settings", word: false };
    const key = a === "home" ? "words" : a;                                  // 예전 이름: 단어 홈 = home
    return { exam: Ward.currentSet(), page: key, word: true };
  }

  function header(o) {
    const tabs = EXAM_KEYS.map(k => {
      const e = EXAMS[k];
      return `<a href="${e.href}"${e.set ? ` data-set="${e.set}"` : ""} class="${k === o.exam ? "on" : ""}"${k === o.exam ? " aria-current=page" : ""}>${esc(e.name)}</a>`;
    }).join("");
    const items = (MENUS[o.exam] || []).map(it => {
      if (it.ready === false) return `<span class="soon" title="준비 중입니다">${esc(it.label)} <small>준비 중</small></span>`;
      const on = matches(it, o.page) && (!it.set || it.set === Ward.currentSet() || !o.word);
      return `<a href="${it.href}"${it.set ? ` data-set="${it.set}"` : ""} class="${on ? "on" : ""}${it.emph ? " emph" : ""}"${on ? " aria-current=page" : ""}>${it.emph ? "📘 " : ""}${esc(it.label)}</a>`;
    }).join("");
    const subNav = o.exam ? `<nav class="sub-nav nav" aria-label="${esc(EXAMS[o.exam].name)} 메뉴" id="sub-nav">${items}</nav>` : "";
    let wordNav = "";
    if (o.word) {
      const cur = Ward.currentSet();
      const links = WORD_NAV.map(([key, href, label]) => `<a href="${href}" class="${key === o.page ? "on" : ""}"${key === o.page ? " aria-current=page" : ""}>${label}</a>`).join("");
      const sets = ["toeic", "toefl"].map(k => `<a href="#" data-wordset="${k}" class="${k === cur ? "on" : ""}">${k === "toeic" ? "토익 단어" : "토플 학술 어휘"}</a>`).join("");
      wordNav = `<nav class="word-nav nav" aria-label="단어 메뉴">${links}<span class="spacer"></span>${sets}</nav>`;
    }
    $("hdr").innerHTML = `<header class="topbar"><div class="topbar-row">
        <a class="brand" href="index.html" aria-label="메인 화면"><span class="brand-mark">TS</span></a>
        <nav class="exam-tabs" aria-label="시험 바로가기">${tabs}</nav>
        <div class="crumb">${o.page === "settings" ? "<b>설정</b>" : ""}</div>
        <button type="button" class="theme-btn" id="theme-btn" aria-label="화면 색상 바꾸기"></button>
        <a class="settings-link${o.page === "settings" ? " on" : ""}" href="settings.html" style="margin-left:0">⚙ 설정</a>
      </div>${subNav}${wordNav}</header>`;
    const tb = $("theme-btn");
    if (tb && window.Theme) {
      const LABEL = { auto: "🌗 자동", dark: "🌙 다크", light: "☀️ 라이트" };
      const show = () => { tb.textContent = LABEL[Theme.get()]; };
      show();
      tb.addEventListener("click", () => { Theme.cycle(); show(); });
    }
    $("hdr").addEventListener("click", e => {
      const ws = e.target.closest("[data-wordset]");
      if (ws) {                                                // 단어 화면에서 토익 단어 ↔ 토플 학술 어휘 바꾸기
        e.preventDefault();
        if (ws.dataset.wordset !== Ward.currentSet()) { Ward.setSetting({ set: ws.dataset.wordset }); location.href = location.pathname.split("/").pop().split("?")[0] || "words.html"; }
        return;
      }
      const a = e.target.closest("a[data-set]");
      if (a) Ward.setSetting({ set: a.dataset.set });          // 시험 탭·단어 메뉴를 누르면 단어 세트도 그 시험으로
    });
    topbarVar();
    window.addEventListener("resize", topbarVar);
    const on = document.querySelector("#sub-nav a.on");        // 좁은 화면에서 지금 항목이 보이게
    if (on) { const n = on.parentElement; n.scrollLeft = on.offsetLeft - (n.clientWidth - on.offsetWidth) / 2; }
  }
  /** 머리글 높이를 CSS 변수로 — 풀이 화면 상단 바가 그 아래에 붙도록 */
  function topbarVar() {
    const h = document.querySelector(".topbar");
    if (h) document.documentElement.style.setProperty("--topbar-h", (getComputedStyle(h).position === "sticky" ? h.offsetHeight : 0) + "px");
  }

  /** 세부 메뉴 줄: '읽기·듣기…'처럼 같은 화면의 구역(#id)으로 가는 항목이면, 지금 보고 있는 구역을 강조한다.
      boot 가 start() 가 끝난 뒤 자동으로 부른다 (구역이 화면에 그려진 뒤여야 하므로). */
  function subNavSpy() {
    const subLinks = [...document.querySelectorAll("#sub-nav a")];
    const here = subLinks.filter(a => new URL(a.href, location.href).pathname === location.pathname);
    const spots = here.map(a => [a, document.getElementById(new URL(a.href, location.href).hash.slice(1))]).filter(x => x[1]);
    if (!spots.length) return;
    const base = here.find(a => !new URL(a.href, location.href).hash) || null;
    const mark = a => subLinks.forEach(x => {
      x.classList.toggle("on", x === a);
      if (x === a) x.setAttribute("aria-current", "page"); else x.removeAttribute("aria-current");
    });
    let pinned = 0;                                  // 방금 누른 항목은 바로 이어지는 자동 스크롤 동안 그대로 둔다
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

  const gradeBadge = level => {                    // 지금 단어 세트의 등급 이름 (토익이면 Orange…Gold, 토플이면 밴드)
    const g = Ward.grades().find(x => x.level === level);
    return g ? `<span class="badge g${level}">${esc(g.name)}</span>` : "";
  };
  /** 토익 등급 배지 (Orange~Gold). 숫자 등급 또는 Score.GRADES 항목. 없으면 '미측정' */
  function toeicBadge(levelOrGrade, suffix = "") {
    const g = levelOrGrade && typeof levelOrGrade === "object" ? levelOrGrade : Score.GRADE_BY_LEVEL[levelOrGrade];
    return g ? `<span class="badge g${g.level}">${esc(g.name)}${esc(suffix)}</span>` : `<span class="badge badge-none">미측정</span>`;
  }

  function fatal(msg) {
    $("app").innerHTML = `<div class="card empty"><h2>열 수 없습니다</h2><p>${esc(msg)}</p></div>`;
  }
  /** 화면 위쪽 알림 띠. kind: "ok" | "error" */
  function flash(msg, kind = "ok") {
    const d = document.createElement("div");
    d.className = `flash ${kind}`;
    d.textContent = msg;
    $("app").before(d);
    return d;
  }

  /* 페이지 시작: 단어 세트를 맞추고 데이터를 읽고 메뉴를 그린 뒤 start() 를 부른다 */
  async function boot(arg, start) {
    if (/^https?:$/.test(location.protocol)) {              // 서버로 열었을 때만: 휴대폰 설치(manifest) + 오프라인(서비스 워커). file:// 에서는 브라우저가 막아 오류만 난다
      if (!document.querySelector("link[rel=manifest]")) {
        const l = document.createElement("link");
        l.rel = "manifest"; l.href = "manifest.webmanifest";
        document.head.appendChild(l);
      }
      if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => { /* 오프라인 저장 없이도 동작 */ });
    }
    let o = normalize(arg);
    try {
      const want = o.exam && EXAMS[o.exam] && EXAMS[o.exam].set;
      if (want && Ward.currentSet() !== want && !o.word) Ward.setSetting({ set: want });
      await Ward.init();
      header(o);
      if (Ward.saveFailed() || TSStore.saveFailed()) $("app").insertAdjacentHTML("beforebegin", `<div class="flash error">브라우저 저장소에 쓸 수 없어 기록이 저장되지 않습니다.</div>`);
      await start();
      subNavSpy();
    } catch (e) {
      if (!$("hdr").innerHTML) header(o);
      fatal(e.message);
    }
  }

  const tts = () => {
    const s = Ward.settings();
    return { rate: Number(s.tts_rate) || 1, accent: s.tts_accent === "mix" ? "us" : s.tts_accent };
  };
  /** 시험 풀이용 음성 설정 (억양 mix 는 문제마다 TTS.accentFor 가 고른다) */
  const ttsRaw = () => { const s = Ward.settings(); return { rate: Number(s.tts_rate) || 1, accent: s.tts_accent || "mix" }; };
  const cleanWord = w => String(w).replace(/~/g, "");
  function sayWord(word) {
    const t = tts();
    TTS.play([{ text: cleanWord(word), gender: "female" }], { rate: t.rate, accent: t.accent });
  }
  const shuffle = a => TSU.shuffle(a.slice());

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
  /** 화면 이동 (뒤로 가기에 남기지 않음 — 세션을 만드는 주소가 다시 열려 같은 세션이 또 만들어지지 않게) */
  const go = href => location.replace(href);

  document.addEventListener("click", e => {                      // <button data-confirm="정말요?"> 확인 창
    const b = e.target.closest("[data-confirm]");
    if (b && !b.hasAttribute("data-start") && !confirm(b.dataset.confirm)) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);

  window.UI = { esc, $, params, intParam, boot, gradeBadge, toeicBadge, sayWord, tts, ttsRaw, shuffle, seg, url, cleanWord, flash, go,
                pct: TSU.pct, fmtTime: TSU.fmtTime, mmss: TSU.mmss };
})();
