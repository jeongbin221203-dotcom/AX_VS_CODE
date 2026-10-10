(function () {
  var BOOT = JSON.parse(document.getElementById('boot').textContent);
  var dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  var UP = dark ? '#ef5b54' : '#d6362f', DOWN = dark ? '#5b8cf0' : '#2b62d9';
  var FG = dark ? '#e4e8f1' : '#1c2230', GRID = dark ? '#262d3b' : '#eef1f6', BG = dark ? '#10141c' : '#ffffff';
  var LC = LightweightCharts, MA = { 5: '#e0a000', 20: '#d6479b', 60: '#2a9d5c', 112: '#0f9d9d', 224: '#555555' };
  var bars = 250, syncing = false, D = null, M = null, focus = BOOT.focus, FI = 0, N = 0;
  function msg(t) { document.getElementById('cmsg').textContent = t || ''; }
  function mk(el, h) {
    return LC.createChart(el, { height: h, width: el.clientWidth, layout: { background: { color: BG }, textColor: FG }, grid: { vertLines: { color: GRID }, horzLines: { color: GRID } },
      rightPriceScale: { borderColor: GRID, minimumWidth: 58 }, timeScale: { borderColor: GRID, rightOffset: 4 }, crosshair: { mode: LC.CrosshairMode.Normal } });
  }
  var ca = mk(document.getElementById('ca'), 300), cb = mk(document.getElementById('cb'), 300);
  [ca, cb].forEach(function (c, i) {
    c.timeScale().subscribeVisibleLogicalRangeChange(function (r) {
      if (syncing || !r) return;
      syncing = true;
      try { (i ? ca : cb).timeScale().setVisibleLogicalRange(r); } catch (e) { /* 아직 데이터 없음 */ }
      syncing = false;
    });
  });
  window.addEventListener('resize', function () {
    ca.applyOptions({ width: document.getElementById('ca').clientWidth });
    cb.applyOptions({ width: document.getElementById('cb').clientWidth });
  });

  function sma(c, n) { var out = [], s = 0; for (var i = 0; i < c.length; i++) { s += c[i]; if (i >= n) s -= c[i - n]; out.push(i >= n - 1 ? s / n : null); } return out; }
  function draw(chart, candles, vols, times, closes) {
    var cs = chart.addCandlestickSeries({ upColor: UP, downColor: DOWN, borderUpColor: UP, borderDownColor: DOWN, wickUpColor: UP, wickDownColor: DOWN });
    var big = closes[closes.length - 1] >= 1000;
    cs.applyOptions({ priceFormat: { type: 'price', precision: big ? 0 : 2, minMove: big ? 1 : 0.01 } });
    cs.setData(candles);
    var v = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol' });
    v.setData(vols);
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
    Object.keys(MA).forEach(function (n) {
      var a = sma(closes, +n), data = [];
      for (var i = 0; i < a.length; i++) if (a[i] != null) data.push({ time: times[i], value: a[i] });
      if (!data.length) return;
      chart.addLineSeries({ color: MA[n], lineWidth: n === '224' ? 2 : 1, priceLineVisible: false, lastValueVisible: false }).setData(data);
    });
    return cs;
  }
  function byTime(a, b) { return a.time < b.time ? -1 : a.time > b.time ? 1 : 0; }

  function build() {
    var cal = M.cal, n = D.d.length, times = [], candles = [], vols = [], closes = D.c;
    for (var i = 0; i < n; i++) {
      var t = cal[D.d[i]]; times.push(t);
      candles.push({ time: t, open: D.o[i], high: D.h[i], low: D.l[i], close: D.c[i] });
      vols.push({ time: t, value: D.v[i], color: (D.c[i] >= D.o[i] ? UP : DOWN) + '99' });
    }
    var fi = focus ? times.findIndex(function (x) { return x >= focus; }) : -1;
    if (fi < 0) fi = n - 1;
    var good = {}; M.good.forEach(function (g) { good[g] = 1; });
    // 예측: 단테 신호 마커
    var A = draw(ca, candles, vols, times, closes), marks = [], recent = [];
    D.g.forEach(function (pair) {
      var i = pair[0], mask = pair[1], labs = [];
      M.dante.forEach(function (j) { if (mask >> j & 1) labs.push(M.labels[j]); });
      var ok = labs.some(function (l) { return good[l]; });
      marks.push({ time: times[i], position: 'belowBar', color: ok ? UP : '#8f98ab', shape: 'arrowUp', text: '' });
      if (i <= fi && i > fi - 6) recent.push(times[i].slice(5) + ' ' + labs.map(function (l) { return l + (good[l] ? ' ✔' : ''); }).join(' · '));
    });
    marks.push({ time: times[fi], position: 'aboveBar', color: '#e0a000', shape: 'arrowDown', text: '기준일' });
    marks.sort(byTime);
    A.setMarkers(marks);
    document.getElementById('ia').textContent = recent.length ? '기준일 전 5봉 신호: ' + recent.reverse().join(' / ') : '기준일 전 5봉 안에 단테 기법 신호가 없습니다.';
    // AI: 확률 선 + 상위 10% 경계 + 돌파 마커
    var B = draw(cb, candles, vols, times, closes), pdata = [], amarks = [], prevTop = false, pf = null;
    for (var k = 0; k < n; k++) {
      if (D.p[k] < 0) { prevTop = false; continue; }
      var pr = D.p[k] / 10, thr = M.thr[cal[D.d[k]]], top = thr != null && D.p[k] / 1000 >= thr;
      pdata.push({ time: times[k], value: pr });
      if (top && !prevTop) amarks.push({ time: times[k], position: 'belowBar', color: '#8a5cd6', shape: 'circle', text: '' });
      prevTop = top;
      if (k === fi) pf = { p: pr, thr: thr };
    }
    amarks.push({ time: times[fi], position: 'aboveBar', color: '#e0a000', shape: 'arrowDown', text: '기준일' });
    amarks.sort(byTime);
    B.setMarkers(amarks);
    var line = cb.addLineSeries({ color: '#8a5cd6', lineWidth: 1, priceScaleId: 'left', priceLineVisible: false, title: 'AI',
      priceFormat: { type: 'custom', minMove: 0.1, formatter: function (v) { return v.toFixed(1) + '%'; } } });
    line.setData(pdata);
    var thrNow = M.thr[times[fi]];
    if (thrNow != null) line.createPriceLine({ price: thrNow * 100, color: '#8a5cd6', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '상위10%' });
    cb.applyOptions({ leftPriceScale: { visible: true, borderColor: GRID, scaleMargins: { top: 0.55, bottom: 0.17 } } });
    document.getElementById('ib').textContent = pf
      ? '기준일 AI 확률 ' + pf.p.toFixed(1) + '%' + (pf.thr != null ? ' (그날 상위 10% 경계 ' + (pf.thr * 100).toFixed(1) + '% — ' + (pf.p / 100 >= pf.thr ? '넘음 ✔' : '못 넘음') + ')' : '')
      : '이 날짜의 AI 확률 기록이 없습니다.';
    FI = fi; N = n;
    range();
    document.getElementById('ctitle').firstChild.textContent = D.n + ' ';
    msg('');
  }
  function range() {
    var before = Math.min(bars, FI + 1), after = Math.max(5, Math.round(bars * 0.35));
    var r = { from: FI - before, to: Math.min(FI + after, N + 4) };
    syncing = true;
    ca.timeScale().setVisibleLogicalRange(r);
    cb.timeScale().setVisibleLogicalRange(r);
    syncing = false;
  }
  document.getElementById('range').addEventListener('click', function (e) {
    var b = e.target.closest('button'); if (!b) return;
    Array.prototype.forEach.call(this.children, function (x) { x.classList.toggle('on', x === b); });
    bars = parseInt(b.getAttribute('data-bars'), 10);
    if (D) range();
  });
  Promise.all([
    fetch('/chartmeta.json').then(function (r) { return r.json(); }),
    fetch('/chartdata/' + encodeURIComponent(BOOT.code) + '.json').then(function (r) { if (!r.ok) throw new Error('이 종목의 차트 데이터가 없습니다'); return r.json(); })
  ]).then(function (x) { M = x[0]; D = x[1]; build(); }).catch(function (e) { msg(e.message || '불러오지 못했습니다'); });
})();
