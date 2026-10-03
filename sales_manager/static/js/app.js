/* 화면 보조 스크립트: 차트 · 행 클릭 · 폼 편의 기능.
   스크립트가 없어도 모든 기능은 서버에서 동작한다(검증·계산은 서버가 최종). */
(function () {
  "use strict";

  // ── 차트 규칙 (모든 화면 공통) ─────────────────────────────────────────
  //  * 색: 지표 이름으로 고정. 실적 계열은 청색, 비교 기준(목표·가중금액)은 황색.
  //  * 축: 금액은 지표 카드와 같은 백만원 단위(축 제목에 표기). 툴팁은 원 단위 정확한 값.
  //  * 막대: 같은 최대 폭, 같은 모서리. 높이는 서버(CHART_HEIGHT)에서 통일.
  const BLUE = "#2747A3", AMBER = "#D9962B", GREEN = "#3E9E6E", GRAY = "#8A94A3";
  const SERIES_COLOR = {
    "매출": BLUE, "금액": BLUE, "목표": AMBER, "가중금액": AMBER,
    "Commit": BLUE, "Best Case": AMBER, "Pipeline": GREEN, "Omitted": GRAY,
    "확정 매출": "#16275E", "미수금": AMBER,
  };
  const FALLBACK = [BLUE, AMBER, GREEN, GRAY];

  // 금액 축은 지표 카드와 같은 백만원 단위로 표기한다 (값이 작으면 원 단위)
  function moneyUnit(datasets) {
    const max = Math.max.apply(null, datasets.flatMap(function (d) { return d.data.map(Math.abs); }).concat([0]));
    return max >= 1e6 ? { div: 1e6, title: "백만원" } : { div: 1, title: "원" };
  }

  // 계열이 하나이고 가로축 항목 자체가 지표 이름이면(예: 예측 구성) 막대마다 그 지표의 색을 쓴다
  function perLabelChart(cfg) {
    return cfg.datasets.length === 1 && cfg.labels.every(function (l) { return SERIES_COLOR[l]; });
  }

  function drawCharts(root) {
    if (!window.Chart) return;
    root = root || document;
    const css = getComputedStyle(document.documentElement);
    const text = css.getPropertyValue("--muted").trim();
    const line = css.getPropertyValue("--line").trim();
    Chart.defaults.font.family = css.getPropertyValue("--font").trim();
    Chart.defaults.font.size = 12;

    root.querySelectorAll("canvas[data-chart]").forEach(function (el) {
      if (Chart.getChart(el)) return;
      const cfg = JSON.parse(el.dataset.chart);
      const isLine = cfg.type === "line";
      const unit = cfg.money ? moneyUnit(cfg.datasets) : null;
      new Chart(el, {
        type: cfg.type,
        data: {
          labels: cfg.labels,
          datasets: cfg.datasets.map(function (d, i) {
            let color = SERIES_COLOR[d.label] || FALLBACK[i % FALLBACK.length];
            if (perLabelChart(cfg)) color = cfg.labels.map(function (l) { return SERIES_COLOR[l]; });
            const fill = Array.isArray(color) ? color.map(function (c) { return c + "D9"; }) : color + "D9";
            return Object.assign({}, d, {
              backgroundColor: isLine ? color : fill,
              borderColor: color,
              borderWidth: isLine ? 2 : 0, borderRadius: 3,
              maxBarThickness: 36, categoryPercentage: .7, barPercentage: .9,
              tension: .25, pointRadius: 3, pointHoverRadius: 5,
            });
          }),
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: {
            subtitle: { display: !!unit, text: unit ? "단위: " + unit.title : "", align: "start",
                        color: text, font: { size: 11 }, padding: { bottom: 12 } },
            legend: {
              display: !perLabelChart(cfg), position: "top", align: "end",
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

  // ── 표: 행 클릭 이동, 전체 선택 ────────────────────────────────────────
  // 행 클릭은 아래 '화면 이동 없이 본문만 바꾸기'(swapMain)에서 처리한다 — 수정 칸으로 바로 내려간다.
  document.addEventListener("change", function (e) {
    if (e.target.matches("[data-check-all]")) {
      e.target.closest("table").querySelectorAll("tbody input[type=checkbox]")
        .forEach(function (cb) { cb.checked = e.target.checked; });
    }
  });

  // ── 긴 선택 목록(거래처·영업기회 등): 입력해서 찾기 ───────────────────────
  //  선택지가 많은 <select> 옆에 검색 입력칸을 붙인다. 실제 값은 원래 <select> 에 넣고 change 를 알리므로
  //  다른 스크립트(거래처별 영업기회 거르기·자동 조회 등)와 폼 전송·필수 검사는 그대로 동작한다.
  const COMBO_MIN = 10;
  function enhanceSelect(sel) {
    if (sel.dataset.combo || sel.multiple || sel.hasAttribute("data-no-search") || sel.closest(".sidebar, .combo")) return;
    const remote = sel.dataset.remote || "";               // 선택지가 아주 많으면 서버에서 찾는다 (_macros.pick)
    if (!remote && sel.options.length < COMBO_MIN) return;
    sel.dataset.combo = "1";
    const box = document.createElement("span");
    box.className = "combo";
    const input = document.createElement("input");
    input.type = "text";
    input.className = "combo-input";
    input.autocomplete = "off";
    const total = remote ? Number(sel.dataset.total || 0) : sel.options.length - (sel.options[0] && sel.options[0].value === "" ? 1 : 0);
    input.placeholder = "입력해서 찾기 (" + total.toLocaleString("ko-KR") + "개)";
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    const list = document.createElement("ul");
    list.className = "combo-list";
    list.setAttribute("role", "listbox");
    list.hidden = true;
    sel.parentNode.insertBefore(box, sel);
    box.appendChild(sel);
    box.appendChild(input);
    box.appendChild(list);
    sel.classList.add("combo-native");
    sel.tabIndex = -1;
    sel.setAttribute("aria-hidden", "true");
    input.disabled = sel.disabled;
    let shown = [], active = -1;

    function label() {
      const o = sel.options[sel.selectedIndex];
      return o && o.value !== "" ? o.text : "";
    }
    function close() {
      list.hidden = true;
      input.setAttribute("aria-expanded", "false");
    }
    function highlight(i) {
      const items = list.querySelectorAll("li[data-i]");
      if (!items.length) return;
      active = Math.max(0, Math.min(i, items.length - 1));
      items.forEach(function (li, k) { li.classList.toggle("active", k === active); });
      items[active].scrollIntoView({ block: "nearest" });
    }
    let timer = null, seq = 0;
    function render(q, found) {
      const words = (q || "").trim().toLowerCase().split(/\s+/).filter(Boolean);
      if (remote && words.length && !found) {                // 서버 검색 (입력이 멈추면 한 번)
        clearTimeout(timer);
        const my = ++seq;
        timer = setTimeout(function () {
          fetch(remote + (remote.indexOf("?") < 0 ? "?" : "&") + "q=" + encodeURIComponent(q.trim()), { credentials: "same-origin" })
            .then(function (r) { return r.ok ? r.json() : []; })
            .then(function (rows) {
              if (my !== seq) return;
              render(q, rows.map(function (r) { return { value: String(r[0]), text: r[1], selected: String(r[0]) === sel.value }; }));
            }).catch(function () {});
        }, 200);
        return;
      }
      list.textContent = "";
      shown = [];
      if (found) {
        shown = found;
      } else {
        Array.prototype.forEach.call(sel.options, function (o) {
          if (o.hidden || (o.value === "" && words.length)) return;
          const text = o.text.toLowerCase();
          if (words.every(function (w) { return text.indexOf(w) >= 0; }) && shown.length < 300) shown.push(o);
        });
      }
      shown.forEach(function (o, i) {
        const li = document.createElement("li");
        li.textContent = o.value === "" ? (o.text || "(선택 안 함)") : o.text;
        li.dataset.i = i;
        li.setAttribute("role", "option");
        if (o.selected && o.value !== "") li.classList.add("current");
        list.appendChild(li);
      });
      if (!shown.length || (remote && !words.length)) {
        const li = document.createElement("li");
        li.className = "empty";
        li.textContent = shown.length ? "이름이나 사업자번호를 입력하면 찾아 줍니다" : "찾는 항목이 없습니다";
        list.appendChild(li);
      }
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
      active = -1;
      if (words.length && shown.length) highlight(0);
    }
    function choose(o) {
      if (remote && o.value !== "" && !Array.prototype.some.call(sel.options, function (x) { return x.value === o.value; })) {
        sel.appendChild(new Option(o.text, o.value));       // 서버에서 찾은 값은 선택지로 넣고 고른다
      }
      if (sel.value !== o.value) {
        sel.value = o.value;
        sel.dispatchEvent(new Event("change", { bubbles: true }));
      }
      input.value = label();
      close();
    }
    input.value = label();
    input.addEventListener("focus", function () { input.select(); render(""); });
    input.addEventListener("input", function () { render(input.value); });
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { e.preventDefault(); if (list.hidden) render(""); highlight(active + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); highlight(active - 1); }
      else if (e.key === "Enter" && !list.hidden && active >= 0 && shown[active]) { e.preventDefault(); choose(shown[active]); }
      else if (e.key === "Escape") { input.value = label(); close(); }
    });
    list.addEventListener("mousedown", function (e) {
      const li = e.target.closest("li[data-i]");
      e.preventDefault();                                 // 입력칸 초점을 잃지 않게
      if (li) choose(shown[Number(li.dataset.i)]);
    });
    input.addEventListener("blur", function () { input.value = label(); close(); });
    sel.addEventListener("change", function () { input.value = label(); });
    sel.addEventListener("invalid", function () { input.focus(); });
  }

  function initWidgets(root) {
  root.querySelectorAll("main select, .content select").forEach(enhanceSelect);
  // ── 거래처 선택 시 해당 거래처의 영업기회만 보이기 ──────────────────────
  root.querySelectorAll("form[data-deal-filter]").forEach(function (form) {
    const cust = form.querySelector("[name=customer_id]");
    const deal = form.querySelector("[name=deal_id]");
    if (!cust || !deal) return;
    if (deal.dataset.dealsUrl) {
      cust.addEventListener("change", function () {
        const keep = deal.value;
        Array.prototype.slice.call(deal.options).forEach(function (o) { if (o.value) o.remove(); });
        if (!cust.value) return;
        fetch(deal.dataset.dealsUrl + "?customer_id=" + encodeURIComponent(cust.value), { credentials: "same-origin" })
          .then(function (r) { return r.ok ? r.json() : []; })
          .then(function (rows) {
            rows.forEach(function (r) {
              const o = new Option(r[1], String(r[0]));
              o.dataset.customer = String(r[2]);
              deal.appendChild(o);
            });
            if (keep && Array.prototype.some.call(deal.options, function (o) { return o.value === keep; })) deal.value = keep;
          }).catch(function () {});
      });
      return;
    }
    function apply() {
      deal.querySelectorAll("option[data-customer]").forEach(function (o) {
        const show = o.dataset.customer === cust.value;
        o.hidden = !show;
        if (!show && o.selected) deal.value = "";
      });
    }
    cust.addEventListener("change", apply);
    apply();
  });

  // ── 영업기회: 제안금액·필요 결재권한 미리보기 ─────────────────────────
  root.querySelectorAll("[data-deal-calc]").forEach(function (box) {
    const form = box.closest("form");
    const out = form.querySelector("[data-deal-preview]");
    const la = form.querySelector("[name=list_amount]");
    const dr = form.querySelector("[name=discount_rate]");
    function apply() {
      const list = Number(la.value || 0), rate = Number(dr.value || 0);
      const amount = Math.floor(list * (100 - rate) / 100);
      const m = Number(box.dataset.discM || 10), e = Number(box.dataset.discE || 20);
      const role = rate <= 0 ? "" : rate <= m ? "팀장" : rate <= e ? "팀장 → 임원" : "팀장 → 임원 → 관리자";
      out.textContent = "제안금액 " + amount.toLocaleString() + "원" +
        (role ? " · 할인 " + rate + "% → " + role + " 결재 필요" : "");
    }
    la.addEventListener("input", apply);
    dr.addEventListener("input", apply);
    apply();
  });

  // ── 매출·견적: 품목을 고르면 적용 단가(특가 → 정가)·과세구분을 채우고 부가세를 미리 계산 ──
  function vatOf(supply, tax) { return tax === "과세" ? Math.floor(supply / 10) : 0; }
  function priceLookup(form, row, done) {
    const prod = row.querySelector("[name=product_id]");
    const cust = form.querySelector("[name=customer_id]");
    const url = form.dataset.priceUrl;
    if (!prod || !prod.value || !url) return;
    const qs = new URLSearchParams({ product_id: prod.value, customer_id: cust ? cust.value : "",
                                     on: (form.querySelector("[name=sale_date],[name=issue_date]") || {}).value || "" });
    fetch(url + "?" + qs, { credentials: "same-origin" }).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d) done(d); }).catch(function () {});
  }
  root.querySelectorAll("form[data-sale-calc]").forEach(function (form) {
    const out = form.querySelector("[data-sale-preview]");
    const q = form.querySelector("[name=qty]"), p = form.querySelector("[name=unit_price]");
    const tax = form.querySelector("[name=tax_type]");
    function apply() {
      const supply = Number(q.value || 0) * Number(p.value || 0), t = tax ? tax.value : "과세";
      const vat = vatOf(supply, t);
      out.textContent = "공급가액 " + supply.toLocaleString() + "원 + 부가세 " + vat.toLocaleString() +
        "원 = 합계 " + (supply + vat).toLocaleString() + "원 (" + t + ") · 결제기일은 거래처 결제조건으로 자동 계산됩니다.";
    }
    const prod = form.querySelector("[name=product_id]");
    if (prod) prod.addEventListener("change", function () {
      priceLookup(form, form, function (d) {
        p.value = d.unit_price;
        if (tax) tax.value = d.tax_type;
        const item = form.querySelector("[name=item]"), code = form.querySelector("[name=item_code]");
        if (item) item.value = d.name;
        if (code) code.value = d.erp_material || d.code;
        const src = form.querySelector("[data-price-source]");
        if (src) src.textContent = d.source === "특가" ? "거래처 특가 적용 (정가 " + d.list_price.toLocaleString() + "원)" : "정가 적용";
        apply();
      });
    });
    [q, p, tax].forEach(function (el) { if (el) { el.addEventListener("input", apply); el.addEventListener("change", apply); } });
    apply();
  });

  // ── 견적: 품목 행 추가 · 행별 단가 조회 · 합계 미리보기 ───────────────────
  root.querySelectorAll("form[data-quote]").forEach(function (form) {
    const body = form.querySelector("[data-lines]"), tmpl = form.querySelector("template[data-line]");
    const out = form.querySelector("[data-quote-total]");
    function apply() {
      let supply = 0, vat = 0;
      body.querySelectorAll("tr").forEach(function (tr) {
        const qty = Number((tr.querySelector("[name=qty]") || {}).value || 0);
        const price = Number((tr.querySelector("[name=unit_price]") || {}).value || 0);
        const rate = Number((tr.querySelector("[name=discount_rate]") || {}).value || 0);
        const line = Math.floor(qty * price * (100 - rate) / 100);
        const t = (tr.querySelector("[name=tax_type]") || {}).value || "과세";
        supply += line; vat += vatOf(line, t);
        const cell = tr.querySelector("[data-line-amount]");
        if (cell) cell.textContent = line.toLocaleString();
      });
      if (out) out.textContent = "공급가액 " + supply.toLocaleString() + "원 · 부가세 " + vat.toLocaleString() +
        "원 · 합계 " + (supply + vat).toLocaleString() + "원";
    }
    function wire(tr) {
      const prod = tr.querySelector("[name=product_id]");
      if (prod) prod.addEventListener("change", function () {
        priceLookup(form, tr, function (d) {
          tr.querySelector("[name=unit_price]").value = d.unit_price;
          tr.querySelector("[name=tax_type]").value = d.tax_type;
          const src = tr.querySelector("[data-price-source]");
          if (src) src.textContent = d.source;
          apply();
        });
      });
      const del = tr.querySelector("[data-remove-line]");
      if (del) del.addEventListener("click", function () { if (body.querySelectorAll("tr").length > 1) { tr.remove(); apply(); } });
      tr.querySelectorAll("input,select").forEach(function (el) { el.addEventListener("input", apply); });
    }
    body.querySelectorAll("tr").forEach(wire);
    const add = form.querySelector("[data-add-line]");
    if (add && tmpl) add.addEventListener("click", function () {
      const tr = tmpl.content.firstElementChild.cloneNode(true);
      body.appendChild(tr); wire(tr); apply();
    });
    apply();
  });

  // ── 입금: 대상을 바꾸면 미수금 전액을 기본값으로 ──────────────────────
  root.querySelectorAll("form[data-payment]").forEach(function (form) {
    const sel = form.querySelector("[name=sale_id]"), amt = form.querySelector("[name=amount]");
    sel.addEventListener("change", function () { amt.value = sel.selectedOptions[0].dataset.remain; });
  });

  // ── 증빙: 공급가액·세액을 넣으면 합계를 채운다 (서버가 다시 검증) ────────
  root.querySelectorAll("form.doc-form").forEach(function (form) {
    const supply = form.querySelector("[data-supply]"), tax = form.querySelector("[data-tax]"),
          total = form.querySelector("[data-total]");
    function apply() {
      if (supply.value === "") return;
      if (tax.value === "" && document.activeElement === supply) tax.value = Math.round(Number(supply.value) * 0.1);
      total.value = Number(supply.value || 0) + Number(tax.value || 0);
    }
    supply.addEventListener("input", apply);
    tax.addEventListener("input", apply);
  });

  // ── 추출 프리셋: 고르면 해당 묶음으로 다시 조회 ───────────────────────
  root.querySelectorAll("select[data-preset]").forEach(function (sel) {
    sel.addEventListener("change", function () {
      if (!sel.value) return;
      sel.form.querySelectorAll("[name=sources]").forEach(function (cb) { cb.checked = false; });
      sel.form.submit();
    });
  });

  }

  // ── 저장 안전장치 ──────────────────────────────────────────────────────
  //  1) 폼마다 한 번만 쓰는 번호(_submit_id) → 두 번 클릭·응답이 끊겨 다시 보내도 서버가 한 번만 처리
  //  2) 보내는 동안 버튼 잠금
  //  3) 연결이 끊기면 보내지 않고 알림 (입력은 그대로 남음)
  //  4) 입력 중인 내용을 이 브라우저에 보관 → 세션 만료·연결 끊김·오류 뒤에도 '불러오기'로 되살림
  //     (비밀번호·파일은 보관하지 않음, 24시간 뒤 자동 삭제, 로그아웃하면 지움)
  const USER = document.body.dataset.user || "anon";
  const DRAFT_PREFIX = "sales-draft:" + USER + ":";
  const DRAFT_TTL = 24 * 3600 * 1000;
  const SKIP = /^(_csrf|_submit_id|password|new_password|current_password|row_version)$/;
  function store() { try { return window.localStorage; } catch (e) { return null; } }
  function newId() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 12);
  }
  function draftKey(form) { return DRAFT_PREFIX + location.pathname + "|" + (form.getAttribute("action") || location.pathname); }
  function fields(form) {
    return Array.prototype.filter.call(form.elements, function (el) {
      return el.name && !SKIP.test(el.name) && el.type !== "file" && el.type !== "password" &&
             el.type !== "hidden" && el.type !== "submit" && el.type !== "button";
    });
  }
  function snapshot(form) {
    const out = [];
    fields(form).forEach(function (el) {
      if (el.type === "checkbox" || el.type === "radio") out.push([el.name, el.value, el.checked]);
      else if (el.multiple) out.push([el.name, Array.prototype.map.call(el.selectedOptions, function (o) { return o.value; }), null]);
      else out.push([el.name, el.value, null]);
    });
    return out;
  }
  function restore(form, data) {
    const used = {};
    data.forEach(function (row) {
      const name = row[0], value = row[1], checked = row[2];
      const list = Array.prototype.filter.call(form.elements, function (el) { return el.name === name; });
      if (checked !== null) {
        list.forEach(function (el) { if (el.value === value) el.checked = checked; });
        return;
      }
      const i = used[name] || 0; used[name] = i + 1;
      const el = list[i];
      if (!el) return;
      if (el.multiple && Array.isArray(value)) Array.prototype.forEach.call(el.options, function (o) { o.selected = value.indexOf(o.value) >= 0; });
      else el.value = value;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    });
  }
  function readDraft(key) {
    const s = store(); if (!s) return null;
    try {
      const d = JSON.parse(s.getItem(key) || "null");
      if (!d || Date.now() - d.at > DRAFT_TTL) { s.removeItem(key); return null; }
      return d;
    } catch (e) { return null; }
  }
  function writeDraft(key, form, pending) {
    const s = store(); if (!s) return;
    try { s.setItem(key, JSON.stringify({ at: Date.now(), pending: !!pending, data: snapshot(form) })); } catch (e) { /* 저장 공간 부족 */ }
  }
  function dropDraft(key) { const s = store(); if (s) try { s.removeItem(key); } catch (e) { /* 무시 */ } }

  // 앞 요청이 성공(성공 알림)했다면 보내기 직전에 보관한 입력은 지운다. 실패·만료면 남겨 둔다.
  (function settlePending() {
    const s = store(); if (!s) return;
    const ok = !!document.querySelector(".alert-success") && !document.querySelector(".alert-error");
    try {
      Object.keys(s).forEach(function (k) {
        if (k.indexOf("sales-draft:") !== 0) return;
        const d = JSON.parse(s.getItem(k) || "null");
        if (!d || Date.now() - d.at > DRAFT_TTL || (ok && d.pending)) s.removeItem(k);
      });
    } catch (e) { /* 무시 */ }
  })();

  const banner = document.querySelector("[data-net-banner]");
  function online() { return navigator.onLine !== false; }
  function showNet() { if (banner) banner.hidden = online(); }
  window.addEventListener("online", showNet);
  window.addEventListener("offline", showNet);
  showNet();

  function initForms(root) {
  root.querySelectorAll("form[method=post], form[method=POST]").forEach(function (form) {
    const id = document.createElement("input");
    id.type = "hidden"; id.name = "_submit_id"; id.value = newId();
    form.appendChild(id);

    const keep = form.matches(".form, [data-quote], [data-sale-calc]") && !form.closest(".narrow") &&
                 !form.hasAttribute("data-no-draft") && fields(form).length > 1;
    const key = draftKey(form);
    if (keep) {
      const d = readDraft(key);
      if (d && JSON.stringify(d.data) !== JSON.stringify(snapshot(form))) {
        const bar = document.createElement("div");
        bar.className = "draft-bar";
        const text = document.createElement("span");
        text.textContent = "저장되지 않은 입력이 있습니다 (" + new Date(d.at).toLocaleString() + ").";
        const load = document.createElement("button"); load.type = "button"; load.className = "btn btn-sm btn-primary"; load.textContent = "보관된 입력 불러오기";
        const drop = document.createElement("button"); drop.type = "button"; drop.className = "btn btn-sm"; drop.textContent = "버리기";
        load.addEventListener("click", function () { restore(form, d.data); bar.remove(); });
        drop.addEventListener("click", function () { dropDraft(key); bar.remove(); });
        bar.appendChild(text); bar.appendChild(load); bar.appendChild(drop);
        form.parentNode.insertBefore(bar, form);
      }
      let timer = null;
      form.addEventListener("input", function () {
        clearTimeout(timer);
        timer = setTimeout(function () { writeDraft(key, form, false); }, 400);
      });
    }

    form.addEventListener("submit", function (e) {
      if (!online()) {
        e.preventDefault();
        if (keep) writeDraft(key, form, false);
        showNet();
        return;
      }
      if (form.dataset.sending) { e.preventDefault(); return; }       // 두 번 클릭
      form.dataset.sending = "1";
      if (keep) writeDraft(key, form, true);
      if (form.hasAttribute("data-logout")) {                          // 공용 PC: 로그아웃하면 보관 입력을 지운다
        const s = store();
        if (s) try { Object.keys(s).forEach(function (k) { if (k.indexOf(DRAFT_PREFIX) === 0) s.removeItem(k); }); } catch (err) { /* 무시 */ }
      }
      const btn = e.submitter;
      if (btn) setTimeout(function () { btn.setAttribute("data-busy", ""); }, 0);
      // 응답이 오지 않으면(연결 끊김) 20초 뒤 다시 누를 수 있게 푼다 — 서버는 같은 번호를 한 번만 처리한다
      setTimeout(function () { delete form.dataset.sending; if (btn) btn.removeAttribute("data-busy"); }, 20000);
    });
  });
  }

  // 뒤로 가기로 돌아온 화면(bfcache)은 잠금을 푼다
  window.addEventListener("pageshow", function (e) {
    if (!e.persisted) return;
    document.querySelectorAll("form[data-sending]").forEach(function (f) { delete f.dataset.sending; });
    document.querySelectorAll("button[data-busy]").forEach(function (b) { b.removeAttribute("data-busy"); });
  });

  initWidgets(document);
  initForms(document);
  window.addEventListener("load", function () { drawCharts(document); });

  // ── 탭: 화면 이동 없이 본문만 바꾼다 ───────────────────────────────────
  //  탭을 누르면 같은 주소를 뒤에서 받아 본문(main)만 갈아 끼운다 → 새로고침·스크롤 이동 없음.
  //  주소창은 바뀌므로 새로고침·즐겨찾기·뒤로 가기는 그대로 동작한다. 실패하면 보통 이동으로 넘어간다.
  //  화면 고정: 누른 탭 줄이 화면에서 있던 자리(위에서 몇 px)에 그대로 남도록 맞춘다.
  //  바뀌는 동안 본문 높이를 잠시 유지해, 새 내용이 짧아도 화면이 위로 끌려 올라가지 않게 한다.
  if ("scrollRestoration" in history) history.scrollRestoration = "manual";
  const ANCHORS = ".tabs, .pager";        // 화면에서 위치를 고정할 기준 (탭 줄, 쪽 넘김)
  function tabsIndex(main, bar) {
    return bar ? Array.prototype.indexOf.call(main.querySelectorAll(ANCHORS), bar) : -1;
  }
  function swapMain(href, push, bar, focusEdit) {
    const main = document.querySelector("main.content");
    if (!main || !window.fetch || !window.DOMParser) { window.location = href; return; }
    const wrapIdx = focusEdit ? Array.prototype.indexOf.call(main.querySelectorAll(".table-wrap"), focusEdit.closest(".table-wrap")) : -1;
    const wrapTop = wrapIdx >= 0 ? focusEdit.closest(".table-wrap").scrollTop : 0;
    const idx = tabsIndex(main, bar);
    const barTop = bar ? bar.getBoundingClientRect().top : null;
    const y = window.scrollY;
    main.setAttribute("aria-busy", "true");
    fetch(href, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
      .then(function (r) {
        if (!r.ok || r.redirected) throw new Error("navigate");
        return r.text();
      })
      .then(function (html) {
        const doc = new DOMParser().parseFromString(html, "text/html");
        const fresh = doc.querySelector("main.content");
        if (!fresh) throw new Error("navigate");
        main.style.minHeight = main.offsetHeight + "px";
        main.innerHTML = fresh.innerHTML;
        main.removeAttribute("aria-busy");
        if (doc.title) document.title = doc.title;
        if (push) history.pushState({ swapped: true }, "", href);
        initWidgets(main);
        initForms(main);
        drawCharts(main);
        const pin = function () {
          const nb = idx >= 0 ? main.querySelectorAll(ANCHORS)[idx] : null;
          if (nb && barTop !== null) window.scrollBy(0, nb.getBoundingClientRect().top - barTop);
          else window.scrollTo(0, y);
        };
        if (focusEdit) {                  // 행을 눌렀으면: 고른 행 표시 + 수정 칸으로 이동
          const wrap = wrapIdx >= 0 ? main.querySelectorAll(".table-wrap")[wrapIdx] : null;
          if (wrap) wrap.scrollTop = wrapTop;
          markSelected(main);
          const target = main.querySelector("[data-edit-anchor]");
          if (target) {
            target.scrollIntoView({ block: "start" });
            const first = target.querySelector("input:not([type=hidden]), select, textarea");
            if (first) first.focus({ preventScroll: true });
          } else {
            window.scrollTo(0, y);
          }
          return;
        }
        pin();
        const active = main.querySelector(".tabs a.active");
        if (active) active.focus({ preventScroll: true });
        // 차트가 그려져 높이가 바뀐 뒤 한 번 더 맞춘다 (높이 유지는 다음 전환까지 둔다 — 풀면 짧은 화면에서 끌려 올라감)
        requestAnimationFrame(pin);
      })
      .catch(function () { window.location = href; });
  }
  document.addEventListener("click", function (e) {
    const a = e.target.closest(".tabs a[href], .pager a[href]");
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (a.origin !== window.location.origin || a.target) return;
    e.preventDefault();
    if (a.classList.contains("active")) return;
    const bar = a.closest(ANCHORS);
    if (bar.classList.contains("tabs")) bar.querySelectorAll("a").forEach(function (x) { x.classList.toggle("active", x === a); });
    swapMain(a.href, true, bar);
  });
  // 목록 행을 누르면 그 건의 수정 칸을 같은 화면에서 연다 (새로고침·맨 위로 튀는 것 없이)
  function markSelected(root) {
    const here = location.pathname + location.search;
    root.querySelectorAll("tr[data-href], .deal-card[data-href]").forEach(function (el) {
      el.classList.toggle("selected", el.getAttribute("data-href") === here);
    });
  }
  document.addEventListener("click", function (e) {      // 보드 카드를 누르면 그 기회의 수정 칸
    const card = e.target.closest(".deal-card[data-href]");
    if (!card || e.target.closest("select, label, a, button") || e.button !== 0) return;
    swapMain(card.dataset.href, true, null, card);
  });
  markSelected(document);
  if (location.hash === "#edit") {                         // 다른 화면(데이터 점검 등)에서 '이 건 고치기'로 왔을 때
    const anchor = document.querySelector("[data-edit-anchor]");
    if (anchor) requestAnimationFrame(function () { anchor.scrollIntoView({ block: "start" }); });
  }
  function openRow(row) {                                  // 다른 화면으로 가는 행은 보통 이동 (사이드바 선택 표시까지 맞게)
    const url = new URL(row.dataset.href, location.href);
    if (url.pathname !== location.pathname) { window.location = url.href; return; }
    swapMain(row.dataset.href, true, null, row);
  }
  document.addEventListener("click", function (e) {
    const row = e.target.closest("tr[data-href]");
    if (!row || e.target.closest("input, a, button, label") || e.button !== 0) return;
    if (e.metaKey || e.ctrlKey || e.shiftKey) { window.open(row.dataset.href, "_blank"); return; }
    openRow(row);
  });
  document.addEventListener("keydown", function (e) {   // 키보드: 행에 초점을 두고 Enter
    const row = e.target.closest && e.target.closest("tr[data-href]");
    if (row && e.key === "Enter") openRow(row);
  });
  window.addEventListener("popstate", function (e) {
    if (e.state && e.state.swapped) swapMain(location.href, false);
  });
  history.replaceState({ swapped: true }, "", location.href);
})();

/* 사이드바 메뉴 편집: 즐겨찾기(☆ → 위쪽 묶음) · 순서(▲▼ 또는 끌어 놓기). 대시보드는 맨 위 고정.
   문서에 한 번만 이벤트를 건다(자재관리와 같은 코드). 저장은 /prefs/menu (core/prefs.py). */
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

/* 영업기회 보드: 카드를 다른 단계 열로 끌어 놓거나, 카드의 '단계' 칸에서 바꾼다.
   저장은 /deals/stage — '단계 변경' 탭과 같은 규칙(필수 조건·이력·감사로그). 막히면 카드는 제자리, 사유를 보여 준다. */
(function () {
  "use strict";
  function won(n) { return "₩ " + Number(n || 0).toLocaleString("ko-KR"); }
  function recount(col, dCount, dSum) {            // 열 머리 건수·합계는 전체 기준(보드에는 일부 카드만) → 더하고 뺀다
    const box = col.querySelector("[data-total-count]");
    const n = Number(box.dataset.totalCount || 0) + dCount, s = Number(box.dataset.totalSum || 0) + dSum;
    box.dataset.totalCount = n;
    box.dataset.totalSum = s;
    col.querySelector("[data-count]").textContent = n.toLocaleString("ko-KR");
    col.querySelector("[data-sum]").textContent = won(s);
  }
  function message(board, text, ok) {
    let box = board.parentNode.querySelector("[data-board-msg]");
    if (!box) {
      box = document.createElement("div");
      box.setAttribute("data-board-msg", "");
      box.setAttribute("role", "status");
      board.parentNode.insertBefore(box, board);
    }
    box.className = "alert " + (ok ? "alert-success" : "alert-error");
    box.textContent = text;
    clearTimeout(box._t);
    box._t = setTimeout(function () { box.remove(); }, ok ? 2500 : 8000);
  }
  function askLostReason() {
    const dlg = document.querySelector("[data-lost-dialog]");
    if (!dlg || !dlg.showModal) {
      return Promise.resolve(window.prompt("실주 사유를 입력하세요") || "");
    }
    const sel = dlg.querySelector("select");
    sel.value = "";
    return new Promise(function (resolve) {
      dlg.addEventListener("close", function onClose() {
        dlg.removeEventListener("close", onClose);
        resolve(dlg.returnValue === "ok" ? sel.value : "");
      });
      dlg.returnValue = "";
      dlg.showModal();
    });
  }
  function move(card, stage) {
    const board = card.closest("[data-board]");
    const from = card.dataset.stage;
    const picker = card.querySelector("[data-board-move]");
    if (!board || stage === from) return;
    const reasonP = stage === "실주" ? askLostReason() : Promise.resolve("");
    reasonP.then(function (reason) {
      if (stage === "실주" && !reason) { if (picker) picker.value = from; return; }
      const body = new URLSearchParams();
      body.append("_csrf", board.dataset.csrf);
      body.append("id", card.dataset.id);
      body.append("stage", stage);
      if (reason) body.append("lost_reason", reason);
      card.classList.add("saving");
      return fetch(board.dataset.stageUrl, { method: "POST", body: body, credentials: "same-origin" })
        .then(function (r) {
          return r.json().catch(function () { return { ok: false, error: "저장하지 못했습니다 (" + r.status + ")" }; });
        })
        .then(function (res) {
          card.classList.remove("saving");
          if (!res.ok) {
            if (picker) picker.value = from;
            const title = card.querySelector(".deal-card-title");
            message(board, (title ? title.textContent.trim() : "") + " — " + res.error, false);
            return;
          }
          const fromCol = card.closest(".board-col");
          const toCol = board.querySelector(".board-col[data-stage=\"" + stage + "\"]");
          toCol.querySelector("[data-drop]").insertBefore(card, toCol.querySelector(".deal-card"));
          card.dataset.stage = stage;
          if (picker) picker.value = stage;
          const amt = Number(card.dataset.amount || 0);
          recount(fromCol, -1, -amt);
          recount(toCol, 1, amt);
          message(board, "‘" + stage + "’ 단계로 옮겼습니다.", true);
        });
    }).catch(function () {
      card.classList.remove("saving");
      if (picker) picker.value = from;
      message(board, "연결이 끊겨 저장하지 못했습니다. 다시 시도하세요.", false);
    });
  }

  document.addEventListener("change", function (e) {
    const sel = e.target.closest && e.target.closest("[data-board-move]");
    if (sel) move(sel.closest(".deal-card"), sel.value);
  });
  let dragged = null;
  document.addEventListener("dragstart", function (e) {
    const card = e.target.closest && e.target.closest("[data-board] .deal-card");
    if (!card) return;
    dragged = card;
    card.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", card.dataset.id);
  });
  document.addEventListener("dragover", function (e) {
    if (!dragged) return;
    const col = e.target.closest && e.target.closest("[data-board] .board-col");
    document.querySelectorAll(".board-col.drop-target").forEach(function (c) { if (c !== col) c.classList.remove("drop-target"); });
    if (!col) return;
    e.preventDefault();
    col.classList.add("drop-target");
  });
  document.addEventListener("drop", function (e) {
    if (!dragged) return;
    const col = e.target.closest && e.target.closest("[data-board] .board-col");
    if (!col) return;
    e.preventDefault();
    col.classList.remove("drop-target");
    move(dragged, col.dataset.stage);
  });
  document.addEventListener("dragend", function () {
    if (dragged) dragged.classList.remove("dragging");
    dragged = null;
    document.querySelectorAll(".board-col.drop-target").forEach(function (c) { c.classList.remove("drop-target"); });
  });
})();

/* 확인이 필요한 폼(data-confirm): 보내기 전에 한 번 묻는다. 다른 제출 처리(중복 방지 등)보다 먼저 — capture 단계. */
document.addEventListener("submit", function (e) {
  const form = e.target;
  if (form.dataset && form.dataset.confirm && !window.confirm(form.dataset.confirm)) {
    e.preventDefault();
    e.stopImmediatePropagation();
  }
}, true);

/* 시연 안내: 배너의 '사용 안내'를 누르거나 #demo-guide 로 오면 접힌 안내를 펼친다 */
(function () {
  function openGuide() {
    const d = document.getElementById("demo-guide");
    if (d) { d.open = true; d.scrollIntoView({ block: "start" }); }
  }
  if (location.hash === "#demo-guide") openGuide();
  document.addEventListener("click", function (e) {
    if (e.target.closest && e.target.closest("[data-open-guide]")) { e.preventDefault(); openGuide(); }
  });
})();

/* 휴대폰 ☰ 메뉴: 사이드바 펼치기/접기 (메뉴를 누르면 접힘) */
(function () {
  function close(btn) {
    document.body.classList.remove("side-open");
    if (btn) { btn.setAttribute("aria-expanded", "false"); btn.textContent = "☰ 메뉴"; }
  }
  document.addEventListener("click", function (e) {
    const btn = e.target.closest && e.target.closest("[data-side-toggle]");
    if (btn) {
      const open = document.body.classList.toggle("side-open");
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      btn.textContent = open ? "✕ 닫기" : "☰ 메뉴";
      if (open) window.scrollTo(0, 0);
      return;
    }
    if (document.body.classList.contains("side-open") && e.target.closest && e.target.closest(".sidebar .menu a[href]")
        && !e.target.closest(".menu.editing")) {
      close(document.querySelector("[data-side-toggle]"));
    }
  });
})();
