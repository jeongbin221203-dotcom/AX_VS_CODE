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
  document.addEventListener("click", function (e) {
    const row = e.target.closest("tr[data-href]");
    if (row && !e.target.closest("input, a, button")) window.location = row.dataset.href;
  });
  document.addEventListener("change", function (e) {
    if (e.target.matches("[data-check-all]")) {
      e.target.closest("table").querySelectorAll("tbody input[type=checkbox]")
        .forEach(function (cb) { cb.checked = e.target.checked; });
    }
  });

  function initWidgets(root) {
  // ── 거래처 선택 시 해당 거래처의 영업기회만 보이기 ──────────────────────
  root.querySelectorAll("form[data-deal-filter]").forEach(function (form) {
    const cust = form.querySelector("[name=customer_id]");
    const deal = form.querySelector("[name=deal_id]");
    if (!cust || !deal) return;
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
  function swapMain(href, push, bar) {
    const main = document.querySelector("main.content");
    if (!main || !window.fetch || !window.DOMParser) { window.location = href; return; }
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
  window.addEventListener("popstate", function (e) {
    if (e.state && e.state.swapped) swapMain(location.href, false);
  });
  history.replaceState({ swapped: true }, "", location.href);
})();
