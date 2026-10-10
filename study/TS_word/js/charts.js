/* 점수 추이·학습량 차트 (window.Charts) — Chart.js(vendor/chart.umd.min.js)가 읽혀 있어야 그려진다. 없으면 조용히 건너뜀. */
(function () {
  "use strict";
  function setup() {
    if (!window.Chart) return null;
    const css = getComputedStyle(document.documentElement);
    const v = name => css.getPropertyValue(name).trim();
    Chart.defaults.color = v("--ink-2");
    Chart.defaults.borderColor = v("--line");
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    return v;
  }

  /** rows: Stats.scoreHistory() 결과 */
  function score(canvas, rows) {
    const v = setup();
    if (!v || !canvas) return;
    const label = s => s.finished_at.slice(5, 10).replace("-", "/") + (s.mode === "diagnostic" ? " 진단" : "");
    const bands = [215, 465, 725, 855];                         // 등급 경계선
    new Chart(canvas, {
      type: "line",
      data: { labels: rows.map(label), datasets: [
        { label: "총점", data: rows.map(s => s.total_est), borderColor: v("--accent"), backgroundColor: v("--accent"), tension: .25, pointRadius: 4 },
        { label: "LC", data: rows.map(s => s.lc_est), borderColor: v("--g3"), backgroundColor: v("--g3"), borderDash: [4, 4], pointRadius: 2 },
        { label: "RC", data: rows.map(s => s.rc_est), borderColor: v("--g1"), backgroundColor: v("--g1"), borderDash: [4, 4], pointRadius: 2 }] },
      options: { maintainAspectRatio: false, scales: { y: { min: 0, max: 990, ticks: { stepSize: 110 } } },
                 plugins: { legend: { position: "bottom", labels: { boxWidth: 12 } } } },
      plugins: [{ id: "bands", beforeDraw(chart) {
        const { ctx, chartArea: a, scales: { y } } = chart;
        ctx.save(); ctx.strokeStyle = v("--line"); ctx.setLineDash([2, 3]);
        for (const b of bands) { const py = y.getPixelForValue(b); ctx.beginPath(); ctx.moveTo(a.left, py); ctx.lineTo(a.right, py); ctx.stroke(); }
        ctx.restore();
      } }],
    });
  }

  /** rows: Stats.dailyCounts() 결과 */
  function daily(canvas, rows) {
    const v = setup();
    if (!v || !canvas) return;
    new Chart(canvas, {
      type: "bar",
      data: { labels: rows.map(d => d.date.slice(5).replace("-", "/")), datasets: [
        { label: "푼 문항", data: rows.map(d => d.questions), backgroundColor: v("--accent"), borderRadius: 3, yAxisID: "y" },
        { label: "단어 카드", data: rows.map(d => d.words), backgroundColor: v("--g5"), borderRadius: 3, yAxisID: "y" }] },
      options: { maintainAspectRatio: false,
                 scales: { x: { stacked: false, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 10 } }, y: { beginAtZero: true } },
                 plugins: { legend: { position: "bottom", labels: { boxWidth: 12 } } } },
    });
  }
  window.Charts = { score, daily };
})();
