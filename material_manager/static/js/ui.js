/* 화면 공통 동작 (인라인 스크립트 없이 — CSP). 화면을 바꿔 끼워도(app.js) 문서 전체에서 이벤트를 받는다.

   - 목록 행 누르기: tr[data-href] 를 누르면 그 건의 화면으로 (Ctrl·Shift·가운데 클릭은 새 탭, 행에서 Enter 도 같음).
   - 긴 선택 목록 찾기: 보기가 10개 넘는 <select> 위에 '입력해서 찾기 (N개)' 칸 — 띄어 쓴 낱말이 모두 든 보기만 남긴다.
     Enter = 첫 보기 고르기, Esc = 지우기. data-no-search 를 붙이면 빼고. 원래 select 는 그대로라 필수·변경 이벤트가 그대로.
   - 확인: data-confirm 이 있는 폼·버튼은 제출 전에 한 번 묻는다.
   - 모두 선택: <input type=checkbox data-check-all="이름"> 이 같은 폼의 그 이름 체크박스를 모두 켜고 끈다.
   - 구매요청 금액 → 필요한 결재 단계를 입력하는 동안 보여 준다 (form[data-pr-tiers]). */
(function () {
  "use strict";

  // ── 목록 행 누르기 ──
  function go(tr, newTab) {
    const href = tr.dataset.href;
    if (!href) return;
    if (newTab) { window.open(href, "_blank"); return; }
    if (window.mmSwap) window.mmSwap(href); else window.location = href;
  }
  document.addEventListener("click", function (e) {
    const tr = e.target.closest && e.target.closest("tr[data-href]");
    if (!tr || e.target.closest("a, button, input, select, textarea, label, summary, form")) return;
    if (e.button !== 0) return;
    go(tr, e.ctrlKey || e.metaKey || e.shiftKey);
  });
  document.addEventListener("auxclick", function (e) {
    const tr = e.target.closest && e.target.closest("tr[data-href]");
    if (tr && e.button === 1 && !e.target.closest("a")) go(tr, true);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && e.target.matches && e.target.matches("tr[data-href]")) go(e.target, e.ctrlKey || e.metaKey);
  });

  // ── 긴 선택 목록 찾기 ──
  const MIN = 10;
  function enhance(sel) {
    if (sel.dataset.combo || sel.multiple || sel.hasAttribute("data-no-search") || sel.options.length < MIN) return;
    sel.dataset.combo = "1";
    const all = Array.prototype.map.call(sel.options, function (o) { return { value: o.value, text: o.text, o: o }; });
    const box = document.createElement("input");
    box.type = "search";
    box.className = "combo-q";
    box.placeholder = "입력해서 찾기 (" + all.length + "개)";
    box.setAttribute("aria-label", "목록에서 찾기");
    sel.parentNode.insertBefore(box, sel);
    function filter() {
      const words = box.value.toLowerCase().split(/\s+/).filter(Boolean);
      const keep = all.filter(function (x) {
        const t = x.text.toLowerCase();
        return !words.length || x.value === sel.value || words.every(function (w) { return t.indexOf(w) >= 0; });
      });
      const current = sel.value;
      sel.textContent = "";
      keep.forEach(function (x) { sel.appendChild(x.o); });
      sel.value = current;
      box.classList.toggle("combo-none", words.length > 0 && keep.length <= (current ? 1 : 0));
      box.title = keep.length ? "" : "찾는 항목이 없습니다";
      return keep;
    }
    box.addEventListener("input", filter);
    box.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { box.value = ""; filter(); return; }
      if (e.key !== "Enter") return;
      e.preventDefault();
      const words = box.value.toLowerCase().split(/\s+/).filter(Boolean);
      const hit = all.find(function (x) { return x.value && words.every(function (w) { return x.text.toLowerCase().indexOf(w) >= 0; }); });
      if (hit) {
        sel.value = hit.value;
        sel.dispatchEvent(new Event("change", { bubbles: true }));
        box.value = "";
        filter();
        sel.focus();
      }
    });
  }
  function initCombos(root) { root.querySelectorAll("select").forEach(enhance); }

  // ── 제출 전 확인 ──
  window.addEventListener("submit", function (e) {
    const form = e.target;
    if (form.dataset.confirmed === "1") { delete form.dataset.confirmed; return; }   // 끊김 확인 뒤 다시 제출 (app.js)
    const btn = e.submitter;
    const msg = (btn && btn.dataset.confirm) || form.dataset.confirm;
    if (msg && !window.confirm(msg)) {
      e.preventDefault(); e.stopImmediatePropagation();
      form.dataset.declined = "1";                        // app.js 가 연결 확인 뒤 다시 보내지 않게
      form.dispatchEvent(new CustomEvent("mm:declined", { bubbles: true }));   // '보내는 중' 표시를 풀게 (app.js)
    }
  }, true);

  // ── 모두 선택 ──
  document.addEventListener("change", function (e) {
    const name = e.target.dataset && e.target.dataset.checkAll;
    if (!name) return;
    const form = e.target.form || e.target.closest("form");
    const boxes = form ? Array.prototype.filter.call(form.elements, function (c) { return c.type === "checkbox" && c.name === name; })
                       : document.querySelectorAll("input[type=checkbox][name='" + name + "']");
    Array.prototype.forEach.call(boxes, function (c) { c.checked = e.target.checked; });   // form="…" 로 밖에 있는 칸도
  });

  // ── 입고: 발주 품목을 고르면 단가 = 발주 단가, 수량이 비었으면 잔량, 거래처가 비었으면 공급처 ──
  document.addEventListener("change", function (e) {
    if (!e.target.matches || !e.target.matches("select[data-po-line]")) return;
    const o = e.target.options[e.target.selectedIndex], f = e.target.form;
    if (!o || !o.value || !f) return;
    if (f.elements.unit_price) f.elements.unit_price.value = o.dataset.price;
    if (f.elements.qty && !f.elements.qty.value) f.elements.qty.value = o.dataset.remaining;
    if (f.elements.partner && !f.elements.partner.value) f.elements.partner.value = o.dataset.supplier || "";
  });

  // ── 작업지시: '남은 계획 수량 모두 채우기' (기본은 빈 칸 — 한 줄만 반납해도 나머지가 투입되지 않게) ──
  document.addEventListener("click", function (e) {
    const b = e.target.closest && e.target.closest("[data-fill-rest]");
    if (!b) return;
    const form = b.form || b.closest("form");
    if (!form) return;
    form.querySelectorAll("input[data-rest]").forEach(function (i) { if (i.dataset.rest && !i.value) i.value = i.dataset.rest; });
  });

  // ── 구매요청 결재 단계 미리 보기 ──
  function prPreview(form) {
    const tiers = (form.dataset.prTiers || "").split(",").map(Number);
    let total = 0;
    const q = form.querySelectorAll("[name=qty]"), p = form.querySelectorAll("[name=price]");
    for (let i = 0; i < q.length; i++) total += (parseFloat(q[i].value) || 0) * (parseFloat(p[i].value) || 0);
    const steps = total <= tiers[0] ? 1 : total <= tiers[1] ? 2 : 3;
    const out = form.querySelector("[data-pr-preview]");
    if (out) out.textContent = total > 0 ? "합계 ₩ " + Math.round(total).toLocaleString("ko-KR") + " → 결재 " + steps + "단계 필요" +
      (steps === 3 ? " (관리자 2명 + 시스템관리자)" : steps === 2 ? " (관리자 2명)" : " (관리자 1명)") : "";
  }
  document.addEventListener("input", function (e) {
    const form = e.target.closest && e.target.closest("form[data-pr-tiers]");
    if (form) prPreview(form);
  });

  // ── 백그라운드 작업 진행 (2초마다 상태를 묻고, 끝나면 새로 고쳐 결과·내려받기를 보인다) ──
  function pollTask(box) {
    if (box.dataset.taskDone === "1") return;
    const bar = box.querySelector("[data-task-bar]"), msg = box.querySelector("[data-task-message]"),
          st = box.querySelector("[data-task-status]");
    let misses = 0;
    const timer = window.setInterval(function () {
      fetch(box.dataset.taskPoll, { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (j) {
          misses = 0;
          if (st) st.textContent = j.status_label;
          if (bar && j.percent != null) bar.value = j.percent;
          if (msg && j.message) msg.textContent = j.message;
          if (j.finished) { window.clearInterval(timer); window.location.reload(); }
        })
        .catch(function () { if (++misses > 30) window.clearInterval(timer); });
    }, 2000);
  }
  document.querySelectorAll("[data-task-poll]").forEach(pollTask);

  function init(root) { initCombos(root); }
  init(document);
  window.mmUI2 = { refresh: init };
})();
