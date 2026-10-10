(function () {
  var BOOT = JSON.parse(document.getElementById('boot').textContent), CODE = BOOT.code;
  var dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  var UP = dark ? '#ef5b54' : '#d6362f', DOWN = dark ? '#5b8cf0' : '#2b62d9';
  var FG = dark ? '#e4e8f1' : '#1c2230', GRID = dark ? '#262d3b' : '#eef1f6', BG = dark ? '#10141c' : '#ffffff';
  // 이평선 세트: a = 256기법에 쓰는 선, b = 14·28·56…(2배씩)
  var SETS = {
    a: { label: '256 세트 · 5·20·60·112·224', periods: [5, 20, 60, 112, 224],
         colors: { 5: '#e0a000', 20: '#d6479b', 60: '#2a9d5c', 112: '#0f9d9d', 224: '#555555' } },
    b: { label: '14·28·56·112·224·448', periods: [14, 28, 56, 112, 224, 448],
         colors: { 14: '#e0a000', 28: '#d6479b', 56: '#2a9d5c', 112: '#0f9d9d', 224: '#555555', 448: '#2b62d9' } }
  };
  var maMode = BOOT.ma || 'a';  // a | b | ab | none
  var LC = LightweightCharts;
  var state = { tf: 'D', bars: 250, data: null };

  function opts(h) {
    return {
      height: h, layout: { background: { color: BG }, textColor: FG }, grid: { vertLines: { color: GRID }, horzLines: { color: GRID } },
      rightPriceScale: { borderColor: GRID, minimumWidth: 78 }, timeScale: { borderColor: GRID, rightOffset: 4 },
      crosshair: { mode: LC.CrosshairMode.Normal }
    };
  }
  var mainEl = document.getElementById('main'), p1El = document.getElementById('p1'), p2El = document.getElementById('p2');
  var main = LC.createChart(mainEl, Object.assign(opts(460), { width: mainEl.clientWidth }));
  var sub1 = LC.createChart(p1El, Object.assign(opts(130), { width: p1El.clientWidth }));
  var sub2 = LC.createChart(p2El, Object.assign(opts(130), { width: p2El.clientWidth }));

  var candle = main.addCandlestickSeries({ upColor: UP, downColor: DOWN, borderUpColor: UP, borderDownColor: DOWN, wickUpColor: UP, wickDownColor: DOWN });
  var vol = main.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol' });
  main.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  var maMain = [], maSecond = [];
  var main2 = null, candle2 = null;
  var bbSeries = ['upper', 'mid', 'lower'].map(function (k) {
    return main.addLineSeries({ color: '#8f98ab', lineWidth: 1, lineStyle: k === 'mid' ? 2 : 0, priceLineVisible: false, lastValueVisible: false });
  });
  var levelLines = [];
  var subSeries = [[], []];

  // 시간 축 동기화 — 보조 차트는 지표 계산 시작이 늦어 봉 개수가 다르므로 '시간' 범위로 맞춘다
  var syncing = false;
  function allCharts() { return main2 ? [main, sub1, sub2, main2] : [main, sub1, sub2]; }
  function link(c) {
    c.timeScale().subscribeVisibleLogicalRangeChange(function () {
      if (syncing) return;
      var r = c.timeScale().getVisibleRange();
      if (!r) return;
      syncing = true;
      allCharts().forEach(function (o) { if (o !== c) try { o.timeScale().setVisibleRange(r); } catch (e) { /* 데이터 없음 */ } });
      syncing = false;
    });
  }
  [main, sub1, sub2].forEach(link);
  window.addEventListener('resize', function () {
    main.applyOptions({ width: mainEl.clientWidth }); sub1.applyOptions({ width: p1El.clientWidth });
    sub2.applyOptions({ width: p2El.clientWidth });
    if (main2) main2.applyOptions({ width: document.getElementById('main2').clientWidth });
  });

  function fmt(n) { return Math.round(n).toLocaleString('ko-KR'); }
  function on(name) { var e = document.querySelector('[data-ov="' + name + '"]'); return e && e.checked; }
  function msg(t) { var m = document.getElementById('msg'); m.hidden = !t; m.textContent = t || ''; }

  function clearSub(i) { subSeries[i].forEach(function (s) { (i ? sub2 : sub1).removeSeries(s); }); subSeries[i] = []; }
  function fillSub(i, kind) {
    var ch = i ? sub2 : sub1, el = i ? p2El : p1El, d = state.data;
    clearSub(i);
    el.style.display = kind ? '' : 'none';
    if (!kind) return;
    var add = function (s) { subSeries[i].push(s); return s; };
    if (kind === 'rsi') {
      var r = add(ch.addLineSeries({ color: '#8a5cd6', lineWidth: 1, priceLineVisible: false }));
      r.setData(d.rsi);
      r.createPriceLine({ price: 70, color: UP, lineWidth: 1, lineStyle: 2, axisLabelVisible: false });
      r.createPriceLine({ price: 30, color: DOWN, lineWidth: 1, lineStyle: 2, axisLabelVisible: false });
    } else if (kind === 'macd') {
      var h = add(ch.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false }));
      h.setData(d.macd.hist.map(function (x) { return { time: x.time, value: x.value, color: x.value >= 0 ? UP : DOWN }; }));
      add(ch.addLineSeries({ color: '#e0a000', lineWidth: 1, priceLineVisible: false, lastValueVisible: false })).setData(d.macd.macd);
      add(ch.addLineSeries({ color: '#2a9d5c', lineWidth: 1, priceLineVisible: false, lastValueVisible: false })).setData(d.macd.signal);
    } else {
      add(ch.addLineSeries({ color: '#e0a000', lineWidth: 1, priceLineVisible: false, lastValueVisible: false })).setData(d.stoch.k);
      var sd = add(ch.addLineSeries({ color: '#2a9d5c', lineWidth: 1, priceLineVisible: false, lastValueVisible: false }));
      sd.setData(d.stoch.d);
      sd.createPriceLine({ price: 80, color: UP, lineWidth: 1, lineStyle: 2, axisLabelVisible: false });
      sd.createPriceLine({ price: 20, color: DOWN, lineWidth: 1, lineStyle: 2, axisLabelVisible: false });
    }
  }

  // ---- 이평선 세트 ----
  function periodsOnMain() { return maMode === 'b' ? SETS.b.periods : maMode === 'none' ? [] : SETS.a.periods; }
  function drawMA(chart, store, set) {
    store.forEach(function (s) { chart.removeSeries(s); });
    store.length = 0;
    if (!set || !state.data) return;
    set.periods.forEach(function (n) {
      var data = state.data.ma[n]; if (!data || !data.length) return;
      var s = chart.addLineSeries({ color: set.colors[n], lineWidth: n === 224 ? 2 : 1, lineStyle: n === 448 ? 2 : 0,
                                    priceLineVisible: false, lastValueVisible: false, title: '' });
      s.setData(data); store.push(s);
    });
  }
  function ensureMain2() {
    if (main2) return;
    var el = document.getElementById('main2');
    main2 = LC.createChart(el, Object.assign(opts(340), { width: el.clientWidth }));
    candle2 = main2.addCandlestickSeries({ upColor: UP, downColor: DOWN, borderUpColor: UP, borderDownColor: DOWN, wickUpColor: UP, wickDownColor: DOWN });
    link(main2);
  }
  function applyMA() {
    var wrap = document.getElementById('main2wrap');
    drawMA(main, maMain, maMode === 'b' ? SETS.b : maMode === 'none' ? null : SETS.a);
    if (maMode === 'ab') {
      wrap.hidden = false; ensureMain2();
      document.getElementById('main2title').textContent = SETS.b.label;
      if (state.data) {
        var big = state.data.last.close >= 1000;
        candle2.applyOptions({ priceFormat: { type: 'price', precision: big ? 0 : 2, minMove: big ? 1 : 0.01 } });
        candle2.setData(state.data.candles);
      }
      drawMA(main2, maSecond, SETS.b);
      main2.applyOptions({ width: document.getElementById('main2').clientWidth });
      var r = main.timeScale().getVisibleRange(); if (r) try { main2.timeScale().setVisibleRange(r); } catch (e) { /* */ }
    } else {
      wrap.hidden = true;
    }
    var title = maMode === 'ab' ? SETS.a.label : '';
    document.getElementById('techsum').setAttribute('data-ma', title);
    Object.keys(tech).forEach(drawTech);   // 이평선과 겹치는 기법 선은 다시 판단
  }

  function applyOverlays() {
    var d = state.data; if (!d) return;
    ['upper', 'mid', 'lower'].forEach(function (k, i) { bbSeries[i].setData(on('bb') ? d.bb[k] : []); });
    levelLines.forEach(function (l) { candle.removePriceLine(l); }); levelLines = [];
    if (on('levels')) d.levels.forEach(function (l) {
      levelLines.push(candle.createPriceLine({
        price: l.price, color: l.kind === 'resistance' ? UP : DOWN, lineWidth: 1, lineStyle: 2,
        title: (l.kind === 'resistance' ? '저항 ' : '지지 ') + l.touches + '회'
      }));
    });
    refreshMarkers();
    main.priceScale('right').applyOptions({ mode: on('log') ? LC.PriceScaleMode.Logarithmic : LC.PriceScaleMode.Normal });
  }

  // ---- 기법: 클릭하면 이 차트에 그린다 ----
  var tech = {};  // name -> {data, series[], levels[]}
  function refreshMarkers() {
    var mk = [];
    if (on('markers') && state.data) state.data.signals.filter(function (s) { return s.side !== 'info'; }).forEach(function (s) {
      mk.push({ time: s.date, position: s.side === 'buy' ? 'belowBar' : 'aboveBar', color: s.side === 'buy' ? UP : DOWN,
                shape: s.side === 'buy' ? 'arrowUp' : 'arrowDown', text: (s.key === 'golden' || s.key === 'dead') ? s.label : '' });
    });
    Object.keys(tech).forEach(function (n) {
      var ms = tech[n].data.markers;
      ms.forEach(function (m, i) {
        mk.push({ time: m.date, position: m.side === 'sell' ? 'aboveBar' : 'belowBar',
                  color: m.side === 'buy' ? UP : m.side === 'sell' ? DOWN : '#8f98ab',
                  shape: m.side === 'buy' ? 'arrowUp' : m.side === 'sell' ? 'arrowDown' : 'circle',
                  text: i >= ms.length - 3 ? m.label : '' });  // 글자는 최근 3개만(겹침 방지)
      });
    });
    mk.sort(function (a, b) { return a.time < b.time ? -1 : a.time > b.time ? 1 : 0; });
    candle.setMarkers(mk);
    listSignals();
  }
  function drawTech(name) {
    var t = tech[name]; if (!t) return;
    (t.series || []).forEach(function (s) { main.removeSeries(s); });
    (t.levels || []).forEach(function (l) { candle.removePriceLine(l); });
    var shown = periodsOnMain();
    t.series = t.data.lines.filter(function (l) {
      var m = /^(\d+)일선$/.exec(l.name);
      return !(m && shown.indexOf(parseInt(m[1], 10)) >= 0);   // 같은 선이 이미 있으면 겹쳐 그리지 않음
    }).map(function (l) {
      var s = main.addLineSeries({ color: l.color, lineWidth: 1, lineStyle: l.dash ? 2 : 0, priceLineVisible: false, lastValueVisible: false, title: '' });
      s.setData(l.data); return s;
    });
    t.levels = t.data.levels.map(function (l) {
      return candle.createPriceLine({ price: l.price, color: l.color, lineWidth: 1, lineStyle: 2, title: l.title });
    });
  }
  function showSummaries() {
    var box = document.getElementById('techsum'); box.textContent = '';
    Object.keys(tech).forEach(function (n) {
      var row = document.createElement('div');
      var a = document.createElement('a'); a.href = '/technique/' + encodeURIComponent(n); a.textContent = n; a.className = 'tag';
      row.appendChild(a); row.appendChild(document.createTextNode(' ' + (tech[n].data.summary || '')));
      box.appendChild(row);
    });
  }
  function addTech(name) {
    return fetch('/api/technique/' + encodeURIComponent(CODE) + '?name=' + encodeURIComponent(name) + '&tf=' + state.tf)
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || '오류'); return j; }); })
      .then(function (j) { tech[name] = { data: j }; drawTech(name); refreshMarkers(); showSummaries(); })
      .catch(function (e) { msg(name + ': ' + e.message); });
  }
  function removeTech(name) {
    var t = tech[name]; if (!t) return;
    (t.series || []).forEach(function (s) { main.removeSeries(s); });
    (t.levels || []).forEach(function (l) { candle.removePriceLine(l); });
    delete tech[name]; refreshMarkers(); showSummaries();
  }
  function reloadTechs() {  // 일/주/월을 바꾸면 기법도 다시 계산
    var names = Object.keys(tech);
    names.forEach(removeTech);
    names.forEach(addTech);
  }
  document.getElementById('techs').addEventListener('click', function (e) {
    var b = e.target.closest('button.tech'); if (!b || b.disabled) return;
    var n = b.getAttribute('data-tech'), on_ = !b.classList.contains('on');
    b.classList.toggle('on', on_);
    if (on_) addTech(n); else removeTech(n);
  });

  function listSignals() {
    var box = document.getElementById('sigs'); box.textContent = '';
    var rows = [];
    if (state.data) state.data.signals.forEach(function (x) { rows.push({ date: x.date, side: x.side, label: x.label, why: x.why, src: '' }); });
    Object.keys(tech).forEach(function (n) {
      tech[n].data.markers.forEach(function (m) { rows.push({ date: m.date, side: m.side, label: m.label, why: m.why, src: n }); });
    });
    rows.sort(function (p, q) { return p.date < q.date ? 1 : p.date > q.date ? -1 : 0; });
    rows.slice(0, 120).forEach(function (s) {
      var row = document.createElement('div');
      var d = document.createElement('span'); d.className = 'd'; d.textContent = s.date;
      var t = document.createElement('span'); t.className = 'tag ' + s.side; t.textContent = (s.src ? s.src + ' · ' : '') + s.label;
      var w = document.createElement('span'); w.textContent = s.why;
      row.append(d, t, w); box.appendChild(row);
    });
    if (!rows.length) box.textContent = '이 기간에는 신호가 없습니다.';
  }

  function render() {
    var d = state.data;
    var big = d.last.close >= 1000;
    candle.applyOptions({ priceFormat: { type: 'price', precision: big ? 0 : 2, minMove: big ? 1 : 0.01 } });
    candle.setData(d.candles);
    vol.setData(d.volume.map(function (v) { return { time: v.time, value: v.value, color: (v.up ? UP : DOWN) + '99' }; }));
    var l = d.last, pr = document.getElementById('price');
    pr.textContent = fmt(l.close) + '  ' + (l.change >= 0 ? '+' : '') + l.change.toFixed(2) + '%';
    pr.className = 'price ' + (l.change > 0 ? 'up' : l.change < 0 ? 'down' : '');
    fillSub(0, document.getElementById('sub1').value);
    fillSub(1, document.getElementById('sub2').value);
    applyOverlays(); applyMA(); listSignals();
    var n = d.candles.length, show = Math.min(state.bars, n);
    main.timeScale().setVisibleLogicalRange({ from: n - show - 1, to: n + 4 });
  }

  var loaded = false;
  function load() {
    msg('불러오는 중…');
    fetch('/api/chart/' + encodeURIComponent(CODE) + '?tf=' + state.tf + '&bars=5000').then(function (r) {
      return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || '오류'); return j; });
    }).then(function (j) { state.data = j; msg(''); render(); if (!loaded) { loaded = true; BOOT.active.forEach(addTech); } else reloadTechs(); }).catch(function (e) { msg(e.message); });
  }

  // 툴팁
  var tip = document.getElementById('tip');
  main.subscribeCrosshairMove(function (p) {
    if (!p.time || !p.point || !state.data) { tip.style.display = 'none'; return; }
    var c = p.seriesData.get(candle), v = p.seriesData.get(vol);
    if (!c) { tip.style.display = 'none'; return; }
    var chg = ((c.close / c.open - 1) * 100).toFixed(2);
    tip.textContent = p.time + '\n시 ' + fmt(c.open) + '  고 ' + fmt(c.high) + '\n저 ' + fmt(c.low) + '  종 ' + fmt(c.close) +
      ' (' + chg + '%)' + (v ? '\n거래량 ' + fmt(v.value) : '');
    var r = mainEl.getBoundingClientRect();
    tip.style.display = 'block';
    tip.style.left = Math.min(p.point.x + r.left + window.scrollX + 14, window.scrollX + document.documentElement.clientWidth - 200) + 'px';
    tip.style.top = (r.top + window.scrollY + 8) + 'px';
  });

  // 컨트롤
  function group(id, attr, fn) {
    document.getElementById(id).addEventListener('click', function (e) {
      var b = e.target.closest('button'); if (!b) return;
      Array.prototype.forEach.call(this.children, function (x) { x.classList.toggle('on', x === b); });
      fn(b.getAttribute(attr));
    });
  }
  group('mapreset', 'data-set', function (v) {
    maMode = v;
    try { var u = new URL(location.href); u.searchParams.set('ma', v); history.replaceState(null, '', u); } catch (e) { /* */ }
    if (state.data) applyMA();
  });
  group('tf', 'data-tf', function (v) { state.tf = v; state.bars = v === 'D' ? 250 : v === 'W' ? 150 : 60; load(); });
  group('range', 'data-bars', function (v) { state.bars = parseInt(v, 10); if (state.data) render(); });
  document.getElementById('overlays').addEventListener('change', function () { if (state.data) applyOverlays(); });
  ['sub1', 'sub2'].forEach(function (id, i) {
    document.getElementById(id).addEventListener('change', function () { if (state.data) fillSub(i, this.value); });
  });
  document.getElementById('star').addEventListener('click', function () {
    var b = this;
    fetch('/api/watch/' + encodeURIComponent(CODE), { method: 'POST' }).then(function (r) { return r.json(); })
      .then(function (j) { b.textContent = j.star ? '★' : '☆'; });
  });
  load();
})();
