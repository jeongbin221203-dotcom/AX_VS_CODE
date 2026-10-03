/* 점수 추이·학습량 차트 (Chart.js 로컬 파일) */
(function () {
  "use strict";
  if (!window.Chart) return;
  const css = getComputedStyle(document.documentElement);
  const v = name => css.getPropertyValue(name).trim();
  Chart.defaults.color = v("--ink-2");
  Chart.defaults.borderColor = v("--line");
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;

  const scores = TS.data("score-data");
  const sc = document.getElementById("score-chart");
  if (scores && sc) {
    const label = s => s.finished_at.slice(5, 10).replace("-", "/") + (s.mode === "diagnostic" ? " 진단" : "");
    // 등급 경계선
    const bands = [215, 465, 725, 855];
    new Chart(sc, {
      type: "line",
      data: {
        labels: scores.map(label),
        datasets: [
          { label: "총점", data: scores.map(s => s.total_est), borderColor: v("--accent"), backgroundColor: v("--accent"), tension: .25, pointRadius: 4 },
          { label: "LC", data: scores.map(s => s.lc_est), borderColor: v("--g3"), backgroundColor: v("--g3"), borderDash: [4, 4], pointRadius: 2, hidden: false },
          { label: "RC", data: scores.map(s => s.rc_est), borderColor: v("--g1"), backgroundColor: v("--g1"), borderDash: [4, 4], pointRadius: 2 },
        ],
      },
      options: {
        maintainAspectRatio: false,
        scales: { y: { min: 0, max: 990, ticks: { stepSize: 110 } } },
        plugins: { legend: { position: "bottom", labels: { boxWidth: 12 } } },
      },
      plugins: [{
        id: "bands",
        beforeDraw(chart) {
          const { ctx, chartArea: a, scales: { y } } = chart;
          ctx.save();
          ctx.strokeStyle = v("--line");
          ctx.setLineDash([2, 3]);
          for (const b of bands) {
            const py = y.getPixelForValue(b);
            ctx.beginPath(); ctx.moveTo(a.left, py); ctx.lineTo(a.right, py); ctx.stroke();
          }
          ctx.restore();
        },
      }],
    });
  }

  const daily = TS.data("daily-data");
  const dc = document.getElementById("daily-chart");
  if (daily && dc) {
    new Chart(dc, {
      type: "bar",
      data: {
        labels: daily.map(d => d.date.slice(5).replace("-", "/")),
        datasets: [
          { label: "푼 문항", data: daily.map(d => d.questions), backgroundColor: v("--accent"), borderRadius: 3, yAxisID: "y" },
          { label: "단어 카드", data: daily.map(d => d.words), backgroundColor: v("--g5"), borderRadius: 3, yAxisID: "y" },
        ],
      },
      options: {
        maintainAspectRatio: false,
        scales: { x: { stacked: false, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 10 } }, y: { beginAtZero: true } },
        plugins: { legend: { position: "bottom", labels: { boxWidth: 12 } } },
      },
    });
  }
})();
