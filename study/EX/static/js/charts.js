/* 작은 SVG 차트: 세로 막대(누적 가능)·가로 막대·선. 마우스를 올리면 값 표시. */
(function () {
  'use strict';
  var NS = 'http://www.w3.org/2000/svg';

  function el(tag, attrs, parent) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) if (attrs[k] !== undefined) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }

  function fmt(v) {
    if (v === null || v === undefined) return '–';
    var a = Math.abs(v);
    if (a >= 1e8) return (v / 1e8).toFixed(a >= 1e9 ? 0 : 1).replace(/\.0$/, '') + '억';
    if (a >= 1e4) return (v / 1e4).toFixed(a >= 1e5 ? 0 : 1).replace(/\.0$/, '') + '만';
    if (Number.isInteger(v)) return v.toLocaleString('ko-KR');
    return v.toLocaleString('ko-KR', { maximumFractionDigits: 2 });
  }
  function full(v) {
    if (v === null || v === undefined) return '–';
    return v.toLocaleString('ko-KR', { maximumFractionDigits: 2 });
  }

  function niceMax(m) {
    if (m <= 0) return 1;
    var p = Math.pow(10, Math.floor(Math.log10(m)));
    var steps = [1, 2, 2.5, 5, 10];
    for (var i = 0; i < steps.length; i++) if (steps[i] * p >= m) return steps[i] * p;
    return 10 * p;
  }

  function tooltip(box) {
    var t = document.createElement('div');
    t.className = 'tooltip';
    box.appendChild(t);
    return {
      show: function (x, y, html) { t.innerHTML = html; t.style.left = x + 'px'; t.style.top = y + 'px'; t.classList.add('show'); },
      hide: function () { t.classList.remove('show'); }
    };
  }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; });
  }

  function legend(box, spec) {
    if (spec.series.length < 2) return;
    var lg = document.createElement('div');
    lg.className = 'chart-legend';
    spec.series.forEach(function (s, i) {
      var span = document.createElement('span');
      var dot = document.createElement('i');
      dot.className = (spec.classes ? 'k-' + spec.classes[i] : 'k' + (i + 1));
      span.appendChild(dot);
      span.appendChild(document.createTextNode(s.name));
      lg.appendChild(span);
    });
    box.appendChild(lg);
  }

  function columns(box, spec) {
    var W = Math.max(box.clientWidth, 280), H = spec.height || 220;
    var pad = { l: 44, r: 8, t: 14, b: 26 };
    var n = spec.labels.length;
    var totals = spec.labels.map(function (_, i) {
      return spec.series.reduce(function (a, s) { return a + Math.max(0, s.values[i] || 0); }, 0);
    });
    var max = niceMax(Math.max.apply(null, totals.concat([0])));
    var svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'img', 'aria-label': spec.title || '막대 차트' }, box);
    var ph = H - pad.t - pad.b, pw = W - pad.l - pad.r;
    var ax = el('g', { 'class': 'axis' }, svg);
    for (var k = 0; k <= 4; k++) {
      var y = pad.t + ph - ph * k / 4;
      el('line', { x1: pad.l, x2: W - pad.r, y1: y, y2: y, 'class': k ? 'gridline' : 'baseline' }, ax);
      el('text', { x: pad.l - 6, y: y + 4, 'text-anchor': 'end' }, ax).textContent = fmt(max * k / 4);
    }
    var bw = pw / n, barW = Math.max(4, Math.min(42, bw * 0.62));
    var every = Math.ceil(n / Math.max(1, Math.floor(pw / 46)));
    var tip = tooltip(box);
    spec.labels.forEach(function (lab, i) {
      var cx = pad.l + bw * i + bw / 2, base = pad.t + ph;
      var marks = [];
      spec.series.forEach(function (s, si) {
        var v = Math.max(0, s.values[i] || 0);
        if (!v) return;
        var h = ph * v / max;
        var cls = spec.classes ? 's-' + spec.classes[si] : 's' + (si + 1);
        var top = base - h;
        var gap = marks.length ? 2 : 0;
        marks.push(el('rect', { x: cx - barW / 2, y: top, width: barW, height: Math.max(0, h - gap), rx: 3, 'class': 'mark ' + cls }, svg));
        base = top;
      });
      if (i % every === 0) el('text', { x: cx, y: H - 8, 'text-anchor': 'middle', 'class': 'lbl' }, svg).textContent = lab;
      var hit = el('rect', { x: pad.l + bw * i, y: pad.t, width: bw, height: ph, 'class': 'hit' }, svg);
      hit.addEventListener('mouseenter', function () {
        var html = '<div>' + esc(lab) + '</div>' + spec.series.map(function (s) {
          return (spec.series.length > 1 ? esc(s.name) + ' ' : '') + '<b>' + full(s.values[i]) + '</b>';
        }).join('<br>');
        tip.show(cx / W * box.clientWidth, (pad.t + ph - ph * totals[i] / max) / H * svg.getBoundingClientRect().height, html);
        marks.forEach(function (m) { m.classList.add('hover'); });
      });
      hit.addEventListener('mouseleave', function () { tip.hide(); marks.forEach(function (m) { m.classList.remove('hover'); }); });
    });
  }

  function hbars(box, spec) {
    var W = Math.max(box.clientWidth, 280);
    var n = spec.labels.length, row = 26;
    var H = n * row + 8;
    var labW = Math.min(140, Math.max(60, W * 0.28));
    var vals = spec.series[0].values;
    var max = Math.max.apply(null, vals.map(function (v) { return Math.abs(v || 0); }).concat([0])) || 1;
    var svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'img', 'aria-label': spec.title || '가로 막대 차트' }, box);
    var valW = 70, pw = W - labW - valW - 10;
    var tip = tooltip(box);
    spec.labels.forEach(function (lab, i) {
      var y = 4 + i * row, v = vals[i] || 0;
      var w = Math.max(v ? 2 : 0, pw * Math.abs(v) / max);
      var t = el('text', { x: labW - 8, y: y + row / 2 + 4, 'text-anchor': 'end', 'class': 'lbl' }, svg);
      t.textContent = lab.length > 14 ? lab.slice(0, 13) + '…' : lab;
      var bar = el('rect', { x: labW, y: y + 5, width: w, height: row - 10, rx: 3, 'class': 'mark s1' }, svg);
      el('text', { x: labW + w + 6, y: y + row / 2 + 4, 'class': 'val' }, svg).textContent = fmt(vals[i]);
      var hit = el('rect', { x: 0, y: y, width: W, height: row, 'class': 'hit' }, svg);
      hit.addEventListener('mouseenter', function () {
        bar.classList.add('hover');
        var sc = svg.getBoundingClientRect().width / W;
        tip.show((labW + w / 2) * sc, (y + 4) * sc, esc(lab) + ' <b>' + full(vals[i]) + '</b>' +
          (spec.extra ? '<br>' + esc(spec.extra[i]) : ''));
      });
      hit.addEventListener('mouseleave', function () { bar.classList.remove('hover'); tip.hide(); });
    });
  }

  function line(box, spec) {
    var W = Math.max(box.clientWidth, 280), H = spec.height || 220;
    var pad = { l: 44, r: 14, t: 14, b: 26 };
    var vals = spec.series[0].values, n = vals.length;
    var max = niceMax(Math.max.apply(null, vals.concat([0])));
    var svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'img', 'aria-label': spec.title || '선 차트' }, box);
    var ph = H - pad.t - pad.b, pw = W - pad.l - pad.r;
    var ax = el('g', { 'class': 'axis' }, svg);
    for (var k = 0; k <= 4; k++) {
      var yy = pad.t + ph - ph * k / 4;
      el('line', { x1: pad.l, x2: W - pad.r, y1: yy, y2: yy, 'class': k ? 'gridline' : 'baseline' }, ax);
      el('text', { x: pad.l - 6, y: yy + 4, 'text-anchor': 'end' }, ax).textContent = fmt(max * k / 4);
    }
    var x = function (i) { return pad.l + (n === 1 ? pw / 2 : pw * i / (n - 1)); };
    var y = function (v) { return pad.t + ph - ph * (v || 0) / max; };
    var d = vals.map(function (v, i) { return (i ? 'L' : 'M') + x(i) + ' ' + y(v); }).join(' ');
    el('path', { d: d, 'class': 'line1' }, svg);
    var every = Math.ceil(n / Math.max(1, Math.floor(pw / 60)));
    spec.labels.forEach(function (lab, i) {
      if (i % every === 0 || i === n - 1) el('text', { x: x(i), y: H - 8, 'text-anchor': 'middle', 'class': 'lbl' }, svg).textContent = lab;
    });
    if (n <= 24) vals.forEach(function (v, i) { el('circle', { cx: x(i), cy: y(v), r: 4, 'class': 'dot1' }, svg); });
    var cross = el('line', { y1: pad.t, y2: pad.t + ph, 'class': 'cross', visibility: 'hidden' }, svg);
    var tip = tooltip(box);
    var hit = el('rect', { x: pad.l, y: pad.t, width: pw, height: ph, 'class': 'hit' }, svg);
    hit.addEventListener('mousemove', function (ev) {
      var r = svg.getBoundingClientRect(), sc = r.width / W;
      var px = (ev.clientX - r.left) / sc;
      var i = n === 1 ? 0 : Math.round((px - pad.l) / pw * (n - 1));
      i = Math.max(0, Math.min(n - 1, i));
      cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i)); cross.setAttribute('visibility', 'visible');
      tip.show(x(i) * sc, y(vals[i]) * sc, esc(spec.labels[i]) + ' <b>' + full(vals[i]) + '</b>');
    });
    hit.addEventListener('mouseleave', function () { cross.setAttribute('visibility', 'hidden'); tip.hide(); });
  }

  function render(box, spec) {
    box.innerHTML = '';
    if (!spec || !spec.labels || !spec.labels.length) {
      box.innerHTML = '<div class="empty">표시할 데이터가 없습니다.</div>';
      return;
    }
    legend(box, spec);
    if (spec.type === 'hbar') hbars(box, spec);
    else if (spec.type === 'line') line(box, spec);
    else columns(box, spec);
  }

  function boot() {
    var src = document.getElementById('chart-data');
    if (!src) return;
    var data = JSON.parse(src.textContent);
    var boxes = document.querySelectorAll('[data-chart]');
    function draw() {
      boxes.forEach(function (b) {
        var spec = data[b.getAttribute('data-chart')];
        if (!spec) return;
        spec.type = b.getAttribute('data-type') || spec.type;
        if (b.getAttribute('data-classes')) spec.classes = b.getAttribute('data-classes').split(',');
        render(b, spec);
      });
    }
    draw();
    var t;
    window.addEventListener('resize', function () { clearTimeout(t); t = setTimeout(draw, 150); });
  }
  document.addEventListener('DOMContentLoaded', boot);
})();
