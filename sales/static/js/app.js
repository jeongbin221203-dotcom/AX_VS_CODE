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
    "확정 매출": "#16275E",
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

  function drawCharts() {
    if (!window.Chart) return;
    const css = getComputedStyle(document.documentElement);
    const text = css.getPropertyValue("--muted").trim();
    const line = css.getPropertyValue("--line").trim();
    Chart.defaults.font.family = css.getPropertyValue("--font").trim();
    Chart.defaults.font.size = 12;

    document.querySelectorAll("canvas[data-chart]").forEach(function (el) {
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

  // ── 거래처 선택 시 해당 거래처의 영업기회만 보이기 ──────────────────────
  document.querySelectorAll("form[data-deal-filter]").forEach(function (form) {
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
  document.querySelectorAll("[data-deal-calc]").forEach(function (box) {
    const form = box.closest("form");
    const out = form.querySelector("[data-deal-preview]");
    const la = form.querySelector("[name=list_amount]");
    const dr = form.querySelector("[name=discount_rate]");
    function apply() {
      const list = Number(la.value || 0), rate = Number(dr.value || 0);
      const amount = Math.floor(list * (100 - rate) / 100);
      const role = rate <= 0 ? "" : rate <= 10 ? "팀장" : rate <= 20 ? "임원" : "시스템관리자";
      out.textContent = "제안금액 " + amount.toLocaleString() + "원" +
        (role ? " · 할인 " + rate + "% → " + role + " 승인 필요" : "");
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
  document.querySelectorAll("form[data-sale-calc]").forEach(function (form) {
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
  document.querySelectorAll("form[data-quote]").forEach(function (form) {
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
  document.querySelectorAll("form[data-payment]").forEach(function (form) {
    const sel = form.querySelector("[name=sale_id]"), amt = form.querySelector("[name=amount]");
    sel.addEventListener("change", function () { amt.value = sel.selectedOptions[0].dataset.remain; });
  });

  // ── 증빙: 공급가액·세액을 넣으면 합계를 채운다 (서버가 다시 검증) ────────
  document.querySelectorAll("form.doc-form").forEach(function (form) {
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
  document.querySelectorAll("select[data-preset]").forEach(function (sel) {
    sel.addEventListener("change", function () {
      if (!sel.value) return;
      sel.form.querySelectorAll("[name=sources]").forEach(function (cb) { cb.checked = false; });
      sel.form.submit();
    });
  });

  window.addEventListener("load", drawCharts);
})();
