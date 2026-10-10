(function () {
  var BOOT = JSON.parse(document.getElementById('boot').textContent);
  var dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  var UP = dark ? '#ef5b54' : '#d6362f', DOWN = dark ? '#5b8cf0' : '#2b62d9';
  var FG = dark ? '#e4e8f1' : '#1c2230', GRID = dark ? '#262d3b' : '#eef1f6', BG = dark ? '#10141c' : '#ffffff';
  var LC = LightweightCharts;
  var SETS = {
    a: { periods: [5, 20, 60, 112, 224], colors: { 5: '#e0a000', 20: '#d6479b', 60: '#2a9d5c', 112: '#0f9d9d', 224: '#555555' } },
    b: { periods: [14, 28, 56, 112, 224, 448], colors: { 14: '#e0a000', 28: '#d6479b', 56: '#2a9d5c', 112: '#0f9d9d', 224: '#555555', 448: '#2b62d9' } },
    none: { periods: [], colors: {} }
  };
  var bars = 250, syncing = false, crossing = false;
  var tip = document.getElementById('tip');

  var INTRA = { '1m': 1, '5m': 1, '15m': 1, '30m': 1, '60m': 1 };
  function isIntra(tf) { return !!INTRA[tf]; }
  function fmtTime(t) { return typeof t === 'number' ? new Date(t * 1000).toISOString().slice(0, 16).replace('T', ' ') : t; }
  function msg(t) { var m = document.getElementById('cmsg'); m.hidden = !t; m.textContent = t || ''; }
  function fmt(n) { return Math.round(n).toLocaleString('ko-KR'); }
  // 동기화는 같은 종류끼리만 — 분봉의 '오늘 하루'를 일봉에 맞추면 일봉이 2개만 찌그러져 보인다
  function compat() {
    if (typeof panes === 'undefined' || !panes || !panes.a || !panes.b) return true;
    var ta = panes.a.settings().tf, tb = panes.b.settings().tf;
    return isIntra(ta) || isIntra(tb) ? ta === tb : true;   // 분봉은 같은 간격끼리만, 일·주·월봉은 서로 맞춘다
  }
  function syncOn() { return document.getElementById('sync').checked && compat(); }

  function Pane(side) {
    var self = this;
    this.side = side;
    this.root = document.querySelector('.cpane[data-side="' + side + '"]');
    this.el = this.root.querySelector('.cchart');
    this.code = BOOT[side].code;
    this.chart = LC.createChart(this.el, {
      height: 520, width: this.el.clientWidth,
      layout: { background: { color: BG }, textColor: FG }, grid: { vertLines: { color: GRID }, horzLines: { color: GRID } },
      rightPriceScale: { borderColor: GRID, minimumWidth: 78 }, timeScale: { borderColor: GRID, rightOffset: 4 },
      crosshair: { mode: LC.CrosshairMode.Normal }
    });
    this.candle = this.chart.addCandlestickSeries({ upColor: UP, downColor: DOWN, borderUpColor: UP, borderDownColor: DOWN, wickUpColor: UP, wickDownColor: DOWN });
    this.vol = this.chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol' });
    this.chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
    this.lines = [];
    this.data = null;
    this.pred = null;        // 겹쳐 그린 '예측'(단테 기법) 또는 'AI' 응답
    this.predSeries = [];    // AI 확률 선
    this.probMap = {};       // 날짜 → AI 확률(툴팁용)
    this.predSeq = 0;

    // 확대·이동 동기화 (시간 범위 기준 — 종목·봉 단위가 달라도 같은 기간을 보여 줌)
    this.chart.timeScale().subscribeVisibleLogicalRangeChange(function () {
      if (syncing || !syncOn()) return;
      var r = self.chart.timeScale().getVisibleRange();
      if (!r) return;
      syncing = true;
      try { other(self).chart.timeScale().setVisibleRange(r); } catch (e) { /* 상대 차트에 그 기간 데이터가 없음 */ }
      syncing = false;
    });
    // 십자선 동기화
    this.chart.subscribeCrosshairMove(function (p) {
      var o = other(self);
      if (crossing || !syncOn() || !o.data) return;
      crossing = true;
      try {
        if (!p.time) o.chart.clearCrosshairPosition();
        else {
          var c = p.seriesData.get(self.candle);
          if (c) o.chart.setCrosshairPosition(c.close, p.time, o.candle);
        }
      } catch (e) { /* 상대 차트에 그 날짜 봉이 없음 */ }
      crossing = false;
      if (p.time && p.point && p.seriesData.get(self.candle)) showTip(self, p);
      else tip.style.display = 'none';
    });
    this.bind();
  }

  function other(p) { return p.side === 'a' ? panes.b : panes.a; }

  function showTip(pane, p) {
    var c = p.seriesData.get(pane.candle), v = p.seriesData.get(pane.vol);
    var pr = pane.probMap[p.time];
    tip.textContent = fmtTime(p.time) + '\n시 ' + fmt(c.open) + '  고 ' + fmt(c.high) + '\n저 ' + fmt(c.low) + '  종 ' + fmt(c.close) +
      ' (' + ((c.close / c.open - 1) * 100).toFixed(2) + '%)' + (v ? '\n거래량 ' + fmt(v.value) : '') +
      (pr !== undefined ? '\nAI 확률 ' + (pr * 100).toFixed(1) + '%' : '');
    var r = pane.el.getBoundingClientRect();
    tip.style.display = 'block';
    tip.style.left = Math.min(p.point.x + r.left + window.scrollX + 14, window.scrollX + document.documentElement.clientWidth - 210) + 'px';
    tip.style.top = (r.top + window.scrollY + 8) + 'px';
  }

  Pane.prototype.settings = function () {
    return { set: this.root.querySelector('.cset').value, tf: this.root.querySelector('.ctf').value, kind: this.root.querySelector('.ckind').value };
  };

  // ---- 겹쳐 그리기: 예측(단테 기법 신호 마커) / AI(확률 선 + 상위 10% 마커) ----
  Pane.prototype.clearPred = function () {
    var self = this;
    this.predSeries.forEach(function (s) { self.chart.removeSeries(s); });
    this.predSeries = []; this.probMap = {}; this.pred = null;
    this.candle.setMarkers(this.focusMarkers());
    if (this.chart.options().leftPriceScale.visible) this.keepRange(function () { self.chart.applyOptions({ leftPriceScale: { visible: false } }); });
    this.root.querySelector('.cpred').textContent = '';
  };

  Pane.prototype.loadPred = function () {
    var self = this, s = this.settings(), seq = ++this.predSeq;
    this.clearPred();
    if (s.kind === 'none' || !this.data) return Promise.resolve();
    if (s.kind === 'ai' && isIntra(s.tf)) { this.root.querySelector('.cpred').textContent = 'AI(상승 확률)는 일봉·주봉·월봉에서만 볼 수 있습니다. 분봉에서는 예측(단테 기법)을 쓰세요.'; return Promise.resolve(); }
    return fetch('/api/predict/' + encodeURIComponent(this.code) + '?kind=' + s.kind + '&tf=' + s.tf + '&bars=5000').then(function (r) {
      return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || '오류'); return j; });
    }).then(function (j) {
      if (seq !== self.predSeq || self.settings().kind !== s.kind) return;   // 그사이 설정이 바뀜
      self.pred = j;
      self.drawPred();
    }).catch(function (e) {
      if (seq === self.predSeq) self.root.querySelector('.cpred').textContent = (s.kind === 'ai' ? 'AI' : '예측') + ': ' + e.message;
    });
  };

  // 왼쪽 눈금(AI %)이 생기거나 없어지면 차트 폭이 바뀌어 보이는 범위가 밀린다 → 바꾸기 전 범위를 되돌린다
  Pane.prototype.keepRange = function (fn) {
    var self = this, keep = this.chart.timeScale().getVisibleRange();
    fn();
    function restore() { if (keep) { syncing = true; try { self.chart.timeScale().setVisibleRange(keep); } catch (e) { /* */ } syncing = false; } }
    restore(); setTimeout(restore, 60);
  };

  Pane.prototype.drawPred = function () {
    var self = this, j = this.pred, box = this.root.querySelector('.cpred');
    if (!j) return;
    var have = {};
    this.data.candles.forEach(function (c) { have[c.time] = true; });
    var ms = j.markers.filter(function (m) { return have[m.date]; });
    var mk = ms.map(function (m, i) {
      return { time: m.date, position: 'belowBar', color: j.kind === 'ai' ? '#8a5cd6' : (m.good ? UP : '#8f98ab'),
               shape: j.kind === 'ai' ? 'circle' : 'arrowUp', text: i >= ms.length - 3 ? m.label : '' };   // 글자는 최근 3개만
    });
    mk = mk.concat(this.focusMarkers());
    mk.sort(function (p, q) { return p.time < q.time ? -1 : p.time > q.time ? 1 : 0; });
    this.candle.setMarkers(mk);
    if (j.kind === 'ai') {
      // AI 확률(%)은 왼쪽 눈금에 — 가격(오른쪽)과 섞이지 않게
      var line = this.chart.addLineSeries({ color: '#8a5cd6', lineWidth: 1, priceScaleId: 'left', priceLineVisible: false, lastValueVisible: true,
                                            title: 'AI', priceFormat: { type: 'custom', minMove: 0.1, formatter: function (v) { return v.toFixed(1) + '%'; } } });
      line.setData(j.prob.map(function (p) { return { time: p.time, value: p.value * 100 }; }));
      line.createPriceLine({ price: j.thr * 100, color: '#8a5cd6', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '상위 10%' });
      this.keepRange(function () { self.chart.applyOptions({ leftPriceScale: { visible: true, borderColor: GRID, scaleMargins: { top: 0.55, bottom: 0.17 } } }); });
      this.predSeries.push(line);
      j.prob.forEach(function (p) { self.probMap[p.time] = p.value; });
    }
    box.textContent = '';
    var b = document.createElement('b'); b.textContent = j.name + ' · '; box.appendChild(b);
    box.appendChild(document.createTextNode(j.summary + (j.note ? '\n' + j.note : '')));
    if (j.kind === 'dante' && ms.length) {
      var last = ms.slice(-5).reverse().map(function (m) { return (m.shown || m.date) + ' ' + m.label + (m.good ? ' ✔' : '') + (m.stat ? ' — ' + m.stat : ''); });
      box.appendChild(document.createTextNode('\n최근 신호: ' + last.join(' / ')));
    }
  };

  // 분봉은 1분마다 새로 받는다(탭이 보일 때만)
  Pane.prototype.setupRefresh = function (tf) {
    var self = this;
    if (this.timer) { clearInterval(this.timer); this.timer = null; }
    if (!isIntra(tf)) return;
    this.timer = setInterval(function () {
      if (document.hidden || self.settings().tf !== tf) return;
      var keep = self.chart.timeScale().getVisibleLogicalRange();
      self.load().then(function () { if (keep) { syncing = true; try { self.chart.timeScale().setVisibleLogicalRange(keep); } catch (e) { /* */ } syncing = false; } });
    }, 60000);
  };

  Pane.prototype.load = function () {
    var self = this, s = this.settings();
    return fetch('/api/chart/' + encodeURIComponent(this.code) + '?tf=' + s.tf + '&bars=5000').then(function (r) {
      return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || '오류'); return j; });
    }).then(function (d) {
      self.data = d;
      self.chart.applyOptions({ timeScale: { timeVisible: isIntra(s.tf), secondsVisible: false } });
      self.setupRefresh(s.tf);
      var big = d.last.close >= 1000;
      self.candle.applyOptions({ priceFormat: { type: 'price', precision: big ? 0 : 2, minMove: big ? 1 : 0.01 } });
      self.candle.setData(d.candles);
      self.vol.setData(d.volume.map(function (v) { return { time: v.time, value: v.value, color: (v.up ? UP : DOWN) + '99' }; }));
      self.drawMA();
      var pr = self.root.querySelector('.cprice');
      pr.textContent = fmt(d.last.close) + '  ' + (d.last.change >= 0 ? '+' : '') + d.last.change.toFixed(2) + '%';
      pr.className = 'cprice ' + (d.last.change > 0 ? 'up' : d.last.change < 0 ? 'down' : '');
      self.root.querySelector('.open').href = '/chart/' + encodeURIComponent(self.code);
      msg('');
      return self.loadPred();
    }).catch(function (e) { msg(self.code + ': ' + e.message); });
  };

  Pane.prototype.drawMA = function () {
    var self = this, set = SETS[this.settings().set];
    this.lines.forEach(function (l) { self.chart.removeSeries(l); });
    this.lines = [];
    if (!this.data) return;
    set.periods.forEach(function (n) {
      var d = self.data.ma[n]; if (!d || !d.length) return;
      var s = self.chart.addLineSeries({ color: set.colors[n], lineWidth: n === 224 ? 2 : 1, lineStyle: n === 448 ? 2 : 0,
                                         priceLineVisible: false, lastValueVisible: false, title: '' });
      s.setData(d); self.lines.push(s);
    });
  };

  // 기준일(신호 화면에서 넘어온 날짜): 그날(없으면 그 뒤 첫 봉)의 봉 번호·표시 시각
  Pane.prototype.focusIndex = function () {
    if (!BOOT.focus || !this.data || isIntra(this.settings().tf)) return -1;
    var c = this.data.candles, i = c.findIndex(function (x) { return x.time >= BOOT.focus; });
    return i < 0 ? c.length - 1 : i;
  };
  Pane.prototype.focusMarkers = function () {
    var i = this.focusIndex();
    return i < 0 ? [] : [{ time: this.data.candles[i].time, position: 'aboveBar', color: '#e0a000', shape: 'arrowDown', text: '기준일' }];
  };

  Pane.prototype.showRange = function () {
    if (!this.data) return;
    var n = this.data.candles.length, show = Math.min(bars, n), f = this.focusIndex();
    if (f >= 0) {   // 기준일을 오른쪽 3분의 2 지점에 — 그 전 흐름과 그 뒤 며칠을 함께 본다
      var before = Math.min(show, f + 1), after = Math.max(5, Math.round(show * 0.35));
      this.chart.timeScale().setVisibleLogicalRange({ from: f - before, to: Math.min(f + after, n + 4) });   // 그 뒤 데이터가 모자라면 끝에서 멈춤
      return;
    }
    this.chart.timeScale().setVisibleLogicalRange({ from: n - show - 1, to: n + 4 });
  };

  Pane.prototype.setStock = function (code, name) {
    this.code = code;
    this.root.querySelector('.cq').value = name || code;
    this.root.querySelector('.cname').textContent = name || code;
    return this.load();
  };

  Pane.prototype.bind = function () {
    var self = this, q = this.root.querySelector('.cq'), box = this.root.querySelector('.cres'), timer = null, items = [];
    function pick(it) {
      box.hidden = true;
      // '같은 종목'이 켜져 있으면 한쪽에 입력해도 양쪽 모두 그 종목으로(왼쪽 예측 · 오른쪽 AI 를 한 종목으로 보는 용도)
      var targets = document.getElementById('same').checked ? [self, other(self)] : [self];
      Promise.all(targets.map(function (p) { return p.setStock(it.code, it.name); })).then(function () {
        if (targets.length > 1) showBoth(); else self.showRange();
        saveUrl();
      });
    }
    q.addEventListener('input', function () {
      clearTimeout(timer);
      var v = q.value.trim();
      if (!v) { box.hidden = true; return; }
      timer = setTimeout(function () {
        fetch('/api/search?q=' + encodeURIComponent(v)).then(function (r) { return r.json(); }).then(function (list) {
          items = list; box.textContent = '';
          list.forEach(function (it) {
            var li = document.createElement('li');
            li.appendChild(document.createTextNode(it.name));
            var sm = document.createElement('small'); sm.textContent = it.code + ' ' + (it.market || ''); li.appendChild(sm);
            li.addEventListener('mousedown', function () { pick(it); });
            box.appendChild(li);
          });
          box.hidden = !list.length;
        });
      }, 150);
    });
    q.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        if (items[0]) pick(items[0]);
        else if (/^[A-Za-z0-9.\-]{1,12}$/.test(q.value.trim())) pick({ code: q.value.trim(), name: q.value.trim() });
      }
    });
    q.addEventListener('blur', function () { setTimeout(function () { box.hidden = true; }, 150); });
    this.root.querySelector('.cset').addEventListener('change', function () { self.drawMA(); saveUrl(); });
    this.root.querySelector('.ckind').addEventListener('change', function () { self.loadPred(); saveUrl(); });
    this.root.querySelector('.ctf').addEventListener('change', function () {
      var v = self.root.querySelector('.ctf').value, o = other(self), ot = o.settings().tf;
      var both = document.getElementById('sync').checked && document.getElementById('same').checked && ot !== v && (isIntra(v) || isIntra(ot));
      // 분봉으로 들어가거나 나올 때는 (동기화·같은 종목이면) 양쪽이 함께 바뀐다 — 분봉과 일봉은 서로 맞출 수 없어서 한쪽만 바뀌면 어색하다
      function fixAi(p) { if (isIntra(v) && p.root.querySelector('.ckind').value === 'ai') p.root.querySelector('.ckind').value = 'dante'; }   // AI 는 일봉 이상에서만
      fixAi(self);
      if (both) { o.root.querySelector('.ctf').value = v; fixAi(o); }
      Promise.all(both ? [self.load(), o.load()] : [self.load()]).then(function () { showBoth(); saveUrl(); });
    });
  };

  function saveUrl() {
    try {
      var u = new URL(location.href), a = panes.a.settings(), b = panes.b.settings();
      u.searchParams.set('a', panes.a.code); u.searchParams.set('b', panes.b.code);
      u.searchParams.set('sa', a.set); u.searchParams.set('sb', b.set);
      u.searchParams.set('ta', a.tf); u.searchParams.set('tb', b.tf);
      u.searchParams.set('ka', a.kind); u.searchParams.set('kb', b.kind);
      history.replaceState(null, '', u);
    } catch (e) { /* 주소 저장 실패는 무시 */ }
  }

  var panes = { a: new Pane('a'), b: new Pane('b') };
  window.__compare = panes;  // 브라우저 콘솔·자동 점검용
  [panes.a, panes.b].forEach(function (p) { p.root.querySelector('.cname').textContent = BOOT[p.side].name; });

  window.addEventListener('resize', function () {
    [panes.a, panes.b].forEach(function (p) { p.chart.applyOptions({ width: p.el.clientWidth }); });
  });

  function showBoth() {
    // 한쪽(A) 범위를 기준으로 양쪽을 같은 기간에 맞춘다. 데이터를 막 바꾼 직후에는 차트가 옛 범위 이벤트를
    // 뒤늦게 내보내므로, 잠시 동기화 이벤트를 막고(syncing) 두 번에 걸쳐 맞춘다.
    function apply() {
      syncing = true;
      panes.a.showRange();
      var r = panes.a.chart.timeScale().getVisibleRange();
      if (syncOn() && r) { try { panes.b.chart.timeScale().setVisibleRange(r); } catch (e) { panes.b.showRange(); } }
      else panes.b.showRange();
      setTimeout(function () { syncing = false; }, 120);
    }
    apply();
    setTimeout(apply, 150);
  }

  Promise.all([panes.a.load(), panes.b.load()]).then(showBoth);

  document.getElementById('range').addEventListener('click', function (e) {
    var b = e.target.closest('button'); if (!b) return;
    Array.prototype.forEach.call(this.children, function (x) { x.classList.toggle('on', x === b); });
    bars = parseInt(b.getAttribute('data-bars'), 10);
    showBoth();
  });
  document.getElementById('sync').addEventListener('change', function () { showBoth(); });

  document.getElementById('quick').addEventListener('click', function (e) {
    var b = e.target.closest('button'); if (!b) return;
    var q = b.getAttribute('data-q'), A = panes.a, B = panes.b;
    function setSel(p, set, tf, kind) {
      p.root.querySelector('.cset').value = set; p.root.querySelector('.ctf').value = tf;
      if (kind) p.root.querySelector('.ckind').value = kind;
    }
    if (q === 'swap') {
      var sa = A.settings(), sb = B.settings(), ca = A.code, cb = B.code;
      var na = A.root.querySelector('.cname').textContent, nb = B.root.querySelector('.cname').textContent;
      setSel(A, sb.set, sb.tf, sb.kind); setSel(B, sa.set, sa.tf, sa.kind);
      Promise.all([A.setStock(cb, nb), B.setStock(ca, na)]).then(function () { showBoth(); saveUrl(); });
      return;
    }
    var name = A.root.querySelector('.cname').textContent;
    if (q === 'sets') { setSel(A, 'a', 'D'); setSel(B, 'b', 'D'); }
    if (q === 'tf') { setSel(A, 'a', 'D'); setSel(B, 'a', 'W'); }
    if (q === 'scalp') { setSel(A, 'a', '5m', 'dante'); setSel(B, 'a', '15m', 'dante'); bars = 120; }
    Promise.all([A.setStock(A.code, name), B.setStock(A.code, name)]).then(function () { showBoth(); saveUrl(); });
  });
})();
