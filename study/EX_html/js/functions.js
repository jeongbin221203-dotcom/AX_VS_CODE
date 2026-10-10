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
    document.title = '함수 사전 · 엑셀 연습장';
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
    document.title = f.name + ' · 함수 사전 · 엑셀 연습장';
    var catName = {};
    F.categories.forEach(function (c) { catName[c[0]] = c[1]; });
    var h = '<div class="page-head"><div><div class="crumb"><a href="#">함수 사전</a> › ' + esc(catName[f.category] || '') + '</div>' +
      '<h1 class="mono">' + esc(f.name) + ((f.aliases || []).length ? ' <span class="small muted">(' + esc(f.aliases.join(', ')) + ')</span>' : '') + '</h1><div>' + trackBadges(f) + '</div></div></div>';
    h += '<div class="grid g2"><section class="card"><p style="margin-top:0">' + esc(f.desc) + '</p><div class="syntax">' + esc(f.syntax) + '</div>';
    if (f.args && f.args.length) h += '<div class="table-wrap" style="margin-top:12px"><table class="t"><thead><tr><th>인수</th><th>설명</th></tr></thead><tbody>' +
      f.args.map(function (a) { return '<tr><td class="nowrap"><b>' + esc(a[0]) + '</b></td><td>' + esc(a[1]) + '</td></tr>'; }).join('') + '</tbody></table></div>';
    h += '</section><section class="card"><h2>예제</h2><div class="formula-box"><code>' + esc(f.example || '') + '</code></div>' + (f.example_desc ? '<p>' + esc(f.example_desc) + '</p>' : '');
    if (f.tips && f.tips.length) h += '<h3>알아 둘 점</h3><ul>' + f.tips.map(function (t) { return '<li>' + esc(t) + '</li>'; }).join('') + '</ul>';
    h += '</section></div><section class="card"><h2>이 함수를 쓰는 문제 <span class="small muted">' + related.length + '</span></h2>';
    if (related.length) h += '<ul class="list">' + related.map(function (p) {
      var cat = p.category || p.id.replace(/-\d+$/, '');
      return '<li><div class="grow"><a class="title" href="learn.html#/' + encodeURIComponent(p.id) + '">' + esc(p.title) + '</a><div class="small muted">' + esc(catName[cat] || cat) + ' · ' +
        (p.type === 'formula' ? '수식' : '보기 고르기') + ' <span class="lv" title="난이도">' + '●'.repeat(p.level || 1) + '○'.repeat(3 - (p.level || 1)) + '</span></div></div><div class="nowrap">' +
        (p.tracks || []).map(function (t) { return '<span class="badge ' + t + '">' + esc(F.tracks[t] || t) + '</span> '; }).join('') + '</div></li>';
    }).join('') + '</ul>';
    else h += '<div class="empty">아직 연결된 문제가 없습니다.</div>';
    h += '</section>';
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
