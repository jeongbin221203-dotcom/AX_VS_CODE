/* 함수 사전: 목록·검색, 함수 하나의 상세(#NAME) */
(function () {
  'use strict';
  var F = window.EXDATA && window.EXDATA.functions;
  var P = window.EXDATA && window.EXDATA.problems;
  var main = document.getElementById('main');
  EX.header('functions.html');
  if (!F) { main.innerHTML = '<div class="card empty">함수 자료(data/functions.js)를 읽지 못했습니다.</div>'; return; }
  var esc = EX.esc;
  var byName = {};
  F.items.forEach(function (f) { byName[f.name.toUpperCase()] = f; (f.aliases || []).forEach(function (a) { byName[a.toUpperCase()] = f; }); });

  function trackBadges(f) {
    return (f.tracks || []).map(function (t) { return ' <span class="badge ' + t + '">' + esc(F.tracks[t] || t) + '</span>'; }).join('');
  }

  function viewList() {
    var h = '<div class="page-head"><div><h1>함수 사전</h1><p>함수 ' + F.items.length + '개 · 형식, 인수, 예제, 컴활 범위.</p></div>' +
      '<input id="fn-search" type="search" placeholder="함수 이름·뜻 검색 (예: VLOOKUP, 합계)" aria-label="함수 검색"></div>' +
      '<div id="fn-none" class="card empty hidden">찾는 함수가 없습니다. 함수 이름의 일부나 \'합계\'·\'개수\'·\'날짜\' 같은 말로 찾아 보세요.</div>';
    F.categories.forEach(function (c) {
      if (c[2] !== 'formula') return;
      var items = F.items.filter(function (f) { return f.category === c[0]; }).sort(function (a, b) { return a.name < b.name ? -1 : 1; });
      if (!items.length) return;
      h += '<section class="card" data-fn-group><h2>' + esc(c[1]) + '</h2><div class="fn-list">' + items.map(function (f) {
        var hay = f.name + ' ' + (f.aliases || []).join(' ') + ' ' + f.desc + ' ' + (f.keywords || '');
        return '<a class="fn-card" href="#' + encodeURIComponent(f.name) + '" data-fn="' + esc(hay) + '"><b>' + esc(f.name) + '</b>' + trackBadges(f) +
          '<span class="d">' + esc(f.desc.length > 60 ? f.desc.slice(0, 60) + '…' : f.desc) + '</span></a>';
      }).join('') + '</div></section>';
    });
    main.innerHTML = h;
    window.scrollTo(0, 0);
    var s = document.getElementById('fn-search');
    s.addEventListener('input', function () {
      var q = s.value.replace(/\s+/g, '').toUpperCase();
      document.querySelectorAll('[data-fn]').forEach(function (c) {
        c.classList.toggle('hidden', !!q && c.getAttribute('data-fn').replace(/\s+/g, '').toUpperCase().indexOf(q) < 0);
      });
      document.querySelectorAll('[data-fn-group]').forEach(function (g) { g.classList.toggle('hidden', !g.querySelector('[data-fn]:not(.hidden)')); });
      document.getElementById('fn-none').classList.toggle('hidden', !q || !!document.querySelector('[data-fn]:not(.hidden)'));
    });
  }

  function viewDetail(name) {
    var f = byName[name.toUpperCase()];
    if (!f) { main.innerHTML = '<div class="card empty">\'' + esc(name) + '\' 함수를 찾을 수 없습니다.<p><a class="btn" href="#">목록으로</a></p></div>'; return; }
    var names = {};
    names[f.name.toUpperCase()] = 1;
    (f.aliases || []).forEach(function (a) { names[a.toUpperCase()] = 1; });
    var related = P ? P.items.filter(function (p) { return (p.functions || []).some(function (n) { return names[n.toUpperCase()]; }); }) : [];
    var h = '<p><a href="#">← 함수 사전</a></p><div class="page-head"><div><h1 style="font-family:var(--mono)">' + esc(f.name) + '</h1><p>' + esc(f.desc) + trackBadges(f) + '</p></div></div>';
    h += '<section class="card"><h2>형식</h2><code class="answer-code">' + esc(f.syntax) + '</code>';
    if (f.args && f.args.length) h += '<table class="t" style="margin-top:8px"><thead><tr><th>인수</th><th>설명</th></tr></thead><tbody>' +
      f.args.map(function (a) { return '<tr><td><code>' + esc(a[0]) + '</code></td><td>' + esc(a[1]) + '</td></tr>'; }).join('') + '</tbody></table>';
    h += '</section>';
    if (f.example) h += '<section class="card"><h2>예제</h2><code class="answer-code">' + esc(f.example) + '</code>' + (f.example_desc ? '<p>' + esc(f.example_desc) + '</p>' : '') + '</section>';
    if (f.tips && f.tips.length) h += '<section class="card"><h2>알아 두기</h2><ul style="margin:0;padding-left:18px">' + f.tips.map(function (t) { return '<li>' + esc(t) + '</li>'; }).join('') + '</ul></section>';
    if (related.length) h += '<section class="card"><h2>이 함수를 쓰는 문제 ' + related.length + '개</h2><ul style="margin:0;padding-left:18px">' +
      related.slice(0, 20).map(function (p) { return '<li><a href="learn.html#/' + encodeURIComponent(p.id) + '">' + esc(p.title) + '</a> <span class="muted small">' + esc(p.id) + '</span></li>'; }).join('') + '</ul></section>';
    main.innerHTML = h;
    window.scrollTo(0, 0);
  }

  function route() {
    var h = decodeURIComponent(location.hash.replace(/^#/, ''));
    if (h) viewDetail(h); else viewList();
  }
  window.addEventListener('hashchange', route);
  route();
})();
