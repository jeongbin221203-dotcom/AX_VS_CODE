/* 화면 보조 스크립트: 차트만 그린다.
   스크립트(또는 CDN)가 없어도 조회·등록은 모두 서버에서 동작한다. */
(function () {
  "use strict";

  // 색은 계열 이름으로 고정: 입고 청색, 출고 적색, 금액 청색
  const BLUE = "#2747A3", RED = "#C23A2E", GRAY = "#8A94A3";
  const SERIES_COLOR = { "입고": BLUE, "출고": RED, "재고금액": BLUE, "입고금액": BLUE, "출고금액": RED, "조정금액": GRAY };
  const FALLBACK = [BLUE, RED, GRAY];

  // 금액 축은 값이 크면 백만원 단위로 표기한다
  function moneyUnit(datasets) {
    const max = Math.max.apply(null, datasets.flatMap(function (d) { return d.data.map(Math.abs); }).concat([0]));
    return max >= 1e6 ? { div: 1e6, title: "백만원" } : { div: 1, title: "원" };
  }

  function drawCharts(root) {
    if (!window.Chart) return;
    const css = getComputedStyle(document.documentElement);
    const text = css.getPropertyValue("--muted").trim();
    const line = css.getPropertyValue("--line").trim();
    Chart.defaults.font.family = css.getPropertyValue("--font").trim();
    Chart.defaults.font.size = 12;

    (root || document).querySelectorAll("canvas[data-chart]").forEach(function (el) {
      const cfg = JSON.parse(el.dataset.chart);
      const unit = cfg.money ? moneyUnit(cfg.datasets) : null;
      new Chart(el, {
        type: cfg.type,
        data: {
          labels: cfg.labels,
          datasets: cfg.datasets.map(function (d, i) {
            const color = SERIES_COLOR[d.label] || FALLBACK[i % FALLBACK.length];
            return Object.assign({}, d, {
              backgroundColor: color + "D9", borderColor: color, borderWidth: 0, borderRadius: 3,
              maxBarThickness: 36, categoryPercentage: .7, barPercentage: .9,
            });
          }),
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: {
            subtitle: { display: !!unit, text: unit ? "단위: " + unit.title : "", align: "start",
                        color: text, font: { size: 11 }, padding: { bottom: 12 } },
            legend: {
              display: cfg.datasets.length > 1, position: "top", align: "end",
              labels: { color: text, boxWidth: 12, boxHeight: 12, usePointStyle: true, pointStyle: "rectRounded" },
            },
            tooltip: { callbacks: { label: function (c) {
              const v = Number(c.parsed.y).toLocaleString();
              return " " + c.dataset.label + ": " + v + (cfg.money ? "원" : "");
            } } },
          },
          scales: {
            x: { ticks: { color: text, maxRotation: 0, autoSkip: true }, grid: { display: false } },
            y: {
              beginAtZero: true, grid: { color: line }, border: { display: false },
              ticks: { color: text, maxTicksLimit: 6, callback: function (v) {
                return (unit ? v / unit.div : v).toLocaleString(undefined, { maximumFractionDigits: 1 });
              } },
            },
          },
        },
      });
    });
  }

  // 선택을 바꾸면 바로 조회 (인라인 onchange 대신 — CSP로 인라인 스크립트를 막기 위해)
  document.addEventListener("change", function (e) {
    if (!e.target.matches("[data-autosubmit]")) return;
    // 서버에 닿지 않을 때 입출고 화면의 자재·창고 선택은 화면을 다시 받지 않고 입력 폼 값만 바꾼다(오프라인 입력)
    if (window.mmNet && window.mmNet.down && e.target.form.hasAttribute("data-offline-select")) {
      window.mmNet.switchSelect(e.target);
      return;
    }
    e.target.form.submit();
  });

  // 차트 높이 (인라인 style 대신 data-height)
  function sizeCharts(root) {
    root.querySelectorAll(".chart[data-height]").forEach(function (el) {
      el.style.height = el.dataset.height + "px";
    });
  }
  sizeCharts(document);

  window.addEventListener("load", function () { drawCharts(document); });
  // 본문만 바꿔 끼웠을 때(아래 화면 전환) 새 본문에 다시 적용
  window.mmUI = { refresh: function (root) { sizeCharts(root); drawCharts(root); } };
})();

/* 입력 보호 · 오프라인 입력.
   - 두 번 제출 막기 (서버도 같은 제출을 두 번 처리하지 않는다 — core/once.py)
   - 서버 연결 끊김 알림, 등록하지 못한 입력을 이 브라우저에 임시 저장 → '입력 되살리기'
   - 오프라인 입력: 입출고·이동 화면(data-offline)은 끊긴 동안 등록을 누르면 이 브라우저 대기열에 쌓고,
     연결되면 입력한 순서대로 한 건씩 서버(/transactions/queue)로 보낸다. 서버가 재고·권한·마감을 다시 확인해
     통과한 것만 반영하고, 거부된 건은 사유와 함께 남겨 사용자가 확인한다. 같은 입력은 한 번만 반영된다.
   - 임시 저장·대기열은 로그인한 사용자별로 나눈다. 다른 사람 것은 보이지도, 보내지지도 않는다.
     로그아웃하면 그 사람의 임시 저장과 보관한 화면을 지운다(대기열은 보내기 전이라 남기고 경고한다).
   - 비밀번호·파일은 저장하지 않는다. */
(function () {
  "use strict";

  const USER = document.body.dataset.user || "";
  const DRAFT = "mm-draft:", PENDING = "mm-pending", QUEUE = "mm-queue", LOCK = "mm-queue-lock", DRAFT_DAYS = 3;
  const SKIP = { _csrf: 1, _once: 1 };
  const net = window.mmNet = { down: false };

  // ── 브라우저 저장소 (막혀 있어도 화면은 동작) ──
  function store(kind) { try { return window[kind]; } catch (e) { return null; } }
  function get(kind, k) { try { return store(kind) ? store(kind).getItem(k) : null; } catch (e) { return null; } }
  function put(kind, k, v) { try { if (store(kind)) store(kind).setItem(k, v); return true; } catch (e) { return false; } }
  function drop(kind, k) { try { if (store(kind)) store(kind).removeItem(k); } catch (e) { /* 무시 */ } }
  function keys(prefix) {
    const out = [];
    try { for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i); if (k.indexOf(prefix) === 0) out.push(k); } }
    catch (e) { /* 무시 */ }
    return out;
  }

  function isPost(form) { return (form.getAttribute("method") || "").toLowerCase() === "post"; }
  function draftable(form) {
    return USER && isPost(form) && !form.hasAttribute("data-nodraft") && !form.hasAttribute("data-logout")
      && !form.querySelector("input[type=password]");
  }
  function draftKey(form) {
    return DRAFT + USER + ":" + location.pathname + location.search + "#" + (form.getAttribute("action") || "");
  }

  function collect(form, withHidden) {
    const out = {};
    Array.prototype.forEach.call(form.elements, function (el) {
      if (!el.name || SKIP[el.name] || el.disabled) return;
      const t = (el.type || "").toLowerCase();
      if (["file", "password", "submit", "button", "reset"].indexOf(t) >= 0) return;
      if (t === "hidden" && !withHidden) return;
      if (t === "checkbox" || t === "radio") {
        if (el.checked) (out[el.name] = out[el.name] || []).push(el.value);
        return;
      }
      if (el.multiple) {
        out[el.name] = Array.prototype.filter.call(el.options, function (o) { return o.selected; })
          .map(function (o) { return o.value; });
        return;
      }
      out[el.name] = el.value;
    });
    return out;
  }
  function same(a, b) { return JSON.stringify(a) === JSON.stringify(b); }

  function saveDraft(form) {
    if (!draftable(form)) return;
    const now = collect(form);
    if (same(now, form._mmBase)) { drop("localStorage", draftKey(form)); return; }
    put("localStorage", draftKey(form), JSON.stringify({ at: Date.now(), values: now }));
  }

  function restore(form, values) {
    Array.prototype.forEach.call(form.elements, function (el) {
      if (!el.name || !(el.name in values)) return;
      const v = values[el.name], t = (el.type || "").toLowerCase();
      if (t === "checkbox" || t === "radio") el.checked = Array.isArray(v) && v.indexOf(el.value) >= 0;
      else if (el.multiple) Array.prototype.forEach.call(el.options, function (o) { o.selected = v.indexOf(o.value) >= 0; });
      else if (["hidden", "file", "password"].indexOf(t) < 0) el.value = v;
    });
  }

  function button(label, onClick, cls) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn btn-sm" + (cls ? " " + cls : "");
    b.textContent = label;
    b.addEventListener("click", onClick);
    return b;
  }

  function offerDraft(form) {
    const raw = get("localStorage", draftKey(form));
    if (!raw) return;
    let d;
    try { d = JSON.parse(raw); } catch (e) { drop("localStorage", draftKey(form)); return; }
    if (!d || !d.values || Date.now() - d.at > DRAFT_DAYS * 864e5) { drop("localStorage", draftKey(form)); return; }
    if (same(d.values, collect(form))) return;                  // 화면에 이미 같은 값이 있다(서버가 다시 채워 줌)
    const bar = document.createElement("div");
    bar.className = "alert alert-warning draft-bar";
    bar.appendChild(document.createTextNode(
      "이 화면에 등록하지 못한 입력이 있습니다 (" + new Date(d.at).toLocaleString() + "). 아직 저장되지 않았습니다. "));
    bar.appendChild(button("입력 되살리기", function () { restore(form, d.values); bar.remove(); }));
    bar.appendChild(button("버리기", function () { drop("localStorage", draftKey(form)); bar.remove(); }));
    form.parentNode.insertBefore(bar, form);
  }

  // 오래된 임시 저장 정리 (모든 사용자 것 — 공용 PC에 오래 남지 않게)
  keys(DRAFT).forEach(function (k) {
    try { const d = JSON.parse(get("localStorage", k)); if (!d || Date.now() - d.at > DRAFT_DAYS * 864e5) drop("localStorage", k); }
    catch (e) { drop("localStorage", k); }
  });

  // ── 오프라인 대기열 ──
  function loadQueue() { try { return JSON.parse(get("localStorage", QUEUE) || "[]"); } catch (e) { return []; } }
  function saveQueue(q) { return put("localStorage", QUEUE, JSON.stringify(q)); }
  function mine(q) { return q.filter(function (x) { return x.user === USER; }); }
  function token() {
    const b = new Uint8Array(16);
    (window.crypto || window.msCrypto).getRandomValues(b);
    return Array.prototype.map.call(b, function (x) { return ("0" + x.toString(16)).slice(-2); }).join("");
  }
  function csrf() { const el = document.querySelector("input[name=_csrf]"); return el ? el.value : ""; }

  function enqueue(form) {
    const fields = collect(form, true);
    const entry = { id: token(), user: USER, kind: form.dataset.offline, fields: fields,
                    label: form.dataset.label || "", qty: fields.qty || "", at: new Date().toISOString(),
                    status: "waiting", error: "" };
    const q = loadQueue();
    q.push(entry);
    if (!saveQueue(q)) { alert("브라우저 저장 공간이 부족해 오프라인 입력을 저장하지 못했습니다."); return false; }
    // 다음 입력을 위해 수량·문서번호 등만 비운다 (자재·창고·일자는 그대로)
    ["qty", "ref_no", "partner", "note", "lot_no", "expiry_date", "po_no", "po_item", "cost_center"].forEach(function (n) {
      const el = form.elements[n];
      if (el && el.tagName === "INPUT") el.value = "";
    });
    drop("localStorage", draftKey(form));
    form._mmBase = collect(form);
    renderQueue();
    return true;
  }

  let panel = null;
  function renderQueue() {
    const q = loadQueue();
    const own = mine(q), others = q.length - own.length;
    if (!USER || (!own.length && !others)) { if (panel) { panel.remove(); panel = null; } return; }
    if (!panel) {
      panel = document.createElement("div");
      panel.className = "alert alert-warning queue-panel";
      const host = document.querySelector("main.content") || document.body;
      const h1 = host.querySelector("h1");
      host.insertBefore(panel, h1 ? h1.nextSibling : host.firstChild);
    }
    panel.textContent = "";
    const waiting = own.filter(function (x) { return x.status === "waiting"; }).length;
    const rejected = own.filter(function (x) { return x.status === "rejected"; }).length;
    const head = document.createElement("div");
    head.appendChild(document.createTextNode(
      "오프라인 입력 — 보낼 것 " + waiting + "건" + (rejected ? " · 거부됨 " + rejected + "건(확인 필요)" : "") +
      (others ? " · 다른 사용자 입력 " + others + "건(그 사용자가 로그인하면 보냄)" : "") + " "));
    if (waiting) head.appendChild(button("지금 보내기", function () { flush(true); }, "btn-primary"));
    panel.appendChild(head);
    if (!own.length) return;
    const list = document.createElement("ul");
    list.className = "queue-list";
    own.forEach(function (x) {
      const li = document.createElement("li");
      li.appendChild(document.createTextNode(
        new Date(x.at).toLocaleString() + " · " + x.label + " · 수량 " + x.qty + " · " +
        (x.status === "waiting" ? "보낼 차례" : "거부됨: " + x.error)));
      li.appendChild(button("삭제", function () {
        if (x.status === "waiting" && !confirm("아직 보내지 않은 입력입니다. 삭제하면 반영되지 않습니다. 삭제할까요?")) return;
        saveQueue(loadQueue().filter(function (y) { return y.id !== x.id; }));
        renderQueue();
      }));
      if (x.status === "rejected") li.className = "rejected";
      list.appendChild(li);
    });
    panel.appendChild(list);
  }

  let sending = false;
  function takeLock() {
    const now = Date.now(), held = Number(get("localStorage", LOCK) || 0);
    if (held && now - held < 30000) return false;               // 다른 탭이 보내는 중
    put("localStorage", LOCK, String(now));
    return true;
  }
  function flush(manual) {
    if (sending || !USER || !csrf()) return Promise.resolve();
    if (!mine(loadQueue()).some(function (x) { return x.status === "waiting"; })) return Promise.resolve();
    if (!takeLock()) return Promise.resolve();
    sending = true;
    let note = "";
    function next() {
      const x = mine(loadQueue()).find(function (y) { return y.status === "waiting"; });
      if (!x) return Promise.resolve();
      const body = new FormData();
      Object.keys(x.fields).forEach(function (k) {
        const v = x.fields[k];
        (Array.isArray(v) ? v : [v]).forEach(function (one) { body.append(k, one); });
      });
      body.append("kind", x.kind);
      body.append("captured_at", x.at);
      body.append("_csrf", csrf());
      body.append("_once", x.id);
      put("localStorage", LOCK, String(Date.now()));
      return fetch("/transactions/queue", { method: "POST", body: body, credentials: "same-origin",
                                            headers: { "X-MM-Queue": "1" } })
        .then(function (res) {
          if (res.redirected && res.url.indexOf("/login") >= 0) { note = "다시 로그인하면 보냅니다."; return "stop"; }
          const type = res.headers.get("Content-Type") || "";
          if (type.indexOf("json") < 0) { note = "보내지 못했습니다(HTTP " + res.status + "). 화면을 새로고침한 뒤 다시 보내세요."; return "stop"; }
          return res.json().then(function (r) {
            const q = loadQueue(), i = q.findIndex(function (y) { return y.id === x.id; });
            if (r.retry) { note = r.message; return "stop"; }
            if (i >= 0) {
              if (r.ok) q.splice(i, 1);
              else { q[i].status = "rejected"; q[i].error = r.message; }
              saveQueue(q);
            }
            return "next";
          });
        }, function () { setDown(true); return "stop"; })
        .then(function (what) { renderQueue(); return what === "next" ? next() : null; });
    }
    return next().then(function () {
      sending = false;
      drop("localStorage", LOCK);
      renderQueue();
      if (note && (manual || panel)) {
        const p = document.createElement("div");
        p.textContent = note;
        if (panel) panel.appendChild(p);
      }
    });
  }

  // ── 서버 연결 확인 ──
  let banner = null;
  function setDown(down) {
    const was = net.down;
    net.down = down;
    document.querySelectorAll(".offline-note").forEach(function (n) { n.hidden = !down; });
    if (down && !banner) {
      banner = document.createElement("div");
      banner.className = "alert alert-error net-banner";
      banner.setAttribute("role", "alert");
      banner.textContent = "서버에 연결할 수 없습니다(네트워크 끊김). " +
        (document.querySelector("form[data-offline]")
          ? "입출고·이동은 등록을 누르면 이 브라우저 대기열에 저장되고, 연결되면 자동으로 보냅니다. "
          : "입력한 내용은 이 브라우저에 임시 저장되어 있으니 연결이 돌아오면 다시 등록을 누르세요. ") +
        "그 전에는 반영되지 않습니다.";
      const host = document.querySelector("main.content") || document.body;
      host.insertBefore(banner, host.firstChild);
    } else if (!down && banner) {
      banner.remove();
      banner = null;
    }
    if (was && !down) flush(false);                               // 연결이 돌아오면 대기열을 보낸다
  }
  function ping() {
    const ctrl = window.AbortController ? new AbortController() : null;
    const timer = setTimeout(function () { if (ctrl) ctrl.abort(); }, 4000);
    return fetch("/health", { cache: "no-store", credentials: "same-origin", signal: ctrl ? ctrl.signal : undefined })
      .then(function () { return true; }, function () { return false; })   // 503(점검 중)이어도 서버에는 닿은 것
      .then(function (ok) { clearTimeout(timer); setDown(!ok); return ok; });
  }
  window.addEventListener("offline", function () { setDown(true); });
  window.addEventListener("online", ping);
  setInterval(function () { if (document.visibilityState === "visible" && (net.down || !navigator.onLine)) ping(); }, 15000);
  setInterval(function () { if (document.visibilityState === "visible") ping(); }, 60000);
  net.ping = ping;

  // 끊긴 동안 자재·창고를 바꾸면 화면을 다시 받지 않고 입력 폼의 값만 바꾼다
  net.switchSelect = function (sel) {
    const target = { material: "material_id", wh: "warehouse_id" }[sel.name];
    if (!target) return;
    const text = sel.options[sel.selectedIndex] ? sel.options[sel.selectedIndex].text : "";
    document.querySelectorAll("form[data-offline]").forEach(function (f) {
      if (f.elements[target]) f.elements[target].value = sel.value;
      const parts = (f.dataset.label || "").split(" · ");
      if (sel.name === "material" && parts.length >= 3) parts[1] = text;
      if (sel.name === "wh" && parts.length >= 3) parts[2] = text.split(" ")[0];
      f.dataset.label = parts.join(" · ");
    });
  };

  // ── 제출 ──
  function markSubmitting(form) {
    form.dataset.submitting = "1";
    put("sessionStorage", PENDING, draftKey(form));
    setTimeout(function () { form.classList.add("is-submitting"); }, 0);   // 제출 값이 다 모인 뒤 표시만 바꾼다
    setTimeout(function () { form.dataset.submitting = ""; form.classList.remove("is-submitting"); }, 5000);
  }
  function offlineSubmit(form) {
    const file = form.querySelector("input[type=file]");
    if (file && file.files && file.files.length) {
      alert("증빙 파일은 오프라인 대기열에 담을 수 없습니다. 파일을 빼고 다시 누르거나, 연결된 뒤 등록하세요.");
      return;
    }
    if (enqueue(form)) {
      const msg = document.createElement("div");
      msg.className = "alert alert-info";
      msg.textContent = "오프라인 대기열에 저장했습니다. 연결되면 자동으로 보냅니다(서버가 재고를 확인한 뒤 반영).";
      form.parentNode.insertBefore(msg, form);
      setTimeout(function () { msg.remove(); }, 6000);
    }
  }
  document.addEventListener("submit", function (e) {
    const form = e.target;
    if (!isPost(form)) return;
    if (form.hasAttribute("data-logout")) {
      const n = mine(loadQueue()).length;
      if (n && !confirm("보내지 않은 오프라인 입력 " + n + "건이 이 브라우저에 남아 있습니다. " +
                        "로그아웃해도 지워지지 않고 다시 로그인하면 보냅니다. 로그아웃할까요?")) { e.preventDefault(); return; }
      keys(DRAFT + USER + ":").forEach(function (k) { drop("localStorage", k); });
      if (navigator.serviceWorker && navigator.serviceWorker.controller) navigator.serviceWorker.controller.postMessage("clear");
      return;
    }
    if (form.dataset.bypass === "1") { form.dataset.bypass = ""; markSubmitting(form); return; }
    if (form.dataset.submitting === "1") { e.preventDefault(); return; }    // 두 번 클릭
    saveDraft(form);
    if (net.down || navigator.onLine === false) {
      e.preventDefault();
      const submitter = e.submitter;
      ping().then(function (ok) {
        if (!ok) { if (form.hasAttribute("data-offline")) offlineSubmit(form); return; }
        form.dataset.bypass = "1";
        if (form.requestSubmit) form.requestSubmit(submitter || undefined); else form.submit();
      });
      return;
    }
    markSubmitting(form);
  }, true);
  window.addEventListener("pageshow", function () {           // 뒤로 가기로 돌아온 화면
    document.querySelectorAll("form.is-submitting").forEach(function (f) {
      f.dataset.submitting = ""; f.classList.remove("is-submitting");
    });
  });

  // ── 화면을 열 때 ──
  const pending = get("sessionStorage", PENDING);
  if (pending) {
    // 처리 성공 화면이면 그 입력의 임시 저장을 지운다 (로그인 화면·오류 화면이면 남겨 둔다)
    if (document.querySelector(".alert-success") && !document.querySelector(".alert-error")
        && location.pathname !== "/login") drop("localStorage", pending);
    drop("sessionStorage", PENDING);
  }
  function initForms(root) {
    root.querySelectorAll("form").forEach(function (form) {
      if (!draftable(form)) return;
      form._mmBase = collect(form);
      offerDraft(form);
      let t = null;
      const later = function () { clearTimeout(t); t = setTimeout(function () { saveDraft(form); }, 400); };
      form.addEventListener("input", later);
      form.addEventListener("change", later);
    });
  }
  initForms(document);
  // 본문만 바꿔 끼웠을 때: 새 입력 폼의 임시 저장, 대기열 안내, 연결 끊김 표시를 다시 붙인다
  net.refresh = function (root) {
    panel = null;
    banner = null;
    initForms(root);
    renderQueue();
    setDown(net.down);
  };

  // 서비스 워커: 로그인한 화면에서만 등록, 로그인 화면에서는 보관한 화면을 지운다(다른 사람이 로그인할 수 있으므로)
  if ("serviceWorker" in navigator) {
    if (USER) navigator.serviceWorker.register("/sw.js").catch(function () { /* HTTP(비보안)에서는 동작하지 않는다 */ });
    else if (location.pathname === "/login" && window.caches) caches.delete("mm-offline-v1").catch(function () {});
  }

  renderQueue();
  if (USER && mine(loadQueue()).some(function (x) { return x.status === "waiting"; })) {
    ping().then(function (ok) { if (ok) flush(false); });       // 다시 로그인했거나 화면을 새로 열었을 때
  }
})();

/* 사이드바 메뉴 편집: 즐겨찾기(☆ → 위쪽 묶음) · 순서(▲▼ 또는 끌어 놓기). 대시보드는 맨 위 고정.
   사이드바는 화면 전환 때 통째로 바뀌므로 문서에 한 번만 이벤트를 건다. 저장은 /prefs/menu (core/prefs.py). */
(function () {
  "use strict";
  function nav() { return document.querySelector(".menu[data-menu-save]"); }
  function items(n) { return Array.prototype.slice.call(n.querySelectorAll(".menu-group a[data-key]")); }

  function refreshLabels(n) {
    const hasFav = !!n.querySelector('[data-group="fav"] a');
    n.querySelectorAll("[data-group-label]").forEach(function (l) {
      l.hidden = !hasFav && !n.classList.contains("editing");
    });
  }

  function save(n, reset) {
    const body = new URLSearchParams();
    body.append("_csrf", n.dataset.csrf);
    if (reset) body.append("reset", "1");
    else {
      body.append("order", items(n).map(function (a) { return a.dataset.key; }).join(","));
      body.append("fav", Array.prototype.map.call(n.querySelectorAll('[data-group="fav"] a[data-key]'),
                                                  function (a) { return a.dataset.key; }).join(","));
    }
    return fetch(n.dataset.menuSave, { method: "POST", body: body, credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error(); })
      .catch(function () { alert("메뉴 설정을 저장하지 못했습니다. 화면을 새로고침한 뒤 다시 시도하세요."); });
  }

  function ctl(cls, text, title) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "menu-ctl " + cls;
    b.textContent = text;
    b.title = title;
    b.setAttribute("aria-label", title);
    return b;
  }

  function decorate(a) {
    const label = a.dataset.label || a.textContent.trim();
    a.dataset.label = label;
    a.textContent = "";
    const fav = a.dataset.fav === "1";
    const star = ctl("star" + (fav ? " on" : ""), fav ? "★" : "☆", fav ? "즐겨찾기 빼기" : "즐겨찾기");
    const span = document.createElement("span");
    span.className = "menu-item-label";
    span.textContent = label;
    a.appendChild(star);
    a.appendChild(span);
    a.appendChild(ctl("up", "▲", "위로"));
    a.appendChild(ctl("down", "▼", "아래로"));
    a.draggable = true;
  }
  function undecorate(a) {
    a.textContent = a.dataset.label || a.textContent;
    a.draggable = false;
  }

  function setEditing(n, on) {
    n.classList.toggle("editing", on);
    items(n).forEach(on ? decorate : undecorate);
    n.querySelector("[data-menu-edit]").textContent = on ? "완료" : "메뉴 편집";
    n.querySelector("[data-menu-reset]").hidden = !on;
    n.querySelector(".menu-help").hidden = !on;
    refreshLabels(n);
  }

  document.addEventListener("click", function (e) {
    const n = nav();
    if (!n || !n.contains(e.target)) return;
    if (e.target.closest("[data-menu-edit]")) {
      const on = !n.classList.contains("editing");
      setEditing(n, on);
      if (!on) save(n);
      return;
    }
    if (e.target.closest("[data-menu-reset]")) {
      save(n, true).then(function () { location.reload(); });
      return;
    }
    if (!n.classList.contains("editing")) return;
    const a = e.target.closest(".menu-group a[data-key]");
    if (!a) return;
    e.preventDefault();                                   // 편집 중에는 메뉴로 이동하지 않는다 (화면 전환보다 먼저 막음)
    const b = e.target.closest(".menu-ctl");
    if (!b) return;
    if (b.classList.contains("star")) {
      const toFav = a.dataset.fav !== "1";
      const group = n.querySelector('[data-group="' + (toFav ? "fav" : "others") + '"]');
      if (toFav) { a.dataset.fav = "1"; group.appendChild(a); }
      else { delete a.dataset.fav; group.insertBefore(a, group.firstChild); }
      decorate(a);
    } else if (b.classList.contains("up") && a.previousElementSibling) {
      a.parentNode.insertBefore(a, a.previousElementSibling);
    } else if (b.classList.contains("down") && a.nextElementSibling) {
      a.parentNode.insertBefore(a.nextElementSibling, a);
    } else {
      return;
    }
    refreshLabels(n);
    save(n);
  });

  // 끌어 놓기 (편집 중에만)
  let dragged = null;
  document.addEventListener("dragstart", function (e) {
    const n = nav();
    const a = e.target.closest && e.target.closest(".menu.editing .menu-group a[data-key]");
    if (!n || !a) return;
    dragged = a;
    a.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", a.dataset.key);
  });
  document.addEventListener("dragover", function (e) {
    if (!dragged) return;
    const group = e.target.closest && e.target.closest(".menu.editing .menu-group");
    if (!group) return;
    e.preventDefault();
    const after = Array.prototype.find.call(group.querySelectorAll("a[data-key]:not(.dragging)"), function (x) {
      const r = x.getBoundingClientRect();
      return e.clientY < r.top + r.height / 2;
    });
    group.insertBefore(dragged, after || null);
  });
  document.addEventListener("dragend", function () {
    if (!dragged) return;
    const n = nav(), a = dragged;
    dragged = null;
    a.classList.remove("dragging");
    if (!n) return;
    if (a.closest('[data-group="fav"]')) a.dataset.fav = "1"; else delete a.dataset.fav;
    decorate(a);
    refreshLabels(n);
    save(n);
  });
})();

/* 화면 전환: 탭·왼쪽 메뉴를 누르면 새로 고침 없이 본문만 바꾼다.
   - 같은 주소를 뒤에서 받아 본문(main)과 왼쪽 메뉴(선택 표시·알림 건수)만 갈아 끼운다.
     왼쪽 메뉴의 스크롤 위치는 그대로 두고, 탭은 누른 탭 줄이 화면의 같은 자리에 남게 맞춘다.
   - 주소창은 바뀌므로 새로고침·즐겨찾기·뒤로 가기는 그대로 동작한다.
   - 로그인 만료(다른 주소로 넘어감)·오류·연결 끊김이면 보통 이동으로 넘어간다(서비스 워커가 보관한 화면 등). */
(function () {
  "use strict";
  if (!document.body.dataset.user || !window.fetch || !window.DOMParser) return;
  if ("scrollRestoration" in history) history.scrollRestoration = "manual";
  let seq = 0;

  function swap(href, push, bar) {
    const main = document.querySelector("main.content");
    const side = document.querySelector(".sidebar");
    if (!main || !side) { window.location = href; return; }
    const idx = bar ? Array.prototype.indexOf.call(main.querySelectorAll(".tabs"), bar) : -1;
    const barTop = bar ? bar.getBoundingClientRect().top : null;
    const y = window.scrollY, my = ++seq;
    main.setAttribute("aria-busy", "true");
    fetch(href, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
      .then(function (r) {
        if (!r.ok || r.redirected) throw new Error("navigate");
        return r.text();
      })
      .then(function (html) {
        if (my !== seq) return;                                  // 그 사이 다른 탭을 눌렀다
        const doc = new DOMParser().parseFromString(html, "text/html");
        const fresh = doc.querySelector("main.content"), freshSide = doc.querySelector(".sidebar");
        if (!fresh || !freshSide) throw new Error("navigate");
        const sideY = side.scrollTop;
        main.style.minHeight = bar ? main.offsetHeight + "px" : "";   // 탭: 새 내용이 짧아도 끌려 올라가지 않게
        if (window.Chart) main.querySelectorAll("canvas").forEach(function (c) {   // 이전 차트를 정리(메모리·크기 감시 해제)
          const ch = window.Chart.getChart(c);
          if (ch) ch.destroy();
        });
        main.innerHTML = fresh.innerHTML;
        main.className = fresh.className;
        main.removeAttribute("aria-busy");
        side.innerHTML = freshSide.innerHTML;
        side.scrollTop = sideY;
        if (doc.title) document.title = doc.title;
        if (push) history.pushState({ mm: true }, "", href);
        if (window.mmUI) window.mmUI.refresh(main);
        if (window.mmNet && window.mmNet.refresh) window.mmNet.refresh(main);
        const pin = function () {
          const nb = idx >= 0 ? main.querySelectorAll(".tabs")[idx] : null;
          if (nb && barTop !== null) window.scrollBy(0, nb.getBoundingClientRect().top - barTop);
          else if (bar === undefined) window.scrollTo(0, y);      // 뒤로 가기
          else window.scrollTo(0, 0);                             // 다른 메뉴: 새 화면의 처음부터
        };
        pin();
        const active = main.querySelector(".tabs a.active");
        if (active) active.focus({ preventScroll: true });
        requestAnimationFrame(pin);                               // 차트가 그려져 높이가 바뀐 뒤 한 번 더
      })
      .catch(function () { if (my === seq) window.location = href; });
  }

  document.addEventListener("click", function (e) {
    const a = e.target.closest(".tabs a[href], .sidebar .menu a[href], .sidebar a.brand, .sidebar a.shortage");
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (a.origin !== window.location.origin || a.target || a.hasAttribute("download")) return;
    e.preventDefault();
    const bar = a.closest(".tabs");
    if (bar) {
      if (a.classList.contains("active")) return;
      bar.querySelectorAll("a").forEach(function (x) { x.classList.toggle("active", x === a); });
    } else {
      document.querySelectorAll(".sidebar .menu a").forEach(function (x) { x.classList.toggle("active", x === a); });
    }
    swap(a.href, true, bar || null);
  });
  window.addEventListener("popstate", function (e) {
    if (e.state && e.state.mm) swap(location.href, false, undefined);
  });
  history.replaceState({ mm: true }, "", location.href);
})();
