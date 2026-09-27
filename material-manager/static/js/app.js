/* 화면 보조 스크립트: 차트만 그린다.
   스크립트(또는 CDN)가 없어도 조회·등록은 모두 서버에서 동작한다. */
(function () {
  "use strict";

  // 색은 계열 이름으로 고정: 입고 청색, 출고 적색, 금액 청색
  const BLUE = "#2747A3", RED = "#C23A2E", GRAY = "#8A94A3";
  const SERIES_COLOR = { "입고": BLUE, "출고": RED, "재고금액": BLUE };
  const FALLBACK = [BLUE, RED, GRAY];

  // 금액 축은 값이 크면 백만원 단위로 표기한다
  function moneyUnit(datasets) {
    const max = Math.max.apply(null, datasets.flatMap(function (d) { return d.data.map(Math.abs); }).concat([0]));
    return max >= 1e6 ? { div: 1e6, title: "백만원" } : { div: 1, title: "원" };
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
    if (e.target.matches("[data-autosubmit]")) e.target.form.submit();
  });

  // 차트 높이 (인라인 style 대신 data-height)
  document.querySelectorAll(".chart[data-height]").forEach(function (el) {
    el.style.height = el.dataset.height + "px";
  });

  // 2단계 인증 등록 QR (서버로 비밀키를 다시 보내지 않고 브라우저에서 그린다)
  document.querySelectorAll("[data-qr]").forEach(function (el) {
    if (!window.qrcode) return;
    const qr = window.qrcode(0, "M");
    qr.addData(el.dataset.qr);
    qr.make();
    el.innerHTML = qr.createSvgTag({ cellSize: 5, margin: 4, scalable: false });
  });

  window.addEventListener("load", drawCharts);
})();
